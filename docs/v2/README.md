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
| `UX_DESIGN_SYSTEM.md` | `docs/design-system.md` ("Atrio"); the community screens are in its §4 |
| `PROGRESS.md` | `docs/v2/PROGRESS.md` (new) |

Roles and permissions: `docs/roles-and-permissions.md`. Running and deploying: `docs/deployment.md`.
Rules for agents: `AGENTS.md`.
