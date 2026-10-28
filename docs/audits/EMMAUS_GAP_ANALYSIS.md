# Emmaus — gap analysis against the product vision

Commit `6f4664f`, 2026-10-28. Companion to `EMMAUS_CURRENT_STATE_AUDIT.md`; evidence and line numbers in `workstreams/E-gaps.md` (modules, journeys, backlog), `C-domain.md` (model mapping) and `B-product-ux.md` (experiences). Classification scale: **existing and reusable** / **partially reusable** / **camp-coupled** / **missing** / **not yet verified**. "MVP" = vision §8 "Must have". Risk uses the repository's labels (`docs/agents/workflow.md`): authorization, tenant isolation, sensitive data, camp flows and non-additive migrations are `high`.

## 1. Capability map (vision §6)

| Module | Classification | What exists (Verified) | Dependencies | Risk of generalising | MVP |
|---|---|---|---|---|---|
| Personas | partially reusable | `auth.User` keyed by e-mail in every creation path (`core/services.py:96-103`, `core/views.py:280-283`, `events/services/team.py:86`); `ParticipantProfile` with contact preference, phone, consent timestamps (`core/models.py:202-227`); `EventParticipant` event-owned, no login, no contact fields (`events/models.py:524-559`); duplicate detection warns only (`events/services/participants.py:25-39`) | — | **high** (identity, camp table): add `Person` and `IdentityLink` as new tables; link camperos through a separate link table, never by editing `EventParticipant` | must |
| Organizaciones / Sedes | existing and reusable, single-tenant seams | `Church` with slug, flags, theme, logo (`core/models.py:27-116`); `Sede` per church with `(church, slug)` unique, `active`, `order` (`events/models.py:76-94`); church-admin-only CRUD inside the event UI; cross-church tests (`test_sedes.py:612`) | — | medium for `CampusAffiliation`; high for tenant resolution and per-tenant namespaces (public camp URLs) | must |
| Eventos / Experiencias | existing, camp-coupled | generic `Event.Kind` and status (`events/models.py:120-131`), gendered copy helpers; everything else assumes a camp: roles tutor/coordinador, `room`, `adult_age`, guardian authorization, ten "Computadora N" created on "Crear campamento" (`events/views/home.py:63-64`); no registration by a person, no public event list | Personas | **high** (camp flows): generalise by wrapping, new event types beside the camp path | must keep stable; registrations later |
| Grupos | missing | no model/view/template; nearest patterns: `EventMembership.team_name` + `TutorAssignment` (one tutor per coordinador), `ParticipantProfile.assigned_mentor`, `EventParticipant.church_group` free text | Personas, RoleAssignment | medium greenfield behind a flag; high where it touches identity and leadership authorization | must |
| Ministerios / Servicio | missing | reusable pattern: `AccessRequest` = ask to join with name + e-mail, no account, admin decides, approval runs the one account-creation path (`events/models.py:448-493`, `events/views/admin.py:549-595`) | Personas | medium | no (interest forms only if validated) |
| Seguimiento | partially reusable, two implementations | `core.FollowUpRequest` (status, source incl. `participant_request`/`inactivity`, assignee, due date, content-free mail) (`core/models.py:415-457`); `events.FollowUp` (care/safety, confidential flag, scoped assignment) (`events/models.py:1036-1096`); no reminders, no multi-step workflow | Personas | medium for a person-based request; **high** to unify with the camp's (sensitive) | partially (join notifications) |
| Mi Iglesia | partially reusable, mostly missing | only "Mi camino" for discipleship participants (`core/views.py:387-408`); the router sends event roles and church admins to the camp (`core/views.py:220-244`); no home for a member who is neither staff nor discipleship participant | Personas, flag, Grupos | medium (new screens) | must |
| Contenido | missing | `Lesson` is the only content entity | — | low | no (links) |
| Discipulado | existing and reusable, coupled to the single church role | paths, lessons, reflections, questions, notes with visibility, optional AI drafts behind `LLM_ENABLED`; nav gated by `discipleship_enabled`, views not (`docs/architecture.md:27`); 73 tests | roles `participant`/`mentor` are exclusive church roles | high to re-key onto Person; low to leave | after MVP |
| Comunicación | partially reusable | e-mail only, always content-free, `email_is_configured()` guard; console backend by default; no in-app notifications | SMTP in production (Unknown) | low (additive) | partially |
| Reportes / Operaciones | existing, camp-coupled | metrics per scope, immutable cutoffs with release-by-k and suppression (`events/services/reports.py:36-47`, `metrics.py:540-553`), role dashboards with deterministic next steps, PDF, names-free deck; church overview only for discipleship | event scopes | high for camp metrics; **low** to reuse `suppress`, `release_cells`, `AuditEvent`, `AuthThrottle` | partially (J5 later) |
| Emmaus Console | missing | only the Django admin (cross-tenant, unaudited; F1) and `administered_churches` for superusers; demo checks and purge commands; no provisioning, toggles, support access or metering | tenancy | high | no (beyond tenant boundaries) |
| Donaciones | missing, out of MVP | no model | — | — | no |

## 2. Camp-specific inventory: generalise, wrap or retain

| Function | Generic? | What couples it to "camp" | Recommendation |
|---|---|---|---|
| Questionnaires, versions, order | yes at model level (church-owned, immutable published versions, sensitivity, conditions, per-event order) | default content "Conversación espiritual"; editor help says "camperos"; screens under the event | **retain** — it already is the vision's `FormDefinition`/`FormVersion`/`Submission`; later a non-event submission owner in a new table, not `Interview` |
| Camperos + CSV import | partly (event-owned by design) | required `room`, `age` 5–99, `church_group`; number per event; no e-mail/phone; purged by retention | **retain**; generalise by link (`EventParticipantLink`), never by adding columns before the camp |
| Interview modes and station flow | behaviourally generic (assisted/supervised, claims, revision, kiosk confinement) | stations "Computadora N", consent by `adult_age`, camp copy | **retain** (most tested, most critical) |
| Follow-ups (camp) | model generic enough | FK to campero; camp reasons; care-team recipients from memberships | **wrap**: keep for events; person-based request beside it; unify after the camp |
| Cutoffs / snapshots | generic mechanism | scopes `evento`/`equipo:<tutor>`; interview metrics | **retain**; reuse release/suppress for any aggregate |
| Dashboards per role | camp role homes | fully event-scoped | **retain**; copy the task-first pattern, not the code |
| Presentation privacy, PDF, exports | generic serializer and helpers | camp titles and columns | **retain**; `safe_cell` reusable |
| Join code / access requests | generic "ask to join a team" with throttles and a cap | requestable roles tutor/coordinador; lookup spans churches | **wrap**: the template for ministry interest and group join requests; scope the lookup per church after the camp |
| Audit | generic, never answer contents, masked for non-flag viewers | camp action labels; list per event | **generalise additively**: nullable `church` FK (or a `core` table), new labels, keep the event page |
| Retention / purge | generic per event | deletes camperos outright | **retain**; once a campero is linked to a person the purge must delete the link and the camp record, never the person (test in the link PR) |
| Consent / guardian | kinds and methods with latest-wins history | FK to campero; `requires_guardian_authorization` | **retain**; a person-level `ConsentRecord` with the same vocabulary and provenance fields (C-08) |
| Sedes | church-level model | UI inside the event; the campero's sede is the only affiliation | **generalise** by adding `CampusAffiliation`; leave the camp field |
| Throttles, idle logout, kiosk middleware | generic | key prefixes | **retain**; reuse for any new public form |

## 3. Journeys J1–J6: what exists, what is missing, the smallest path

| Journey | Exists | Missing | Smallest path (touches) |
|---|---|---|---|
| J1 visitor → connected attendee | camp-oriented landing for the first active church; `Invitation` (admin-initiated); `self_registration_enabled` and `public_welcome_text` are inert (`core/models.py:30,42`, no reader) | public church/sede page; a contact request without login; person-based welcome queue | after `Person`: `ContactRequest` throttled like `join:`, stored as `Person(status=visitor)` plus a follow-up record; welcome-team list reuses the follow-up list UI. Touches `core` (or a new app), one public path, notifications. Nothing under `events/`. |
| **J2 camp → small group** (first milestone) | steps 1–2 complete (registration, interviews, event-scoped staff views); step 3 partial (closed events stop interviews, archived leave the list, memberships only `is_active`, retention purge); **step 7 holds by construction** (answers reachable only through `event_view`; a church pastor without the flag sees no individual answers; views audited) | steps 4–6 entirely: campero has no account/contact/person; no member home; no groups; no leader scope | six PRs in order: `Person` + `IdentityLink` (invisible) → `RoleAssignment` + `core/authz.py` compatibility layer → `Group`/`GroupMembership`/`GroupJoinRequest` + leader screens behind `Church.community_enabled` → Mi Iglesia home → join notifications → `EventParticipantLink` + audited "Vincular con una persona" on the campero page (post-event; the only camp-screen touch) with the J2.7 negative tests. Never a `UserChurchRole` or `EventMembership` for a group leader; group views never query `Interview`/`Answer`/`FollowUp`. |
| J3 one person, several roles | event roles per event work; church pastor is pastor in every event unless removed; deactivating a membership removes only that event; accounts are e-mail-unique de facto | a second church role in the same church (unique constraint); explicit current church (`core/access.py:20-23`); group/ministry scopes; `start_at`/`end_at` | `RoleAssignment` for new scopes only, legacy tables read through the compatibility layer; session-held current church for multi-church users (no change for single-church users) |
| J4 ministry volunteering | nothing; patterns to copy: `AccessRequest`, pastoral-information gating | `Ministry`, `TeamMembership`, `InterestRequest` | Phase 3, copy of the `AccessRequest` flow keyed to church + ministry |
| J5 pastor and church admin | one-event dashboards with deterministic agenda; follow-up assignment; k-release (`min_group_size` 3–20); church overview only for discipleship | church/campus view across events and groups; delegation beyond follow-up assignment | church home listing pending group/contact requests and open follow-ups with `suppress`ed counts |
| J6 second tenant | FK scoping and cross-church tests for sedes, questionnaires, accounts; "otra iglesia" oracle | `/setup/` once; first-active-church landing/brand/theme; `churches[0]` on create; global `Event.slug` and cross-church join lookup; first-church core resolver; cross-tenant Django admin | tenant resolution in the request (session church; `/iglesia/<slug>/` landing), church choice on create, per-church uniqueness for **new** objects, `seed_second_church` fixture with isolation tests; re-scoping camp URLs waits for the camp to end |

## 4. Building blocks to reuse (patterns, not code moves)

- The scoping pattern: a leader sees exactly the people of their own unit, derived from explicit assignment rows (`TutorAssignment` → `supervised_coordinator_ids`, `events/access.py:99-141`), never inferred from names.
- The request pattern: `AccessRequest` (no account on request, admin decision, audit, caps and throttles, approval through the single account-creation path).
- The follow-up shape: `FollowUpRequest.Source` and `by_state` ordering (`core/views.py:125-136`); the privacy rules of `events.FollowUp`.
- The task-first home: one tinted panel with the next question, then figures (`coordinator/home.html`, `admin/_home_live.html`).
- Aggregation privacy: `suppress()`, `release_cells()` with tests (`events/tests/test_reporting.py:119-148`).
- Content-free notifications: `send_email_safely` + `email_is_configured()`; the tests that assert mails carry no names (`core/tests.py:561-572`).
- Per-request shell selection and the one bar partial (`events/context_processors.py:23`, `partials/ui/bar.html`): "contextual Liderazgo" already exists; Mi Iglesia is `base.html` with four links and a conditional "Liderazgo" button (B §6.2).
- Per-church boolean flags with `db_default=False` and a data migration that turns them on where in use (`core/0010`).
- Expand/contract with `db_default` on every new NOT NULL column and reverse guards (`events/0011`).

## 5. Verified facts vs assumptions

| Statement | Status |
|---|---|
| `v2/foundation` has zero application-code change over `main` | Verified (`git diff --stat`) |
| Authorization is centralised and every view lookup is scoped | Verified (D §2–§3; 69 tests run) |
| J2.7 "no exposure to the new leader" holds today for anyone without an event role or church admin/pastor role | Verified by construction (`events/views/common.py:30-34`, `access.py:175-209`); **not** tested as such → P2-7 |
| Church admins are `is_staff`; `/admin/` login unthrottled | Verified (`core/services.py:102`, `emmaus/urls.py:11`) |
| Transitive dependencies unpinned; image carries compilers and pytest | Verified (`requirements.txt`, `Dockerfile`) |
| The camp database holds real participants | Unknown (not read by rule; the ledger shows demo accounts active on 2026-10-28; both may be true) |
| SMTP, HSTS and SSL redirect configured in production | Unknown (`.env` not read; `SECURE_SSL_REDIRECT` forced false by the host compose, TLS terminates upstream) |
| Phone usability of in-table scrolling and select-in-row | Hypothesis (G-10) until measured (B §7 tasks T2, T4) |
| PostgreSQL still holds `events_event.questionnaire_version_id` | Inferred from migration 0011; production schema not inspected |
| Django 5.2.18 supports Python 3.14 (local venv) | Unknown; CI and the image use 3.12 |

## 6. Where the vision and the code conflict (report, do not assume)

| Vision text | Code today | Resolution proposed |
|---|---|---|
| AGENTS.md invariant 2 "neither implies the other" | church admin ⇒ event admin; church pastor ⇒ event pastor (`events/access.py:260-268`, tested) | reword the invariant to describe the implication and its one-way direction; keep the behaviour (P0-3) |
| AGENTS.md invariant 1 "a person's record never depends on an event" | camperos are deleted with the event's retention purge | state it as the goal once `Person` exists; camperos stay event-bound until linked (P0-3) |
| Vision §2.10 / invariant 7 "never sent to an AI service" | discipleship sends a participant's *question text* to an external LLM when `LLM_ENABLED` (off by default; the camp has no call site) | church-level consent text and a per-church switch (F12); decision Q-P3 |
| Vision §4.2 Mi Iglesia navigation `Inicio · Explorar · Mi actividad · Perfil` | the router sends church admins into the camp; member nav exists only for discipleship participants | flag-gated router change in Phase 2 (B-04) |
| Vision §5.3 "evaluate tenant, scope_type, scope_id, subject, action, start_at, end_at" | three one-per-scope mechanisms, no dates, no group scope | projected `RoleAssignment` for new scopes; legacy tables stay the source for the camp |
| Vision §5.4 guardians and households | guardian = staff attestation on a `ConsentRecord`; no guardian identity | after `Person`: `Guardianship` behind a flag |
| Vision §9 "mobile-first" | the camp design assumes shared computers; phones are supported but unmeasured | measure first (B §7), then design |
