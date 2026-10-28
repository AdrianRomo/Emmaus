# Proposal (later phase): a thin Python queue for low-risk local jobs

**Status: proposal, not built.** Decision 0001 defers it until the first autonomous issues have
shipped through Routines and the local gate.

## What exists today (observed 2026-10-28 on the AI workstation)

- `ai-router`, an MCP server on the AI workstation (LAN only), offers:
  local-model calls (`local_fast`, `local_cheap_code`, `local_code`, `local_reason`) over Ollama;
  `classify_task` / `route_task`; a **persistent job queue** (`enqueue_job`, `list_jobs`,
  `run_next_job`, `get_job`, `cancel_job`) that has no daemon (jobs run only when something calls
  `run_next_job`); `codex_review` (read-only Codex), `run_quality_gate` / `run_tests`,
  `worktree_list` / `worktree_cleanup`, `scan_secrets`, `verify_claims_against_repo`,
  `check_patch_against_acceptance_criteria`; `create_scheduled_workflow` with templates
  (`weekly_repo_audit`, `worktree_cleanup`, …) that render systemd timers; a local dashboard on
  `127.0.0.1:8787`; `model_orchestration_status` (VRAM headroom on the RTX 3090).
- Ollama models present: `qwen3-coder:30b`, `qwen2.5-coder:7b`, `gpt-oss:20b`, `qwen3:14b`,
  `deepseek-r1:32b`, `gemma-4-12b`; `bge-m3` for embeddings.
- The queue currently holds only cancelled test jobs. Nothing routes Emmaus work to it.

## Proposal: `emmaus-queue`

A ~300-line Python 3.12 program (stdlib + `gh`), run by a systemd timer on the AI workstation
every 30 minutes, that never opens a Claude session and never competes with the subscription:

1. **Intake.** `gh issue list --label agent:ready --label exec:local-ok --label risk:low --json …`
   and `gh pr list --label agent:review` for PRs that lack a "first-pass review" comment.
2. **Job types** (all read-only on the repository, outputs are comments or files):
   - `triage-summary`: restate the issue as objective / criteria / risk and flag missing readiness items.
   - `first-pass-review`: a local-model review of the PR diff, posted as a comment headed
     `Local first pass (qwen3-coder:30b) — advisory, not a verdict`.
   - `test-failure-digest`: summarise a red CI run's log into the failing tests and likely files.
   - `lint-fix-proposal`: `ruff --fix --diff` output attached to the issue, never committed.
   - `docs-draft`: a draft of a runbook or doc section from a template, attached as a file.
3. **Execution.** Either the `ai-router` MCP over HTTP (`local_code`, `local_reason`) or
   `codex exec --oss --local-provider ollama -s read-only --ephemeral`. The job checks
   `model_orchestration_status` first and skips when the needed model does not fit (ComfyUI or
   another model may hold the VRAM).
4. **Budgets.** 10 min per job, 3 attempts, 20 jobs per day, no network beyond GitHub and the
   local router, no repository writes, no job on issues that lack `exec:local-ok`.
5. **State.** The job record lives in `ai-router`'s queue (`enqueue_job` with `kind=workflow`) so
   the dashboard shows it; the issue comment is the durable result; a JSONL log in
   `$EMMAUS_AGENT_HOME/queue.jsonl` for the status report.
6. **Promotion rule.** A job type graduates from advisory to required only after a month in which
   humans accepted more of its findings than they rejected (tracked in the status report).

## Why not now

- No issue has shipped through the human-reviewed path yet; the lane needs real PRs to review.
- Local-model output is advisory by policy; without the Routines and the gate in place it would
  add noise without a consumer.
- `ai-router` already provides the queue and the routing; the orchestrator is glue, and glue
  should be written against a workflow that exists.

## When to build it

Phase E in decision 0001: after Routines run for two weeks and the status report shows a steady
queue of `exec:local-ok` issues or more than five agent PRs per week waiting for first review.
