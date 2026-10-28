<!-- Draft PR against v2/foundation. Keep every section; write "n/a" rather than deleting one. -->

## What and why

Closes #<issue>. One paragraph on what the diff does (not what the issue asked for, if they differ).

## Risk and invariants

- Risk label on the issue: `risk:low | medium | high`
- Invariants touched (AGENTS.md): none | list them, with the test that covers each
- Camp screens or flows affected: no | which, and why it is safe during the event
- Migrations: none | additive (`<app>/<nnnn>`) | non-additive (needs `risk:high` and a human)

## Gate (paste the summary block from `scripts/agents/quality-gate.sh`)

```
<summary block, verbatim>
```

Flags used: `--postgres` yes/no, `--e2e` yes/no. Skipped steps and why: none | …

## Independent review (paste the summary from `scripts/agents/codex-review.sh`)

```
<verdict and findings>
```

Findings addressed or rebutted: …

## Spanish copy and accessibility

- New or changed UI text (Spanish, tú voice): none | list
- Screens checked at phone width: none | list; axe violations: 0

## Rollback

Revert this PR | reverse migration `<app> <nnnn>` | feature flag `<name>`

## Checklist

- [ ] Branch is `v2/issue-<n>-<slug>` from `origin/v2/foundation`; nothing touches `main`
- [ ] No `.env`, dumps, exports, media or participant data were read or committed
- [ ] Gate output above is real and complete; CI is green or its failure is explained
- [ ] Diff under 400 changed lines, or the issue says why not
