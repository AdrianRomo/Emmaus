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
| `UX_DESIGN_SYSTEM.md` | `docs/design-system.md` ("Atrio") and, for a church on the new modules, `docs/v2/DESIGN_SYSTEM.md` ("Santuario", the layer over it) |
| `PROGRESS.md` | `docs/v2/PROGRESS.md` (new) |

Roles and permissions: `docs/roles-and-permissions.md`. Running and deploying: `docs/deployment.md`.
Rules for agents: `AGENTS.md`.

The redesign directive (2026-11-17, "Complete Product Redesign") added four living documents here:

| Document | Contents |
|---|---|
| `DESIGN_SYSTEM.md` | Santuario: tokens, the shell, the components added, what is not built |
| `UX_AUDIT.md` | What was found in the product as a church platform, and the state of the fifteen journeys |
| `SCREEN_MATRIX.md` | Every screen or family: route, role, redesign status, test and visual coverage, the waves |
| `REFERENCE_RESEARCH.md` | What was observed of Subsplash and ChurchSuite, with evidence levels, and what Emmaus took |
| `AGENT_HANDOFF.md` | Where the work is, how to run it, what to do next |
