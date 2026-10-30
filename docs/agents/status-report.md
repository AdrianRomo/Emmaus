# Status report

`scripts/agents/status-report.py` prints what a human needs to decide or unblock, from GitHub
alone (no local state): the queue by state, open agent PRs with their CI result and review
decision, recent CI failures, stale claims, and the counters the workflow is measured by.

```bash
scripts/agents/status-report.py              # Markdown to stdout
scripts/agents/status-report.py --json       # machine-readable
scripts/agents/status-report.py --repo AdrianRomo/Emmaus --stale-days 2
```

Sections:

1. **Needs a human** — `agent:needs-human`, `needs:decision`, PRs with changes requested, red CI
   on `agent:review` PRs, Codex `needs_human` or an unanswered `request_changes` (no later
   `**agent:…**` comment), claims older than `--stale-days`.
2. **In flight** — `agent:in-progress` with claim age and branch; `agent:review` PRs with CI
   status (`success` / `failure` / `pending`) and the Codex verdict
   (`review codex=approve|request_changes|needs_human|stale|none`). The verdict comes from the
   latest "Independent review" comment (posted by the `Codex review` workflow); a comment for an
   older head SHA is `stale` and does not count.
3. **Blocked** — `agent:blocked` with the last comment's first line.
4. **Ready queue** — `agent:ready` ordered by age, with risk and `exec:local-ok`.
5. **Recent failures** — the last failed workflow runs on `main` and `v2/foundation`.
6. **Counters (last 30 days)** — merged agent PRs, PRs closed unmerged, first-run gate pass
   rate (CI success on the first run of a PR), escalations. These are the measure in decision
   0001: verified, mergeable changes per unit of work.

Run it from a Routine or a cron to post the Markdown where the owner reads it; it needs only
`gh` authenticated with read access to the repository.
