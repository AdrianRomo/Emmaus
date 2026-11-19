# Wave 10 — Servir: roles, rotation, swaps, reminders and the order of a date (#95, first part)

Branch `v2/wave-10-rotas`. App: `serving` (migration `0002_rotas`, additive). Everything stays behind the
`community` module and inside `request.church`.

## What was built

- **Roles and what a date needs.** A team has named roles (`TeamRole`, with "solo personas adultas"); a date says
  how many people of each it needs (`RoleNeed`); an assignment may name a role (`Assignment.role`, nullable).
  The team page and the date page say what is missing per role. A team without roles sees its pages as before.
- **Rotation.** Per role, the people in order (`RotationEntry`) and, per team, "cada N semanas"
  (`Team.rotation_every_weeks`). `serving.rotas.build_proposal` proposes people for the places still missing on
  the team's existing dates of the next 8 weeks; one screen shows it, editable, and "Confirmar" assigns.
- **Swaps.** `SwapRequest`: a person asks one eligible teammate; the assignment moves only on acceptance (and,
  with the team setting, on the coordinator's yes), in one transaction with the date, the assignment and the
  request locked.
- **Reminders.** `serving.reminders` and the scheduler job `recordatorios_servir`: one content-free e-mail per
  assignment (`ServiceReminder`, one-to-one), per-team setting none / 2 / 5 days (default none), per-person
  choice on "Servir" (`ServingPreference`).
- **Order of a date.** `PlanItem`: title, minutes, optional responsible person, in order, with "Copiar del anterior".
- Seed: `seed_community_demo --rotas`. People-merge inventory: five new relations, each with its builder and test.

## Findings about what existed

- A serving date is **not** tied to an event: `ServiceDate` has no foreign key to `events.Event` or to a series.
  It stays so; the rotation works on the dates the coordinator already wrote and creates none.
- The "existing clash check" was only `services.assigned_on` (same day, not declined), used for a warning.
  `rotas.excluded` is now the one rule, by day, not by hour.

## Product decisions taken here (conservative option; change them on the issue)

1. **One eligibility rule** (`rotas.excluded`) for the rotation, swap candidates, swap acceptance and approval:
   not an active member or a joined record; marked the day; already asked for that date (whatever the answer);
   serves that day on another date of any team; role for adults and not an adult by a **verified** date of birth.
2. **Adults-only is enforced on every assignment with that role**, by hand too, not only on proposals: a flag
   that only the rotation honoured would not protect anything.
3. **"Cada N semanas" means rest**: a person is not proposed when they hold (or are proposed) a date of the team
   less than N weeks away. Pure round-robin otherwise: the turn continues after whoever had the role last, and a
   skipped person simply loses that turn. No scoring, no statistics.
4. **The rotation needs roles.** A team that wants proposals defines at least one role and says how many each date
   needs; otherwise there is no number of places to fill.
5. **The confirmation carries a token** over every date, need, holder, option and proposed name of the page. If
   anything changed the page is shown again and nothing is assigned. People confirmed this way still answer
   "Confirmar / No puedo" themselves.
6. **Minors and swaps / orders.** A person whose record says they are a minor (verified or not) cannot ask for a
   swap, is never listed as a swap candidate and cannot be the responsible person of a moment: each would show
   where a minor will be to a teammate. They arrange it with the coordinator. Unknown age is not treated as minor
   here (it is for adults-only roles).
7. **Who sees a swap**: the two people, who coordinates the team, and the church administration (it manages every
   team already). Nobody else, teammates included.
8. **A swap accepted makes the assignment "Confirmó"** for the new person. It expires 7 days after it was asked or
   when the date starts (its hour; the end of its day when it has none), compared as instants. No reason is stored
   for any answer. One open request per assignment. Removing the teammate, moving the date to another day,
   declining or losing the date closes the request.
9. **The reminder does not name the team.** The brief's text was "Tienes un servicio próximo en «equipo»";
   `core.notify` refuses anything but the church name and a link, and a team's name can say something about a
   person. The e-mail says "Tienes un servicio próximo en «iglesia»" with the link to "Servir".
10. **Reminder recipients** as the notices: account that can sign in, e-mail in "Mis datos", record not joined,
    not declined, not opted out; active church with the module on; needs `EMMAUS_PUBLIC_BASE_URL` and a delivering
    backend. A swap or a change of day gives the new person/day its own reminder. Up to 3 attempts.
11. **Order of a date without notes.** Title (one line, 80 characters, no links), minutes (1–300) and responsible
    only; no free-text notes, no revision history. Read by who serves that date and has not said no.
12. **Role and moment names** are one short line; audit rows carry ids and fixed words, never those names.

## Routes (all under `/mi-iglesia/servir/`) and who may use them

Coordinator of the team or church administration (`views._coordinated_team` → `_may_manage`:
`is_church_admin or has_scope(ministry, manage)`), active team only: `equipos/<id>/organizar/`,
`roles/guardar/`, `roles/<id>/retirar/`, `rotacion/guardar/`, `ajustes/`, `equipos/<id>/rotacion/`,
`cambios/<id>/decidir/`, writes on `fechas/<id>/orden/…`.
The person: `mis-fechas/<assignment>/cambio/` (own assignment), `cambios/<id>/responder/` (who was asked),
`cambios/<id>/retirar/` (who asked), `recordatorios/`. Read of `fechas/<id>/orden/`: who serves that date
(`rotas.may_read_plan`) or who manages the team. Another church's id is a 404; module off is a 404.

## Not built

- Safeguarding training / background-check expiry records: **out of scope** (church leadership's decision).
- Songs, chords, lyrics, files, links, usage/licensing records (needs the rights approval of #99).
- Revision history of an order; tying an order to an event's programme; dates created from a series or an event.
- E-mail or notice when a swap is asked, answered or approved (the screens say nothing is sent).
- Clash checks by hour; "undo" of a confirmed proposal (dates are edited one by one); pagination of long lists.
- (Added at integration: `tenant_integrity` reads the new tables that hold two links which could disagree —
  `assignment_role_team`, `role_need_role_team`, `rotation_entry_person_church`, `swap_request_from_person_church`,
  `swap_request_to_person_church`, `plan_item_responsible_church`. `TeamRole`, `ServingPreference` and
  `ServiceReminder` hold one link each: nothing to compare.)

## Not verified

- PostgreSQL: only SQLite ran here. `select_for_update(of=("self",))` is used where a query joins; the partial
  unique constraint and the lock order (date, team, assignment, swap) were not exercised on PostgreSQL.
- Real concurrency: races are tested with stale copies and a patched reader, not with two connections.
- Mail delivery beyond the in-memory backend. Screens were looked at with the synthetic seed only.
- A residual race: a teammate assigned by **another team** at the instant a swap is accepted is not locked out.
