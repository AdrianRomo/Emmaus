# Wave 12 — what a church repeats every week (#94 the part left out, #95 from its open list)

Branch `v2/wave-12-repeticion`. Apps: `espacios` (migration `0002_reservas_que_se_repiten`) and `serving`
(migration `0003_lo_que_se_repite`), both additive. `agenda/` was only read (`agenda.series`' public
functions and models). Everything stays behind the `community` module and inside `request.church`.

## What was built

### A. Bookings that repeat (`espacios/repeticion.py`)

- `BookingSeries`: a rule (space, title, every 1–4 weeks, from/until, two hours on the church's wall clock,
  buffers, who asked, status, optional `event_series`). The weekday is the one `starts_on` falls on.
- `BookingSeriesDay`: the series' memory of each day, unique by (series, day): `apartada` (with its ordinary
  `Booking`, one-to-one), `ocupado` (another booking held the hours: nothing was booked) or `sin_hora` (that
  local hour does not exist that day). The `Booking` table did not change.
- `generate` writes ordinary approved bookings for a rolling window of 8 weeks (`agenda.series.horizon`), under
  the space's lock, after reading what overlaps — the same rule as `request_booking`. It runs when a series
  is approved and on every pass of the scheduler. A second run writes nothing.
- A day that clashes is **not** booked and is not retried: it is listed on the series («No se pudo apartar: ya
  está ocupado»). The administration opens it, sees what is in the way now and books it with the existing
  audited override (`book_day` → `services._check_clash` with the ids it saw → `espacio_reserva_sobre_choque`),
  or leaves it. If the hours are free again, the same page simply books the day.
- One decision per series: approve or reject (with a reason only the administration reads). «Este día no» is the
  existing cancellation of that day's booking; its row stays, so the day is not booked again. Ending a series
  stops new dates and leaves the ones that exist; who asked ends (or, while it waits, withdraws) their own.
- A series tied to an `agenda.EventSeries` of the same church (administration only): its days are that series'
  occurrences that are on, at the hour each event starts, with the booking's own duration and buffers; the
  booking carries the event. A day called off before it is written is skipped and written if it is on again.
- The week of a space: the administration reads, on a day a series in force has not reached, the quiet line
  «Suele ocuparse de 19:30 a 20:30: …, cada domingo. Todavía no está apartado.» Nobody else does.
- Screens: the request form's «Si se repite»; the series page; rejecting a series; deciding a listed day; the
  queue and «Lo que pediste» list a series as one row; a booking row says «Se repite cada …» to who reads its title.

### B. Servir: dates that follow a series, and templates of the order (`serving/follow.py`)

- `Team.follows_series`: "servimos en cada fecha de «serie»". `sync_team` writes one ordinary `ServiceDate` per
  occurrence that is on and has not begun (`ServiceDate.occurrence`, unique by team and occurrence), with the
  event's day, hour, name and place, the role needs each role asks "por omisión" (`TeamRole.default_count`,
  new) and the team's default template. Nobody is assigned by it.
- A day the series calls off cancels the team's date **at read time** (`ServiceDate.called_off` reads the
  occurrence's status): nothing is written, so it holds the instant the administration presses «Este día no hay»
  in Eventos, with no change to `agenda`. The assignments stay and are listed for the coordinator under «Este
  día no hay» («Emmaus no avisa: avísales tú»); the person reads «Cancelada» on Servir with nothing to answer.
  Such a date takes no write, no answer, no swap, no reminder and no proposal, and does not make anybody "busy".
  If the day is on again, the date is too, with its people and answers.
- `PlanTemplate` / `PlanTemplateItem`: titles and minutes in order, no people. Saved from a date's order, used
  only into an empty order («Copiar del anterior» stays), 10 per team, removable. `Team.default_template`.
- One scheduler job, `lo_que_se_repite` (acting; after `series_eventos`): `repeticion.generate_due` and
  `follow.sync_due`, each isolated; a failure is `RepetitionFailed`. Skipped with the community switches; each
  half skips a church that is not active or has the module off.
- Seed: `seed_community_demo --repeticion`. `tenant_integrity`: four checks. People merge: `BookingSeries.requested_by` moves.

## Product decisions taken (conservative; change them on the issue)

| Decision | Why |
|---|---|
| A series that waits for approval **holds nothing**; its dates are written when it is approved | Holding eight weeks of a room for an undecided request blocks everybody else. The form and the series page say so |
| A day that clashed is decided once, by a person; the generator never retries it | "Listed for administration to decide" must not be undone silently when the other booking is cancelled |
| Generated bookings are approved by who approved the series (`decided_by`), asked by who asked | The audit and the rows say whose decision each date is |
| A series keeps writing dates when who asked loses their role | It is the church's activity; it ends at its last day (≤ 6 months) or when the administration ends it |
| The rule cannot be edited: end it and ask again | As a single booking's hours (wave 9) |
| At most 183 days ahead (`MAX_AHEAD_DAYS`), every 1–4 weeks (also a database check), 40 open series per space, and 3 series waiting for a decision per person per space (`MAX_WAITING_PER_REQUESTER`, review of wave 12; the administration's and a space without approval are not waiting) | Bounded input: one person cannot use up a space's 40 |
| «Suele ocuparse» skips a day the followed series of events called off (review of wave 12) | The generator skips it too; the line must not say the room is usually taken that day |
| A series' end hour is on the wall clock too; a day whose start or end hour does not exist is skipped and listed | 10:00–11:30 stays 10:00–11:30 across a change of clock |
| Linked series: the name of the event series is shown to the administration only; who asked reads "en cada fecha de una serie de eventos" | A series still in preparation is not theirs to read |
| An event date called off **after** its booking exists does not cancel the booking; the series page flags it («Revisa») | The brief asks to skip; freeing a room silently could lose it. One press («Este día no») frees it |
| «Suele ocuparse» is shown for any future day of the rule with no row yet, administration only | "Beyond the window", and still true if the scheduler is switched off |
| Who coordinates may choose only published, active series; the administration any active one | An unpublished series' name is not a coordinator's to read in a select |
| A day that already has a hand-made date gets no generated date | "Existing dates are untouched", and no duplicates |
| A date written for the followed series cannot be deleted while the team follows it | It would be written again. The page says to set its needs to zero, or stop following |
| A generated date does not follow later changes of its event; the team page says «Revisa … Esta fecha no cambia sola» | Moving a date asks everybody again: a person decides |
| Stopping following leaves every date written | Reversible; nothing is deleted |
| Saving a template with a name that exists is refused (no overwrite) | Less surprising than replacing |
| The person sees the cancelled date on Servir (not on «Mi iglesia», not in the pending count) | Honest without promising a notice |
| New audit rows carry ids, counts and fixed words only | Invariant |

## Routes and who may use them

Espacios (`/mi-iglesia/espacios/`), all `community_view` (module, account, person of `request.church`):

| Route | Who | Rule |
|---|---|---|
| `<space>/pedir/` (now also a series) | who may ask | `access.can_request`, `espacios/views.py::request_booking`; `event_series` only in the administration's form and refused by `repeticion.request_series` otherwise |
| `series/<id>/`, `series/<id>/terminar/` | who asked, the administration; else 404 | `espacios/views.py::_series` |
| `series/<id>/aprobar/` (POST), `series/<id>/rechazar/`, `series/<id>/dias/<day>/apartar/` | administration | `church_admin_view` + `_series` |
| `reservas/<id>/cancelar/` («Este día no») | unchanged | `access.can_read_details` |

Servir (`/mi-iglesia/servir/`): `equipos/<id>/serie/` (POST), `equipos/<id>/plantillas/<tid>/quitar/`,
`fechas/<id>/orden/plantilla/guardar/` (POST), `…/usar/` (POST): who coordinates the team or the administration
(`rota_views._working_team` / `_plan_date(to_edit=True)` → `views._may_manage`). Another church's id: 404. Module off: 404.

## Not built

Editing a series' rule; resuming an ended series; a notice or e-mail of any decision; monthly rules; "este día
no servimos" for one generated date; a generated date following its event's later changes; overwriting or
renaming a template; songs, chords, files or links in an order or a template (rights, #99); Django admin.

## Could not verify

- PostgreSQL and the browser gate were not run (the integrator's). Locks: space → series → day
  (`select_for_update(of=("self",))` where a query joins); team → date. Real concurrency was not run: races are
  tested with stale copies and the unique constraints.
- Screens were looked at with the synthetic seed at 390, 768 and 1440 px with axe (zero violations, no
  overflow, one h1, at most one filled button): Espacios, the queue, the series page (approved with a day not
  booked, waiting with a 105-character title, rejected, with a day off), rejecting a series, a listed day,
  ending, the week near and beyond the window, the request form and its errors, Organizar, the team page, the
  order (with folds open, and empty), removing a template, a generated date's form, a member's Servir and week.
  Opened as images and judged: the series page, the queue, the far week, the form with errors, Organizar, the
  team page, the order and the member's Servir (phone), the series page (desktop). The rest were checked by
  the script only. No new action acts in place (each has fields or leads to a page), so nothing was tried
  offline; keyboard and screen readers were not tried by hand.

## Recommended journey steps (`e2e/community-flow.mjs`, not edited)

1. Guide: «Pedir este espacio» with «Cada semana» and a last day → the series page says nothing is held.
2. Administration: Solicitudes → «Aprobar» the series → 8 «Fechas apartadas»; audit the page.
3. Guide: «Este día no» on one date → it moves to «Días que no»; the week shows the others.
4. Administration: a week ten weeks ahead shows «Suele ocuparse»; the guide's same week does not.
5. Coordinator: Organizar → «Servimos en cada fecha de» Reunión general → the team page lists the dates.
6. Administration: «Este día no hay» on a date of the series → the team page lists it under «Este día no hay»
   and the member's Servir says «Cancelada».

## Rollback

Revert the branch's commits. The new tables and columns can stay unused (all nullable or with database defaults).
