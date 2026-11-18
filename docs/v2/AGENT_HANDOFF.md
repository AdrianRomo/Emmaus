# Handoff for the next session

Read this first, then `PROGRESS.md` (newest entry), then `SCREEN_MATRIX.md`. Rules: `AGENTS.md`.

## Where the work is

| Branch | Base | Holds | State |
|---|---|---|---|
| `v2/foundation` | | Everything through wave 4: pull requests #73, #74 and #78 are merged | The base. Never merged into `main`; nothing is deployed |
| `v2/wave-7` | `v2/foundation` (waves 1 to 6 merged: #73, #74, #78, #79, #81) | Serving through `has_scope` (decision 0016), integrity checks, the member's welcome, pagination, "Ahora y después" | Worktree `../Emmaus.worktrees/redesign-santuario`. Gate in `PROGRESS.md`; its pull request is named there |
| older `v2/redesign-*`, `v2/wave-4-*`, `v2/wave-5-*`, `v2/wave-6*`, `v2/wave-7-<slice>` | | Merged | Local only; the worktrees and branches can be removed |

The owner said on 2026-11-18 to merge pull requests as needed. The rule followed: only into
`v2/foundation`, only when the branch's required checks pass on the pull request (never with
`--admin`), never into `main`, never a deploy. `v2/foundation`'s ruleset requires the CI jobs,
which run on pull-request events: after retargeting a stacked PR, merge the base into its branch
and push so they run.

After pulling wave 5 on a database that already had the earlier code: run
`manage.py resettle_registrations` (dry run, then `--apply`), see `docs/deployment.md`.

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

For a fuller demonstration add `--panorama --eventos` to `seed_community_demo`: a pastor who is not
an administrator (`pastor.iglesia@demo.invalid`), weeks of group lists, a simple event that starts
today with a programme, questions, a visitor and two minors (one waiting for the authorization),
an important notice, and an event that ended with opinions. The browser gate uses the plain seed.

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
write in `services.py` and one audit row per write. Until wave 4 they could not touch `events` or
`core` (decision 0005). **Decision 0013 lifted that**: additive migrations are allowed there, and
`events/0013` (`Event.camp_tools`) is the first. The gate fails a migration that removes, renames,
runs raw SQL, has a `RunPython` with no way back, or changes a migration that already existed,
unless the PR carries `risk:high`.

| App | What | Read from the home and the shell through |
|---|---|---|
| `notices` | Avisos for the church or one group, with a date to appear, a date to expire and a read state | `notices.services.for_home`, `unread_count` |
| `agenda` | `EventPage` beside `events.Event` (published, description, hour, sign-up, capacity) and `EventRegistration` (sign-up, attendance) | `agenda.services.upcoming_for` |
| `resources` | `Devotional` (one per church and day) with a private read mark; `MediaItem` as links by series and speaker | `resources.services.today_devotional`, `latest_media` |
| `serving` | `Team`, `TeamMembership` (coordina / sirve), `ServiceDate`, `Assignment` (por confirmar / confirmó / no puede), `Unavailable` | `serving.services.my_dates`, `pending_count`, `has_teams` |
| `overview` | "Panorama": totals for the church roles pastor and administration; no models | `overview.access.can_see_overview`, `overview.services.build` (10 queries) |
| `feedback` | `EventFeedback` (random key, no person, day only) and `FeedbackGiven` (who answered, no date); results after 14 days and from 3 answers | `feedback.services.pending_for`, `results` |
| `care` | `FollowUp` about a person with an owner, a date and a state; `FollowUpStep` from four fixed kinds; **no free text anywhere** | `care.services.open_count`, `unassigned_count` |

A PostgreSQL-only lesson from this round: `select_for_update()` with `select_related()` over a
nullable foreign key fails there and passes on SQLite. Use `select_for_update(of=("self",))`, and
run `quality-gate.sh --postgres` before believing a module's own SQLite run.

## Next, in order

The owner's order is decision 0014; decisions 0015 and issue #80 hold what was answered and what
is still a default. Built through wave 7: everything in 0014 up to and including the waiting
list, e-mail notices, church policies, the public event page, the camp issues #46–#51, and serving
through `has_scope` (decision 0016). Left:

1. **Host work, with the owner**: off-site backups and a restore drill (#45) before anything from
   v2 runs where the first camp's data lives; scheduling the commands listed in `PROGRESS.md`
   (wave 6); shipping the request log and alerting (#52); once after deploy:
   `resettle_registrations --apply` and `rebuild_role_assignments --apply`.
2. **Answers to #80**, above all Panorama's privacy rule and the door's override.
3. **Fold `agenda.EventPage` into `events`** as its own reviewed change (decision 0013).
4. **Multi-church accounts** (#32, #37): high risk, needs the owner and a supervised session; it
   becomes necessary before a second church is onboarded.
5. Small, none blocking: a survey for the camp's team; a church's own logo as the icon;
   pagination of an event's Inscritos; pre-dismissing the member's welcome for people who already
   use Mi iglesia; the interview and campero screens looked at by eye with the module on.
6. Later, each with its approval (decision 0014): guardian accounts and households (phase B,
   behind #75), reading plans, moderated photos (#60), the site map and workshops (#58, the
   whereabouts of minors: not built on purpose), children's check-in, payments recorded by staff,
   giving.

**Release gates. Do not enable in production before the issue is closed:** minors' registration
(#75: the authorization's wording, accepted evidence and retention are provisional) and the
retention periods of attendance (#76; no individual pastoral access is built).

## Open questions for the owner

Q-C1 to Q-C4 in `docs/decisions/0008`, unchanged. Issues #64, #66–#71 and #77 are answered and
closed; their answers are decisions 0009–0015. Open (see also the triage notes on #32, #37, #45,
#49, #50, #52, #58, #59, #60):

| Issue | What |
|---|---|
| #75 | Release gate: church leadership approves the authorization policy for minors |
| #76 | Release gate: named pastoral access to attendance, and the retention periods |
| #80 | Defaults taken in waves 5 and 6, to confirm or change |

Answered on 2026-11-17: the word is **"Eventos"** everywhere. Still without an issue, low stakes:
one flag (`community`) turns on the modules and the new look together.
