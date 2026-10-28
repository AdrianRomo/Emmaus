# Agent workflow — index

Emmaus uses Claude Code as the main harness, GitHub Issues and pull requests as the task queue,
Codex CLI as an independent reviewer and local Ollama models for bounded low-risk jobs. The
rules every agent follows are in the repository root (`AGENTS.md`, included by `CLAUDE.md`).
The decision and the phased plan are in `docs/decisions/0001-autonomous-engineering-workflow.md`.

| Document | Read it when |
|---|---|
| [`workflow.md`](workflow.md) | You pick up, hand over or triage a task |
| [`policies.md`](policies.md) | You wonder whether to continue, retry, block or escalate; you set up credentials |
| [`quality-gates.md`](quality-gates.md) | You run or change CI or the local gate |
| [`codex-review.md`](codex-review.md) | You review a PR or want a second opinion |
| [`routines.md`](routines.md) | You schedule cloud sessions |
| [`orchestrator-proposal.md`](orchestrator-proposal.md) | You consider routing jobs to local models |
| [`status-report.md`](status-report.md) | You want to know what needs a human |

Scripts live in `scripts/agents/`; each prints usage with `-h`.

| Script | Purpose |
|---|---|
| `bootstrap.sh` | Virtualenv with the requirements and the pinned gate tools |
| `new-task-worktree.sh` | Isolated worktree + branch for one issue; optional claim |
| `quality-gate.sh` | The deterministic gate (same steps as CI) |
| `check-deploy.sh`, `pip-audit.sh`, `run-tests.sh`, `e2e-gate.sh` | Gate steps that CI and the gate script share |
| `codex-review.sh` | Read-only Codex review with a structured verdict |
| `audit-log.sh` | Append to the audit log and, optionally, the issue |
| `status-report.py` | Markdown/JSON report of queue, PRs, failures, human attention |
| `sync-labels.sh` | Create or update the labels from `.github/labels.json` |
| `tests/test-tooling.sh` | Regression tests for the tooling itself (run in CI) |
