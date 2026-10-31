# Task workflow: GitHub Issues as the queue

One issue is one unit of autonomous work. Labels carry its state; comments carry its evidence.

## Labels

| Label | Meaning |
|---|---|
| `agent:ready` | The issue passes the readiness checklist below. An agent may claim it. |
| `agent:in-progress` | Claimed. The claim comment names the branch, worktree, host and session. One claim at a time. |
| `agent:review` | A draft PR is open, the gate has run, review (Codex + human) is pending. |
| `agent:blocked` | A gate failed after the retry budget, or a dependency is open. The last comment says what unblocks it. |
| `agent:needs-human` | Escalated: a human decision is required (see `policies.md`, Escalate). |
| `agent:done` | Merged and verified, or closed as won't do. |
| `risk:low` | Docs, tests, tooling, copy, isolated UI; no schema, no authorization, no camp screens. |
| `risk:medium` | Additive schema or behaviour outside the camp flows; new screens. |
| `risk:high` | Authorization, tenant isolation, sensitive data, camp flows, non-additive migrations. |
| `exec:local-ok` | Bounded, non-sensitive, reversible: a local model may pre-process or execute it. |
| `needs:triage` | New issue from the form; a human sets risk and readiness. |
| `needs:decision` | A product or architecture decision is open; not executable. |
| `area:camp`, `area:v2`, `area:infra`, `area:docs` | Routing only. |

`scripts/agents/sync-labels.sh` creates or updates them from `.github/labels.json`.

## State machine

```
needs:triage ──(human: criteria + risk)──▶ agent:ready
agent:ready ──(claim comment, worktree)──▶ agent:in-progress
agent:in-progress ──(gate green, draft PR)──▶ agent:review
agent:review ──(human merge)──▶ agent:done
agent:review ──(changes requested)──▶ agent:in-progress      (same or a new session)
agent:in-progress ──(retry budget spent / dependency open)──▶ agent:blocked
agent:blocked ──(fixed)──▶ agent:ready        agent:blocked ──(needs a decision)──▶ agent:needs-human
any ──(escalation condition)──▶ agent:needs-human ──(human answers)──▶ agent:ready | closed
```

Rules:

- Exactly one `agent:*` label at a time. Every transition is a comment with the evidence (gate
  summary, PR link, failing step, question for the human). `scripts/agents/audit-log.sh` writes
  both the comment and the local log line.
- Only humans move an issue to `agent:done` (they merge) or close it. Because PRs target
  `v2/foundation` and not the default branch, GitHub does **not** auto-close `Closes #n` on merge:
  whoever merges (or the follow-up routine on its next run) closes the issue and labels it `agent:done`.
- An agent that cannot make the next transition within its budget stops; it does not wait.

## Session protocol (one task per session)

1. **Claim.** `scripts/agents/new-task-worktree.sh <issue> --claim` checks the issue is open and
   `agent:ready`, creates `Emmaus.worktrees/issue-<n>-<slug>` on branch `v2/issue-<n>-<slug>`
   from `origin/v2/foundation`, moves the label to `agent:in-progress` and posts the claim.
2. **Work** only in that worktree. Commit in small steps with messages that name the issue
   (`fix(events): … (#<n>)`). Never `git add -A`.
3. **Gate.** `scripts/agents/quality-gate.sh` (add `--postgres` for anything touching queries
   or migrations, `--e2e` for anything touching templates, CSS or JavaScript). On failure, fix
   and rerun once; after two failed runs, label `agent:blocked` with the summary and stop.
4. **Review.** `scripts/agents/codex-review.sh`. Address each finding or explain in the PR why
   not. A `request_changes` verdict gets one fix cycle and one re-review.
5. **PR.** `gh pr create --draft --base v2/foundation` filled from the template; paste the gate
   summary and the Codex verdict; link the issue (`Closes #<n>`); label the issue `agent:review`.
6. **Stop.** The session ends here. Review comments are handled by the PR follow-up routine or a
   new session that reads the PR first.

## Readiness checklist (`agent:ready` requires all of these)

- Objective in one paragraph, and acceptance criteria a test or a screenshot can check.
- Scope in and out; dependencies listed and closed.
- Risk label set; invariants touched listed (from `AGENTS.md`); for `risk:high`, a reviewed
  rollout and rollback plan in the issue.
- Verification plan: which gates, which manual checks, which synthetic data.
- No real participant data, credentials or production access needed.
- UI changes name the Spanish copy and the screens, and say whether a camp screen is affected.

Use the **Agent task** issue form (`.github/ISSUE_TEMPLATE/agent-task.yml`); it asks for each item.
