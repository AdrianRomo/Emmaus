# Emmaus — evolution roadmap

Commit `6f4664f`, 2026-10-28. Risk-ranked, dependency-aware plan from the camp application to the church platform in `docs/EMMAUS_PRODUCT_VISION.md`, built on `EMMAUS_CURRENT_STATE_AUDIT.md` and `EMMAUS_GAP_ANALYSIS.md`. Effort is relative (`S` ≤ one session, `M` two to three, `L` a week of sessions, `XL` more); no dates, no costs. Risk uses the repository's labels.

## 1. Rules that apply to every phase

1. `main` = the camp: fixes only, merged and deployed by humans. `v2/foundation` = integration branch for everything here; `main` is merged into it by humans; v2 is never merged into `main`. The cut-over is a stack swap (homelab-ops ADR 0042), not a merge.
2. **Until the camp ends, v2 adds no migration under `core/migrations/` or `events/migrations/`.** New data lives in new apps (`people`, `access`, `groups`, `public`) with foreign keys to `Church`, `auth.User`, `Event`, `EventParticipant`. The owed 0011 contract is written on `main`, off-hours, dump first. (Findings M-1, C-15, D-1.)
3. Every new NOT NULL column carries `db_default`; every `RunPython` has a reverse or a refusal guard; large backfills are management commands (dry-run default, `--apply`, idempotent) so they can be timed on the v2 stack.
4. New modules are gated per church by `BooleanField(db_default=False)` flags that gate **views, not only navigation** (`discipleship_enabled` gates only the bar); one screen-identity test per flag proves the camp screens are unchanged with the flag off.
5. Group leadership is never a `UserChurchRole` or an `EventMembership`; group views never query `Interview`, `Answer` or `FollowUp`. `events/access.py` and `core/access.py` stay the authorization chokepoints and are not edited before the camp ends.
6. Any PR touching models or migrations runs the gate with `--postgres`; any PR touching templates runs `--e2e`; every PR gets the Codex review; humans merge.

## 2. Must fix before the camp vs must not change before the camp

**Must fix before (none is application code):**

| ID | Item | Status |
|---|---|---|
| P0-1 | scheduled backup of the camp DB and media, off-site copy, restore drill | hourly timer installed 2026-10-28 (`emmaus-camp-backup.timer`, 48 dumps + 14 media tars); off-site + drill open |
| P0-2 | `.nodeploy` on the production compose directory | done 2026-10-28 (docker-sandbox PR #52) |
| P0-3 | reword AGENTS.md invariants 1–2 to match the code and state the goal; ADR 0002 | docs only, `risk:low` |
| P0-4 | migration-path guard in the gate/CI: a PR against `v2/foundation` that adds a file under `core/migrations/` or `events/migrations/` fails with the rule | `S`, `area:infra` |
| P0-9 | operational pre-camp checklist: no required `confidencial` question in the live questionnaire; at least one active `can_view_sensitive` member; scheduler running; edge rate limit for `/cuenta/entrar/` and `/estacion/desbloquear/` at Traefik/CrowdSec (F5, F6) | runbook + infra, no app code |
| P0-10 | re-enable Code scanning (CodeQL red), required checks on `v2/foundation` and `main` | owner, issue #13 |

**Must not change before the camp ends (grounded in tests and docs):** `events/access.py`, `core/access.py`, `can_view_sensitive` and the confidential levels, report scopes; the interview lifecycle, claims, kiosk, unlock throttle and middleware; questionnaires, versions, order and `interview_status` derivation (migration 0011's reverse guard); metrics, cutoffs, release/suppression, PDF, deck; exports, join code/access requests, sedes, purge; camp templates and copy (tú voice, axe zero); the schema of `events/*`, `core.Church`, `core.UserChurchRole`; the deployment path. Any change there is `risk:high` and needs a reviewed rollout and rollback plan.

**Security items that touch camp code and therefore wait for a decision (Q-S1..Q-S3) or the camp's end:** F1 (`is_staff` and the admin), F2 (pastor level), F7 (puppet account), C-13 (erasure vs open safety report). Their mitigations without code: no permissions on the role groups, minimal superusers, `/admin/` blocked at the proxy, two-person rule for the level by policy.

## 3. Phases and gates

| Phase | Scope | Gate (what must be true to pass) | Rollback |
|---|---|---|---|
| **0 — Audit** (this) | the four reports, the backlog, the first slice as issues | owner agrees on what exists, what is fragile, what is preserved; first slice approved | — |
| **1 — Domain and authorization foundations** | new apps: `people` (`Person`, `IdentityLink`, `CampusAffiliation`, `Guardianship` later), `access` (`RoleAssignment` projection + `core/authz.py` compatibility layer), per-church flags, tenant resolution helper (`current_church`), second-church fixture, integrity check command, consent provenance (when `events` may change) | one user holds roles in two churches and one event with no leakage (J3 test); cross-tenant tests by guessed id pass; every existing test unchanged and green; screen-identity test green; `events/access.py` untouched | code rollback: tables unused by the camp; `migrate <app> zero` for the new apps |
| **2 — Community MVP** | `groups` app (`Group`, `GroupMembership`, `GroupJoinRequest`), leader tools, discovery and "Pedir unirme", Mi Iglesia home, notifications, shared account helper, `EventParticipantLink` + "Vincular" (post-event), J2.7 negative tests, Spanish/a11y pass, usability script | a real former campero joins an ongoing group without manual admin work beyond the audited link; non-technical tasks succeed in the usability session; axe zero with the member role; J2.7 tests enumerate every camp URL | flags off per church |
| **3 — Recurring engagement** | Explorar public page, `ContactRequest` (J1), church home (J5), person-based follow-up unification, ministries, content links, discipleship on `Person`, `EventRegistration` for non-camp kinds, event-role lifecycle policy; the camp-code fixes parked above (F1, F2, F7, C-13, G-05, G-06, C-11) | regular use beyond events; leaders' admin burden measured down | per-module flags |
| **4 — Commercial SaaS** | provisioning console, per-tenant namespaces (`Event.slug`, join lookup, `/iglesia/<slug>/`), time-bounded audited support access, RLS evaluation, per-tenant toggles, export/import, metrics | a second church with a different organisation model runs without a fork | stack per tenant decision |

Cut-over (after the camp): the 0011 contract and `EventParticipant.person` land on `main`, are merged down, `v2/foundation` becomes `main`, and the camp stack is rebuilt from the new `main` with a restored dump; the old stack stays until the new one has run a full day.

## 4. Architecture decisions to record (ADRs, numbered after 0001)

| ADR | Decision | Status proposed |
|---|---|---|
| 0002 | Target domain and mapping: `Church` = tenant (reuse), `Sede` = campus (reuse), `auth.User` = identity (reuse, harden e-mail uniqueness), new `Person` + `IdentityLink` (church-scoped, one person per account per church, never auto-merged across churches by name/e-mail/phone), `RoleAssignment` as a projection for new scopes, `Questionnaire` stack = form engine, `EventMembership`/`TutorAssignment` = event assignments | first PR |
| 0003 | Authorization compatibility layer: `core/authz.py` `has_scope(user, church, scope, action)` delegates to `UserChurchRole`/`EventMembership` for existing scopes and reads `RoleAssignment` only for new ones; `resolve_access` unchanged; explicit `current_church` in the session for multi-church users | Phase 1 |
| 0004 | Feature flags: per-church booleans with `db_default=False` that gate views; env kill switch `EMMAUS_V2_MODULES`; screen-identity test per flag; flags removed one release after general enablement | Phase 1 |
| 0005 | Migration governance across branches: new apps only during the camp; `core`/`events` numbers owned by `main`; contract migrations with vendor-guarded SQL; management-command backfills; gate guard | now |
| 0006 | Django admin: superuser-only, church roles not `is_staff`, throttled or proxy-blocked login, audited sensitive edits, tenant-scoped querysets where kept | after decision Q-S3 |
| 0007 | Consent provenance: `ConsentRecord` gains notice hash/excerpt/purpose; person-level consent table with the same vocabulary | when `events` may change |

## 5. Migration strategy (expand/contract, additive)

Steps from `workstreams/C-domain.md` §8, re-ordered under rule 2 (no `core`/`events` migration during the camp):

| # | Step | App | Flag | Backfill | Rollback | Effort | When |
|---|---|---|---|---|---|---|---|
| S2 | `Church.people_enabled`, `groups_enabled` (booleans) | core | — | — | drop columns | S | **after the camp** (touches `core`), or held as a `people.ChurchSettings` one-to-one row during it |
| S3 | `Person` (church FK, names, `normalized_name`, birth date, e-mail, phone, `primary_sede`, `user` null SET_NULL, `merged_into` tombstone); `IdentityLink` semantics = `Person.user` unique per church | people | S2 | none (empty) | `DeleteModel` | M | Phase 1, first PR |
| S7 | `RoleAssignment` projection (`subject_user`, `church`, `scope_type`, `scope_id`, `role`, `status`, `start_at`, `end_at`, `source_table`, `source_id`), filled by a command and post-save hooks; read by nothing in the camp | access | — | full rebuild command | drop table | M | Phase 1 |
| S4' | campero ↔ person link as a **separate table** `EventParticipantLink(participant, person, linked_by, linked_at)` (instead of a column on `EventParticipant`) | people | S2 | none; human-confirmed only | `DeleteModel` | M | Phase 2, post-event action |
| S5 | linking flow: same-church candidates by `normalized_name` token match, one click, `persona_vinculada` audit; export/erasure extended to the person; purge deletes link + campero, never the person | people | S2 | dry-run report (counts) | flag off | M | Phase 2 |
| S8 | `Group`, `GroupMembership`, `GroupJoinRequest` | groups | S2 | none | `DeleteModel` | L | Phase 2 |
| S1 | `AuditEvent.church` nullable FK with write-through | events | — | idempotent `UPDATE` | drop column | S | after the camp; interim: a `people.ChurchAuditEvent` table |
| S6 | `ConsentRecord` provenance (`notice_sha256`, `notice_excerpt`, `purpose`) and person/guardian FKs | events | — | none (blank) | drop columns | S | after the camp |
| S9 | `Enrollment.person` nullable beside `Enrollment.participant` | core | S2 | optional | drop column | M | Phase 3 |
| S10 | contract: drop `events_event.questionnaire_version_id` with vendor-guarded `DROP COLUMN IF EXISTS` | events | — | — | reverse SQL re-adds a nullable column | S | on `main`, after the camp, dump first |
| S11 | e-mail uniqueness report → partial unique index on `LOWER(email)`; hash `Invitation.token` with dual-read for 14 days | core | — | one-off report | drop index/column | M | Phase 3 |
| S12 | `RoleAssignment` as the source of truth; per-tenant `Event.slug`; `Sede` moved to `core` | mixed | per church | — | flag off | XL | Phase 4 |

Expand → deploy code that writes both → read-only integrity command (`tenant_integrity`: cross-church `Interview.version`, `participant.sede`, `coordinator.event`, `Interview.event != participant.event`; C-05/C-06) → contract. Rollback per release = previous image + the dump taken before `migrate`; additive steps also allow code-only rollback.

## 6. Test, rollout and rollback gates per phase

- **Phase 1:** `CampFixture` gains `other_church` and a user with roles in both; for every new view a positive, a denied and a guessed-id cross-tenant test; PostgreSQL-only tests for new unique constraints; migration round-trip on a scratch PG for apps with real reverses; screen-identity test with all flags off; the missing security tests from D §9.2 that need no camp-code change (`test_church_a_admin_cannot_open_church_b_event_or_objects`, two-church person, event privileges expiring, church mentor cannot open the camp, public pages inventory, cookie-less join throttle).
- **Phase 2:** `e2e/` gains a J2 script on a `seed_community_demo` seed; `review-all.mjs` runs twice (flags off/on) with a member role in `all_urls.py`; `test_security_review.py` extended with the new routes; P2-7 negative tests enumerate `participant_detail`, `interview_detail`, `follow_ups`, `answers_export`, `participant_data_export`, reports for a linked campero's group leader.
- **Phase 3+:** `simulate_camp_day.py` as the load regression; a second-tenant seed and `review-all` per tenant; before Phase 4 a cross-tenant fuzz over every URL with ids from tenant B.
- **Gate cost control:** fix #14 (shrink the purge seed) before `--postgres` is mandatory on every PR; keep `e2e` near 10 minutes or move the all-screens review to nightly.
- **Rollout:** every PR lands on the `emmaus-v2` stack first (synthetic data); nothing is deployed to the camp. Deploys follow the runbook (tag, dump, build, `up -d`, smoke, edge check).

## 7. Prioritised backlog

Columns: effort, dependencies, risk label, whether a local model may pre-process (`exec:local-ok`). Items marked ★ are the first implementation slice (§8). IDs continue `workstreams/E-gaps.md` §5 where they coincide.

### P0 — camp safety, critical security, data integrity
| ID | Item | Effort | Deps | Risk | Local |
|---|---|---|---|---|---|
| P0-1 | off-site copy of the hourly dumps (restic set) + inventory registration + one restore drill into the v2 DB | S | timer (done) | low (infra) | no |
| P0-3 ★ | reword AGENTS.md invariants 1–2; ADR 0002 target domain | S | — | low | yes (draft) |
| P0-4 ★ | gate/CI guard against `core`/`events` migrations on v2 PRs during the camp | S | — | low | yes (script draft) |
| P0-5 | F1 mitigation without code now (no group permissions, minimal superusers, block `/admin/` at the proxy); code fix after Q-S3 | S / M | Q-S3 | high (auth) | no |
| P0-6 | F2 decision: pastor may or may not widen the level alone; test documenting the choice | S | Q-S1 | high | no |
| P0-7 | F7: a usable password alone is not enough (an admin can create an unflagged account, activate it from the on-screen link, then grant the flag); require a second administrator's confirmation for every `can_view_sensitive` grant, and a regression test that creates an unflagged account, activates it and then tries the grant (after the camp) | M | Q-S2 | high (camp code) | no |
| P0-8 | C-13: refuse manual erasure while a safety report is open (after the camp) | S | — | high (camp code) | no |
| P0-9 | pre-camp operational checklist + edge rate limits (F5/F6) | S | homelab-ops | medium | no |
| P0-10 | Code scanning on; required checks on both branches | S | owner | low | no |
| P0-11 | record the gate baseline on `6f4664f` under `docs/audits/` (counts, run links) | S | — | low | no |

### P1 — foundational enablers (one session each where possible)
| ID | Item | Effort | Deps | Risk | Local |
|---|---|---|---|---|---|
| P1-1 ★ | ADR 0003 authz compatibility layer, ADR 0004 flags, ADR 0005 migration governance | S | P0-3 | low | yes |
| P1-2 ★ | `people` app: `Person` (church-scoped, `persons_for(user)` helper), read-only admin, tests | M | P1-1 | medium | partial |
| P1-3 ★ | backfill command `link_people` from `ParticipantProfile` and church-role holders, idempotent, no person for camperos; reverse no-op | M | P1-2 | medium | no |
| P1-4 | `CampusAffiliation` | S | P1-2 | medium | partial |
| P1-5 | `access` app: `RoleAssignment` projection + `core/authz.py` `has_scope` delegating to legacy tables; denied-path tests | M | P1-1 | high (escalate per policy) | no |
| P1-6 | explicit `current_church` in the session for multi-church users; `get_user_role` reads it with fallback | M | — | high | no |
| P1-7 | `Church.community_enabled` (or `people.ChurchSettings` during the camp) gating every new URL; screen-identity test | S | — | low | yes |
| P1-8 | `core/notify.py` content-free notification helper | S | — | low | yes |
| P1-9 | church-level audit (`people.ChurchAuditEvent` now; `AuditEvent.church` after the camp) | S | — | medium | no |
| P1-10 | `seed_second_church` fixture + `test_tenant_isolation.py` (D §9.2 first row) | S | P1-2 | low | yes |
| P1-11 | `current_church(request)` for landing/brand/theme with fallback | M | P1-6 | medium | no |
| P1-12 | `tenant_integrity` read-only check command (C-05/C-06), run in CI and nightly | S | — | low | yes |
| P1-13 | `.env` and admin runtime review (superusers, group permissions, HSTS, SMTP) documented in the camp runbook | S | — | low | no |

### P2 — camp-to-small-group MVP (J2)
| ID | Item | Effort | Deps | Risk | Local |
|---|---|---|---|---|---|
| P2-1 | `groups` app: `Group`, `GroupMembership`, `GroupJoinRequest` + admin | M | P1-2, P1-5 | medium | partial |
| P2-2 | leader tools: my groups, members, requests, approve/decline via `RoleAssignment(scope=group)`; guessed-id 403; audited | M | P2-1 | high (authz) | no |
| P2-3 | discovery + "Pedir unirme" (church-scoped open groups, idempotent request, throttled) | M | P2-1 | medium | partial |
| P2-4 | Mi Iglesia home `/mi-iglesia/` for any user with a `Person` in the church: groups, requests, upcoming events (name/date); router "Mi Iglesia first" behind the flag (B-04) | M | P1-3, P1-7 | medium | partial |
| P2-5 | join-request notifications via `core/notify.py` | S | P1-8, P2-1 | low | yes |
| P2-6 | `EventParticipantLink` + admin-only audited "Vincular con una persona" on the campero page (post-event; refused while `activo`); purge deletes link + campero, never the person | M | P1-2 | high (camp screen) — after the camp | no |
| P2-7 | J2.7 negative tests enumerating every camp URL for a linked campero's group leader | S | P2-2, P2-6 | high | yes (enumerate) |
| P2-8 | shared account-creation helper `core/accounts.py` for Person → account; `team.add_member` keeps its copy until after the camp | M | P1-3 | medium | no |
| P2-9 | Spanish copy + a11y pass for the new screens; `all_urls.py` member role; axe zero | S | P2-2..4 | low | yes |
| P2-10 | usability script for the Phase 2 gate (B §7 tasks T6–T8) | S | P2-3 | low | yes |
| P2-11 | glossary decision: the person noun ("Campero" for the camp, "Participante" reserved for `Person`); rename the event kind "Programa de discipulado" at the next content release (B-03) | S | Q-P1 | low | yes |

### P3 — broader engagement
| ID | Item | Effort | Deps | Risk | Local |
|---|---|---|---|---|---|
| P3-1 | Explorar: public church/sede page from `public_welcome_text`, `contact_email`, sedes, service times | M | P1-11 | low | partial |
| P3-2 | `ContactRequest` (J1) → `Person(status=visitor)` + welcome queue | M | P1-2, P1-8 | medium | partial |
| P3-3 | church home for admin/pastor (J5) with `suppress`ed counts | M | P2-1, P3-2 | medium | no |
| P3-4 | person-based follow-up unification (`FollowUpRequest` → `Person`; `events.FollowUp` untouched); one read-only "Seguimiento" union list first (B-02) | L | P1-3 | high | no |
| P3-5 | ministries (`Ministry`, `TeamMembership`, `InterestRequest`) | L | P1-5 | medium | partial |
| P3-6 | content links (`ContentItem`/`Series`) | M | P3-1 | low | yes |
| P3-7 | discipleship on `Person`/`RoleAssignment` | L | P1-5 | high | no |
| P3-8 | `EventRegistration` by a person for non-camp kinds + public event list; "Mis eventos" inside Mi Iglesia (G-01) | L | P1-2, P2-4 | high (camp adjacent) | no |
| P3-9 | event-role lifecycle policy (closed events read-only after N days, hidden) with `start_at`/`end_at` on new scopes | M | P1-5 | high | no |
| P3-10 | snapshots store suppressed counts or a raw table readable only by the release job (C-11) | M | — | high (camp code) | no |
| P3-11 | questionnaire editor: option pickers instead of typed codes; new default version wording (G-05) | M | — | high (camp code) | partial |
| P3-12 | human-readable per-person export (PDF/CSV beside JSON) (G-06) | S | — | high (camp code) | partial |
| P3-13 | F1 code fix (ADR 0006), F2/F7/C-13 fixes, a test of the scheduler's existing `SessionStore.clear_expired()` call, archived-event closure, 403 keeps the event bar, a11y leftovers batch | M | after the camp | high | partial |
| P3-14 | split runtime/dev requirements; constraints file; multi-stage image; pin `postgres:16` by digest in the repo compose (R-1, R-2) | S | #11 | low | yes |

### P4 — SaaS readiness
| ID | Item | Effort | Deps | Risk | Local |
|---|---|---|---|---|---|
| P4-1 | tenant provisioning console (superuser-only): church + first admin without `/setup/` | L | P1-11 | high | no |
| P4-2 | per-tenant namespaces: `Event.slug` per church, join lookup scoped, `/iglesia/<slug>/` landing; redirects from old paths | L | P4-1 | high (camp URLs) | no |
| P4-3 | time-bounded, audited support access | M | P1-5, P1-9 | high | no |
| P4-4 | row-level security evaluation on PostgreSQL 16 | M | — | medium | yes |
| P4-5 | per-tenant toggles, export/import of a church's data, operating metrics | L | P4-1 | high | no |

## 8. The first implementation slice (proposal, not implemented)

**Epic: "Foundations, step 1 — `Person` in a new `people` app (additive, invisible, zero camp impact)".** Three PRs, each one session, draft against `v2/foundation`:

1. **Docs PR (`risk:low`, needs the owner's decision on the new wording of invariants 1–2 before merge)** — P0-3 + ADR 0002: reword AGENTS.md invariants 1–2; the target-domain ADR with the current → target mapping and the "never auto-merge across churches" rule. Companion: P0-4 gate guard against `core`/`events` migrations during the camp.
2. **`people` app PR (`risk:medium`)** — `Person` (`church` FK, `first_name`, `last_name`, `normalized_name`, `email` blank, `phone` blank, `preferred_contact_method` copied from `ParticipantProfile.ContactMethod`, `created_from` in `setup/invitation/team/backfill/manual`, `user` nullable SET_NULL, `merged_into` nullable self-FK, timestamps; `unique (church, user)` where user is set; index `(church, normalized_name)`); `people/persons.py` with `persons_for(user)` (church-scoped queryset); read-only Django admin (superuser only); tests. No view, URL, nav item, change under `events/`, change to `User` creation paths, and no `Person` for any `EventParticipant`.
3. **Backfill PR (`risk:medium`)** — management command `link_people` (dry-run default, `--apply`, idempotent): one `Person` per `ParticipantProfile` and per distinct `(church, user)` in `UserChurchRole`, carrying phone and contact preference; reverse = no-op (the rows are new); tests.

**Why it is the safest start.** It is the one dependency of every P2 item and of J3/J6, yet it changes no behaviour: no view reads it, so every existing test, the browser gate and the camp screens are untouched; the migrations are in a new app (rule 2) and purely additive; rollback is `migrate people zero`; it touches neither `events/access.py` nor `core/access.py`; the only escalation condition it meets is the
wording of invariants 1–2 itself, which is why the docs PR needs the owner's explicit decision on that
wording before merge (policies.md, Escalate); and it settles the identity vocabulary before anyone builds groups on `User`.

**Acceptance tests (SQLite and PostgreSQL):**
1. `test_person_is_church_scoped` — `persons_for(user_of_church_A)` never returns a person of church B, including `get(pk=…)` on the helper queryset.
2. `test_identity_link_is_unique_per_church_and_user` — a second `Person` with the same `(church, user)` raises `IntegrityError`; the same user may be a person in another church.
3. `test_backfill_creates_one_person_per_profile_and_role_holder` — with `seed_dev_data` and `seed_camp_demo`, the command creates exactly one person per profile and per distinct `(church, user)` role holder, with phone and preference copied; a second run changes nothing.
4. `test_no_person_is_created_for_event_participants` — after the backfill the count equals the number above and no person shares a name with a seeded campero unless they are a staff user.
5. `test_people_migrations_reverse_cleanly` — `migrate people zero` succeeds on a database holding backfilled rows.
6. `test_core_and_events_migrations_unchanged` — `core` still has 10 and `events` 12 migrations; `makemigrations --check` is clean.
7. `test_person_has_no_url` — no `people` URL name resolves; `/admin/people/person/` is reachable only by a superuser.
8. The whole gate: same pass/skip counts as the baseline (1,414/6 SQLite, 1,409 + 11 deselected PostgreSQL), browser gate unchanged, `check --deploy` only W005/W021, ruff clean, Codex verdict attached.

**Rollout/rollback:** additive migrations in a new app; lands on the `emmaus-v2` stack only; rollback = `migrate people zero` or the pre-upgrade dump.

## 9. Cost drivers to measure (grounded)

| Driver | Grounding | Measure |
|---|---|---|
| docker-sandbox headroom | 4 cores / 8 GB; two stacks (3×4 + 2×4 gunicorn threads, two Postgres, two 800 MB images) | RSS of both stacks on a camp day; whether `emmaus-v2` must pause during the event |
| image build | ~35 min cold on the sandbox; single stage with compilers | cold vs cached; multi-stage saving |
| DB connections | 12 + 1 per stack + 1 per command | `max_connections`; a managed ~20-connection tier would not fit two stacks |
| CI minutes | e2e ~10 min; PG suite capped 25 min; #14 seed 31 min | minutes per PR on the public-repo free tier |
| Codex review | one CLI run per PR push with `OPENAI_API_KEY` | tokens per PR |
| e-mail | console backend today; J1/J2 need invitations, join notices, resets | messages per day once groups exist; provider pricing |
| media | logos only; "Contenido" adds media; no object storage, no `MEDIA_URL` | GB and bandwidth once content exists |
| backups | hourly 48-dump rotation + daily media tar on the host | dump size × retention × stacks; off-site cost |
| public hostnames | one-level names, manual tunnel entries | per-tenant hostnames in Phase 4: tunnel vs wildcard |
