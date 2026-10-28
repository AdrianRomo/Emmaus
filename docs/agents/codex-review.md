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
`~/.codex/config.toml` sets the model; `CODEX_MODEL` overrides it, and when the model is at
capacity the script tries `CODEX_REVIEW_FALLBACK_MODELS` (default `gpt-5.6-sol gpt-6-astra gpt-5.5`).

Alternative on the AI workstation: the `ai-router` MCP tool `codex_review` runs the same
`codex exec -s read-only --ephemeral` with a secret scan in front.

## In CI (`.github/workflows/codex-review.yml`)

Runs on every same-repository PR against `v2/foundation` or `main` when the repository secret
`OPENAI_API_KEY` exists (it skips with a notice otherwise): installs the requirements and
`@openai/codex@0.163.0`, logs in with the key, runs `scripts/agents/codex-review.sh --base
origin/<base>`, and posts the summary as a PR comment headed "Independent review (Codex CLI)".
The verdict is advisory: the job fails only when the tool itself fails (exit 1), never on
`request_changes`, so a model's false positive cannot block a merge; humans and the follow-up
routine read the comment. Forked PRs get no review (secrets are unavailable to forks).

## Policy

- Required for every agent PR; for `risk:medium` and `risk:high` the verdict is pasted in full.
- `request_changes` gets one fix cycle and one re-review (`policies.md`); after that the findings
  stay in the PR for the human reviewer.
- A `needs_human` verdict moves the issue to `agent:needs-human`.
