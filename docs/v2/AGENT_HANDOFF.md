# Handoff for the next session

Read this first, then `PROGRESS.md` (newest entry), then `SCREEN_MATRIX.md`. Rules: `AGENTS.md`.

## Where the work is

| Branch | Base | Holds | State |
|---|---|---|---|
| `v2/slice-1-camp-to-group` | `origin/v2/foundation` (f72d4f6) | People, groups, flags, the first community slice (10 commits) | Local only. Gate passed 2026-11-17 on e422cf0 |
| `v2/redesign-santuario-shell` | the branch above (98f9bf3) | Santuario: tokens, shell, Mi iglesia, groups, "Mis datos", meetings and attendance, the camp in the shell, "Eventos", and the merged modules `notices`, `agenda`, `resources`, `serving`, `care`; these documents | Local only. Worktree `../Emmaus.worktrees/redesign-santuario`. Gate result in `PROGRESS.md` |
| `v2/redesign-avisos`, `v2/redesign-eventos-personas`, `v2/redesign-recursos`, `v2/redesign-servir`, `v2/redesign-seguimiento` | the redesign branch | One module each, built by a sub-agent in its own worktree and **already merged** into the redesign branch | Local only; the worktrees and branches can be removed |

Nothing is pushed and no pull request is open: issue #64 asks the owner.
The second branch is stacked on the first; it cannot be reviewed or merged before it.

## How to run it

```bash
cd ../Emmaus.worktrees/redesign-santuario
P=/home/adrian/projects/Emmaus/.venv/bin/python        # the worktree has no .venv of its own
export DEBUG=true DATABASE_URL=sqlite:////tmp/emmaus-dev.sqlite3
$P manage.py migrate && $P manage.py seed_camp_demo --password DemoCamp-2026 \
  && $P manage.py seed_dev_data --password DemoCamp-2026 && $P manage.py seed_community_demo --password DemoCamp-2026
$P manage.py runserver 127.0.0.1:8765     # with the reloader: --noreload serves stale templates
```

Accounts (synthetic, password `DemoCamp-2026`): `valeria.soto@demo.invalid` (former camper, no
group), `emilio.vargas@demo.invalid` (request pending), `guia.grupo@demo.invalid` (guides two
groups), `admin.comunidad@demo.invalid` (church administration).

Gates: `PYTHON=$P scripts/agents/quality-gate.sh --postgres --e2e`. The browser gate needs
`e2e/node_modules`; in a worktree, symlink it to the main checkout's (never `git add -A`: the
symlink is untracked on purpose). The community journey alone:
`PYTHON=$P scripts/agents/e2e-gate.sh --skip-flow --skip-review`.

## What to know before touching the design

- Santuario is a **layer**: `static/css/santuario.css` after `rotulo.css`, only under
  `<html data-ui="2">`. Do not edit `rotulo.css` for the redesign: a church with the module off must
  get the same bytes (`people/tests/test_screens_unchanged.py`, the bar's byte-level tests).
- The navigation is data: add a destination in `core/shell.py`, not in a template. A link is listed
  only when its view would answer; the view still checks by itself.
- Tokens go on `:root[data-ui="2"]`, never bare `:root`: the church's own colours must keep winning
  (`core/test_shell.py::PiecesTests`).
- Atrio's rules still hold: one filled button per form, one next-step panel per screen, no icons
  outside the navigation, no shadows or gradients, data as a sentence, natural Mexican Spanish in tú.
- New page checklist: `docs/design-system.md` §7, plus: add it to `e2e/community-flow.mjs`, add its
  view name to the right set in `core/shell.py` so the rail marks the place, and add a row to
  `SCREEN_MATRIX.md`.

## How the five newer modules are put together

Each is its own app with its own migration, behind the `community` flag (no new flag), with every
write in `services.py` and one audit row per write. They do not touch `events`, `core`, `people`
or `groups` models: the CI gate `migration-scope` forbids `core/` and `events/` migrations
(decision 0005; issue #68 asks to lift it).

| App | What | Read from the home and the shell through |
|---|---|---|
| `notices` | Avisos for the church or one group, with a date to appear, a date to expire and a read state | `notices.services.for_home`, `unread_count` |
| `agenda` | `EventPage` beside `events.Event` (published, description, hour, sign-up, capacity) and `EventRegistration` (sign-up, attendance) | `agenda.services.upcoming_for` |
| `resources` | `Devotional` (one per church and day) with a private read mark; `MediaItem` as links by series and speaker | `resources.services.today_devotional`, `latest_media` |
| `serving` | `Team`, `TeamMembership` (coordina / sirve), `ServiceDate`, `Assignment` (por confirmar / confirmó / no puede), `Unavailable` | `serving.services.my_dates`, `pending_count`, `has_teams` |
| `care` | `FollowUp` about a person with an owner, a date and a state; `FollowUpStep` from four fixed kinds; **no free text anywhere** | `care.services.open_count`, `unassigned_count` |

A PostgreSQL-only lesson from this round: `select_for_update()` with `select_related()` over a
nullable foreign key fails there and passes on SQLite. Use `select_for_update(of=("self",))`, and
run `quality-gate.sh --postgres` before believing a module's own SQLite run.

## Next, in order

Decisions are open as issues #64 to #71; without an answer, follow this order and skip what is blocked.

1. **A simple event that is not a camp** (blocked on #68): creation without the interview set-up,
   and "Eventos donde sirves" telling a camp from a service.
2. **Walk-ups at an event** (#69, recommended first): the administration adds someone at the door.
3. **The pastor's view of groups, attendance, sign-ups and serving** (blocked on #66).
4. **Serving through `has_scope`**: `serving` decides who coordinates by its own rows; decision 0003
   wants every decision in `core.authz`. Add a ministry scope to `access` with a decision record.
5. **A first-run welcome for a member** (`core/onboarding.py` has the pattern for roles), and a
   coordinator who is also a person of the church in the seed, so the automated modules-on review
   sees an event role inside the shell (it was checked by hand).
6. Small: `tenant_integrity` checks for every new table; pagination where lists stop at 50, 100 or
   200; day and sede filters in Grupos; a WhatsApp share link on a notice (#70); a compact list of
   a date's people in `serving`; look at the interview and campero screens with the module on.
7. Not started and waiting on decisions: guardians and households (#67), check-in for children,
   photos (#60), FAQ (#55), programme (#56), post-event survey (#54), giving (#71).

## Open questions for the owner

Q-C1 to Q-C4 in `docs/decisions/0008`, unchanged. The rest are GitHub issues, each with the default
that is in place until answered:

| Issue | Decision |
|---|---|
| #64 | Push the branches and open draft PRs? |
| #65 | Keep the serif titles on the camp's own screens? |
| #66 | Who may read group attendance? |
| #67 | Minors signing up for events, and guardian accounts |
| #68 | Lift the migration freeze on `events` and `core`? |
| #69 | Waiting list, household sign-up, walk-ups, payments |
| #70 | Should a notice also go out by e-mail or WhatsApp? |
| #71 | Order and scope of the modules that are not built |
| #72 | (bug, camp) Atrio's bar wraps a long event name for a pastor at 1440 px |

Answered on 2026-11-17: the word is **"Eventos"** everywhere. Still without an issue, low stakes:
one flag (`community`) turns on the modules and the new look together.
