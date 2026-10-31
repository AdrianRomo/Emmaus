# Emmaus — open questions

Only material questions that the audit could not settle from the code, grouped by who decides. Each carries a proposed default so work can continue if nobody objects; the default is applied by the roadmap items named. Commit `6f4664f`, 2026-10-28.

## Product owner and church leadership

| ID | Question | Why it matters | Proposed default |
|---|---|---|---|
| Q-P1 | What is the one word for a person across the product? "Campero" is the camp participant; "Participante" today means a discipleship account; the vision needs one noun for `Person`. | B-03; every Phase 2 screen and the glossary depend on it | "Campero" stays inside the camp; "Participante" is reserved for the new `Person`; the event kind "Programa de discipulado" is renamed "Curso" at the next content release (P2-11) |
| Q-P2 | Does the first milestone include a self-service claim ("este campero soy yo" by code) or only a staff-made link? | self-service needs a public path and identity proofing for minors; staff-made is audited and simple | staff-made, audited link after the event (P2-6); self-service deferred |
| Q-P3 | May the discipleship module keep sending a participant's *question text* to an external LLM (off by default today)? Invariant 7 forbids it for answers. | F12; church-level consent | keep the feature off by default; add a per-church switch and consent text before any church turns it on; the camp never uses it |
| Q-P4 | Which denominations' vocabulary and roles must be configurable in Phase 1 (church admin/pastor/mentor labels; "sede" vs "campus")? | vision §3 "titles vary by church" | labels configurable per church from Phase 3; Phase 1–2 keep today's words |
| Q-P5 | Mi Iglesia navigation: `Inicio · Explorar · Mi actividad · Perfil` (vision §9) or a shorter set? | B §6.2 | the four labels, validated in usability tasks T7–T8 before shipping |
| Q-P6 | After the camp, should event memberships of a closed event become read-only and hidden after N days? | J2 step 3; today closed events stay readable to everyone with access | read-only after 30 days, hidden from the list after 90, superusers excepted (P3-9) |
| Q-P7 | Three parked wording decisions from the UX audit: D1 24-hour times, D3 "administrador/administración", D6 "Nombre corto para la dirección" | B-05 | decide with volunteers in tasks T5–T6; no change before the camp |

## Security and privacy owner

| ID | Question | Why it matters | Proposed default |
|---|---|---|---|
| Q-S1 | May a pastor (including a church-wide pastor with no membership) widen "Respuestas confidenciales" alone? Today yes, audited (F2). | invariant 4's spirit; a single-person path to confidential answers of later interviews | require the ADMIN role, or a second person's confirmation, after the camp; during the camp, a written two-person rule |
| Q-S2 | May an event admin grant `can_view_sensitive` to an account that has never set its own password (F7)? | puppet-identity path to every answer | no, and a password check alone is not enough (an admin can create an unflagged account, activate it from the on-screen link, then grant): require a second administrator's confirmation for every grant, as the self-grant guard already does for the admin's own account (P0-7, after the camp) |
| Q-S3 | What is the Django admin for? Church admins and pastors are `is_staff` today with no permissions (F1). | unthrottled login surface; cross-tenant once any permission exists | superuser-only, church roles not `is_staff`, `/admin/` blocked at the proxy during the camp, audited sensitive edits (ADR 0006) |
| Q-S4 | Retention for staff data, access requests and audit rows (today none; events without `ends_on` never purge) | C-12; privacy statement | audit rows kept 3 years; access requests 90 days after decision; a deploy warning for active events without an end date |
| Q-S5 | Should snapshots persist raw sub-threshold counts (today yes; suppression is a render rule, C-11)? | anyone with DB/admin read access sees exact small counts | store suppressed counts, or a raw table readable only by the release job (P3-10) |
| Q-S6 | Is the manual erasure of a campero allowed while a safety report about them is open (today it deletes the report, C-13)? | safeguarding record vs erasure right | refuse while open, as the automatic purge does (P0-8) |
| Q-S7 | What consent text and version must a `ConsentRecord` be able to prove (C-08)? | the notice was rewritten by two migrations | store notice hash, excerpt and purpose on every new consent (ADR 0007) |
| Q-S8 | Where does per-IP rate limiting live: Traefik/CrowdSec at the edge or in the app (F5/F6)? | brute force on login/unlock/join | edge first (P0-9); app-level only if the edge cannot see the client IP |

## Engineering

| ID | Question | Why it matters | Proposed default |
|---|---|---|---|
| Q-E1 | New apps (`people`, `access`, `groups`, `public`) or new models inside `core`? | rule 2 (no `core`/`events` migrations during the camp); merge friction M-1 | new apps until the camp ends; revisit after cut-over |
| Q-E2 | `IdentityLink` as its own table or as `Person.user` unique per church? | the vision names a table; one account per person per church is the only known need | `Person.user` unique per church now; a table only when one account must map to several persons in one church |
| Q-E3 | When does `v2/foundation` become `main`? | cut-over is a stack swap; the camp's `main` keeps receiving fixes | after the camp ends and the 0011 contract has run on `main`; the old stack stays one full day |
| Q-E4 | Should the camp stack keep `migrate --noinput` on container start (D-1)? | docs say manual; schema never rolls back with the image | keep for `emmaus-v2`; document the actual behaviour for the camp; explicit `migrate` with web stopped for the 0011 contract |
| Q-E5 | Python 3.12 in the local gate too (today 3.14 locally, 3.12 in CI and the image)? | T-3 | `bootstrap.sh` prefers `python3.12` when present |
| Q-E6 | Widen the lint rule set and add coverage reporting? | T-2 | own PR, no threshold at first |
| Q-E7 | Per-tenant namespaces: prefix (`/iglesia/<slug>/…`) or per-church uniqueness of `Event.slug` and join codes? | public camp URLs on printed sheets | prefix for new public pages now; camp URLs unchanged until Phase 4 (P4-2) |

## Operations (homelab)

| ID | Question | Why it matters | Proposed default |
|---|---|---|---|
| Q-O1 | Off-site destination for the hourly dumps (NAS restic set, R2), and who runs the restore drill? | D-2; docker-sandbox is "not backed up" by policy while it hosts production | restic set with the fleet's existing off-site sync; one restore into the `emmaus-v2` database before the camp weekend (P0-1) |
| Q-O2 | Should the camp move off docker-sandbox after the event, or should the sandbox policy change? | the service page says the host is disposable | decide at cut-over (Q-E3); until then the backup timer and `.nodeploy` cover the gap |
| Q-O3 | Is `/admin/` reachable through the tunnel and the LAN route today? | F1 surface | block at the proxy during the camp unless a superuser needs it (P0-5) |
| Q-O4 | Scheduler liveness: push monitor from `run_once` or a cutoff-age endpoint? | D-3 silent failure | AutoKuma label on a tiny read-only endpoint after the camp; a human checks the admin home during it |
