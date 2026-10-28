# Quality gates

One gate, two places: `scripts/agents/quality-gate.sh` locally and `.github/workflows/ci.yml` in
GitHub Actions run the same steps with the same commands. A PR is mergeable only when CI is green
and the PR body carries the local gate summary.

| Step | Command | Checks | Timeout | CI job |
|---|---|---|---|---|
| Lint | `python -m ruff check .` (`ruff.toml`) | pyflakes + syntax/indentation pycodestyle groups; seven pre-existing findings are per-file-ignored and tracked | 2 min | `lint` |
| System checks | `DEBUG=true manage.py check` | Django system checks incl. `events/checks.py` | 3 min | `checks` |
| Migrations | `DEBUG=true manage.py makemigrations --check --dry-run` | every model change ships its migration | 3 min | `checks` |
| Deploy check | `scripts/agents/check-deploy.sh` | `check --deploy` with production-like settings; only `security.W005` and `security.W021` are accepted (documented in `docs/deployment.md`) | 3 min | `checks` |
| Dependency audit | `scripts/agents/pip-audit.sh` | pip-audit on the pinned `requirements.txt` (direct pins); ignores listed in `.github/pip-audit-ignore.txt`, each with an issue | 5 min | `security` |
| Tests (SQLite) | `scripts/agents/run-tests.sh sqlite` | the full suite (1,414 pass, 6 skip on row locks) | 15 min | `test-sqlite` |
| Tests (PostgreSQL) | `scripts/agents/run-tests.sh postgres` with `DATABASE_URL=postgres://…` | the full suite incl. `test_concurrency`, minus the node ids in `scripts/agents/postgres-deselect.txt` (today one class whose seed takes ~31 min on PostgreSQL; it still runs on SQLite; each entry names its issue) | 20 min | `test-postgres` |
| Browser gate | `scripts/agents/e2e-gate.sh` | synthetic seed → `station-flow.mjs` (shared-computer interview regression) → `review-all.mjs` (every screen per role at 3 widths: axe WCAG 2.2 AA at zero violations, no overflow, one h1, the stylesheet, no JS errors) | 25 min | `e2e` |
| Static analysis | CodeQL (python, javascript) | security queries on every PR and weekly | 30 min | `.github/workflows/codeql.yml` |
| Independent review | `scripts/agents/codex-review.sh` | Codex CLI read-only verdict posted as a PR comment (advisory) | 25 min | `.github/workflows/codex-review.yml` |
| Tooling tests | `scripts/agents/tests/test-tooling.sh` | the gate's own failure boundaries with mocked commands (failing step → FAIL/exit 1, stale Codex verdict rejected, backend guards, CI-state table) | 15 min | `agent-tooling` |
| Secret scanning | GitHub secret scanning + push protection (repository setting, on) | credentials never reach the repo | — | — |

Spanish UI copy is checked by the existing suites (`core/test_audit_lenguaje.py`,
`events/tests/test_audit_lenguaje.py`, `test_copy_pass.py`, `core/test_locale.py`) and by the
PR checklist; there is no automated language detector because word heuristics misclassify.

## Running locally

```bash
scripts/agents/quality-gate.sh                    # lint, checks, migrations, deploy check, pip-audit (if installed), SQLite suite
scripts/agents/quality-gate.sh --postgres         # adds a throwaway postgres:16 container (needs Docker) and the PostgreSQL suite
scripts/agents/quality-gate.sh --e2e              # adds the browser gate (needs e2e/node_modules and Playwright's chromium)
scripts/agents/quality-gate.sh --skip-tests       # only the fast steps
```

Interpreter: `$PYTHON`, else `.venv/bin/python` in the worktree, else the main checkout's
`.venv` (worktrees share it), else `python3`. Logs and the summary go to
`$EMMAUS_AGENT_HOME/gates/<timestamp>/`; the summary block is what you paste into the PR.

Setup (once per checkout): `scripts/agents/bootstrap.sh` (virtualenv, requirements, ruff, pip-audit);
browser gate: `cd e2e && npm ci && npx playwright install chromium`.

## What "verified" means

- A gate step is verified when its output (or the CI run link) is in the PR.
- A feature is verified when a test or a browser check exercises it on synthetic data, and the
  PR says which one.
- A skipped step is stated as skipped with the reason. A step that was not run is not "green".

## Changing the gate

- Widen the lint rule set, add a check or raise a timeout in its own PR labelled `area:infra`,
  never inside a feature PR.
- Every ignore (`ruff.toml` per-file ignores, `.github/pip-audit-ignore.txt`,
  `scripts/agents/postgres-deselect.txt`) names the issue that removes it.
