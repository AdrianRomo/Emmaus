# Emmaus — instructions for coding agents

These rules apply to every agent that works in this repository (Claude Code, Codex CLI, local
models driven by a script) and to the humans who review their work.

Emmaus is a Spanish-language (es-MX) Django platform for church life. **`main` runs a real camp in
production.** `v2/foundation` is the integration branch for the next version (church-first,
multi-sede, tenant-isolated later). Direction: `docs/EMMAUS_PRODUCT_VISION.md`. What exists:
`docs/architecture.md`, `docs/roles-and-permissions.md`, `docs/privacy-and-safeguarding.md`,
`docs/design-system.md`. How agents work here: `docs/agents/README.md`; why: `docs/decisions/0001`.

## Stack (verified 2026-10-28)

- Django 5.2, Python 3.12 in the image (`Dockerfile`; the local venv may be newer), PostgreSQL 16
  (SQLite only when `DATABASE_URL` is unset), gunicorn + WhiteNoise, server-rendered templates,
  `static/js/emmaus.js` is the only JavaScript, `static/css/rotulo.css` the only stylesheet.
- Apps: `core` (church, roles, logo, discipleship) and `events` (camp). Every authorization
  decision lives in `events/access.py` and `core/access.py`.
- Tests: pytest-django (`pytest.ini`), ~1,420 tests; browser checks in `e2e/` (Playwright + axe).

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DEBUG=true .venv/bin/python manage.py migrate
DEBUG=true .venv/bin/python -m pytest -q -p no:cacheprovider --ignore=e2e   # SQLite, ~75 s
scripts/agents/quality-gate.sh                   # lint, checks, migrations, deploy check, tests (= CI)
scripts/agents/quality-gate.sh --postgres --e2e  # plus the PostgreSQL suite and the browser gate
scripts/agents/codex-review.sh                   # independent review of HEAD against v2/foundation
```

Run commands from the worktree directory with an absolute interpreter (there is no editable
install). Never `git add -A` (worktrees may hold `db.sqlite3`, `e2e/node_modules` symlinks).

## Branch and task rules

- Base every change on `origin/v2/foundation`; branch `v2/issue-<n>-<slug>`; one issue per
  branch; one task per session; work only inside the worktree that
  `scripts/agents/new-task-worktree.sh <issue>` creates.
- Never commit to `main` or `v2/foundation`. Never merge. Never deploy. Never touch a host,
  a database, a tunnel or a compose file outside this repository.
- Camp fixes go to `main` through humans; humans merge `main` into `v2/foundation`.
- Open pull requests as **drafts** against `v2/foundation`, filled from
  `.github/PULL_REQUEST_TEMPLATE.md`, with the gate summary pasted verbatim. Never state that a
  gate passed, a test ran or a feature works without the output that proves it. Say which gates
  were skipped and why.
- Keep diffs small (under 400 changed lines; split otherwise). No drive-by refactors, no
  dependency changes unless the issue asks for them, no new product features during setup work.
- Stop and escalate (`agent:needs-human`) under the conditions in `docs/agents/policies.md`.

## Invariants (never break; add a test when you touch the area)

1. **People exist independently of events.** A person's record never depends on an event, and
   ending an event role never deletes or hides the person.
2. **Church roles and event roles have distinct scopes.** `UserChurchRole` (church) and
   `EventMembership` (event) are not interchangeable and neither implies the other.
3. **Church data is tenant-isolated.** Every query that serves a request is scoped to the
   caller's church; cross-church access is tested as denied, including by guessed ids.
4. **Sensitive participant information requires authorization.** Confidential answers,
   follow-ups and pastoral notes need the explicit, audited access in `events/access.py`
   (`can_view_sensitive`, confidential levels). Pastoral or administrative status alone does not
   unlock them. Reports never widen access.
5. **Existing camp functionality must not regress.** Camperos never get accounts; a supervised
   capture never exposes coordinator privileges; cutoffs stay immutable; the full suite,
   `e2e/station-flow.mjs` and `e2e/review-all.mjs` keep passing; N=1-questionnaire events keep
   their screens unchanged.
6. **All customer-facing UI text is natural Mexican Spanish**, in the tú voice, and every
   workflow stays simple and accessible (WCAG 2.2 AA; axe at zero violations).
7. **Spiritual and personal answers are never scored, judged, sent to an AI service or e-mailed.**

## Data and secrets

- Never read `.env`, database dumps, exports, `media/`, or anything that could hold real
  participant data. Work only with the synthetic seed (`seed_camp_demo`, `@demo.invalid` accounts).
- Never put participant data, questionnaire answers or credentials in prompts, logs, issues,
  PRs or commit messages.

## Workflow documents

| Document | Contents |
|---|---|
| `docs/agents/workflow.md` | Issue state machine, labels, session protocol, readiness checklist |
| `docs/agents/policies.md` | When to block, retry or escalate; quotas, budgets, timeouts, audit log, permissions |
| `docs/agents/quality-gates.md` | Every gate, what it checks, how to run it, what "verified" means |
| `docs/agents/codex-review.md` | Independent Codex CLI review |
| `docs/agents/routines.md` | Claude Code cloud Routines: setup procedure and prompts |
| `docs/agents/orchestrator-proposal.md` | Later phase: a thin queue that routes low-risk jobs to local models |
| `docs/agents/status-report.md` | The task/PR/failure report for humans |
| `docs/decisions/` | Architecture decision records (0001 explains this setup) |
