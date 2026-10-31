# Emmaus — current-state audit

- **Commit audited:** `6f4664f` on `v2/foundation` (= `main` 032d846, the camp in production, plus the vision documents and the agent workflow; zero application-code difference). Date: 2026-10-28.
- **Method:** read-only. Five parallel workstreams (`workstreams/A-inventory.md` repository and release, `B-product-ux.md` product and UX, `C-domain.md` data model, `D-security.md` security and authorization, `E-gaps.md` reuse and gaps), then this synthesis, which re-read every High and Medium finding in the code. Static evidence only, plus scratch SQLite introspection and two existing test suites run read-only; no server, no `.env`, dumps, media or production data. The host checks in §9 were made over SSH against docker-sandbox (read-only commands).
- **Tags:** **Verified** = read at `path:line` in this commit. **Inferred** = reasoned from verified facts. **Unknown** = could not be verified, reason given. Recommendations are marked as such. Vision = `docs/EMMAUS_PRODUCT_VISION.md`, a target, never a description of the present.

## 1. Executive summary

1. **One Django 5.2 monolith, two apps, one database.** `core` (church, church roles, logo, discipleship) and `events` (the camp). Server-rendered templates, one stylesheet, one script, PostgreSQL 16, gunicorn plus a scheduler loop. No API, queue, cache or external service besides optional SMTP and an optional LLM endpoint for discipleship drafts. [Verified `emmaus/settings.py`, `docs/architecture.md:3`]
2. **The camp code is solid and tested.** 1,420 tests (1,414 pass and 6 skip on SQLite, 1,420 on PostgreSQL), a browser gate at zero axe violations, threaded concurrency tests, a load script. Authorization is centralised (`events/access.py`, `core/access.py`); every id lookup in every view is scoped; the sensitive-data rules are explicit, audited and covered by 69 dedicated tests. [Verified D §0–§3]
3. **There is no person.** A campero is one `EventParticipant` row per event with no account, no contact field and no link to any other event; a discipleship participant is a Django user with a profile; nothing connects the two. The vision's first milestone (camp → small group, J2) cannot be built on today's tables. [Verified `events/models.py:524-559`, `core/models.py:202-227`]
4. **Roles are three disjoint one-per-scope mechanisms**: superuser, `UserChurchRole` (one per church), `EventMembership` (one per event). Church admins are event admins everywhere in their church and church pastors are event pastors unless removed; the discipleship side binds a person to the first church role by id. [Verified `events/access.py:255-269`, `core/access.py:20-23`]
5. **Tenant = `Church`, isolation is implicit.** Every aggregate has a church FK root, but 17 of 22 camp tables carry only an event FK; isolation is enforced by services and querysets, not the schema. Single-tenant assumptions survive in `/setup/`, the landing brand, event creation (`churches[0]`), the global event slug and the cross-church join lookup. [Verified C §4, D §4]
6. **Highest security finding (F1, High):** church admins and pastors are Django `is_staff`; `/admin/` is mounted with Django's unthrottled login and no ModelAdmin is tenant-scoped; `can_view_sensitive` edits there are not audited. Empty admin today, an unlimited password-guessing surface and a cross-tenant back door the moment a permission is granted. [Verified `core/services.py:102`, `core/views.py:286`, `emmaus/urls.py:11`, `events/admin.py:33-36`]
7. **Two sanctioned single-person paths reach confidential answers** (F2, F7, Medium): a pastor without the flag can widen "Respuestas confidenciales" for later interviews; an event admin can create a flagged second account and set its password from the on-screen activation link. Both are audited; neither needs a second person. [Verified `events/views/admin.py:1008`, `:476-534`]
8. **Operations finding (D-2, High):** the production camp database and media volume had no scheduled backup, only hand-made pre-deploy dumps; the host check confirmed no cron, no timer. An hourly dump timer was installed during this audit (§9.4); off-site copy and a restore drill are still open.
9. **The UX is role-first and accessible, and already "contextual Liderazgo" inside events.** The seams the vision will cross are vocabulary (Campero vs Participante), two follow-up inboxes, and the entry rule that sends every church admin into the camp. Nothing in UX blocks the camp. [Verified B §2, §6]
10. **Migrations are additive and disciplined** (`db_default` everywhere, backfills, reverse guards) with one owed contract (`events_event.questionnaire_version_id`) and a governance risk: `main` and `v2` will both want the next `core`/`events` migration numbers while the camp is live. [Verified `events/migrations/0011:4-5`, A M-1]

## 2. Confirmed stack and runtime

| Layer | Fact | Evidence |
|---|---|---|
| Framework | Django 5.2.18, Python 3.12 in the image (`python:3.12-slim`); local venv 3.14 | `requirements.txt`, `Dockerfile:1` |
| Apps | `core` (15 models, 10 migrations), `events` (22 models, 12 migrations); stock `auth.User`, no custom user model | A §1.1, C §1 |
| Database | PostgreSQL 16 via psycopg 3; SQLite when `DATABASE_URL` unset; `CONN_MAX_AGE` 60 | `emmaus/settings.py:86-114` |
| Web | gunicorn 3 workers × 4 threads (camp), 2 × 4 (v2 stack); WhiteNoise manifest static; `/health/` without DB | `Dockerfile:41`, `core/views.py:156` |
| Background | one `scheduler` container: `run_report_cutoffs --loop 300` (claims, kiosk expiry, cutoffs, session cleanup, throttle purge, daily retention purge); healthcheck disabled; silent on failure outside the admin home | `run_report_cutoffs.py:64-113`, `docker-compose.prod.yml:36-37` |
| Routes | 134 application routes: `/admin/`, 7 account, 77 `/eventos/`, 7 `/estacion/` (kiosk), 2 public join, 37 core; 72 views carry `@event_view` | A §1.4, B §1 |
| Middleware | Security → WhiteNoise → `PrivateResponseMiddleware` (no-store, CSP, Permissions-Policy) → session → locale → CSRF → auth → messages → `KioskConfinementMiddleware` → `StaffIdleLogoutMiddleware` | `emmaus/settings.py:48-61`, `events/middleware.py` |
| Integrations | SMTP (optional, content-free mails), OpenAI-compatible LLM for discipleship drafts behind `LLM_ENABLED` (default off); nothing else | `core/services.py:112-118, 279-330` |
| Feature flags | per-church booleans (`discipleship_enabled`, theme, `self_registration_enabled`, `leaders_can_invite_participants`) and per-event settings; no flag library; `discipleship_enabled` gates navigation, not views | A §1.9, `docs/architecture.md:27` |
| Tests | 1,420 collected in 50 modules; 3 `TransactionTestCase` classes; fixture = one church, two events (`events/tests/base.py`); e2e `station-flow.mjs`, `review-all.mjs`; load `simulate_camp_day.py` | A §3 |
| Tooling (this branch) | CI 7 jobs (ruff, Django checks, tooling tests, pip-audit, SQLite, PostgreSQL, browser gate), CodeQL, advisory Codex review; `scripts/agents/*` | A §4 |
| Deployment | docker-sandbox: camp stack `emmaus-demo` (checkout on `main`, `migrate --noinput` on every start, `.nodeploy`), v2 stack `emmaus-v2` (own checkout, DB, media, network, `.nodeploy`); public names via a Cloudflare tunnel | A §5.3, homelab-ops ADR 0042 |

## 3. Architecture map

- **Request path (events):** URL `/eventos/<slug>/…` → `event_view(*roles)` loads the `Event` by **global** slug, calls `resolve_access(user, event)`, raises 403 without access, enforces the role allow-list, activates the event time zone, remembers the slug in the session → the view receives `(event, access)` and derives every queryset from `access` (`participants()`, `interviews()`, `follow_ups()`, `snapshots()`). [Verified `events/views/common.py:24-44`, `events/access.py:131-160`]
- **Request path (core):** `role_required(*roles)` → `get_user_role(user)` = the first `UserChurchRole` by id → `visible_*` querysets filtered by that church. [Verified `core/views.py:139-153`, `core/access.py:20-23, 60-131`]
- **Entry router:** `/dashboard/` sends anyone with an accessible event, any superuser and every church admin into the camp first; participants and mentors to their homes; pastors to the church panorama only if discipleship is on. [Verified `core/views.py:221-244`]
- **Shells:** six (`base.html` for church/discipleship/auth/errors; leader, coordinator, focus, kiosk and deck shells for the camp), one bar partial, the event shell chosen per request from `access.role`. [Verified `events/context_processors.py:23`, `partials/ui/bar.html`]
- **Kiosk:** a supervised interview hands the computer to the campero by logging the staff out and opening `/estacion/` under a server-side `ParticipantSession` (hashed tokens, 10-min idle, 60-min max); the middleware confines that browser to `/estacion/`. [Verified `events/services/interviews.py:761-843`, `events/middleware.py:26-39`]
- **Reports:** immutable `ReportSnapshot` cutoffs per scope, k-suppression at render, a whitelist serializer for the projector deck, ReportLab PDF. [Verified `events/services/reports.py`, `presentation.py`, `models.py:1143-1153`]
- **Data flow is request/response plus polling** (20–60 s fragments with `X-Requested-With: fetch`); no websockets, no events bus. [Verified `static/js/emmaus.js:119-132`]

## 4. Implemented user journeys (as coded)

| Role | Entry and home | Primary tasks | Ends |
|---|---|---|---|
| Campero (no account) | handed a computer: `welcome.html` "Hola, {first_name}." with the three promises and "¿Quién lee tus respuestas?" | one question per screen, "Prefiero no responder", "Parar por ahora" saves; review; "Ya terminé" | hand-back screen; coordinator unlocks with password; idle → awaiting unlock |
| Coordinador | "Mi inicio": one tinted panel with the next action | choose computer, register campero (duplicate warning), assisted or supervised interview, resume/take over, decide follow-up, correct with reason, report risk, paper backup list | "¡Terminaste!"; idle logout at 30 min with "Seguir aquí" |
| Tutor | "Mi equipo": stalled interviews sign, progress per coordinator | supervise assigned coordinators, pending, follow-ups, team cutoffs | none explicit |
| Pastor | "Resumen general": five questions in order, k-anonymised outcomes, deterministic agenda | progress, follow-ups, reports, presentation; can set "Respuestas confidenciales" | — |
| Administración | "Inicio": first blocker sign, "k de 6 pasos listos" | create event (10 stations + default questionnaire), procedures, CSV import, team and join code, tutors, questionnaires and versions, cutoffs, exports, sedes, sessions, audit log | event Terminado/Archivado |
| Church admin | always lands in the camp (B-04); church panorama via "Discipulado" | settings/logo/colours, users, invitations, paths, questions, follow-ups | — |
| Discipleship participant / mentor | invitation token → "Mi camino" / "¿Quién necesita atención hoy?" | lessons, reflections, questions, "Pedir una conversación"; mentor answers, escalates, optional AI draft | "Terminaste la ruta." |

Public (no login): landing, `/health/`, church logo by slug, `/setup/` (first run only), auth pages, `/unirme/` and `/e/<code>/`, the seven kiosk routes (gated by the kiosk session, not login). [Verified B §1]

## 5. Current roles and authorization

| Level | Mechanism | Rule | Evidence |
|---|---|---|---|
| Global | `User.is_superuser` | admin of every event and church; sees archived events | `events/access.py:261,275-276,296-297` |
| Global, vestigial | Django `Group` named after the role | set by `assign_role`, consulted by nothing | `core/services.py:58-68` |
| Church | `UserChurchRole` unique `(church, user)`: `church_admin`, `pastor`, `mentor`, `participant` | `church_admin` ⇒ event ADMIN in every event of the church; `pastor` ⇒ event PASTOR unless a deactivated membership exists; `mentor`/`participant` ⇒ discipleship only | `events/access.py:260-268` |
| Event | `EventMembership` unique `(event, user)`: `administrador`, `pastor`, `tutor`, `coordinador`, plus `can_view_sensitive`, `team_name`, `is_active`; `TutorAssignment` | roles never leak across events; the flag is per event and cannot be self-granted | `events/models.py:370-401`, `events/views/admin.py:529-530, 658-661` |
| None | `EventParticipant` | camperos have no login; identified by `#number` and a UUID in URLs | `events/models.py:524-526` |

Who reads answers (the asset the invariants protect): `general` → anyone who sees the campero; `sensible` → the flag, the assigned coordinator, or an admin who conducted that interview; `confidencial` → by the stricter of the interview's and the event's level (`cuidado` → flag only, `coordinacion` → as `sensible`, `todo_el_equipo` → anyone who sees the campero); an interview keeps the level it started with, so widening reaches only later interviews. Reports never widen access (scope evaluated "as of today"). Every confidential view is audited. [Verified `events/access.py:175-209`, `ConfidentialLevelTests`]

The full role × action × resource × scope matrix, with the code that enforces each cell and the cells with no evidence (sede scope, group leader, campero account, dated privileges), is in `workstreams/D-security.md` §1.

**Conflict with the written invariants.** `AGENTS.md` invariant 2 says church and event roles "are not interchangeable and neither implies the other"; the code, intentionally and with tests, makes a church admin an event admin and a church pastor an event pastor. Invariant 1 ("a person's record never depends on an event") describes the goal, not camperos today, whose rows are purged with the event. Both texts need rewording (roadmap P0-3). [Verified `AGENTS.md`, `events/access.py:255-269`, `purge_expired_event_data.py:44-46`]

## 6. Domain and data model

- **Entities.** 37 models (plus `auth.User`). `core`: `Church`, `UserChurchRole`, `Invitation`, `ParticipantProfile`, `DiscipleshipPath`, `Lesson`, `ReflectionQuestion`, `Enrollment`, `LessonProgress`, `ReflectionAnswer`, `ParticipantQuestion`, `QuestionResponse`, `FollowUpRequest`, `PastoralNote`, `AIDraft`. `events`: `Sede`, `Event` (+ `sedes` M2M), `EventMembership`, `TutorAssignment`, `AccessRequest`, `InterviewStation`, `EventParticipant`, `ConsentRecord`, `Questionnaire`, `QuestionnaireVersion`, `EventQuestionnaire`, `Section`, `Question`, `QuestionOption`, `Interview`, `Answer`, `AnswerRevision`, `ParticipantSession`, `FollowUp`, `ReportSnapshot`, `AuditEvent`, `AuthThrottle`. The ER diagram in `docs/architecture.md` has no wrong edge; it omits `AccessRequest`, the denormalised `Interview.event`/`FollowUp.event` edges, the user FKs and the whole `core` app. [Verified C §1–§2]
- **Identity.** `username == e-mail`, e-mail not unique at DB level, `Invitation.token` stored in clear while camp tokens are SHA-256 (C-01). Staff identity across events = `user_id`; campero identity = per event only; `normalized_name` only warns within one event ("nothing is ever merged"). [Verified C §3]
- **Scoping.** Church FK: all `core` content tables, `Sede`, `Event`, `Questionnaire`. Event FK only: `EventMembership`, `TutorAssignment`, `AccessRequest`, `InterviewStation`, `EventParticipant`, `EventQuestionnaire`, `Interview`, `FollowUp`, `ReportSnapshot`, `AuditEvent` (nullable event, no church). Neither: `ConsentRecord`, version structure, `Answer`, `AnswerRevision`, `ParticipantSession`. The schema cannot refuse a cross-church `Interview.version`, `EventParticipant.sede` or `coordinator` written outside the services; four denormalised FK pairs have no agreement constraint (C-05, C-06). [Verified C §4]
- **State.** `Event.status` is a free form choice with no transition guard; `Interview` transitions only in services under row locks; `ReportSnapshot` immutable once `listo` at the model layer (queryset bypass possible); `QuestionnaireVersion.RETIRED` is dead code. [Verified C §1.3]
- **Minors and consent.** `is_minor = age < event.adult_age`; guardian authorization required to start when the event asks; assent/consent recorded as append-only `ConsentRecord` rows; no guardian identity, no notice version/hash/purpose; the default notice text was rewritten by two migrations (C-08). Manual erasure cascades into open safety reports, unlike the automatic purge (C-13). Retention per event from `ends_on`; an event without an end date never purges (C-12). [Verified C §5]
- **Versioning.** Published versions immutable; interviews pin version and level; `AnswerRevision` on corrections; snapshots store raw sub-threshold counts, suppression is a render rule (C-11); audit metadata never holds answers, e-mails or tokens (all 44 call sites read). [Verified C §6]
- **Migrations.** 22, additive; 6 forward-only data steps; one state-only `RemoveField` whose column (`events_event.questionnaire_version_id`) still exists on PostgreSQL and no longer on SQLite (C-15, dev/prod divergence). [Verified C §7, A §2]

## 7. UX observations

Strengths, test-pinned: skip link, single `h1` per page, 3 px focus ring, reduced-motion switch, modal menu with `inert`, split live regions, 44–60 px targets, contrast checks for church colours, tú-voice Spanish with a locale file that overrides Django's "usted" strings and a test that fails on drift, no English in authored strings. [Verified B §4–§5]

Findings (full register in `workstreams/B-product-ux.md` §8):

| ID | Severity | Observation |
|---|---|---|
| B-02 | Medium | two follow-up/question systems and two "Seguimiento(s)" navs: the exact seam J2 crosses |
| B-03 | Medium | "Campero" vs "Participante" vs an event kind called "Programa de discipulado" |
| B-04 | Medium | every church admin lands in the camp; two homes both called "Inicio" |
| G-05 | Medium | questionnaire editor needs typed option codes; default first question locked in published versions |
| G-06 | Medium | the per-person export for a minor's record is raw JSON |
| G-10 | Medium (hypothesis) | phone patterns to measure: scrolling tables, select-in-row, all links inside "Menú", event/role hidden ≤ 600 px |
| B-01, B-05, G-01..G-04, G-08, G-09, 8 a11y leftovers | Low | mixed URL language, "administrador/administración", 403 loses the event bar, settings density, native controls' language, `aria-invalid`, `th scope`, PDF `lang` |

## 8. Security findings

| ID | Severity | Finding | Evidence | Recommendation (incremental) |
|---|---|---|---|---|
| F1 | **High** | Church admins/pastors are `is_staff`; `/admin/` uses Django's unthrottled login; no tenant scoping in any ModelAdmin; `can_view_sensitive` edits there unaudited; `AuditEventAdmin` allows delete | `core/services.py:102`, `core/views.py:286`, `emmaus/urls.py:11`, `events/admin.py:33-36,68-77`, `core/admin.py:41-84` | stop setting `is_staff` for church roles and clear it by data migration; throttle or proxy-block `/admin/`; superuser-only admin with `audit.record` on the two sensitive save paths; tests `test_church_roles_are_not_django_staff`, `test_admin_login_is_throttled` |
| F2 | Medium | a pastor, even church-wide with no membership, can set the level to `todo_el_equipo` and read later confidential answers without the flag; the assisted-interview notice is free text that does not follow the level | `events/views/admin.py:1008-1035`, `access.py:199-201`, `interview/start.html:36` | require ADMIN (or a second person) for the level; derive the start-screen sentence from the level |
| F7 | Medium (insider) | an event admin can create a flagged second account and set its password from the on-screen activation link, then export every answer under that identity | `events/views/admin.py:476-512, 526-542` | a password check alone is insufficient (create unflagged, activate from the link, then grant); require a second administrator's confirmation for every grant, with a regression test of that sequence |
| F3 | Medium (High with two tenants) | event creation assigns the alphabetically first administered church | `events/views/home.py:44-47` | explicit church choice when more than one |
| F4 | Medium (vision blocker) | core binds a person to the first church role by id; two-church users cannot reach their second church | `core/access.py:20-23` | session-held `current_church` with a switcher |
| F5, F6 | Low–Medium | public join endpoints throttled per cookie created on demand; no IP rate limit anywhere | `events/views/join.py:172-176`, `core/views.py:949-951` | IP-keyed limit at the edge; no session on misses |
| F8–F13 | Low | account/other-church oracle; archived events reachable by URL; TLS values depend on `.env`; participant questions sent to the LLM when enabled; safety reports assignable to non-readers | D §8 | see D §8 |

Controls verified sound: object scoping of every lookup (§3 of D), field-level answer rules and conditional-row hiding, confidential referrals, follow-up detail gating, report scoping, presentation whitelist and k-suppression, kiosk token/claim model and confinement, DB-backed fail-closed throttles for login/reset/unlock, CSV formula neutralisation, byte-level logo validation, open-redirect guards, audit-log contents and masking, per-campero deletion and retention purge, demo-account deploy check, no raw SQL, no `|safe`. [Verified D §3, §6, §7]

Missing proofs (tests that do not exist): church A admin against church B objects by id/slug (the one test that would prove invariant 3 as written), admin-login throttle, puppet-account grant, pastor level change, event-creation church choice, two-church user, archived-by-URL, cookie-less join throttle, admin-site audit, safety-assignment-to-reader, scheduler session cleanup. Exact names and assertions: D §9.2.

## 9. Tooling, tests, CI, deployment and backups

### 9.1 Tests and gates
- Collected 1,420; one-church fixture (`events/tests/base.py:34`), so most views are never exercised against a second tenant (T-1). No coverage measurement, no typing; ruff on `E4,E7,E9,F` with 7 per-file baseline ignores. `PurgeDemoDataTests` deselected on PostgreSQL (31-minute seed, issue #14). No migration round-trip test. [Verified A §3.3]
- CI and the local gate run the same steps; CodeQL is red only until Code scanning is re-enabled in the repository settings (owner step, issue #13). [Verified A §4]

### 9.2 Deployment reality vs documentation (D-1, Medium)
The host compose runs `migrate --noinput` on every `web` start while `docs/deployment.md:72` says migrations are manual; an image rollback never reverses the schema. Fine while every migration is additive, wrong for an 0011-type change. [Verified docker-sandbox `compose/emmaus/compose.yml:12-14`]

### 9.3 Migration governance (M-1, Medium)
`main` (camp fixes) and `v2/foundation` will both add `core`/`events` migrations; the next `main → v2` merge produces two leaves per app and a data step may run out of order. Rule proposed: until the camp ends, v2 adds migrations only in new apps; the 0011 contract is written on `main`. [Verified A §2]

### 9.4 Backups (D-2, High → mitigated)
- Before this audit: no cron, no timer, nothing registered in the fleet backup state; only hand-made pre-deploy dumps (`/root/backups/*.dump`, newest 2026-10-28 09:17); database 14 MB; media volume `emmaus-demo_media` (church logos). [Verified over SSH on docker-sandbox, 2026-10-28]
- During this audit: a systemd timer `emmaus-camp-backup.timer` (hourly, `Persistent=true`) now runs the repository's own `scripts/backup_db.sh` (`pg_dump -Fc` inside the db container, verified with `pg_restore`, 48 dumps kept) into `/var/backups/emmaus/`, plus one media tar per day (14 kept). Rollback: `systemctl disable --now emmaus-camp-backup.timer`. Still open: off-site copy (restic to the NAS/R2 per the fleet standard), inventory registration, and one restore drill into the `emmaus-v2` stack's throwaway database.

### 9.5 Dependencies
`pytest 8.4.2` carries PYSEC-2026-1845 and ships in the production image because there is one requirements file (R-1; issue #11); transitive dependencies are unpinned (R-2); `postgres:16` floats in the repo compose and is digest-pinned on the host. [Verified A §6]

## 10. Consolidated findings register (Critical/High/Medium)

| ID | Severity | Area | Finding | Where fixed (roadmap) |
|---|---|---|---|---|
| D-2 | High | ops | no scheduled backup of the camp DB/media (timer installed 2026-10-28; off-site + drill open) | P0-1 |
| F1 | High | security | Django admin: `is_staff` church roles, unthrottled login, no tenant scoping, unaudited sensitive edits | P0-5 |
| C-07 | High (direction) | domain | no `Person`; camperos are per-event rows; no guardian identity | P1-2..P1-4 |
| F2 | Medium | security | pastor can widen the confidential level alone | P0-6 (decision Q-S1) |
| F7 | Medium | security | flagged puppet account via on-screen activation link (a password check alone does not close it) | P0-7 (decision Q-S2) |
| F3 / C-02 / C-04 / F4 / A-1 | Medium | tenancy | first-church resolver, `churches[0]` on create, first-active-church landing, `/setup/` once | P1-6, P1-11 |
| C-05 / C-06 | Medium | domain | implicit church isolation; denormalised FK pairs unconstrained | P1-9, P1-10 (integrity check) |
| C-08 | Medium | privacy | consent rows lack notice hash/version/purpose; notice rewritten by migrations | P1-12 |
| C-11 | Medium | privacy | snapshots persist raw sub-threshold counts | P3-10 |
| C-13 | Medium | privacy | manual erasure deletes open safety reports | P0-8 (after camp) |
| C-15 / M-1 / D-1 | Medium | migrations | owed 0011 contract, dev/prod divergence, branch leaf collision, migrate-on-start vs docs | P0-4, P1-0 |
| B-02 / B-03 / B-04 | Medium | UX | two inboxes, two nouns, two homes | P2-4, glossary now |
| G-05 / G-06 | Medium | product | expert-only questionnaire editor; JSON-only per-person export | P3-11, P3-12 |
| T-1 | Medium | tests | single-church fixture; cross-church denial rarely exercised | P1-10 |
| R-1 | Medium | deps | pytest advisory in the production image | #11 |

## 11. What could not be verified

- The production database contents (real vs synthetic participants), `.env` values (HSTS, SSL redirect, SMTP, LLM), extra superusers or permissions granted at runtime, whether `/admin/` or gunicorn's port is reachable without the proxy, proxy-level rate limits.
- Runtime behaviour of `main` as deployed (only `v2/foundation` was read; application code is identical by diff).
- PostgreSQL-specific behaviours (case folding, `select_for_update`) beyond the existing PG suite; the browser gates were not re-run for this audit (CI ran them green on this commit).
- Assistive-technology and real-phone behaviour; JavaScript branches were read, not executed.
- Whether Django 5.2.18 officially supports the local Python 3.14 venv; host memory headroom with two stacks.
