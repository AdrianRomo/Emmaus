# Runbook: the «Ventanas» redesign (branch `v2/redesign-claridad`)

What changed, where it lives, how to see it, how to revert it. Nothing here was pushed, merged into
`v2/foundation` or `main`, or deployed. No new dependencies. Three additive migrations, all from the owner's
decisions of 2026-11-18: `events/0016_follow_up_decision_change` (a new table), `events/0017_answer_declined`
(two boolean columns with a database default) and `events/0018_follow_up_decision_change_reopened` (choices only,
no SQL). The redesign itself changes no data.

## What changed

- One design system, «Ventanas» (`DESIGN_SYSTEM.md`), and its behaviour contract (`CRAFT.md`), applied to
  every screen of the product: the member's and the guide's «Mi iglesia», groups, Servir, Acompañar,
  events (people and administration), notices, devotional/resources/plans, people, Panorama, feedback,
  policies, the public page, church settings, account and the doors, help, discipleship, and the camp
  (coordinator, tutor, pastor, administration, interview, kiosk, a campero's record).
- One shared stylesheet layer, `static/css/santuario.css`, served only under `<html data-ui="2">` (a church
  with the community module on); `static/css/rotulo.css` is untouched. Shared JavaScript in
  `static/js/emmaus.js` (actions in place, busy/offline/timeout, toasts, focus) runs only under `data-ui="2"`.
- Fonts: Mona Sans (subset) and Source Serif 4 italic in `static/fonts/` (OFL licences beside them).
- Server-side changes, all tested: actions in place return to a safe `siguiente` (`core.nav.safe_next`);
  bulk assignment of coordinators (`events:participants_assign`, administration only, this event only,
  audited); `?autorizacion=falta` and `?estado=iniciada` on the camper list; «Por decidir» answered in place;
  «Nadie sirve el …» on a team page; messages that name who and what. Two messages also show with the
  module off (more specific, nothing new revealed): the interview record's follow-up decision and the
  group decisions.
- Module off (churches without the community module, i.e. the camp as it runs today): every shared template
  keeps its old markup byte for byte in an `{% else %}` branch (`people/tests/test_screens_unchanged.py`,
  per-area byte checks).

## Where

- Branch `v2/redesign-claridad`, worktree `../Emmaus.worktrees/redesign-claridad`. Area branches
  (`v2/area-*`, `v2/rc-*`) are merged into it and can be deleted after review.
- Evidence: `docs/v2/REVIEW_2026-11-18.md` (three reviews, two critiques, every defect and its commit),
  `docs/v2/REDESIGN_CHECKLIST.md` (areas, phases, the owner's open decisions), `docs/v2/USABILITY_TEST.md`
  (the test with real people: planned, not done).

## How to see it

```bash
cd ../Emmaus.worktrees/redesign-claridad
P=/home/adrian/projects/Emmaus/.venv/bin/python
export DEBUG=true DATABASE_URL=sqlite:////tmp/emmaus-redesign.sqlite3   # any scratch path
$P manage.py migrate && $P manage.py seed_camp_demo --password DemoCamp-2026 \
  && $P manage.py seed_dev_data --password DemoCamp-2026 \
  && $P manage.py seed_community_demo --password DemoCamp-2026 --panorama --eventos
$P manage.py runserver 127.0.0.1:8791        # check the port is free first: ss -ltn | grep :8791
```

Accounts: see `REDESIGN_CHECKLIST.md` and the seed commands' output (synthetic `@demo.invalid` only).

## Checks

```bash
DEBUG=true $P -m pytest -q -p no:cacheprovider --ignore=e2e     # ~2,700 tests, ~5 min
$P -m ruff check . && DEBUG=true $P manage.py check
PYTHON=$P bash scripts/agents/e2e-gate.sh                       # browser gate: flows, axe, overflow, h1
```

## The owner's decisions of 2026-11-18 (product behaviour, both modes)

- Interview questions are all optional; «Prefiero no responder» on every question, stored apart from a blank;
  «Antes de empezar» requires «Sí, es {nombre}» and the applicable authorizations (`d19861f`, `dfae14b`).
- «Cambiar decisión de seguimiento», audited, with the care task handled as decided (`95cf6b3`); then the owner's
  answers: never closed by the change, marked «Decisión cambiada» on every list with «Revisar» for its owner,
  reopened when needed again (migration `events/0018`, choices only).
- A camp administrator's ordinary sign-in opens the one camp underway; several, a chooser (`388765c`).
- One sign-in error across Emmaus (`0f8117d`).
Reverting any of them: `git revert -m 1 <merge>` or `git revert <commit>`; for the two merges with migrations,
first `manage.py migrate events 0015` (or 0016 or 0018, if keeping the follow-up correction) on a copy, and check
nothing depends on the dropped columns (declines on text questions read as «Sin responder» afterwards).

## How to revert

- Not merged anywhere: to drop it, do not merge `v2/redesign-claridad` (and delete the branch and its
  worktrees with `git worktree remove <path>` and `git branch -D <branch>` once nobody needs them).
- After a merge into `v2/foundation`: `git revert -m 1 <merge commit>` reverts the whole redesign in one commit.
- Without reverting code: turning the community module off for a church (`people.flags.set_enabled(church,
  "community", False)`) serves that church the old screens (`data-ui` is not set), byte for byte.

## Comparing with the product before the redesign

The baseline is `origin/v2/foundation` (`519e738`, where this branch forked; the local `v2/foundation` ref is
stale at `509b596` and predates the community module). A detached checkout of it sits at
`../Emmaus.worktrees/baseline` (remove with `git worktree remove ../Emmaus.worktrees/baseline`). Both versions
share migrations and seeds, so one seeded database copied twice gives an identical before/after
(`REVIEW_2026-11-18.md`, «Acceptance review»).

## Left running / scratch

- Dev servers used during the work were stopped; the review and critique servers on 8880–8882 are scratch
  (`pgrep -f "runserver 127.0.0.1:88"` lists them; stop with `kill <pid>`).
- Scratch data and captures live in the session's scratchpad (synthetic data only); nothing in the
  repository depends on them.
