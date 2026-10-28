# Execution and review policies

These rules decide, without judgment calls, when an agent continues, retries, blocks or escalates,
and what it is allowed to use. They apply to local Claude Code sessions, cloud Routines, Codex
runs and local-model jobs alike.

## Block (do not start, or stop immediately)

- The issue is not `agent:ready`, lacks acceptance criteria or a risk label, or has an open
  dependency.
- The task would require any of: merging, deploying, host or database access, secrets, reading
  `.env`/dumps/exports/`media/`, real participant data, a commit on `main` or `v2/foundation`.
- The task needs a schema migration on camp tables (`events/*`, `core.Church`,
  `core.UserChurchRole`) and the issue is not `risk:high` with a reviewed rollout and rollback plan.
- The diff would exceed 400 changed lines or 15 files and the issue does not say so.
- A dependency change (`requirements.txt`, `e2e/package.json`, GitHub Actions versions) that
  the issue did not ask for.
- The gate cannot run (missing interpreter, missing `node_modules` for `--e2e`, no Docker for
  `--postgres`) and the step cannot be honestly reported as skipped for this issue.

## Retry

| Situation | Budget |
|---|---|
| Quality gate failure | 2 fix-and-rerun cycles per session, then `agent:blocked` with the summary |
| Codex `request_changes` | 1 fix cycle and 1 re-review, then leave the findings in the PR for a human |
| A step that passes on rerun ("flake") | Record it in the PR; a second flake in the same session counts as a failure |
| Sessions per issue | 2 autonomous sessions; the third needs a human comment first |
| CI red on a draft PR | 1 follow-up session; then `agent:needs-human` |

Every retry is logged (`audit-log.sh --action retry`). Budgets never reset silently.

## Escalate (`agent:needs-human`)

- Any invariant in `AGENTS.md` would be touched, or its interpretation for the task is unclear.
- Authorization, tenant isolation or sensitive-data visibility changes: `events/access.py`,
  `core/access.py`, `can_view_sensitive`, confidential levels, report scopes.
- A migration that is not purely additive (drop, rename, type change, data migration).
- A copy or flow change on a camp screen used by volunteers during the event.
- The vision document and the current behaviour conflict (vision §0: report and propose).
- A high-severity finding (CodeQL, pip-audit, Codex) outside the issue scope.
- The owner's intent is ambiguous and the readings lead to materially different work.

The escalation comment states the decision needed, the options, and the agent's recommendation.

## Quotas and budgets

| Quota | Value |
|---|---|
| Issues in `agent:in-progress` at once | 3 |
| Open agent PRs | 5 (stop claiming until one merges or closes) |
| Autonomous cloud sessions per day | 2 executor runs + 4 follow-up runs |
| Session wall clock | 2 h; post a status comment and stop at 90 min if not done |
| Local gate | 25 min total: lint 2, checks 3, deploy check 3, pip-audit 5, SQLite suite 15, PostgreSQL suite 20, browser gate 25 (each step has its own `timeout`) |
| CI | per-job `timeout-minutes` in `.github/workflows/ci.yml`; one run per ref at a time |
| Local-model jobs | 10 min, 3 attempts, no network, no repository writes |
| Codex review | 1 run per gate-green state of the branch; read-only, ephemeral |

When a budget is exhausted: comment with the evidence, set `agent:blocked` or
`agent:needs-human`, stop. Activity beyond a budget is a defect, not diligence.

## Least privilege

- **GitHub Actions:** `permissions: contents: read` at the workflow level; only the CodeQL job
  gets `security-events: write`. No deploy keys, no host secrets in this repository's Actions.
- **Agent GitHub identity:** a fine-grained personal access token scoped to `AdrianRomo/Emmaus`
  with Contents, Issues and Pull requests read/write and Metadata read; no Administration,
  Actions, Secrets or Workflows. The owner's classic token is never used by automation.
  Where it lives: for local automation (cron, the future queue, a non-interactive script) in
  `~/.emmaus-agent/env` as `GH_TOKEN=…` (mode 0600, sourced by the unit that runs it; `gh` reads
  `GH_TOKEN`); cloud Routines do not need it (their GitHub proxy authenticates), and if a separate
  identity is wanted there it goes into the cloud environment's variables as `GH_TOKEN`. Never in
  the repository, never in GitHub Actions secrets for this workflow, never in a prompt or an issue.
- **Routines:** repository access through the claude.ai GitHub connection only; no SSH keys,
  no `.env`, no host access.
- **Codex review:** `-s read-only --ephemeral`. **Local models:** no network, no repository
  writes; outputs become comments or proposals, never commits on shared branches.
- **Hosts:** no agent holds credentials for docker-sandbox, the Cloudflare tunnel, SMTP or the
  production database. Deploys follow the homelab runbooks and are run by a human.

## Audit log

- Durable log: issue comments (claim, gate result, PR, retry, block, escalation) written by
  `scripts/agents/audit-log.sh --comment`.
- Local log: JSONL at `$EMMAUS_AGENT_HOME/audit.jsonl` (default `~/.emmaus-agent/`) with
  timestamp, actor, host, issue, branch, action and detail; gate logs under
  `$EMMAUS_AGENT_HOME/gates/<timestamp>/`.
- Second record: the CI run on the PR. "Verified" means a pasted output or a run link exists.
- Environment changes (deploys, hosts) are logged in the homelab ledger by the human who runs them.

## Honesty rules

- Never fabricate CI results. Never claim an unverified feature works. If a step was skipped,
  say which one and why. If a test is flaky, say so and show both runs.
- Report what the diff does, not what the issue asked for, when the two differ.
