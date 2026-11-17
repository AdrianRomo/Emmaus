# Migration and rollout — first community slice

The whole path from the camp application to the church platform is
`docs/audits/EMMAUS_EVOLUTION_ROADMAP.md` §3 (phases) and §5 (schema steps). This page is what the
branch `v2/slice-1-camp-to-group` adds to it and how to put it on the sandbox.

## How v1 and v2 coexist

- **Nothing of the camp changes.** No migration under `core/` or `events/`; `events/access.py` and
  `core/access.py` are byte-identical; every test outside `people/` passes unmodified. In
  `people/tests/` three files changed: the migration pin, two migration round-trips that now restore
  every app, and the integrity warning, narrowed to people created from a church role.
- **New tables only**: `people_churchsettings`, `people_churchauditevent`,
  `people_eventparticipantlink`, `access_roleassignment`, `groups_group`, `groups_groupmembership`,
  `groups_groupjoinrequest`, plus one choice added to `people_person.created_from` (no column change).
- **Off by default.** Every new URL answers 404 until `set_church_module` turns a module on for a
  church; `EMMAUS_V2_MODULES=false` turns all of them off at once.
- **Files of the camp that were edited**, each behind the flag and covered by
  `people/tests/test_screens_unchanged.py`: `core/context_processors.py` (bar links),
  `core/views.py` (home of an account with no role; "Mi cuenta"), `templates/base.html` (the bar's
  label comes from the context), `templates/core/account.html`, `templates/core/ayuda.html`,
  `static/css/rotulo.css` (two rules appended).

## Data steps

| Step | Command | Reversible |
|---|---|---|
| Schema | `migrate` (people 0003, access 0001, groups 0001) | `migrate groups zero`, `migrate access zero`, `migrate people 0002` |
| People for existing accounts | `link_people` (dry run), then `--apply`; or implicitly by `set_church_module … on` for that church | delete `created_from="backfill"` people |
| Turn the modules on for a church | `set_church_module <slug> people on`, `… community on` | `… off` (nothing deleted) |
| Camperos → people | by hand, one by one, in Personas › Traer de un evento, after the event | "Quitar vínculo" on the person |
| Accounts for people | by hand, "Dar acceso" | deactivate the account |

## Sandbox (`emmaus-v2` stack, synthetic data)

Not done by this session: the stack follows `v2/foundation`, and agents do not merge or deploy
(`AGENTS.md`). After the pull request is merged, the homelab runbook applies as for any v2 change
(tag the running image, dump, build, `up -d --no-deps web`, smoke). Then, on the sandbox only:

```bash
python manage.py seed_community_demo --password "$EMMAUS_DEMO_PASSWORD"   # needs DEBUG=true
python manage.py rebuild_role_assignments --check
python manage.py tenant_integrity
```

Manual check, on a phone: sign in as `valeria.soto@demo.invalid` → lands on Mi iglesia, sees
"Campamento de Jóvenes 2025" under "Donde has estado" → Ver los grupos → Jóvenes Roca → Pedir
unirme. Sign in as `guia.grupo@demo.invalid` → Ver solicitudes → Aceptar. Back as Valeria: the
group, its next meeting and its place. As Valeria, `/mi-iglesia/personas/` must answer 403.

## Production

Nothing in this slice touches production. Before it ever does: the owner's answers to Q-C1…Q-C4
(decision 0008), a dump taken before `migrate`, the modules left off, and then one church switched
on deliberately. The first real use would be linking the camperos of the November 2026 camp, which
is personal data of minors: it needs the owner's explicit go.

## Irreversible or consequential, needing the owner

- Accepting decisions 0003, 0004 and 0008 (merging the pull request does it).
- Q-C1 retention of "took part in event X" after the purge; Q-C2 accounts for minors.
- SMTP on the hosts: until then every notice is "seen when they sign in" and access links are
  shared by hand.
