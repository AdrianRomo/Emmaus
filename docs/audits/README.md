# Audits

Read-only audits of the Emmaus repository, written as the Phase 0 of the next version
(`docs/EMMAUS_PRODUCT_VISION.md` §11, brief in `docs/EMMAUS_AUDIT_PROMPT.md`). They describe what
exists at a given commit; they are not a specification.

| Report | Question it answers |
|---|---|
| `EMMAUS_CURRENT_STATE_AUDIT.md` | What exists today, how it works, where it is fragile |
| `EMMAUS_GAP_ANALYSIS.md` | What the vision needs that the code has, half has, or lacks |
| `EMMAUS_EVOLUTION_ROADMAP.md` | In what order to evolve it without putting the camp at risk |
| `EMMAUS_OPEN_QUESTIONS.md` | What only a human can decide, with a proposed default |
| `BASELINE.md` | The gate numbers (tests, PostgreSQL deselects, browser checks, axe) on a named `v2/foundation` commit, refreshed when they move |
| `workstreams/*.md` | The five evidence reports the synthesis rests on (A inventory and release, B product and UX, C domain, D security, E gaps), with every claim tagged and line-cited |

Conventions: every claim is tagged **Verified** (file and line read at the audited commit),
**Likely / inferred** (reasoned from evidence, stated), **Unknown** (could not be verified, with the
reason) or **Recommendation**. Severity is Critical / High / Medium / Low with the rationale. No
secret values and no participant data appear in these documents; environment variables are named,
never valued.

Audit of 2026-10-28: commit `6f4664f` on `v2/foundation` (= `main` 032d846 + the vision documents +
the agent workflow), run as five parallel read-only workstreams (inventory and release; product and
UX; domain model; security and authorization; reuse and gaps) followed by a synthesis that
re-checked every Critical and High finding in the code.
