# Emmaus audit — Workstream A (repository and runtime inventory) and F (architecture and release strategy)

- **Subject:** `<checkout>`, branch `v2/foundation`, commit `6f4664f` (merge of PR #12). Merge-base with `main` is `032d846` = `main` HEAD; the branch adds 42 files (+2,609/−2), all tooling and docs, no application code `[Verified: git diff --stat main...HEAD]`.
- **Method:** read-only. Files read, `git log`, `DEBUG=true manage.py check`, `pytest --collect-only`, `migrate` + `showmigrations` + `makemigrations --check` on an empty scratch SQLite DB, `scripts/agents/check-deploy.sh` (scratch DB), `ruff check`, `pip-audit` on the venv. No server started, no `.env`, dumps or `media/` read. Host state comes from the homelab-ops ledger and the `docker-sandbox` compose repo, not from SSH.
- **Tags:** `[Verified path:line]` = read it; `[Inferred: …]` = deduced; `[Unknown: …]` = could not check. Brief: `docs/EMMAUS_AUDIT_PROMPT.md`, workstreams A and F.

---

## 1. Repository map and boundaries

### 1.1 Layout

| Path | Role | Size / notes |
|---|---|---|
| `emmaus/` | Project: `settings.py` (198 lines), `urls.py`, `wsgi.py`, `asgi.py` | one settings module, env-driven `[Verified emmaus/settings.py:1-198]` |
| `core/` | App: church, roles, logo, discipleship module, auth views, error pages | `models.py` 508, `views.py` 1,021, `forms.py` 636, `services.py` 328, `access.py` 132 lines; 15 models; 10 migrations |
| `events/` | App: camp operations | `models.py` 1,194, `views/` 7 modules (`admin.py` 1,209, `dashboards.py` 700, `interview.py` 644), `services/` 14 modules (`interviews.py` 1,055, `metrics.py` 822, `pdf.py` 570), `access.py` 298; 22 models; 12 migrations; `fonts/` for the PDF |
| `templates/` | 138 HTML templates: `core/` 29+1, `events/` 11 + 9 sub-dirs (admin 20, interview 13, rotulo 9, kiosk 7, supervision 7 …), `errors/` 5, `layout/` 1, `partials/ui/` 7, `registration/` 5 | `[Verified: find templates]` |
| `static/` | `css/rotulo.css` (1,583 lines, the only stylesheet), `js/emmaus.js` (639 lines, the only script), one WOFF2 font, one SVG mark | no vendor JS, no CDN `[Verified static/ listing; docs/architecture.md:260]` |
| `locale/es_MX/` | `django.po/.mo` overriding Django's "usted" strings | `.mo` committed `[Verified; emmaus/settings.py:128-129]` |
| `e2e/` | Playwright + axe scripts (`station-flow.mjs`, `review-all.mjs`, `design-review.mjs`, `all_urls.py`), `package.json` | devDeps `playwright 1.63.0`, `axe-core 4.10.2` `[Verified e2e/package.json]` |
| `scripts/` | `backup_db.sh` (108 lines), `simulate_camp_day.py` (167 lines, load test), `agents/` (13 files: gate, runners, Codex review, worktree, audit log, status report, tooling tests) | `[Verified listing]` |
| `.github/` | `workflows/ci.yml`, `codeql.yml`, `codex-review.yml`; issue/PR templates; `labels.json`; `pip-audit-ignore.txt` | `[Verified]` |
| `docs/` | 20 Markdown docs incl. `architecture.md` (328 lines), `deployment.md` (342), `roles-and-permissions.md`, `privacy-and-safeguarding.md`, `decisions/0001-…`, `agents/*` (8), `EMMAUS_PRODUCT_VISION.md` (371), `EMMAUS_AUDIT_PROMPT.md`; ~160 screenshots under `docs/img/` | `[Verified listing]` |
| Root | `Dockerfile`, `docker-compose.yml`, `docker-compose.prod.yml`, `requirements.txt` (8 pins), `pytest.ini`, `ruff.toml`, `Makefile`, `.env.example`, `.dockerignore`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `SECURITY.md` | no `pyproject.toml`, `mypy.ini`, `setup.cfg`, `tox.ini`, `.pre-commit-config.yaml` `[Verified: ls]` |

Application boundaries, as actually wired:

- **Frontend** = server-rendered Django templates + one progressive-enhancement script; no SPA, no API, no WebSocket. Live regions poll with `fetch()` and the header `X-Requested-With: fetch` (`events/views/common.py:46-47` `is_fragment`) `[Verified]`.
- **Backend** = two apps. Every event screen goes through `event_view(*roles)` which loads the `Event` by slug, calls `resolve_access`, raises 403, activates the event time zone and stores the slug in the session (`events/views/common.py:23-44`) `[Verified]`. 72 view functions carry `@event_view` (admin 29, dashboards 12, interview 12, coordinator 10, reports 6, home 2, ayuda 1) `[Verified: grep]`.
- **Worker / jobs** = one process, `run_report_cutoffs --loop 300` (section 1.11). No queue, no Celery, no cache backend configured (`CACHES` absent → per-process LocMem; sessions are database-backed) `[Verified: emmaus/settings.py has no CACHES/SESSION_ENGINE]`.
- **Database** = PostgreSQL 16 via `psycopg` 3; SQLite when `DATABASE_URL` is unset (`emmaus/settings.py:86-114`) `[Verified]`.
- **Auth** = Django `auth.User` + e-mail login (`ThrottledLoginView`, `emmaus/urls.py:12`), DB-backed attempt limits (`AuthThrottle`), Django password reset `[Verified emmaus/urls.py:12-31]`.
- **Integrations** = SMTP (optional) and an OpenAI-compatible LLM endpoint (optional, discipleship only). Nothing else at runtime `[Verified emmaus/settings.py:180-191; docs/architecture.md:3]`.
- **Reports** = `ReportSnapshot` cutoffs, server-rendered SVG charts (`events/charts.py`, 278 lines), ReportLab PDF, presentation deck `[Verified listing; docs/architecture.md:13,257]`.

### 1.2 Settings: every environment variable read by `emmaus/settings.py`

| Variable | Default in code | Effect | Evidence |
|---|---|---|---|
| `DEBUG` | `False` | `env_bool`; also selects the dev SECRET_KEY fallback and the static storage | `settings.py:25`, `:29`, `:144-147` |
| `SECRET_KEY` | none; `"unsafe-dev-secret-key-change-me"` only when `DEBUG` or testing; otherwise `ImproperlyConfigured` | | `:27-32` |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | comma list | `:34` |
| `CSRF_TRUSTED_ORIGINS` | empty | comma list | `:35` |
| `DATABASE_URL` | unset → SQLite `BASE_DIR/db.sqlite3` | `postgres*://` or `sqlite://`; other schemes raise | `:86-114` |
| `DB_CONN_MAX_AGE` | `60` | `CONN_MAX_AGE`; `CONN_HEALTH_CHECKS=True` fixed | `:105-106` |
| `TEST_DATABASE_NAME` | Django default | test DB name (PostgreSQL only) | `:107` |
| `TIME_ZONE` | `America/Mexico_City` | server zone (`USE_TZ=True`) | `:130-132` |
| `MEDIA_ROOT` | `BASE_DIR/media` | uploads (church logo only); **no `MEDIA_URL`** | `:137-139` |
| `EMMAUS_IDLE_LOGOUT_SECONDS` | `1800` | staff idle logout | `:163` |
| `SECURE_HSTS_SECONDS` | `0` | HSTS header; no `INCLUDE_SUBDOMAINS`/`PRELOAD` setting exists | `:166` |
| `LOG_LEVEL` | `INFO` | root logger level | `:175` |
| `EMAIL_BACKEND` | console backend | | `:180` |
| `EMAIL_HOST`, `EMAIL_PORT` (587), `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` (`True`), `DEFAULT_FROM_EMAIL` (`Emmaus <no-reply@example.org>`) | | STARTTLS only; no `EMAIL_USE_SSL` | `:181-186` |
| `LLM_ENABLED` (`False`), `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | | section 1.12 | `:188-191` |
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | `not DEBUG` | | `:194-195` |
| `SECURE_SSL_REDIRECT` | `False` | `/health/` exempt | `:197-198` |

Read outside settings: `EMMAUS_DEMO_PASSWORD` (seed commands, `.env.example:55-56`), `POSTGRES_PASSWORD` (compose files), `EMMAUS_AGENT_HOME` and `PYTHON` (`scripts/agents/lib.sh`), `BACKUP_DIR`/`BACKUP_KEEP`/`EMMAUS_DB_CONTAINER`/`COMPOSE_PROJECT_NAME`/`COMPOSE_DB_SERVICE` (`scripts/backup_db.sh:7-13`), `E2E_OUT`/`E2E_PORT` (`e2e-gate.sh`), `CHECK_DEPLOY_USE_DB`/`CHECK_DEPLOY_SECRET_KEY` (`check-deploy.sh:5,16`) `[Verified]`. `.env.example` documents the production values and warns that comments after a value become part of the value `[Verified .env.example:1-22]`.

Settings fixed in code (not env): `SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO","https")` always on (`:193`); `X_FRAME_OPTIONS="DENY"` (`:164`); `SECURE_REFERRER_POLICY="same-origin"` (`:165`); `SESSION_EXPIRE_AT_BROWSER_CLOSE=True`, `SESSION_COOKIE_AGE=12h`, `SAMESITE=Lax` (`:160-162`); four password validators (`:119-124`); `PASSWORD_HASHERS=[MD5]` under tests (`:168-169`); `CSRF_FAILURE_VIEW=core.views.csrf_failure` (`:196`); `DEFAULT_AUTO_FIELD=BigAutoField` (`:152`); `LANGUAGE_CODE="es-mx"` single language (`:126-127`) `[Verified]`. Not set: `ADMINS`/`MANAGERS` (no error mail), `CACHES`, `DATA_UPLOAD_MAX_*`, any error-tracking SDK `[Verified: absent from settings.py and requirements.txt]`.

`check --deploy` with production-like values reports only `security.W005` and `security.W021` (the two HSTS warnings the docs accept) `[Verified: scripts/agents/check-deploy.sh run 2026-10-28]`; `manage.py check` is clean `[Verified]`.

### 1.3 Middleware chain

`SecurityMiddleware → WhiteNoiseMiddleware → events.PrivateResponseMiddleware → Session → Locale → Common → Csrf → Authentication → Messages → events.KioskConfinementMiddleware → events.StaffIdleLogoutMiddleware → XFrameOptions` `[Verified emmaus/settings.py:48-61]`.

| Custom middleware | Behaviour | Evidence |
|---|---|---|
| `PrivateResponseMiddleware` | `Cache-Control: no-store`, `Pragma: no-cache`, a fixed CSP (`script-src 'self'`, `style-src 'self' 'unsafe-inline'`, `img-src 'self' data:`, `frame-ancestors 'none'`, `form-action 'self'`) and a Permissions-Policy on every non-`/static/` response; the church logo keeps its public cache headers unless a cookie was set | `events/middleware.py:67-88` |
| `KioskConfinementMiddleware` | a session holding `KIOSK_KEY` may only reach `/estacion/`, `/static/`, `/health/`; the logo path passes before the session is read | `events/middleware.py:26-39` |
| `StaffIdleLogoutMiddleware` | logs out after `EMMAUS_IDLE_LOGOUT_SECONDS`; `last_seen` refreshed at most every 30 s and never by `X-Requested-With: fetch` polls | `events/middleware.py:42-64` |

### 1.4 URL mounts (134 routes)

| Mount | Routes | Contents | Evidence |
|---|---|---|---|
| `/admin/` | Django admin | 24 of the 37 models registered (all 15 `core`, 9 of 22 `events`); no `get_queryset` tenant scoping on any `ModelAdmin` | `emmaus/urls.py:11`; `core/admin.py:32-84`; `events/admin.py:26-77` |
| `/cuenta/…`, `/accounts/login/` | 7 | throttled login, password reset (throttled), logout, reset confirm/complete, legacy redirect | `emmaus/urls.py:12-32` |
| `/eventos/` | 77 (`events.urls`, namespace `events`) | per-event screens under `<slug>/`; roles: coordinator, interviews, tutor/pastor, reports, administración, sedes | `events/urls.py:10-94` |
| `/estacion/` | 7 (`kiosk`) | supervised capture: home, step, review, finish, unlock, heartbeat, timeout | `events/kiosk_urls.py` |
| `/unirme/`, `/e/<code>/` | 2 | public join by printed code (outside `event_view`) | `emmaus/urls.py:36-37` |
| `/` (`core.urls`) | 37 | landing, `health/`, `iglesia/<slug>/logotipo/<variant>/`, `setup/`, `dashboard/`, `ayuda/`, `invite/<token>/`, `participant/*` (6), `mentor/*` (4), `church/*` (20, discipleship management) | `core/urls.py:6-42` |
| Error handlers | 4 | `handler400/403/404/500 → core.views.*` | `emmaus/urls.py:41-44` |

Notable: event URLs carry **no church segment**; the church is reached through `Event.church` (`events/views/common.py:31`), and `Event.slug` is **globally unique** (`events/models.py:142`, `unique=True`) `[Verified]`.

### 1.5 Management commands (8)

| Command | Purpose | Guard | Evidence |
|---|---|---|---|
| `core/create_starter_path` | create/update the editable starter discipleship path | — | `:117` |
| `core/generate_followups_for_inactive_participants` | follow-up requests for participants idle ≥ 7 days | not scheduled anywhere `[Verified: not in compose/Makefile]` | `:8` |
| `core/seed_dev_data` | discipleship demo data | `DEBUG` only, demo password rules | `docs/deployment.md:46` |
| `events/seed_camp_demo` | synthetic camp (400 camperos, 19 staff, interviews, cutoffs) | refuses unless `DEBUG=true` | `:81`; `README.md:34` |
| `events/deactivate_demo_accounts` | deactivate `@example.org`/`@demo.invalid` accounts | — | `:7` |
| `events/purge_demo_data` | remove exactly the demo churches/users, dry-run default, `--yes` to delete, refuses when tangled with real data | works with `DEBUG=false` | `docs/deployment.md:138-151` |
| `events/purge_expired_event_data` | retention purge, dry-run default, `--apply` | event must be Terminado/Archivado with `ends_on` | `docs/deployment.md:229-235` |
| `events/run_report_cutoffs` | the scheduler (section 1.11) | `--loop N` | `events/management/commands/run_report_cutoffs.py:24-113` |

A system check `emmaus.W001` (deploy only) warns while demo accounts are active `[Verified events/checks.py:5-28]`.

### 1.6 Services layout

- `events/services/`: `interviews.py` (lifecycle, claims, kiosk, 1,055 lines), `questionnaire.py` (steps, conditions, versions, 493), `event_questionnaires.py` (which questionnaires an event uses; derived `interview_status`, 247), `metrics.py` (822), `reports.py` (cutoffs, 297), `presentation.py` (privacy serializer), `pdf.py` (570), `participants.py` (registration, CSV import, erasure, 398), `exports.py` (139), `team.py` (131), `join.py`, `throttle.py` (84), `audit.py` (93), `notifications.py` (e-mail without content) `[Verified listing + wc; docs/architecture.md:39]`.
- `core/services.py` (328): church creation (`create_initial_church`, first admin `is_staff=True` at `:102`), invitations, `send_email_safely` (`:112-118`), `progress_for_participant`, `generate_ai_draft` (`:279-330`) `[Verified]`.
- Authorization lives in `events/access.py` (`EventAccess` with role predicates and scoped querysets `participants()`, `interviews()`, `follow_ups()`, `snapshots()`, `can_view_answer`, `report_scopes`; `resolve_access`; `accessible_events`; `administered_churches`) and `core/access.py` (`get_user_role`, `get_role_context`, `visible_*`) `[Verified events/access.py:35-296; core/access.py:20-122]`.

### 1.7 Static and media

- Static: WhiteNoise `CompressedManifestStaticFilesStorage` when `DEBUG=false` and not testing; `collectstatic` runs at image build with a placeholder key (`Dockerfile:28`); no volume on `staticfiles/` by design `[Verified settings.py:141-150; docs/deployment.md:201-205]`.
- Media: only the church logo (`Church.logo`, `Church.logo_dark`, `core/models.py:105-108`); served exclusively by `core.views.church_logo` (`core/urls.py:8`), PNG only, re-validated on read (`core/logos.py`, 211 lines); `/app/media` is the only writable directory in the image (`Dockerfile:29-30`) `[Verified]`.

### 1.8 Logging and error pages

- One console handler; root at `LOG_LEVEL`; `django.request` at WARNING without propagation; comment states request bodies are never logged `[Verified settings.py:171-178]`. Six modules create loggers (`core/forms.py`, `core/logos.py`, `core/services.py`, `events/services/pdf.py`, `events/services/reports.py`, `run_report_cutoffs.py`) `[Verified: grep getLogger]`. No structured logging, no request id, no error tracker `[Verified: absence]`.
- Error pages: `errors/400|403|404|500|csrf.html`; `server_error` renders `errors/500.html` with **no request context** so a DB failure cannot fail again while drawing it (`core/views.py:262-264`; template comment `templates/errors/500.html:2-6`); `csrf_failure` → 403 page (`:267-268`) `[Verified]`.
- Health: `GET /health/` returns `"ok"` with no DB touch (`core/views.py:156-157`); exempt from the SSL redirect; the image healthcheck sends the first `ALLOWED_HOSTS` entry as `Host` and `X-Forwarded-Proto: https` (`Dockerfile:38-39`) `[Verified]`.

### 1.9 Feature flags (as they exist)

| Flag | Where | Gates | Notes | Evidence |
|---|---|---|---|---|
| `Church.discipleship_enabled` | BooleanField, default False; turned on by data migrations `core/0004`, `core/0010` | leaders' navigation (bar, `/dashboard/` redirect, "Discipulado" link) — **not** the discipleship views themselves | 15 files reference it; architecture doc states the views do not check it | `core/models.py:44`; `core/context_processors.py:46-50`; `core/views.py:241`; `docs/architecture.md:27` |
| `Church.custom_theme_enabled` + 8 colour fields | BooleanField | optional church theme (inline styles) | 9 files | `core/models.py:49-96` |
| `Church.self_registration_enabled`, `leaders_can_invite_participants` | BooleanField | discipleship onboarding | | `core/models.py:42-43` |
| `Event.join_by_name`, `Event.join_code` | `db_default` | public join | 10 files | `events/migrations/0006` |
| `Event.questionnaires_in_order` | `db_default=False` | multi-questionnaire order | 9 files | `events/migrations/0011:155-164` |
| `Event.confidential_level`, `Interview.confidential_level` | `db_default='cuidado'` | who reads confidential answers; stricter of the two applies | 24 files | `events/migrations/0012`; `docs/architecture.md:182` |
| `Event.requires_guardian_authorization`, `procedures_approved_at`, `report_interval_minutes`, `retention_days` | per-event settings | interviews blocked until procedures confirmed; cutoff cadence; retention | 11 / 19 / 10 files | `docs/architecture.md:85-97` |
| `LLM_ENABLED` | env | AI drafts (discipleship only) | 3 files | `settings.py:188`; `core/services.py:280` |

There is no flag framework (no `waffle`/`flags` dependency) `[Verified requirements.txt]`. The established pattern is a `BooleanField` with `db_default` plus checks in context processors/views.

### 1.10 Tenancy shape today (facts other workstreams build on)

- `Church` is the root: `Event.church` (`events/models.py:140`), `Sede.church` (`:80`), `Questionnaire.church` (`:644`), and every `core` aggregate (`UserChurchRole`, `Invitation`, `ParticipantProfile`, `DiscipleshipPath`, `ParticipantQuestion`, `FollowUpRequest`, `PastoralNote`, `AIDraft`) carries a `church` FK `[Verified core/models.py:128-495]`.
- `UserChurchRole` is unique per `(church, user)` (`core/models.py:136`), so a user **can** hold roles in several churches, but `get_user_role` returns `church_roles…order_by("id").first()` — the **first church wins** everywhere `core/access.py` is used (`core/access.py:20-23`) `[Verified]`.
- Event access resolution reads the church role by `event.church_id` and the `EventMembership`; superuser or church admin ⇒ event admin; church pastor ⇒ event pastor unless removed (`events/access.py:255-270`) `[Verified]`.
- Public pages brand **the first active church** (`core/context_processors.py:9-11`, `:16-18`); `/setup/` works only while no `Church` exists (`core/views.py:199-201`); there is no in-app way to create a second church other than the Django admin `[Inferred: no route in core/urls.py creates a Church; administered_churches at events/access.py:293 implies multi-church admins are anticipated]`.
- Camperos are `EventParticipant` rows (event-scoped, UUID `public_id`, purged by retention); discipleship participants are `auth.User` + `ParticipantProfile`. There is no `Person` entity independent of both `[Verified events/models.py:524-560; core/models.py:202-212]`.
- Church admins and pastors are created with `is_staff=True` (`core/services.py:102`; `core/views.py:286`), which opens `/admin/`; what they can see there depends on model permissions `[Unknown: permissions granted at runtime not inspected — workstream D]`.

### 1.11 The scheduler

`python manage.py run_report_cutoffs --loop 300` in the `scheduler` container (`docker-compose.prod.yml:25-38`; host: `compose/emmaus/compose.yml:45-60`). Each pass: release stale interview claims, expire idle kiosk sessions (and close any older than 3 h), `run_due_cutoffs`, `SessionStore.clear_expired()`, purge throttle counters and decided access requests, then the daily retention purge after 03:00 event-local time; exceptions are logged and the loop continues; `close_old_connections()` each pass `[Verified run_report_cutoffs.py:64-113]`. Its container healthcheck is disabled (`docker-compose.prod.yml:36-37`); the only liveness signal is the administración "Preparación → Cortes" row that turns to "Revisar" when a slot is >15 min late (`docs/deployment.md:225`) `[Verified]`. `--loop` keeps per-process state (`_retention_checked`), so a restart re-checks that day — idempotent by `data_purged_at` `[Verified :27-32]`.

### 1.12 SMTP and the optional LLM

- SMTP: `send_mail` through `core.services.send_email_safely` only (`core/services.py:112-118`); callers are invitations, password reset (Django), safety alerts. `events/services/notifications.py:1` states notices never include answers or names; `notify_safety_report` body confirms it (`:26-45`) `[Verified]`. Without SMTP everything goes to the container log (`docs/deployment.md:118`).
- LLM: `core.services.generate_ai_draft` posts `{system prompt, participant question text, sensitivity label}` to `LLM_BASE_URL + /chat/completions` with `urllib` and a bearer key, 30 s timeout, stores an `AIDraft` (`core/services.py:279-330`). Only the discipleship "participant question" flows reach it; the camp module has no call site `[Verified: grep LLM_ in events/ → none]`. Off by default.

---

## 2. Migrations

22 migrations (`core` 10, `events` 12), linear within each app, no merge migrations; `makemigrations --check --dry-run` reports no changes; all apply on an empty SQLite scratch DB `[Verified: showmigrations + makemigrations on `…/tmp/audit/a.sqlite3`]`.

| Migration | Operations | Reversibility / rolling-deploy note |
|---|---|---|
| `core/0001_initial` | 15 `CreateModel` | reversible (drops tables) |
| `core/0002` | 8 `AddField` (theme colours) | reversible |
| `core/0003` | 2 `AddField` (`custom_theme_enabled`, `discipleship_enabled`, no `db_default`) + 26 `AlterField` labels | older code cannot insert a `Church` once applied (`docs/deployment.md:333`) |
| `core/0004` | **`RunPython(forwards, noop)`**: flag on where paths exist; deactivates `participant@example.org` | forward-only; reverse does not reactivate the user `[Verified core/migrations/0004:11-23]` |
| `core/0005` | `AlterModelOptions` | no schema |
| `core/0006` | **`RunPython(forwards, noop)`**: clears the exact legacy English notice to `""` | forward-only, text not recoverable by reverse `[Verified 0006:13-21]` |
| `core/0007` | 2 `AddField` `FileField` with `db_default=""` | rolling-safe |
| `core/0008`, `0009` | `AlterField` labels only | no data change |
| `core/0010` | **`RunPython(turn_on_where_in_use, noop)`** | forward-only flag change `[Verified 0010:11-24]` |
| `events/0001_initial` | 18 `CreateModel`, indexes, constraints | reversible (drops every camp table) |
| `events/0002_security_review` | `CreateModel AuthThrottle`; `RemoveConstraint` + `AddField FollowUp.confidential` + `AddConstraint` | reverse fails once an interview has both an ordinary and a confidential referral (`docs/deployment.md:336`) |
| `events/0003`, `0005` | `AlterField` adding `db_default` | made for rolling deploys |
| `events/0004` | `AddField strikes` + labels | reversible |
| `events/0006` | 2 `AddField` with `db_default`, `AddConstraint`, **`RunPython(backfill_join_codes, noop)`** last ("schema first, data last" for PostgreSQL pending-trigger errors) | reverse of data step is a no-op `[Verified 0006:5-7, 50]` |
| `events/0007` | `CreateModel AccessRequest` | reversible |
| `events/0008_sede` | `CreateModel Sede`, M2M `Event.sedes`, nullable `EventParticipant.sede` (`PROTECT`) | reverse drops every campero's sede |
| `events/0009`, `0010` | labels + **`RunPython(replace_old_default_notice, noop)`** (frozen copies of the notice text) | forward-only |
| `events/0011_event_questionnaires` | `CreateModel EventQuestionnaire`; `AddField` (`db_default=False`); `AddField Interview.questionnaire` **nullable**; **`RunPython(link_existing_versions, restore_single_version)`**; `AlterField` → **NOT NULL**; `RemoveConstraint`/`AddConstraint` (unique index swap); **`SeparateDatabaseAndState(RemoveField Event.questionnaire_version)`** state-only; **`RunPython(noop, _refuse_reverse…)`** guard | expand/contract: the column `events_event.questionnaire_version_id` **still exists, empty, nullable** and must be dropped by a later migration (`0011:4-5`); reverse refuses with `RuntimeError` while any campero has live interviews of two questionnaires; the window between `migrate` and the new web is **not** compatible with old code (`docs/deployment.md:337-338`) `[Verified 0011:1-211]` |
| `events/0012` | 2 `AddField` with `db_default='cuidado'` | rolling-safe; reverse drops the chosen levels |

Summary: 8 `RunPython` operations in 7 migrations; 6 have a `noop` reverse (forward-only data), 1 has a real reverse (0011), 1 is a reverse-only guard (0011). One `RemoveField` (state-only). One nullability tightening (0011, `Interview.questionnaire` → NOT NULL). Two `RemoveConstraint`. No `DeleteModel`, no `RenameField`, no `RunSQL`, no column type changes `[Verified: grep over migrations]`. The 0011 contract step is still pending, and the memory of the deploy (homelab-ops ledger 2026-10-27) confirms 0011+0012 are applied on the production database `[Verified: ledger event da88b696]`.

**What this means for expand/contract and rollback**

- The team already practises expand/contract (`db_default` on every new NOT NULL column since `events/0003`; 0011 keeps the old column for one release) `[Verified]`. The discipline is documented per migration in `docs/deployment.md:331-342`.
- Rollback of code alone is safe for every migration except 0011 (and 0002's constraint). Rollback of schema is **not** a path the docs recommend: "To really go back, stop web and scheduler, restore the dump taken before the upgrade, and start the previous image" (`docs/deployment.md:342`) — i.e. rollback readiness equals backup readiness (section 5).
- Forward-only data migrations (`core/0004`, `0006`, `0010`, `events/0006`, `0009`, `0010`) are small and idempotent, but they run inside `migrate` on container start on the host (section 5.3), so there is no operator pause between schema and data steps.

**Finding M-1 — Severity: Medium.** Pending contract of `events/0011` plus parallel branch life. `main` (camp) will add the column-drop migration (`events/0013_…`) after the camp; `v2/foundation` is the base for new work and will also want `events/0013_…`/`core/0011_…`. Two leaf nodes per app appear on the next `main → v2` merge, requiring `makemigrations --merge`, and a data step authored on one side may run before/after a schema step from the other. Likelihood: high if v2 touches `core`/`events` models before the camp ends (that is the vision's Phase 1). Impact: medium (merge friction, risk of a wrong order on the v2 stack; the camp is unaffected because v2 never merges into main). **Recommendation:** until the camp ends, v2 adds migrations only in **new apps** (`people`, `tenancy`, `groups`, …) and references `core`/`events` models by FK; `core`/`events` migration numbers stay owned by `main`; the 0011 contract migration is written on `main` (dump first, off-hours) and merged down. Add a gate line in `check`: fail when a PR against `v2/foundation` adds a file under `core/migrations/` or `events/migrations/` (one `grep` in `quality-gate.sh`, label `area:infra`).

**Finding M-2 — Severity: Low.** `core/0006` and `events/0009-0010` overwrite church-written text only when it equals a frozen default, which is correct; `core/0004` deactivates a user by e-mail, which a reverse cannot undo. No action; note it in the migration policy ("data steps that touch `auth.User` need an explicit reverse").

---

## 3. Tests

### 3.1 Inventory (1,420 collected `[Verified: pytest --collect-only, 2026-10-28]`)

| Module | Tests | Covers (from the module docstring) |
|---|---|---|
| `events/tests/test_join_code.py` | 88 | joining with the printed code: public pages, requests, decisions, throttling |
| `core/test_church_logo.py` | 66 | PNG validator, serving, template tag, cache headers, PDF embed |
| `events/tests/test_sedes.py` | 64 | sedes per church, registration, CSV import matching, filters, PROTECT |
| `events/tests/test_audit_a11y_ui.py` | 52 | fixes from the 2026-10-26 UI/a11y audit |
| `events/tests/test_multi_questionnaires_progress.py` | 49 | "k of N questionnaires" on campero and team screens |
| `events/tests/test_ux_seguimiento.py` | 48 | follow-up assignment and return paths |
| `events/tests/test_ux_admin_flujos.py` | 46 | administración: registration, team, import, editor, lists |
| `core/test_ux_discipulado.py` | 45 | discipleship package of the role-first round |
| `events/tests/test_camp_admin.py` | 44 | administración walkthrough from an empty DB; retention overlap (PG only) |
| `events/tests/test_audit_lenguaje.py` | 44 | language and privacy findings (LANG-xx); safety-report delivery (`TransactionTestCase`) |
| `events/tests/test_security_review.py` | 43 | adversarial security review 2026-10-23, each test encodes the secure expectation |
| `events/tests/test_multi_questionnaires_admin.py` | 43 | Cuestionarios admin: new, order, remove, versions |
| `events/tests/test_reporting.py` | 39 | metrics, cutoffs, presentation privacy |
| `events/tests/test_rotulo.py` | 37 | design foundation: scoping, progress marks, chart projection, stylesheet balance |
| `events/tests/test_camp_interviews.py` | 36 | interview flow findings from the user guide |
| `events/tests/test_audit_flows.py` | 35 | UX audit flow fixes |
| `events/tests/test_multi_questionnaires.py` | 34 | `EventQuestionnaire` links, order, start/finalize/discard, derived status |
| `events/tests/test_ux_coordinator.py` | 33 | coordinación + campero package |
| `events/tests/test_audit_followup.py` | 33 | UX audit follow-ups |
| `events/tests/test_multi_questionnaires_flow.py` | 31 | several questionnaires on the screens, kiosk, paper sheet |
| `events/tests/test_interviews.py` | 30 | interview workflow on shared stations |
| `core/tests.py` | 28 | (no docstring) original discipleship MVP |
| `events/tests/test_ux_marco.py` | 27 | one bar, event-list name, Ayuda everywhere |
| `events/tests/test_progreso_agregados.py` | 27 | aggregate "k of N" on pastor summary and Avance |
| `events/tests/test_church_logo.py` | 26 | logo on camp screens, slides, PDF |
| `events/tests/test_access.py` | 26 | role scope, cross-event isolation, sensitive fields, reports |
| `events/tests/test_copy_pass.py` | 25 | behaviour changed by the copy pass |
| `events/tests/test_camp_flow.py` | 25 | headline baseline, change-since-cutoff sentence |
| `events/tests/test_ux_admin_inicio.py` | 24 | administración home and configuration |
| `events/tests/test_camp_visual.py` | 22 | visual fixes: answers list, 44 px targets, file control |
| `events/tests/test_ux_pastoral.py` | 21 | pastoral decision-first, deck timestamp |
| `events/tests/test_answers_export.py` | 21 | "Descargar las respuestas" CSV |
| `core/test_audit_a11y_ui.py` | 20 | church colours readable, hover changes |
| `core/test_camp_flow.py` | 19 | discipleship fixes: AI drafts reviewed once, notices |
| `events/tests/test_rotulo_leaders.py` | 16 | leader screens |
| `events/tests/test_questionnaire.py` | 16 | structure, conditions, optional sensitive answers, versioning |
| `events/tests/test_progreso_fase2_fix.py` | 16 | refuter-found defects in "Por cuestionario" |
| `events/tests/test_camp_ops.py` | 14 | `purge_demo_data` (deselected on PostgreSQL, issue #14) |
| `core/test_acompanamiento_rotulo.py` | 14 | discipleship daily screens |
| `events/tests/test_multi_questionnaires_metrics.py` | 13 | outcomes keyed per questionnaire, old cutoffs |
| `core/test_landing.py` | 13 | public front door |
| `events/tests/test_ui.py` | 11 | language, a11y basics, empty/loading states, live fragments |
| `core/test_camp_admin.py` | 10 | first account, forgotten password from empty DB (1 PG-only) |
| `events/tests/test_rol_visible.py` | 9 | role visible in the bar |
| `events/tests/test_admin_screens.py` | 8 | admin screens on Rótulo |
| `core/test_audit_lenguaje.py` | 8 | language findings in accounts/discipleship |
| `core/test_rotulo_gestion.py` | 7 | discipleship management screens (creates a second church) |
| `events/tests/test_participants_sort.py` | 5 | sortable camperos table |
| `core/test_locale.py` | 5 | `.po`/`.mo` in sync, tú-voice overrides |
| `events/tests/test_concurrency.py` | 4 | real threads against PostgreSQL row locks (PG only) |

Backend split: 1,414 pass + 6 skip on SQLite; 1,420 on PostgreSQL (`docs/agents/quality-gates.md:14-15`; issue #11 body) `[Verified docs; not re-run here]`.

### 3.2 Infrastructure

- `TransactionTestCase`: `test_concurrency.StationConcurrencyTests` (threaded with `threading.Barrier`, `@skipUnless(vendor == "postgresql")`, `:49-50`), `test_audit_lenguaje.SafetyReportDeliveryTests` (`:214`), `test_camp_admin.RetentionOverlapTests` (PG only, `:710-711`); `core/test_camp_admin.py:119` skips one name-matching test on SQLite `[Verified: grep]`.
- Builders: `events/tests/base.py` — `make_user`, `CampFixture.setUpTestData` builds **one church**, two events, admin/pastor/care/2 tutors/3 coordinators/outsider, five camperos (minors with and without guardian consent), ten stations, the default questionnaire; helpers `login`, `url`, `step`, `question` (`:25-120`) `[Verified]`. Password hashing is MD5 under tests (`settings.py:168-169`).
- e2e: `station-flow.mjs` (shared-computer interview regression, 113 lines), `review-all.mjs` (every screen per role at 1440/820/390 with axe, overflow, h1, stylesheet, JS-error checks), `design-review.mjs` (captures), `all_urls.py` (URL list from the seed; side effect: creates a draft version) `[Verified heads]`. `scripts/agents/e2e-gate.sh` seeds a scratch SQLite DB and serves it with `runserver` on a free port `[Verified e2e-gate.sh:1-6]`.
- Load: `scripts/simulate_camp_day.py` drives ten stations over HTTP with two pastor pollers and checks for duplicate interviews `[Verified :1-12]`.

### 3.3 Gaps visible from the inventory

| Gap | Evidence | Severity |
|---|---|---|
| No coverage measurement: `pytest-cov` not pinned; `.coverage`/`htmlcov` only in `.gitignore` | `requirements.txt`; `.gitignore:5-6` | Low |
| No type checking (no mypy/pyright config), lint limited to `E4,E7,E9,F` with 7 baseline ignores | `ruff.toml:8-19`; `ruff --statistics` without ignores = 7 `[Verified]` | Low |
| Cross-**church** denial is tested in a handful of places (`test_join_code.py:815`, `test_camp_admin.py:110`, `test_sedes.py`, `core/test_rotulo_gestion.py`) but the shared fixture has one church, so most views are never exercised against a second tenant | `events/tests/base.py:34` | Medium for the vision (J6), none for the camp |
| `PurgeDemoDataTests` deselected on PostgreSQL (seed ~31 min there) — the purge is CI-tested only on SQLite | `scripts/agents/postgres-deselect.txt:3`; issue #14 | Low |
| No migration round-trip test (apply → reverse → apply) for the migrations with real reverses (0011) | none found `[Verified: grep "migrate" in tests → none for 0011]` | Low |
| `core/tests.py` lacks a docstring; discipleship coverage is thinner (five modules) than the camp's (41) | counts above | Info |

---

## 4. Tooling now present on the branch (describe, not re-audit)

- **CI (`.github/workflows/ci.yml`)**: 7 jobs on PR/push to `main` and `v2/foundation` — `lint` (ruff 0.17.2), `checks` (`manage.py check`, `makemigrations --check`, `check-deploy.sh`), `agent-tooling` (`scripts/agents/tests/test-tooling.sh`), `security` (pip-audit 2.9.0 on the installed environment with `.github/pip-audit-ignore.txt`), `test-sqlite`, `test-postgres` (postgres:16 service, deselect list), `e2e` (Node 22, Playwright chromium, artifacts on failure). Python 3.12 everywhere; `permissions: contents: read`; concurrency cancels in-progress runs `[Verified ci.yml:1-155]`.
- **CodeQL (`codeql.yml`)**: python + javascript, on PR/push and Mondays 06:17 UTC `[Verified]`.
- **Codex review (`codex-review.yml`)**: advisory, same-repo PRs only, skips without `OPENAI_API_KEY`, posts a verdict comment; fails only when the tool produced no verdict; relaxes AppArmor user-namespace restriction on the runner for the read-only sandbox `[Verified codex-review.yml:1-89]`.
- **`scripts/agents/`**: `quality-gate.sh` (same steps as CI with timeouts and a summary block; `--postgres` spins a throwaway `postgres:16` container; `--e2e`), `run-tests.sh` (backend-true labels), `check-deploy.sh` (accepts only W005/W021), `pip-audit.sh`, `e2e-gate.sh`, `codex-review.sh` (exit 0/2/3/1), `new-task-worktree.sh` (one issue per worktree, `v2/issue-<n>-<slug>`), `audit-log.sh` (JSONL + issue comment), `sync-labels.sh`, `status-report.py`, `bootstrap.sh` (venv + ruff + pip-audit), `tests/test-tooling.sh`, `lib.sh` `[Verified heads]`.
- **`ruff.toml`**: `target-version py312`, `line-length 120`, rule set `E4,E7,E9,F`, six per-file ignores covering the 7 baseline findings, each marked as debt `[Verified]`. `ruff check .` passes `[Verified run]`.
- **Templates and labels**: agent-task issue form (objective, acceptance criteria, scope, dependencies, risk, invariants, verification, rollback), bug form, PR template (gate block, Codex block, Spanish copy/a11y, rollback), `labels.json` state machine `[Verified heads]`.
- **Governance state**: decision 0001 (status *proposed*) records branch model, queue, gates; branch protection is Phase C and **not yet in place** `[Verified docs/decisions/0001:117-118, 138]`. Open issues #11, #13, #14, #16, #17, #19; open PR #18 against `v2/foundation` is **not** a draft (AGENTS.md:43 says agent PRs are drafts) `[Verified gh]`.

---

## 5. Deployment and rollback readiness

### 5.1 Image (`Dockerfile`)

`python:3.12-slim`; `build-essential` + `libpq-dev` installed and **kept** in the single-stage runtime image (`:9-11`); unprivileged `emmaus` uid/gid 10001 (`:14-16`, `:32`); `pip install -r requirements.txt` without hashes (`:18-19`); `COPY . .` filtered by `.dockerignore` (`.env*`, `.git`, `.venv`, DBs, `media`, `staticfiles`, `e2e/node_modules`, `docs/img`); `collectstatic` at build with `SECRET_KEY=build-only-not-secret` (`:28`); `/app` read-only for the app user except `/app/media` (`:29-30`); `HEALTHCHECK` every 30 s against `/health/` (`:38-39`); `CMD gunicorn … --workers 3 --threads 4 --timeout 60` (`:41`) `[Verified]`. The test runner (`pytest`, `pytest-django`) ships in the production image because there is one requirements file `[Verified requirements.txt:9-10; Dockerfile:18-19]`.

### 5.2 Compose in the repo

- `docker-compose.yml` (dev): `web` as root with `runserver`, source bind-mounted, `db` postgres:16 `[Verified]`.
- `docker-compose.prod.yml`: `web` (gunicorn, `127.0.0.1:8000`, `media` volume), `scheduler` (healthcheck disabled), `db` (`postgres:16` floating tag, `postgres_data`); `DATABASE_URL` built from `POSTGRES_PASSWORD`; **no migrate on start** — `docs/deployment.md:72`: "The containers do not run migrations on start. Run `migrate` before `up -d` on every upgrade." `[Verified]`.

### 5.3 How production actually runs (docker-sandbox, from the compose repo and the ledger)

- Camp stack `emmaus-demo` (`/home/adrian/projects/docker-sandbox/compose/emmaus/compose.yml`): `web` **runs `python manage.py migrate --noinput && gunicorn …` on every start** (`:12-14`), image `emmaus-demo-web` built from `/opt/sandbox/apps/Emmaus`, `restart: unless-stopped`, Traefik labels on the `coolify` network (`entrypoints=http`; TLS terminates upstream), `SECURE_SSL_REDIRECT: "false"`, AutoKuma monitor label; `scheduler` depends on `web` healthy; `db` pinned by **digest** (`:63`) `[Verified]`. `.nodeploy` was added 2026-10-28 "after the timer recreated the db container for a Renovate digest bump during the camp" (`compose/emmaus/.nodeploy:4`; commit `0c6b9d4`) `[Verified]`.
- Deploy practice (ledger, deploys #7-#10): pre-deploy `docker tag` (`pre-camp10-050e71e`) + `pg_dump` (`emmaus-pre-camp10-20261028T151751Z.dump`), `up -d --no-deps web scheduler`, db untouched, smoke tests, edge 200 `[Verified: ledger events 1b14782a, 0e8d84aa, da88b696, 698e7b62]`. Live image at `main` `032d846` = `a68ab9b56d60` `[Verified ledger 2026-10-28T15:21Z]`.
- v2 stack `emmaus-v2` (`compose/emmaus-v2/compose.yml`): own checkout `/opt/sandbox/apps/Emmaus-v2`, own `.env`, Postgres volume, media volume and `backend` network; 2 gunicorn workers; same migrate-on-start command (`:17-19`); `.nodeploy`; LAN name `emmaus-v2.sandbox.thestarrynight.dev`; public name pending the owner's tunnel step; synthetic data only, `down -v` is the reset (homelab-ops ADR 0042; runbook `docs/runbooks/emmaus-v2-environment.md`) `[Verified]`.

**Finding D-1 — Severity: Medium.** The documented upgrade procedure (`migrate` by hand, then `up -d`) and the host's behaviour (migrate on every container start, including host reboots and `restart: unless-stopped` restarts) differ. Consequences: (a) a code rollback by retagging the previous image does **not** reverse the schema — fine for additive migrations, wrong for an 0011-type change; (b) the "run `migrate` and `up -d` back to back, outside interview hours" guidance for 0011-type changes is not under operator control; (c) a Renovate digest bump that recreates `web` migrates implicitly (now prevented by `.nodeploy`). Likelihood: low during the camp (additive-only rule, `.nodeploy`), medium for v2. **Recommendation:** keep migrate-on-start for `emmaus-v2` (convenient, disposable data) but for the camp stack document the actual behaviour in `docs/deployment.md` and keep the additive-only rule until the camp ends; for the post-camp 0011 contract, run `migrate` explicitly with `web` stopped. No change to the running stack now.

### 5.4 Backups

- Tooling: `scripts/backup_db.sh` dumps `pg_dump -Fc` from the running `db` container, verifies by `pg_restore -f /dev/null`, keeps `KEEP` dumps (default 48), `umask 077`, `flock`, cleans partials; hand-named dumps are never rotated `[Verified scripts/backup_db.sh:1-108]`. Media (logo) copy via `tar` from the `web` container `[Verified docs/deployment.md:251-254]`. Restore procedure documented (`docs/deployment.md:258-272`).
- Schedule: the docs give an hourly crontab line (`docs/deployment.md:249-252`). Whether it is installed on docker-sandbox: `[Unknown: host crontab not inspected]`.
- Fleet registry: `get_backup_state("emmaus")` → `has_backup: false`, `backups: []`, service not in the homelab-ops inventory (`inventory: null`) `[Verified: homelab-context 2026-10-28T12:46Z state]`. The only dumps with evidence are the ad-hoc pre-deploy dumps in the ledger (`emmaus-pre-camp7…10`, `emmaus-pre-demo2q…`) `[Verified ledger]`. Off-site copy: `[Unknown]`. Restore drill: `[Unknown: none in the ledger for emmaus]`.

**Finding D-2 — Severity: High.** No registered, scheduled, verified backup of the production camp database and media volume; rollback readiness (`docs/deployment.md:342`) depends entirely on a dump existing. Likelihood: medium — the db container was already recreated once by automation during the camp (`.nodeploy` note), and `postgres_data` is a single named volume on one host. Impact: high (interview answers of minors, consents, cutoffs). **Recommendation (incremental, read-only to the app):** (1) install the documented hourly cron on docker-sandbox into `/var/backups/emmaus`; (2) register `emmaus` (DB + `media` volume) in `inventory/services.yml` and the restic backup set so `get_backup_state` stops reporting none; (3) copy dumps off the host (NAS/R2 per the existing restic sync); (4) verify by the newest file's timestamp after the first hour, not by the cron line; (5) run one restore into the `emmaus-v2` stack's throwaway database as the drill (synthetic re-seed afterwards).

### 5.5 Health, monitoring, rollback summary

| Item | State | Evidence |
|---|---|---|
| App health | `/health/` 200 without DB; image healthcheck; Uptime Kuma HTTP monitor on the LAN name via AutoKuma label | `core/views.py:156`; `Dockerfile:38`; `compose/emmaus/compose.yml:32-33` |
| Scheduler health | healthcheck disabled; no external alert; manual "Revisar" row in administración | `docker-compose.prod.yml:36-37`; `docs/deployment.md:225` |
| Error tracking | none (console logs; no `ADMINS`) | `settings.py:171-178` |
| Rollback of code | retag previous image, `up -d --no-deps web scheduler` | `docs/deployment.md:325-329`; ledger |
| Rollback of schema | restore the pre-upgrade dump with web+scheduler stopped; never `migrate events zero` | `docs/deployment.md:258-266, 341-342` |
| Environment separation | two compose projects, two checkouts, two DBs, two media volumes, both `.nodeploy`; `main` never receives v2 | ADR 0042; `compose/*/.nodeploy`; `AGENTS.md:40-42` |

**Finding D-3 — Severity: Low.** Scheduler silent failure: a stopped `scheduler` stops cutoffs, session cleanup and retention; detection is a human reading the administración home. **Recommendation:** an Uptime Kuma push monitor pinged from `run_once` (one `urllib` call behind an env var, default off), or an AutoKuma label on a tiny read-only endpoint that reports the age of the newest automatic cutoff. Measure first how often cutoffs are late today (the `AuditEvent`/`ReportSnapshot` tables already hold the timestamps).

---

## 6. Dependency and maintenance risks

| Package / item | Pinned | Observation | Severity |
|---|---|---|---|
| `Django==5.2.18` | yes | 5.2 is an LTS line `[Inferred: Django release policy; support window not verified here]` | — |
| `pytest==8.4.2` | yes | `pip-audit` reports **PYSEC-2026-1845**, fix `9.0.3` `[Verified: pip-audit run 2026-10-28]`; ignored in CI via `.github/pip-audit-ignore.txt:3`; tracked as issue #11 (`agent:ready`, risk:low) `[Verified gh]`. Ships in the production image although it is test-only `[Verified Dockerfile:18-19]`. Exploitability in production: low `[Inferred: pytest is never invoked at runtime]` | Medium (hygiene) |
| `reportlab==4.5.1`, `pillow==13.0.0`, `whitenoise==6.12.0`, `psycopg[binary]==3.3.6`, `gunicorn==23.0.0`, `pytest-django==4.14.0` | yes | no advisories reported `[Verified pip-audit]`; comment at `requirements.txt:1-2` describes the upgrade routine | — |
| Transitive deps (`asgiref`, `sqlparse`, `psycopg-binary`) | **no** | resolved at build time; no `--require-hashes`, no lock file → image builds drift over time | Low |
| `postgres:16` | floating tag in `docker-compose.prod.yml:41`; digest-pinned on the host | Renovate bumps on the host recreated the db container once during the camp | Low (host fixed by `.nodeploy`) |
| Python | image 3.12 (`Dockerfile:1`); local venv **3.14.4** `[Verified python --version]`; CI 3.12 | `CLAUDE.md:13-15`: `manage.py test --parallel` fails on 3.14 + SQLite; worktrees share the main checkout's venv (`docs/agents/quality-gates.md:35-36`), so a local green run is on 3.14 while the image is 3.12. CI covers 3.12. Whether Django 5.2.18 officially supports 3.14 `[Unknown]` | Low |
| Node devDeps `playwright 1.63.0`, `axe-core 4.10.2` | yes (`package-lock.json`) | CI only | — |
| `build-essential` in the runtime image | — | ~800 MB image (ADR 0042), compilers in production | Low |
| Django admin at `/admin/` | — | all models registered without tenant scoping; church admins/pastors are `is_staff` | see §1.10; workstream D |
| Unmaintained libraries | — | none identified; LLM client is stdlib `urllib` | — |

**Finding R-1 — Severity: Medium.** Close issue #11 (pytest 9.0.3) and split `requirements.txt` into runtime and dev/test files so the image stops carrying the test runner (two files, `Dockerfile` installs one; gate installs both). **Finding R-2 — Severity: Low.** Add a constraints file produced by `pip freeze` (or `--require-hashes`) so image builds are reproducible; pin `postgres:16` by digest in `docker-compose.prod.yml` as the host does.

---

## 7. Architecture and release strategy for the vision (Workstream F)

### 7.1 Feasibility of an incremental, multi-tenant-ready evolution in this stack

**Feasible, and the cheapest path, provided three single-church assumptions are removed behind flags and the camp apps are left alone until the event ends.** Grounds:

1. The tenant root already exists: `Church` is the FK root of every aggregate in both apps (§1.10) `[Verified]`. The vision's `Tenant` is `Church`; nothing needs renaming.
2. Authorization is centralised in two modules with a `request.access` object and role-derived querysets (`events/access.py:131-160`; `docs/architecture.md:202-211`) `[Verified]`. New scopes (group, sede, church) can be added as new predicates without touching the camp's `resolve_access`.
3. Expand/contract and `db_default` are already the house style (§2), and the gate checks `makemigrations --check` on every PR `[Verified ci.yml:49-50]`.
4. Per-church feature flags exist as a pattern (`discipleship_enabled`, `custom_theme_enabled`) with data migrations that turn them on for churches in use `[Verified core/0010]`.
5. The runtime is a modular monolith already (two apps, services layer, one process type + one scheduler); there is no distributed-systems pressure in the evidence: no queue, no cache, polling dashboards, 12+1 DB connections (`docs/deployment.md:217`).

Blockers that are structural (none affects the camp, all are small):

| # | Blocker | Evidence | Removal cost |
|---|---|---|---|
| B1 | Role resolver returns the first church only | `core/access.py:20-23` | S: resolver reads an explicit "current church" (session) with a switcher; falls back to the single role; flag-gated |
| B2 | Public pages speak for the first active church; `/setup/` only when no church | `core/context_processors.py:9-18`; `core/views.py:199-201` | S: new public routes under `/i/<church-slug>/…`; keep `/` as is for the camp |
| B3 | `Event.slug` is globally unique; event URLs have no church segment | `events/models.py:142`; `events/urls.py:7` | none now: keep global slugs (they are short codes on printed signs) and treat them as tenant-unique by construction; revisit only for Phase 4 |
| B4 | No `Person` independent of `auth.User` and `EventParticipant` | `core/models.py:202-212`; `events/models.py:524` | M: new app (below) |
| B5 | Django admin: 24 models registered without tenant scoping, `is_staff` for church roles | `core/admin.py:32-84`; `events/admin.py:26-77`; `core/services.py:102` | S: restrict to superusers or scope `get_queryset` (D decides) |
| B6 | Navigation built per single role in a context processor | `core/context_processors.py:14-83` | M: replaced when `RoleAssignment` lands; keep the camp bars (`events/context_processors.py`) untouched |

### 7.2 Options per concern

| Concern | Adapt existing | Compatibility layer | Selective replacement | Recommendation and why |
|---|---|---|---|---|
| Tenant boundary | keep `Church` | — | new `Tenant` model | **Adapt.** Every FK already points at `Church`; a rename is churn with zero camp benefit. |
| People | add fields to `ParticipantProfile` | **new `people` app**: `Person(church, user=OneToOne null, …)`, `GuardianLink`, `IdentityLink`; nullable `EventParticipant.person` FK (`db_default NULL`) | replace `EventParticipant` | **Compatibility layer.** The camp keeps writing `EventParticipant`; a backfill links by explicit review, never by name (vision §5.2); retention purge still deletes `EventParticipant` rows and leaves `Person` only when linked by a church decision. |
| Roles | extend `UserChurchRole.role` choices | **new `access` app**: `RoleAssignment(subject, role, scope_type, scope_id, start_at, end_at, status)`; adapter in `core/access.py` that unions old and new behind a per-church flag | replace `UserChurchRole`/`EventMembership` | **Compatibility layer.** Invariants 1-2 (`AGENTS.md:52-56`) require both camp tables to stay; `resolve_access` for events is untouched until the camp ends. |
| Events / camp | — | — | — | **Retain as is** (camp-coupled, business-critical). Generalisation (event types) only after the camp, and only by adding choices. |
| Forms / questionnaires | — | treat `Questionnaire`/`QuestionnaireVersion` as the vision's `FormDefinition`/`FormVersion` through naming in docs, not code | replace | **Adapt by documentation.** Already tenant-scoped and immutable-versioned (`docs/architecture.md:181`). |
| Groups, ministries | — | **new `groups` app** with `Group`, `GroupMembership`, `JoinRequest`; nav behind `Church.groups_enabled` | — | New, additive, flag-gated. The J2 milestone needs only this plus `Person`. |
| Public / Explorar | — | new `public` app, church-slug routes | — | New; removes B2 without touching `/`. |
| Discipleship navigation | — | — | replace `core/context_processors.current_role` nav with `RoleAssignment`-driven nav once Phase 1 lands | Selective replacement, after the camp; it is the one module where the single-role model is baked into UI. |
| Django admin | restrict | — | — | Adapt now (cheap; D's call). |

Why not a rewrite or a service split: the suite (1,420 tests, axe at zero, station-flow) is the camp's safety net and it is bound to these models and templates; a parallel codebase would lose it.

### 7.3 Feature-flag strategy

1. **Per-church module flags as `BooleanField(db_default=False)` on `Church`** (`groups_enabled`, `public_pages_enabled`, `person_records_enabled`, `role_assignments_enabled`), following `core/0007`/`0012` (additive, rolling-safe). No flag library (zero new dependencies, `AGENTS.md:48`).
2. **Gate access, not only navigation.** The `discipleship_enabled` precedent gates the bar but not the views (`docs/architecture.md:27`). New modules must 404 in the view when the church flag is off, with a denied-path test per view (vision §15).
3. **Process-level kill switch via env** for infrastructure-shaped behaviour (`EMMAUS_V2_MODULES=false` read in `settings.py` like `LLM_ENABLED`), so the v2 stack can be run with the camp's exact behaviour for comparison.
4. **Screen-identity test**: for every flag, one test asserts the camp screens render byte-identical with the flag off (pattern already used for "N=1 events keep their screens unchanged", `AGENTS.md:63-66`), plus `review-all.mjs` run with all flags off as the gate's default.
5. Flags are removed (contract) one release after the module is on for every church; each removal is its own PR with the migration policy below.

### 7.4 Migration and release sequencing that keeps the camp stable

Rules (apply to every phase):

- `main` ← camp fixes only, by humans; `main` → `v2/foundation` merges by humans; v2 never into `main` (`docs/decisions/0001:48-51`). The cut-over is a stack swap (ADR 0042 "reconsider if"), not a merge.
- Until the camp ends, v2 adds **no migration under `core/migrations/` or `events/migrations/`** (Finding M-1); new data lives in new apps with FKs to `Church`, `auth.User`, `Event`, `EventParticipant`.
- Every new NOT NULL column carries `db_default`; every `RunPython` has a reverse or a 0011-style refusal guard; large backfills are **management commands** (dry-run default, `--apply`, idempotent, like `purge_demo_data`) rather than `RunPython`, so they can be rerun and timed on the v2 stack.
- Any PR touching models or migrations runs the gate with `--postgres` (SQLite hides row locks, `db_default` behaviour and constraint failures) `[Verified docs/architecture.md:215]`.
- Rollback per release = previous image + the dump taken before `migrate` (`docs/deployment.md:320-342`); additive steps also allow code-only rollback.

Sequence:

| Step | Scope | Gate | Rollback |
|---|---|---|---|
| 0. Hygiene (now, both branches) | backups registered and verified (D-2); #11; migration-path guard in the gate; admin restriction | gate green; `get_backup_state` shows a snapshot | revert PR |
| 1a. Expand (v2 only) | new apps `people`, `access`, `groups`, `public` with models, flags on `Church`, nullable `EventParticipant.person` **(this one FK is in `events` — defer it to after the camp or hold it in a one-to-one table inside `people`)** | PG suite; cross-tenant tests with a two-church fixture; screen-identity test | code rollback (tables unused) |
| 1b. Backfill (v2 stack, synthetic) | `link_people --apply` from `auth.User`+`UserChurchRole`+`ParticipantProfile`; explicit review list for `EventParticipant` ↔ `Person` | command idempotent on rerun; counts logged to `AuditEvent` without names | `--undo` writes nothing destructive (links only) |
| 2. Resolver switch | `core/access.py` adapter reads `RoleAssignment` ∪ legacy when `role_assignments_enabled`; church switcher | J3 test: one user, roles in two churches and one event, no leakage; `test_access.py` unchanged and green | flag off |
| 3. Community MVP | groups, join requests, Mi Iglesia home, public pages | e2e J2 flow on synthetic seed; axe zero | flags off |
| 4. Post-camp contract (on `main`, then merged down) | drop `events_event.questionnaire_version_id`; `EventParticipant.person`; event generalisation | explicit `migrate` with web stopped; dump first | restore dump |
| 5. Cut-over | `v2/foundation` becomes `main`; `emmaus-v2` stack becomes production or the camp stack is rebuilt from the new `main` with a restored dump | full gate + restore drill into v2 first | keep the old stack until the new one has run a full day |

### 7.5 Test strategy per phase

- **Phase 1:** extend `CampFixture` with `other_church` and a user holding roles in both; for every new view, a positive, a denied and a guessed-id cross-tenant test (vision §15); PostgreSQL-only constraint tests for new unique constraints; a migration round-trip script (`migrate <app> <prev>` then forward on a scratch PG) run by the gate for apps that gain a real reverse; the screen-identity test (§7.3.4).
- **Phase 2:** `e2e/` gains a J2 script (campero → event ends → group join → leader approves) on a `seed_community_demo` seed; `review-all.mjs` runs twice (flags off / on); `test_security_review.py` extended with the new routes.
- **Phase 3+:** keep `simulate_camp_day.py` as the load regression; add a second-tenant seed and run `review-all` per tenant; before Phase 4, a cross-tenant fuzz over every URL with ids from tenant B (the `all_urls.py` list already enumerates every screen).
- **Gate cost control:** fix #14 (shrink the `PurgeDemoDataTests` seed) before making `--postgres` mandatory on every PR; keep `e2e` at ~10 min or move the all-screens review to nightly (decision 0001 "reconsider if" at 20 min).

### 7.6 Cost drivers that need measurement (grounded only)

| Driver | Grounding | What to measure |
|---|---|---|
| docker-sandbox headroom | 4 cores / 8 GB host two stacks: 3×4 + 2×4 gunicorn threads, two Postgres, two ~800 MB images (ADR 0042) | RSS of both stacks during a camp day; whether `emmaus-v2` must be stopped during the event |
| Image build time | ~35 min cold cache on the sandbox (ADR 0042); single-stage with compilers | cold vs cached build; a multi-stage build's saving |
| DB connections | 12 (`web`) + 1 (`scheduler`) + 1 per command per stack (`docs/deployment.md:217`) | `max_connections` on the host Postgres; fine self-hosted, would not fit a ~20-connection managed tier for two stacks |
| CI minutes | `e2e` ~10 min (decision 0001), PG suite up to 25 min cap, `PurgeDemoDataTests` ~31 min on PG (#14) | minutes per PR; GitHub free tier for a public repo |
| Codex review | per-PR Codex CLI run with `OPENAI_API_KEY` (`codex-review.yml`) | tokens per PR |
| E-mail | console backend today; J1/J2 need invitations, join notifications, password resets | messages/day once groups exist; provider pricing |
| Media | logos only; vision "Contenido" adds media; no object storage, no `MEDIA_URL` | GB and bandwidth once content exists; CDN/object-storage need |
| Backups | hourly 48-dump rotation (docs) + media tar; off-site via existing restic/R2 | dump size × retention × stacks |
| Public hostnames | one-level names only, manual Cloudflare tunnel entries (ADR 0042 / runbook) | per-tenant hostnames in Phase 4 → tunnel vs wildcard decision |

---

## 8. Findings register (consolidated)

| ID | Severity | Finding | Evidence | Recommendation |
|---|---|---|---|---|
| D-2 | **High** | No registered/scheduled/verified backup for the production camp DB and media; only ad-hoc pre-deploy dumps | `get_backup_state` 2026-10-28; ledger; `docs/deployment.md:249-252` | install the documented cron, register in inventory/restic, off-site copy, verify by timestamp, restore drill into the v2 DB |
| M-1 | Medium | Pending 0011 contract + migration leaf collision between `main` and `v2` in `core`/`events` | `events/migrations/0011:4-5`; branch model `decisions/0001:48-51` | v2 migrations only in new apps during the camp; contract on `main`; gate guard |
| D-1 | Medium | Host runs `migrate` on every container start while docs say migrations are manual; schema does not roll back with the image | `compose/emmaus/compose.yml:12-14`; `docs/deployment.md:72` | document actual behaviour; additive-only until camp end; explicit migrate for 0011 contract |
| R-1 | Medium | pytest 8.4.2 advisory in the production image; single requirements file | `pip-audit`; `requirements.txt:9`; `Dockerfile:18` | issue #11; split runtime/dev requirements |
| T-1 | Medium (vision) | Single-church test fixture; cross-church denial covered in few tests; no two-tenant seed | `events/tests/base.py:34` | `other_church` in `CampFixture`; two-tenant seed for `review-all` |
| A-1 | Medium (vision) | First-church resolver, first-active-church branding, no in-app second church | `core/access.py:20-23`; `core/context_processors.py:9-18`; `core/views.py:199-201` | flag-gated resolver adapter; church-slug public routes |
| A-2 | Medium (hand to D) | Django admin registers 24 of 37 models with no tenant scoping; church admins/pastors are `is_staff` | `core/admin.py:32-84`; `events/admin.py:26-77`; `core/services.py:102` | restrict to superusers or scope querysets |
| D-3 | Low | Scheduler failure is silent outside the admin home | `docker-compose.prod.yml:36-37`; `docs/deployment.md:225` | push monitor or cutoff-age endpoint |
| R-2 | Low | Unpinned transitive deps, no hashes; floating `postgres:16` in repo compose; compilers in runtime image | `requirements.txt`; `docker-compose.prod.yml:41`; `Dockerfile:9-11` | constraints/hashes; digest pin; multi-stage later |
| T-2 | Low | No coverage, no typing, lint limited with 7 baseline ignores; `PurgeDemoDataTests` deselected on PG | `ruff.toml`; `postgres-deselect.txt:3` | coverage report (no threshold), fix #14, widen ruff in its own PR |
| T-3 | Low | Local venv 3.14 vs image 3.12; worktrees share the main checkout venv | `python --version`; `quality-gates.md:35-36` | `bootstrap.sh` with `python3.12` where available |
| F-1 | Info | `discipleship_enabled` gates navigation, not views | `docs/architecture.md:27` | pattern to avoid in new modules (§7.3.2) |
| G-1 | Info | PR #18 open as non-draft against `v2/foundation`; branch protection not yet enabled | `gh pr list`; `decisions/0001:117-118` | Phase C of decision 0001 |

---

## 9. What I could not verify

- Whether the production database on docker-sandbox holds real participants or only the synthetic seed: not inspected by rule. The ledger shows demo accounts active and a demo admin granted confidential access on 2026-10-28 (event `1b14782a`), and `AGENTS.md:6-7` states `main` runs a real camp `[Unknown: both may be true on the same stack]`.
- The camp stack's `.env` values (`ALLOWED_HOSTS`, HSTS, `EMAIL_BACKEND`, `LLM_ENABLED`): not read. `SECURE_SSL_REDIRECT` is forced `false` by the host compose `[Verified]`; whether HSTS is set `[Unknown]`.
- Whether the hourly backup cron exists on the host, whether any dump has left the host, and whether a restore has ever been exercised for Emmaus `[Unknown]`.
- Runtime Django admin permissions of church-role users (`is_staff` without model permissions sees nothing; superusers see everything) `[Unknown: not inspected]`.
- Live CI results on GitHub for commit `6f4664f` (I ran the local equivalents: `check`, `check --deploy`, `ruff`, `pip-audit`, collect-only; not the full suite, not `e2e`) `[Unknown: gh run list not queried]`.
- Django 5.2.18's official support for Python 3.14 (the local venv) `[Unknown]`.
- Memory/CPU headroom on docker-sandbox with both stacks up `[Unknown: no host metrics read]`.
- Whether `generate_followups_for_inactive_participants` is scheduled anywhere outside the repo `[Unknown: not in any compose file read]`.

## 10. Top 5 for the synthesis

1. **Backups are the rollback plan and there is no evidence of a scheduled one** — `get_backup_state("emmaus")` = none, only ad-hoc pre-deploy dumps in the ledger; `docs/deployment.md:342` makes restore the only true rollback. (D-2, High)
2. **Migration governance across the two branches**: the host migrates on container start (`compose/emmaus/compose.yml:12-14`) against docs that say manual; 0011's column drop is pending (`events/migrations/0011:4-5`); v2 must keep its migrations in new apps until the camp ends. (M-1, D-1)
3. **Multi-tenancy is structurally close**: `Church` is already the FK root and authorization is centralised (`events/access.py`, `core/access.py`), but three resolvers assume one church — `core/access.py:20-23`, `core/context_processors.py:9-18`, global `Event.slug` at `events/models.py:142`. Incremental removal behind per-church flags is the recommended path; no rewrite. (A-1)
4. **Django admin is mounted, unscoped, and reachable by `is_staff` church roles** (`core/admin.py:32-84`, `events/admin.py:26-77`, `core/services.py:102`) — cheap to close, needs D's permission check first. (A-2)
5. **Tooling is real and green but asymmetric**: CI 7 jobs + CodeQL + Codex, `ruff` clean, `check --deploy` only W005/W021, 1,420 tests; yet pytest's advisory ships in the prod image (#11), the purge test is PG-deselected (#14), the fixture has one church, and there is no coverage or typing. (R-1, T-1, T-2)
