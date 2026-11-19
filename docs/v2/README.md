# Emmaus v2 — where everything is

`EMMAUS_V2_CLAUDE_CODE_PROMPT.md` and `EMMAUS_V2_PRODUCT_BLUEPRINT.md` are the owner's directives
(2026-11). The repository already had a vision, an audit, a roadmap, decision records and a design
system for v2, so this folder does not repeat them. Each document the directive asks for is either
written here or is the existing source of truth:

| The directive asks for | Source of truth |
|---|---|
| `PRODUCT_VISION.md` | `docs/EMMAUS_PRODUCT_VISION.md`, refined by `EMMAUS_V2_PRODUCT_BLUEPRINT.md` (this folder) |
| `FEATURE_MATRIX.md` | `docs/v2/FEATURE_MATRIX.md` (new); the code-level capability map is `docs/audits/EMMAUS_GAP_ANALYSIS.md` §1 |
| `ARCHITECTURE_AND_ADRS.md` | `docs/architecture.md` and `docs/decisions/` (0002 domain, 0003 authorization, 0004 flags, 0005 migrations, 0008 community) |
| `MIGRATION_AND_ROLLOUT.md` | `docs/v2/MIGRATION_AND_ROLLOUT.md` (new: this slice); the whole path is `docs/audits/EMMAUS_EVOLUTION_ROADMAP.md` §3 and §5 |
| `IMPLEMENTATION_BACKLOG.md` | `docs/audits/EMMAUS_EVOLUTION_ROADMAP.md` §7 (IDs P0-n … P4-n) and the GitHub issues; the next slice is in `PROGRESS.md` |
| `UX_DESIGN_SYSTEM.md` | `docs/design-system.md` ("Atrio") and, for a church on the new modules, `docs/v2/DESIGN_SYSTEM.md` («Ventanas», the layer over it) and `docs/v2/CRAFT.md` (how it behaves) |
| `PROGRESS.md` | `docs/v2/PROGRESS.md` (new) |

Roles and permissions: `docs/roles-and-permissions.md`. Running and deploying: `docs/deployment.md`.
Rules for agents: `AGENTS.md`.

The redesign directive (2026-11-17, "Complete Product Redesign") added four living documents here:

| Document | Contents |
|---|---|
| `DESIGN_SYSTEM.md` | «Ventanas» (Santuario's second edition, 2026-11-18): tokens, type, surfaces, rows, the shell, the module-off rule |
| `UX_AUDIT.md` | What was found in the product as a church platform, and the state of the fifteen journeys |
| `SCREEN_MATRIX.md` | Every screen or family: route, role, redesign status, test and visual coverage, the waves |
| `REFERENCE_RESEARCH.md` | What was observed of Subsplash and ChurchSuite, with evidence levels, and what Emmaus took |
| `AGENT_HANDOFF.md` | Where the work is, how to run it, what to do next |

The «Ventanas» redesign (branch `v2/redesign-claridad`, 2026-11-18, not merged) added:

| Document | Contents |
|---|---|
| `CRAFT.md` | How Emmaus behaves: states, actions in place, motion, surfaces, precision, journeys |
| `REDESIGN_CHECKLIST.md` | Phases, areas and their merges, the owner's decisions and the ones taken under his delegation |
| `REVIEW_2026-11-18.md` | The adversarial review, the critiques and the before/after acceptance review, each defect with its fix |
| `RUNBOOK_REDESIGN.md` | What changed, where, how to see it, check it and revert it |
| `USABILITY_TEST.md` | A five-task test with real people: planned, not done |
