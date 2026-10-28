# Independent review with Codex CLI

The author model never reviews its own PR alone. Codex CLI reads the branch in a read-only,
ephemeral sandbox and returns a structured verdict; the PR records it.

## Local (verified 2026-10-28)

```bash
scripts/agents/codex-review.sh                 # HEAD against origin/v2/foundation
scripts/agents/codex-review.sh --base main     # against another base
scripts/agents/codex-review.sh --out review.json
```

What it runs: `codex exec -s read-only --ephemeral -C <worktree> --output-schema
scripts/agents/codex-review.schema.json -o <file> "<prompt>"`. Codex may run `git diff`,
read files and run the test suite; it cannot write. The prompt (inside the script) asks for:
correctness against the issue, the invariants in `AGENTS.md` (tenant isolation, authorization,
camp regressions), security, migrations, Spanish copy, accessibility, tests that prove the change.

Verdicts: `approve` (exit 0), `request_changes` (exit 2), `needs_human` (exit 3). Findings carry
severity, file, line, evidence and a suggestion. Paste the summary block into the PR under
"Independent review"; address or rebut each `high`/`medium` finding.

Prerequisite: `codex login` on the machine that runs it (ChatGPT or API key).
`~/.codex/config.toml` sets the model; the script does not override it.

Alternative on the AI workstation: the `ai-router` MCP tool `codex_review` runs the same
`codex exec -s read-only --ephemeral` with a secret scan in front.

## In CI (not set up; follow-up)

A GitHub Action can run the same script with `OPENAI_API_KEY` as a repository secret and post
the verdict as a PR comment. It is not added here because it cannot be verified without the
secret; when added, keep it `workflow_dispatch` plus a label trigger (`review:codex`), read-only,
with a 15-minute timeout, and never let it approve or merge.

## Policy

- Required for every agent PR; for `risk:medium` and `risk:high` the verdict is pasted in full.
- `request_changes` gets one fix cycle and one re-review (`policies.md`); after that the findings
  stay in the PR for the human reviewer.
- A `needs_human` verdict moves the issue to `agent:needs-human`.
