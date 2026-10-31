# Gate baseline

The reference numbers every later PR is compared against. Taken with
`scripts/agents/quality-gate.sh --postgres --e2e` on the `v2/foundation` head named below, from the
shared workstation virtualenv (Python 3.14, pytest 9.0.3), on 2026-10-31. CI ran the same commit green:
[CI run 41159756875](https://github.com/AdrianRomo/Emmaus/actions/runs/41159756875),
[CodeQL run 41159756860](https://github.com/AdrianRomo/Emmaus/actions/runs/41159756860).

```
GATE SUMMARY  v2/issue-40-p0-11-record-the-gate-baseline-on-the-me @ 509b596  2026-10-31T02:35:27Z
step            status           secs  detail
lint            PASS                0  All checks passed!
check           PASS                0  System check identified no issues (0 silenced).
migrations      PASS                0  No changes detected
deploy-check    PASS                0  check --deploy OK: only accepted warnings [security.W005 security.W021]
migration-scope PASS                0  migration scope OK: no core/ or events/ migration added since 509b596
pip-audit       PASS                1  No known vulnerabilities found
tests-sqlite    PASS               86  1427 passed, 6 skipped, 1021 warnings, 547 subtests passed in 84.48s (0:01:24)
tests-postgres  PASS              135  1422 passed, 11 deselected, 1022 warnings, 547 subtests passed in 132.76s (0:02:12)
e2e             PASS              194  02:35:27 browser gate OK
overall: PASS   logs: /home/adrian/.claude/jobs/e045f6a7/tmp/v2wf/agent-home/gates/20261031T022829Z
```

Browser gate detail: `station-flow.mjs` OK (assisted and supervised flow); `review-all.mjs` 234
page/width checks, **0 axe violations**; the 3 "problems" are the expected 403 probe (a church admin
opening event administration), as the script documents.

What the counts contain: the camp suite (1,414 SQLite / 1,409 PostgreSQL at the audit commit
`6f4664f`) plus the 13 `people` tests added by #25. The PostgreSQL run deselects
`events/tests/test_camp_ops.py::PurgeDemoDataTests` (11 tests, issue #14); they run on SQLite.

How to refresh: after a merge into `v2/foundation` that changes test counts, rerun the same command on
the new head (`scripts/agents/bootstrap.sh` first if `requirements.txt` changed), replace the block,
the commit and the run links, and say in the PR what moved and why.
