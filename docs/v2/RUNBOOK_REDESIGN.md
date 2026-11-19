# Runbook: the «Ventanas» redesign (branch `v2/redesign-claridad`)

What changed, where it lives, how to see it, how to revert it. Nothing here was pushed, merged into
`v2/foundation` or `main`, or deployed. No new dependencies. Four additive migrations, all from the owner's
decisions of 2026-11-18: `events/0016_follow_up_decision_change` (a new table), `events/0017_answer_declined`
(two boolean columns with a database default), `events/0018_follow_up_decision_change_reopened` (choices only,
no SQL) and `events/0019_follow_up_decision_change_continued` (three nullable or defaulted columns). The redesign
itself changes no data. The merge of `origin/v2/foundation` into this branch (`1b3caa3`) also brought
`agenda/0006_avisos_publicos_del_evento` (#101, public notices of an event: one new table), which is
foundation's, not the redesign's.

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
  «Nadie sirve el …» on a team page and on the team coordinator's «Mi iglesia»; an event's recent public notice
  under its line on «Mi iglesia» and in Eventos; messages that name who and what. Two messages also show with the
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
  reopened when needed again (migration `events/0018`, choices only); then «Reabierto el …» until its owner moves it
  on, and «Continuar seguimiento» with a reason for whoever may manage the task (migration `events/0019`).
- A camp administrator's ordinary sign-in opens the one camp underway; several, a chooser (`388765c`).
- One sign-in error across Emmaus (`0f8117d`).
- The identity confirmation kept and shown («Identidad confirmada por …»); a decline reads «Prefiero no responder»
  to the campero and «Prefirió no responder» to staff and exports (`7c0f57f`).
- A resumed interview shows both people: «Identidad confirmada por …», each «Identidad verificada de nuevo por …»
  and «Entrevista a cargo de …»; a staff member who did not start it ticks «Sí, es {nombre}» once before going on,
  audited `identidad_verificada` (`03255ff`, merged `eff0e46`; no migration).
- «Eventos de la iglesia»: a running event says «En curso»; an interview-only camp shows its camperos (`9dfc09a`).
Reverting any of them: `git revert -m 1 <merge>` or `git revert <commit>`; for the two merges with migrations,
first `manage.py migrate events 0015` (or 0016, 0018 or 0019, if keeping the follow-up correction) on a copy, and check
nothing depends on the dropped columns (declines on text questions read as «Sin responder» afterwards).

## Decisions taken under the owner's delegation (2026-11-18)

Recorded with their reasons in `REDESIGN_CHECKLIST.md` («Decided by me»). None has a migration; each reverts
with `git revert <commit>` (`970d340` also records the decisions in the checklist: keep that part if the record
should stay).

- A church that uses the community module reads «Tu iglesia, entre domingo y domingo.» on its landing, with
  «Cada persona ve solo lo que le toca.» and «Tú decides cómo te contactan.»; the camp's door (module off) keeps
  «Platicar con cada joven, sin perder a nadie.» (`970d340`, `core/test_puertas_ventanas.py`).
- A computer that lost an interview reads «Esta entrevista se abrió en otra computadora o pasó mucho tiempo sin
  cambios. Lo que se respondió sigue guardado.»; the page it lands on says how to go on (`970d340`,
  `CLAIM_LOST_MESSAGE` in `events/services/interviews.py`).
- «Nadie sirve el …» also on the team coordinator's «Mi iglesia», up to three dates, with «Pedir a alguien»; who only
  serves never sees it (`24d4970`, `serving/tests/test_home_nadie_sirve.py`).
- An event's public notice of the last 7 days is one line under the event on «Mi iglesia» and in Eventos, linked to
  the event's notices; a church notice is never tied to an event (`d59e1ee`, `agenda/tests/test_recent_notice.py`).
- «Entrevista a cargo de …» only when it is not the person who confirmed the identity at the start (`e738324`,
  `events/tests/test_antes_de_empezar.py`).

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

## Final review round (2026-11-18): security review and browser QA

Three independent passes over the finished branch: a security review of the server-side changes, a browser QA of every
role's journeys (184 paths at 390 and 1280 px, axe, overflow, one h1, focus, console), and a documentation refresh
(merged `05e000e`). What they found, and the fix (each with a test; no migration):

| Found | Fix |
|---|---|
| A retired event notice still showed under its event for up to 7 days | `8a2cd04` (excludes retired notices) |
| An event named «En curso» would have been unreachable behind `/eventos/en-curso/` | `8a2cd04` (slug reserved) |
| On a free or expired claim, another person took the interview before «Sí, es …» | `58ce6d9` (check first, everywhere) |
| «a cargo de» compared names, not accounts | `58ce6d9`, then `12f69b0` |
| A changed decision's reason (free text about a campero) on every list | `78a2004` (task page and record only) |
| A follow-up could be given to someone who could not open it | `31e07b0` (`forms.assignable`) |
| The record's first line named the wrong computer and repeated names | `12f69b0` |
| Marked camperos were lost by a search or by leaving, without a word | `ec58078` |
| A comma starting a line, a duplicated region name (axe), two wordings for «Nadie sirve», one group's requests only | `8c1f9c0` |
| «Retomar» went back to a question skipped on purpose | `c282c2d` (merged in the commit after `ce9d11a`) |
| A computer whose campero was reassigned got a 404 | `33a6a9a` |
| An administración without confidential access could take an interview she could not continue | `33a6a9a` |

Not changed: three module-off camp screens have axe `color-contrast` at 1280 px; `rotulo.css` is unchanged from
`v2/foundation`, so it predates this branch (left for a separate change, not to touch the old screens here).

Worktrees of this round (detached or merged; remove when no longer needed, `git worktree remove <path>`, and
`git branch -D v2/docs-final v2/qa-entrevista` once reviewed): `../Emmaus.worktrees/docs-final`,
`../Emmaus.worktrees/qa-entrevista`, `../Emmaus.worktrees/rc-critic`, `../Emmaus.worktrees/rc-verify`.

## Left running / scratch

- Dev servers used during the work were stopped; the review and critique servers on 8880–8882 are scratch
  (`pgrep -f "runserver 127.0.0.1:88"` lists them; stop with `kill <pid>`).
- Scratch data and captures live in the session's scratchpad (synthetic data only); nothing in the
  repository depends on them.
