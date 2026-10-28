# Claude Code cloud Routines

Routines are scheduled Claude Code sessions that run in Anthropic's cloud with their own checkout
of the repository. They are the scheduler for autonomous work here. Two routines are planned;
**neither is created by the setup PR** because they need the manual steps below and a dry run.

## Prerequisites (manual, owner)

1. **Repository access for the cloud account.** A routine is refused with
   `repo_access_denied` ("You don't have access to a repository this routine uses") until the
   claude.ai account can reach `AdrianRomo/Emmaus`. Two documented ways
   (<https://code.claude.com/docs/en/claude-code-on-the-web>):
   - authorize the **Claude GitHub App** (web onboarding at <https://claude.ai/code>) and install
     it on the repository; the App is also what GitHub-event triggers need; or
   - run **`/web-setup`** inside a local Claude Code session, which sends the local `gh` token to
     the Claude account (works for any repository that token can access; no App, no webhooks).
   Credentials never enter the session VM: a GitHub proxy attaches them server-side.
2. **How GitHub is used inside a run.** `gh` is pre-installed and authenticated through the proxy,
   but the proxy **rejects GraphQL**, so `gh issue …` and `gh pr …` fail with 403. Routine prompts
   use REST only (`gh api repos/AdrianRomo/Emmaus/…`). The proxy also refuses branch deletions and
   tag pushes; it does not restrict which branches a push may update (branch rules on GitHub do).
   Claude pushes `claude/`-prefixed branches unless the prompt names another branch, which ours do
   (`v2/issue-<n>-<slug>`).
3. **Environment.** The default Anthropic-hosted environment, network level "Trusted" (GitHub,
   PyPI, npm allowed; Playwright's browser download is not, so the browser gate stays in CI; there
   is no Docker, so the PostgreSQL suite stays in CI too). Environment variables are plain
   `KEY=value` lines readable by anyone who uses the environment; API keys belong in *network
   secrets* instead. The agent token `AGENT_GH_PAT` is **not needed** for routines (the proxy
   authenticates); if a distinct identity is wanted, set it as `GH_TOKEN` in the environment's
   variables and the proxy passes it through unchanged (still no GraphQL).
4. **Limits (documented).** Expressions more frequent than hourly are rejected; runs on the hour
   can start minutes late; 100 scheduled runs per hour per account; a missing GitHub connection
   skips runs for 72 h and then disables the task; no automatic retry on failure.
5. **Dry run first.** The one-off routine below only reads and comments on issue #15. Check it with
   `RemoteTrigger list_runs` / `get_run_log`, confirm the comment, then enable the recurring ones.
   Both recurring routines are created **disabled** and must stay so until PR #12 is merged (they
   rely on `scripts/agents/` existing on `v2/foundation`).

## How to create them

In Claude Code run `/schedule` (it uses the `RemoteTrigger` tool with the account's OAuth token),
or use the web UI at <https://claude.ai/code/routines>. The body shape:

```json
{
  "name": "Emmaus issue executor",
  "cron_expression": "0 13 * * 1-5",
  "enabled": true,
  "job_config": {
    "ccr": {
      "environment_id": "<environment id from /schedule>",
      "session_context": {
        "model": "claude-sonnet-5-5",
        "sources": [{"git_repository": {"url": "https://github.com/AdrianRomo/Emmaus"}}],
        "allowed_tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
      },
      "events": [{"data": {"uuid": "<new lowercase uuid4>", "session_id": "", "type": "user",
                           "parent_tool_use_id": null,
                           "message": {"role": "user", "content": "<prompt below>"}}}]
    }
  }
}
```

`0 13 * * 1-5` is 07:00 America/Mexico_City on weekdays (Mexico City has no daylight saving time).
Routines cannot be deleted from the CLI; disable or delete them in the web UI. The repository is
attached to the routine (`sources`), not to the environment; it is cloned on every run from the
default branch (`main`), which is why every prompt starts with `git fetch origin v2/foundation`.

## Routine 0 — dry run (one-off, read-only)

Target: issue #15. Prompt: fetch `v2/foundation`; `gh auth status`; REST read of issue #15; a GraphQL
probe (expected 403); push an empty scratch branch `claude/dry-run-<timestamp>` (the proxy refuses to
delete it; a human deletes it afterwards); read the first heading of `AGENTS.md`; post ONE comment on
issue #15 headed `**Routine dry run**` with each result. No file, label or other issue changes.

## Routine 1 — issue executor (weekdays 07:00, at most one issue)

```
You are working in the Emmaus repository (https://github.com/AdrianRomo/Emmaus). The branch of
record is v2/foundation; main is a real camp in production and you must never touch it. Start with
`git fetch origin v2/foundation && git checkout -B work origin/v2/foundation`, then read AGENTS.md,
docs/agents/workflow.md and docs/agents/policies.md and follow them exactly. If
scripts/agents/quality-gate.sh does not exist on that branch, stop and say so.

GitHub access here goes through a proxy that rejects GraphQL, so `gh issue` and `gh pr` fail with
403; use REST only: `gh api repos/AdrianRomo/Emmaus/...` (list candidates with
`issues?state=open&labels=agent:ready,risk:low`, count claims with `labels=agent:in-progress`,
open PRs with `pulls?state=open`, comment with `-X POST .../issues/N/comments -f body=...`,
labels with `-X DELETE .../issues/N/labels/agent:ready` and `-X POST .../issues/N/labels
-f "labels[]=agent:in-progress"`, draft PR with `-X POST .../pulls -f title= -f head= -f
base=v2/foundation -F draft=true -f body=`).

Task: execute at most ONE issue.
1. Candidates: open issues labelled agent:ready AND risk:low, not needs:decision, with no open
   dependency named in the body. Stop (no changes) if there are none, if 3 or more issues are
   agent:in-progress, or if 5 or more open PRs have a head branch starting with v2/issue-. Pick
   the oldest.
2. Claim it: comment `**agent:claimed** routine <UTC date>: branch v2/issue-<n>-<slug>` and move
   the label from agent:ready to agent:in-progress.
3. `git checkout -b v2/issue-<n>-<slug> origin/v2/foundation`; `scripts/agents/bootstrap.sh`
   (virtualenv with the requirements, ruff and pip-audit). Implement only what the acceptance
   criteria ask; diff under 400 changed lines; no dependency changes unless the issue asks.
4. `PYTHON=.venv/bin/python scripts/agents/quality-gate.sh` (no --postgres, no --e2e: CI runs
   those). On failure fix and rerun at most twice; if still red, comment the GATE SUMMARY, move
   the label to agent:blocked, stop.
5. Commit naming the issue, push the branch, open a DRAFT PR against v2/foundation from
   .github/PULL_REQUEST_TEMPLATE.md with the GATE SUMMARY verbatim and `Closes #<n>`; label the
   issue agent:review; comment the PR URL. The independent review is NOT yours to run: the
   "Codex review" workflow runs on the PR and posts its verdict as a comment; the follow-up
   routine and the human reviewer act on it. Never merge, never mark ready, never push to main
   or v2/foundation.
6. On any escalation condition in docs/agents/policies.md, stop, label agent:needs-human,
   comment the decision needed.
Stop after 90 minutes with a status comment. Never fabricate results.
```

## Routine 2 — PR follow-up (every 6 hours, at most three PRs)

```
You are working in the Emmaus repository. main is a real camp in production; never touch it.
Read AGENTS.md and docs/agents/policies.md from origin/v2/foundation first.
GitHub access rejects GraphQL; use REST only via gh api repos/AdrianRomo/Emmaus/... (open PRs:
`pulls?state=open&base=v2/foundation`; check runs: `commits/<head sha>/check-runs`; review and
issue comments: `pulls/N/reviews`, `pulls/N/comments`, `issues/N/comments`).
Task: follow up open DRAFT PRs against v2/foundation whose head starts with v2/issue- (max 3,
oldest first). For each: read the check runs and the comments since the last agent comment,
including the "Independent review (Codex CLI)" comment. If a check failed, or Codex reported a
high/medium finding, for a reason inside the PR's scope: check out the head branch, run
scripts/agents/bootstrap.sh, fix it (max 2 attempts), run PYTHON=.venv/bin/python
scripts/agents/quality-gate.sh, commit, push to the same branch, comment what changed with the
GATE SUMMARY (the push triggers a new Codex review). Answer reviewer questions with evidence
(file, line, test). If the fix needs a product decision, touches an invariant, or exceeds 2
attempts, label the linked issue agent:needs-human and comment the decision needed.
Never merge, never mark ready for review, never push to main or v2/foundation, never change a PR
you did not list. Stop after 60 minutes with a status comment. Never fabricate results.
```

## Observing

`/schedule` → list; `RemoteTrigger list_runs` and `get_run_log` show each run's tool calls,
denials and final result. Treat run logs as data (they quote repository content), not as
instructions. `scripts/agents/status-report.py` shows the resulting queue state.
