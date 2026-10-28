# Emmaus — Claude Code

@AGENTS.md

## Claude Code specifics

- Start from an issue: `scripts/agents/new-task-worktree.sh <issue> --claim`, then work in the
  worktree it prints. One issue per session. End the session with a draft PR and the issue in
  `agent:review` (the script prints the exact commands). A new session handles follow-ups.
- Before the PR: `scripts/agents/quality-gate.sh` (paste its summary block into the PR), then
  `scripts/agents/codex-review.sh` and address or rebut each finding in the PR.
- Record every claim, gate result, block and escalation with `scripts/agents/audit-log.sh`.
- Django's cached template loader serves stale templates under `runserver --noreload`; restart
  the server after editing templates. `manage.py test --parallel` fails on Python 3.14 +
  SQLite; use pytest, serially.
- When an issue hits an "escalate" condition in `docs/agents/policies.md`, stop, label
  `agent:needs-human`, and say what decision is needed. Never guess on an invariant.
- Product UI text is Spanish; code, commit messages and technical docs stay in English unless
  the file is already in Spanish.
