# Handoff for the next session

Read this first, then `PROGRESS.md` (newest entry), then `SCREEN_MATRIX.md`. Rules: `AGENTS.md`.

## Where the work is

| Branch | Base | Holds | State |
|---|---|---|---|
| `v2/slice-1-camp-to-group` | `origin/v2/foundation` (f72d4f6) | People, groups, flags, the first community slice (10 commits) | Local only. Gate passed 2026-11-17 on e422cf0 |
| `v2/redesign-santuario-shell` | the branch above (98f9bf3) | Santuario: tokens, shell, Mi iglesia, groups, "Mis datos", meetings and attendance, these documents | Local only. Worktree `../Emmaus.worktrees/redesign-santuario`. Gate result in `PROGRESS.md` |

Nothing is pushed and no pull request is open: `PROGRESS.md`, "Needs the owner", says why.
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

## Next, in order

1. **The camp inside the shell** (matrix wave 2; audit R13). The largest visible inconsistency left.
   Design note first: an event becomes a place in the rail with the role's links under it; the bar
   and its byte-level tests stay for the module-off path. Risk: high (invariant 5); keep
   `station-flow.mjs` and `review-all.mjs` green and add a modules-on run of the review.
2. **Look at every camp and discipleship screen with the module on** at three widths; only the
   event administration's home and the event list were looked at. Fix what the new tokens disturb.
3. **Events for people** (P3-8, #61): an event that is not a camp, a member-facing page,
   "Inscribirme", "Mis eventos", then check-in. Needs a short decision record before code.
4. **Avisos** (#57) on Mi iglesia, with the notification preferences they need.
5. **The pastor's view of groups and attendance**, blocked on Q-C3 (do pastors see every group?).
6. Small: `tenant_integrity` checks for groups, meetings and links; day and sede filters in Grupos
   once a church has enough groups; a first-run welcome for a member (`core/onboarding.py` has the
   pattern for roles); a meeting's seed in `seed_community_demo`.

## Open questions for the owner

Q-C1 to Q-C4 in `docs/decisions/0008`, unchanged, plus:

- **Q-S1.** Is the serif voice (Newsreader) wanted on the camp's own screens too? Today it is, for a
  church on the new modules, except on the projector deck.
- **Q-S2.** "Eventos" replaces "Campamentos" in the shell. Keep both words, or move the page title too?
- **Q-S3.** Attendance is visible to the group's guides and the administration only. Should a member
  see their own? Should a pastor see totals per group?
- **Q-S4.** One flag (`community`) turns on the modules and the new look together. A separate flag
  for the look would let a church try one without the other; it was judged not worth a column yet.
