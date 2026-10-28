# 0001 - Autonomous engineering workflow: Claude Code as the harness, GitHub Issues as the queue, deterministic gates

- **Status:** proposed (draft PR against `v2/foundation`)
- **Date:** 2026-10-28
- **Deciders:** owner (branch model, hosting, merge authority); Claude Code drafted the setup

## Context

Emmaus is moving from a camp application (`main`, in production for a real camp on
docker-sandbox, public at `emmaus.thestarrynight.dev`) to a church platform
(`docs/EMMAUS_PRODUCT_VISION.md`). The owner wants agents (Claude Code first, Codex CLI as an
independent reviewer, local Ollama models for cheap work) to execute bounded tasks without
putting the camp at risk, and wants the output measured as verified, mergeable changes per unit
of model usage rather than activity.

What the audit found on 2026-10-28 (verified in the repository and on the hosts):

- **Stack.** One Django 5.2.18 project, two apps (`core`, `events`), server-rendered templates,
  one JavaScript file, PostgreSQL 16 (SQLite when `DATABASE_URL` is unset), gunicorn +
  WhiteNoise, ReportLab PDFs, a `scheduler` container for cutoffs. Python 3.12 in the image,
  3.14 in the local virtualenv. 22 migrations. `requirements.txt` pins eight packages.
- **Tests.** pytest-django suite (`pytest.ini`), 1,4xx tests across `core/` and `events/tests/`;
  six skip on SQLite (row locks). Browser checks in `e2e/` (Playwright 1.63, axe-core 4.10):
  `station-flow.mjs` (shared-computer interview flow), `review-all.mjs` (every screen per role at
  three widths with axe, overflow, h1 and stylesheet checks), `all_urls.py` (URL list from the
  synthetic seed). `scripts/simulate_camp_day.py` is a load test.
- **Tooling gaps.** No `.github/` directory: no CI, no issue or PR templates, no CodeQL. No
  `CLAUDE.md` or `AGENTS.md`. No lint configuration (ruff 0.17.2 is installed; the small
  `E4,E7,E9,F` rule set reports seven findings today). No branch protection on `main` or
  `v2/foundation`. Default GitHub labels only. Actions enabled with a read-only default token;
  secret scanning and push protection on.
- **Branches.** `main` = the camp (PR #10 merged 2026-10-28, deploy #10 live). `v2/foundation`
  = `main` + the vision and audit documents, nothing else; it is the integration branch for the
  next version and must never be merged into `main`.
- **Workstation tooling.** Claude Code 2.1.312; Codex CLI 0.163 logged in (ChatGPT);
  Ollama with `qwen3-coder:30b`, `qwen2.5-coder:7b`, `gpt-oss:20b`, `qwen3:14b` and others; the
  `ai-router` MCP server (local-model routing, a persistent job queue with no daemon, a
  read-only `codex_review` tool, worktree helpers); `gh` 2.46 authenticated as the owner.
- **Hosting.** docker-sandbox (4 cores, 8 GB, ~220 GB free) runs the camp as compose project
  `emmaus-demo` from `/opt/sandbox/apps/Emmaus` (branch `main`). A Gitea-synced compose repo
  with an auto-deploy timer (`gitea-clone-deploy.sh`, `DEPLOY_ENABLED=true`) deploys compose
  directories that change on `main` unless they carry a `.nodeploy` marker. LAN names under
  `*.sandbox.thestarrynight.dev` reach the host's Traefik through a wildcard route; public names
  are one level deep and configured by hand in the Cloudflare tunnel.

## Decision

1. **Branch model.** `main` stays production: camp fixes only, merged and deployed by humans.
   `v2/foundation` is the default base and target for agent work until the next version is
   stable; humans merge `main` into it. Agents never push to either branch, never merge, never
   deploy, never touch a host.
2. **Queue.** GitHub Issues with a label state machine (`agent:ready` → `agent:in-progress` →
   `agent:review` → `agent:done`, plus `agent:blocked` and `agent:needs-human`), risk labels
   (`risk:low|medium|high`), `exec:local-ok` for work a local model may do, and an issue form
   that requires acceptance criteria, scope, dependencies, risk, invariants touched,
   verification and rollback (`docs/agents/workflow.md`).
3. **Isolation.** One issue per session, in its own git worktree and branch
   (`v2/issue-<n>-<slug>`) created by `scripts/agents/new-task-worktree.sh`. The claim, every
   gate result, the PR and every block or escalation are issue comments (the durable audit log)
   plus a local JSONL file.
4. **Gates.** The same deterministic gate runs locally (`scripts/agents/quality-gate.sh`) and in
   GitHub Actions (`.github/workflows/ci.yml`): ruff on a baseline rule set that passes today,
   Django system checks, `makemigrations --check`, `check --deploy` accepting only the two
   documented HSTS warnings, the suite on SQLite and on PostgreSQL 16, pip-audit on the installed
   environment (one documented, issue-tracked ignore), CodeQL for Python and JavaScript, and
   the browser gate on synthetic data (`station-flow.mjs` regression + `review-all.mjs`
   accessibility at zero axe violations). Spanish UI copy is covered by the existing
   `test_audit_lenguaje` suites and the PR checklist.
5. **Review.** Every agent PR is a draft against `v2/foundation`, filled from the PR template
   with the gate output pasted verbatim, then reviewed independently by Codex CLI in a
   read-only, ephemeral sandbox with a structured verdict (`scripts/agents/codex-review.sh`).
   Humans merge. The policies in `docs/agents/policies.md` say when an agent must block, retry
   or escalate, and set quotas, budgets, timeouts and permissions.
6. **Scheduling.** Claude Code cloud Routines are the scheduler for (a) executing one ready,
   low-risk issue and (b) following up open agent PRs. The setup is documented
   (`docs/agents/routines.md`) and left for the owner to create after connecting GitHub in the
   cloud environment; nothing is scheduled by this PR.
7. **Local models.** Ollama is used only for bounded, non-sensitive, reversible jobs
   (classification, summaries, first-pass review, lint-fix proposals) through the existing
   `ai-router`. A thin Python queue orchestrator is proposed for a later phase
   (`docs/agents/orchestrator-proposal.md`); it is not built now.
8. **Second environment.** `v2/foundation` gets its own stack on docker-sandbox
   (`emmaus-v2`: own checkout, database, media volume and network; LAN name
   `emmaus-v2.sandbox.thestarrynight.dev`; public name to be added by hand), deployed manually
   and excluded from the auto-deploy timer with `.nodeploy`. Recorded in homelab-ops ADR 0042.

## Consequences

- Positive: every change an agent makes is traceable to an issue, a branch, a gate run and a
  PR; the camp cannot be touched by automation; the gate is the same everywhere; review is
  independent of the author model; the measure of success (merged, verified PRs per session) is
  visible in `scripts/agents/status-report.py`.
- Negative / trade-offs: the browser gate adds ~10 minutes to CI; the lint gate starts narrow
  (seven baseline findings are per-file-ignored and tracked); Routines and the Codex GitHub
  Action need manual authentication steps; the auto-deploy timer still has no `.nodeploy` on the
  production `emmaus` directory (pre-existing residual risk, out of scope here).

## Alternatives considered

- GitHub Projects as the queue: adds a board the scripts cannot drive as simply as labels.
- A queue daemon now: `ai-router` already has a persistent queue; building another before the
  first autonomous issue ships is premature.
- Letting Routines merge or auto-merge: rejected; the invariants and the camp need a human
  merge.
- Gitea Actions: the repository is on GitHub, where Actions are free for a public repo.
- Running CI only locally: not independent of the author and not visible on the PR.

## Incremental plan

- **Phase A (this PR).** Foundations: `AGENTS.md`/`CLAUDE.md`, `docs/agents/*`, issue and PR
  templates, labels, `ruff.toml`, CI (lint, checks, SQLite, PostgreSQL, pip-audit, CodeQL,
  browser gate), the agent scripts, this ADR, the second environment. Verified by running the
  gate locally and watching the first CI run on the draft PR.
- **Phase B.** First dogfood issue (bump `pytest` to clear PYSEC-2026-1845) executed by a
  supervised local Claude Code session with the full protocol: claim, worktree, gate, Codex
  review, draft PR, human merge.
- **Phase C.** Branch protection on `v2/foundation` requiring the CI checks and one review;
  `main` protected the same way.
- **Phase D.** Routines: a one-off dry-run routine that only reads and comments, then the
  executor (daily, one issue) and the follow-up (every 6 h) routines, each with the quotas in
  the policy document.
- **Phase E.** Local-model lane: first-pass review and triage summaries via `ai-router`; then
  the thin queue orchestrator if the lane proves useful.
- **Phase F.** Metrics: merged-and-verified PRs per session, gate pass rate on first run,
  escalations per issue; reviewed monthly and used to widen or narrow autonomy.

## Reconsider if

- The camp ends and `main` stops being production (then `v2/foundation` may become `main`).
- CI time exceeds 20 minutes on a typical PR (split the browser gate to a nightly run).
- Routines cannot open PRs from the cloud environment (fall back to local scheduled sessions).
- Local models produce findings that reviewers reject more often than they accept.

## Follow-up tasks

- [ ] Issue: bump `pytest` (PYSEC-2026-1845) and remove the pip-audit ignore.
- [ ] Issue: fix the seven baseline ruff findings and drop the per-file ignores.
- [ ] Branch protection on `v2/foundation` and `main` once the CI check names exist.
- [ ] Connect GitHub in the Claude Code cloud environment; create the dry-run routine.
- [ ] Add the public hostname `emmaus-v2.thestarrynight.dev` in the Cloudflare tunnel.
- [ ] Prune the ~38 stale worktrees under `Emmaus.worktrees/` (owner's call).
