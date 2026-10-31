# Emmaus audit — Workstreams B (product & information architecture) and G (UX & product validation)

**Repository:** `<checkout>`, branch `v2/foundation`, commit `6f4664f` (read-only).
**Date:** 2026-10-28. **Target direction:** `docs/EMMAUS_PRODUCT_VISION.md` (§3, §4, §7, §9), read as *target*, not current state.
**Prior UX evidence reused, not repeated:** `docs/auditoria-ux-2026-10.md` (code `4b6393b`, 136 deduplicated findings, IDs `UX-nn`, `UI-nn`, `A11Y-nn`, `LANG-nn`) and `docs/ux-review.md` (Playwright rounds 0–20, three widths, axe-core). Where this document says "prior audit", it means those two files.

## Method and evidence conventions

- Static reading of `emmaus/urls.py`, `core/urls.py`, `events/urls.py`, `events/kiosk_urls.py`, every view module, `core/access.py`, `events/access.py`, `events/middleware.py`, both context processors, all shells and 60+ templates, `static/css/rotulo.css`, `static/js/emmaus.js`, `locale/es_MX/LC_MESSAGES/django.po`, forms, model enums, and the docs above.
- One read-only Django introspection against an **empty scratch SQLite database** (`DATABASE_URL=sqlite:///<audit-tmp>/b.sqlite3`, migrated first): walking `get_resolver().url_patterns` produced 137 patterns (1 Django admin include + 136 application patterns). No server, no browser, no repo database, no `.env`, no `media/`.
- Tags: `[Verified path:line]` = read in the code at that location. `[Inferred: …]` = follows from code but not executed. `[Unknown: …]` = could not be established without running something.
- Findings are numbered `B-nn` (IA/product) and `G-nn` (UX/validation). Each carries `Severity` with rationale and an incremental `Recommendation`. Nothing here is a request to change code before the camp.

---

## 0. Summary of what exists (ten lines)

1. Emmaus is one Django 5.2 site with two products under one shell system: **Campamentos** (`events/`, the camp) and **Discipulado** (`core/`, church roles + lessons + questions), joined by a router view `core.views.dashboard` [Verified core/views.py:221-244].
2. 136 application URL patterns: 7 top-level (auth, join), 38 `core/`, 84 `events/`, 7 `estacion/` (kiosk). Eleven are public (no login): landing, health, church logo, setup (first run only), login/reset/set-password, `/unirme/`, `/e/<code>/`, and all seven kiosk routes (gated by a server-side `ParticipantSession`, not by login) [Verified emmaus/urls.py:12-37; core/urls.py:6-12; events/kiosk_urls.py:8-14; events/views/kiosk.py:1-6].
3. Authorization is centralized: every event view goes through `@event_view(*roles)` → `resolve_access()` → an `EventAccess` object whose querysets scope everything [Verified events/views/common.py:24-44; events/access.py:255-269]; every discipleship view through `@role_required(*roles)` reading **the first** `UserChurchRole` of the user [Verified core/views.py:139-153; core/access.py:20-23].
4. Five camp roles (Administrador, Pastor, Tutor, Coordinador, Campero-without-account) and four church roles (Administración de la iglesia, Pastor, Mentor, Participante) [Verified events/models.py:363-367; core/models.py:20-24].
5. Six shells: `layout/skeleton.html` → `base.html` (church/discipleship/auth/errors/event list), `events/rotulo/shell_leader.html` (pastor, admin, tutor), `events/rotulo/shell_coordinator.html`, `events/shell_focus.html` (interview), `events/shell_kiosk.html` (campero), `events/presentation/deck.html` (projector). The event shell is chosen per request by role [Verified events/context_processors.py:23].
6. Navigation is role-first and short: 4 links for a coordinator, 5 for pastor and tutor, 6 (+Discipulado) for an administrator, 2–7 for church roles; one "Menú" dialog under 760 px [Verified events/views/common.py:61-98; core/context_processors.py:23-73; static/css/rotulo.css:396-399].
7. "Realtime" is polling of HTML fragments every 20 s (coordinator, tutor), 30 s (pastor, admin, progress) or 60 s (deck), sent with `X-Requested-With: fetch` so it never counts as activity for the 30-minute idle logout [Verified static/js/emmaus.js:119-132; events/middleware.py:61].
8. Language: `es-mx`, tú voice, one locale file overriding 60 Django form/password strings; no English UI strings found in templates; the remaining English is browser-native controls (`type="date"`, `type="time"`, file picker) and the URL paths of the discipleship module (`/participant/`, `/mentor/`, `/church/`) [Verified emmaus/settings.py:126-131; locale/es_MX/LC_MESSAGES/django.po; events/forms.py:53-54,127-128; core/urls.py:13-42].
9. Accessibility as written is strong and tested: skip link, `lang="es-MX"`, one `h1` per page (74 includes of `_page_head.html` plus tests), 3 px focus ring, `prefers-reduced-motion` global off-switch with a test, `forced-colors`, modal menu with `inert`, split live regions, error-summary pattern, 44–60 px targets [Verified layout/skeleton.html:2,20; rotulo.css:131-147,935-943; events/tests/test_rotulo.py:221,461].
10. The product vision's three experiences map onto existing surfaces: **Explorar** ≈ landing + `/unirme/` + `/e/<code>/` (7 routes, camp-worded), **Mi Iglesia** ≈ `base.html` + `participant/*` (3–4 links), **Liderazgo** ≈ the event shells + `church/*`. The seams that hurt are the two "Inicio" homes for a church administrator, the Campero/Participante vocabulary split, and the `dashboard` router that sends anyone with an event straight into the camp.

---

## 1. Complete route inventory

Conventions: `E` = `eventos/<slug:slug>/`; `I` = `<uuid:public_id>/`. "Who may open" is the decorator or check **as written**; object-level scoping (what rows they see) is in `events/access.py` and is not restated per row. PUBLIC = reachable without login.

### 1.1 `emmaus/urls.py` [Verified emmaus/urls.py:10-44]

| Path | View | Template | Who may open (as coded) | Journey |
|---|---|---|---|---|
| `admin/` | `django.contrib.admin.site.urls` [L11] | Django admin | `is_staff` (Django) | Operator / developer |
| `cuenta/entrar/` | `core.views.ThrottledLoginView` with `EmailAuthenticationForm` [L12] | `registration/login.html` | **PUBLIC** (throttled) | Entry for every account |
| `cuenta/olvide-mi-contrasena/` | `core.views.ThrottledPasswordResetView` [L13] | `registration/password_reset_form.html` [Inferred: Django default name; file exists] | **PUBLIC** (throttled) | Account recovery |
| `cuenta/olvide-mi-contrasena/enviado/` | `PasswordResetDoneView` [L14-18] | `registration/password_reset_done.html` | **PUBLIC** | Account recovery |
| `cuenta/salir/` | `LogoutView` [L19] | — (POST, `partials/ui/logout.html` form) | authenticated | All |
| `cuenta/contrasena/<uidb64>/<token>/` | `PasswordResetConfirmView`, `TuSetPasswordForm` [L20-26] | `registration/password_set.html` | **PUBLIC** (token) | First password / recovery |
| `cuenta/contrasena/lista/` | `PasswordResetCompleteView` [L27-31] | `registration/password_set_done.html` | **PUBLIC** | Recovery |
| `accounts/login/` | `RedirectView` → `login` [L32] | — | **PUBLIC** | Compatibility |
| `eventos/` | include `events.urls` [L33] | | | Camp |
| `estacion/` | include `events.kiosk_urls` [L34] | | | Campero |
| `unirme/` | `events.views.join.join` [L36] | `events/join/join.html` | **PUBLIC** (10 searches / 10 min per browser session) [Verified events/views/join.py:25,53-71] | Volunteer onboarding |
| `e/<str:code>/` | `events.views.join.event_entry` [L37] | `events/join/entry.html`, `events/join/requested.html` | **PUBLIC** (5 requests / h; ≤50 pending per event) [Verified events/views/join.py:26-28,79-112] | Volunteer onboarding |
| `` | include `core.urls` [L38] | | | |
| 400/403/404/500 | `core.views.bad_request/permission_denied/page_not_found/server_error` [L41-44] | `errors/*.html` | — | All |

### 1.2 `core/urls.py` — discipleship module and church shell [Verified core/urls.py:6-42; decorators core/views.py as cited]

Role names: `CA` = `Role.CHURCH_ADMIN`, `P` = `Role.PASTOR`, `M` = `Role.MENTOR`, `PT` = `Role.PARTICIPANT`. `role_required` returns 403 for a wrong role and redirects to `landing` with "Tu cuenta todavía no está vinculada a ninguna iglesia…" when the user has no church role [Verified core/views.py:139-153].

| Path | View | Template | Who may open | Journey |
|---|---|---|---|---|
| `` | `landing` [views:190-196] | `core/landing.html` | **PUBLIC**; no church → `initial_setup`; authenticated → `dashboard` | Explorar (today: camp front door) |
| `health/` | `health` [156-157] | — ("ok") | **PUBLIC** | Ops |
| `iglesia/<slug>/logotipo/<variant>/` | `church_logo` [164-187] | — (PNG, `Cache-Control: public, max-age=86400`) | **PUBLIC** | Brand everywhere |
| `setup/` | `initial_setup` [199-217] | `core/initial_setup.html` | **PUBLIC only while no `Church` exists**; then redirect | First run → `events:list` ("Crear campamento") |
| `dashboard/` | `dashboard` [221-244] | — (router) | `login_required` | Entry after login (`LOGIN_REDIRECT_URL`) [Verified emmaus/settings.py:155] |
| `ayuda/` | `ayuda` [1019-1021] | `core/ayuda.html` | `login_required` (no church role needed) | Help outside an event |
| `invite/<token>/` | `accept_invitation` [272-308] | `core/accept_invitation.html`, `core/invitation_unavailable.html` | **PUBLIC** (token) | Discipleship onboarding |
| `participant/` | `participant_dashboard` [388-408] | `core/participant_dashboard.html` | PT | Participant: "Mi camino" |
| `participant/lesson/<id>/` | `participant_lesson` [412-464] | `core/participant_lesson.html` | PT | Participant |
| `participant/questions/` | `participant_questions` [468-470] | `core/participant_questions.html` | PT | Participant |
| `participant/questions/<id>/` | `question_detail` [612-719] | `core/question_detail.html` | CA, P, M, PT (+ per-object check) | Participant / mentor / pastor |
| `participant/questions/ask/` | `ask_question` [474-490] | `core/ask_question.html` | PT | Participant |
| `participant/follow-up/request/` | `request_follow_up` [494-509] | `core/request_follow_up.html` | PT | Participant: "Pedir una conversación" |
| `mentor/` | `mentor_dashboard` [513-545] | `core/mentor_dashboard.html` | M | Mentor home |
| `mentor/participants/<id>/` | `participant_detail` [549-598] | `core/participant_detail.html` | CA, P, M | Mentor / pastor |
| `mentor/questions/<id>/` | `question_detail` | `core/question_detail.html` | CA, P, M, PT | Mentor |
| `mentor/follow-ups/` | `follow_up_list` [816-837] | `core/follow_up_list.html` | CA, P, M | Mentor |
| `church/` | `church_dashboard` [723-762] | `core/church_dashboard.html` | CA, P | Church admin / pastor: "Panorama del ministerio" |
| `church/users/` | `user_list` [784-788] | `core/user_list.html` | CA, P | Church admin |
| `church/participants/` | `participants_list` [766-780] | `core/participants_list.html` | CA, P | Church admin / pastor |
| `church/participants/<id>/assign-mentor/` | `assign_mentor` [792-800] | `core/assign_mentor.html` | CA, P | Church admin / pastor |
| `church/questions/` | `questions_inbox` [804-806] | `core/questions_inbox.html` | CA, P | Pastor |
| `church/questions/<id>/` | `question_detail` | `core/question_detail.html` | CA, P, M, PT | Pastor |
| `church/escalations/` | `escalations` [810-812] | `core/escalations.html` | CA, P | Pastor: "Para el pastor" |
| `church/follow-ups/` | `follow_up_list` | `core/follow_up_list.html` | CA, P, M | Pastor / admin |
| `church/follow-ups/<id>/complete/` | `complete_follow_up` [841-852] | — | CA, P, M | Pastor / admin |
| `church/notes/` | `notes_list` [856-858] | `core/notes_list.html` | CA, P | Pastor |
| `church/invitations/` | `invitations_list` [342-357] | `core/invitations_list.html` | CA, P, M | Church admin |
| `church/invitations/new/` | `create_invitation` [312-335] | `core/invitation_form.html` | CA, P, M | Church admin |
| `church/settings/` | `church_settings` [361-370] | `core/church_settings.html` | CA only | Church admin: logo, colours, module switches |
| `church/paths/` · `new/` · `<id>/` · `<id>/edit/` | `path_list/path_create/path_detail/path_edit` [862-899] | `core/path_list.html`, `core/path_form.html`, `core/path_detail.html` | CA, P | Church admin: "Rutas" |
| `church/paths/<id>/lessons/new/` · `church/lessons/<id>/edit/` | `lesson_create/lesson_edit` [903-924] | `core/lesson_form.html` | CA, P | Church admin |
| `church/lessons/<id>/questions/new/` | `reflection_question_create` [928-938] | `core/reflection_question_form.html` | CA, P | Church admin |

### 1.3 `events/urls.py` — camp [Verified events/urls.py:10-94; decorators from the view modules as cited]

Role constants: `ADMIN`, `PASTOR`, `TUTOR`, `COORDINATOR`; `LEADERS = (ADMIN, PASTOR, TUTOR)`; `EVERYONE` = all four [Verified events/views/common.py:16-21]. `@event_view()` with no roles = any resolved role (object scoping still applies). A church `church_admin` or superuser is always `ADMIN` in every event of that church; a church `pastor` with no membership is `PASTOR` [Verified events/access.py:255-269].

| Path | View | Template | Who may open | Journey |
|---|---|---|---|---|
| `eventos/` | `home.events_home` [home.py:19-39] | `events/events_list.html` | `login_required`; auto-redirects to the last / only active / only event unless `?todos=1` | Entry: "Campamentos y eventos" |
| `eventos/cuenta/sigo-aqui/` | `session.still_here` [session.py:26-40] | — (204, `X-Idle-Remaining`) | `require_GET` + `login_required` | "Seguir aquí" before idle logout |
| `eventos/nuevo/` | `home.event_create` [home.py:42-68] | `events/event_form.html` | `login_required` + `administered_churches` else 403 | Admin: "Crear campamento" (creates 10 "Computadora N" + default questionnaire) |
| `E` | `home.event_home` [71-73] | — | EVERYONE → redirect to role home | All |
| `E ayuda/` | `ayuda.ayuda` [ayuda.py:8-11] | `events/ayuda.html` | EVERYONE | Help, five tasks per role |
| `E computadora/` | `home.choose_station` [76-88] | `events/choose_station.html` | EVERYONE + `can_conduct_interviews` (coord/admin) else 403 | Coordinator: "¿En qué computadora estás?" |
| `E mi-inicio/` | `coordinator.coord_home` [coordinator.py:52-62] | `events/coordinator/home.html` (+ `_home_live.html` fragment) | COORDINATOR | Coordinator home |
| `E mis-camperos/` | `coordinator.my_participants` [65-117] | `events/coordinator/participants.html` | COORDINATOR, ADMIN | Coordinator |
| `E camperos/nuevo/` | `coordinator.participant_create` [161-188] | `events/participant_form.html` | COORDINATOR, ADMIN + `can_register_participants` | Registration |
| `E pendientes/mias/` | `coordinator.coord_pending` [191-217] | `events/coordinator/pending.html` | COORDINATOR | Coordinator: "Entrevistas pendientes" |
| `E mi-avance/` | `coordinator.coord_progress` [220-227] | `events/coordinator/progress.html` (+ `_progress_live.html`) | COORDINATOR | Coordinator |
| `E lista-de-respaldo/` | `coordinator.backup_list` [120-158] | `events/backup_list.html` | any role (scoped) | Paper fallback |
| `E camperos/I` | `coordinator.participant_detail` [230-260] | `events/participant_detail.html` | any role; object via `access.participants()` | Campero record |
| `E camperos/I editar/` | `coordinator.participant_edit` [263-280] | `events/participant_form.html` | COORDINATOR, ADMIN + `can_edit_participant` | Correct data |
| `E camperos/I autorizacion/` · `…/quitar/` | `guardian_authorization` / `…_withdraw` [290-341] | `events/guardian_authorization.html`, `…_withdraw.html` | COORDINATOR, ADMIN (+ edit check) | Minors: consent record |
| `E camperos/I entrevista/` | `interview.interview_start` [interview.py:163-244] | `events/interview/start.html` | COORDINATOR, ADMIN + `can_interview` | Interview: "Antes de empezar" |
| `E camperos/<uuid>/riesgo/` | `interview.safety_report` [598-644] | `events/interview/safety.html` | any role (scoped) | Safety escalation |
| `E entrevistas/iniciar/` | `interview.interview_search` [124-160] | `events/interview/search.html` | COORDINATOR, ADMIN | Interview |
| `E entrevistas/I` | `interview.interview_detail` [510-544] | `events/interview/detail.html` | any role; answers per `can_view_answer` | Read answers |
| `E entrevistas/I continuar/` | `interview.interview_resume` [264-267] | — (claims, redirects) | COORDINATOR, ADMIN | "Retomar" |
| `E entrevistas/I otra-computadora/` | `interview.interview_conflict` [270-292] | `events/interview/conflict.html` | COORDINATOR, ADMIN + `can_interview` | Lock conflict / "Tomar el control aquí" |
| `E entrevistas/I paso/<n>/` | `interview.interview_step` [301-366] | `events/interview/step.html` | COORDINATOR, ADMIN | Assisted question |
| `E entrevistas/I revision/` | `interview.interview_review` [369-419] | `events/interview/review.html` | COORDINATOR, ADMIN | Review + follow-up decision |
| `E entrevistas/I completada/` | `interview.interview_done` [422-449] | `events/interview/done.html` | COORDINATOR, ADMIN | Done |
| `E entrevistas/I entregar/` | `interview.interview_handoff` [452-481] | `events/interview/handoff.html` → `kiosk:home` | COORDINATOR, ADMIN | "Entregar la computadora" |
| `E entrevistas/I descartar/` | `interview.interview_discard` [484-504] | `events/interview/discard.html` | COORDINATOR, ADMIN | Discard |
| `E entrevistas/I corregir/<n>/` | `interview.interview_correct` [547-595] | `events/interview/step.html` (mode `correction`) | COORDINATOR, ADMIN + `can_correct` | Correction with reason |
| `E mi-equipo/` | `dashboards.tutor_home` [dashboards.py:90-110] | `events/tutor/home.html` (+ `_home_live.html`) | TUTOR | Tutor home |
| `E coordinadores/` · `/<id>/` | `dashboards.coordinators` / `coordinator_detail` [113-138] | `events/supervision/coordinators.html`, `coordinator_detail.html` | LEADERS | Tutor / pastor / admin |
| `E entrevistas/` | `dashboards.interviews` [144-186] | `events/supervision/interviews.html` | LEADERS | Supervision |
| `E pendientes/` | `dashboards.pending` [189-229] | `events/supervision/pending.html` | TUTOR, PASTOR, ADMIN | Tutor: "Pendientes" |
| `E resumen/` | `dashboards.pastor_home` [263-294] | `events/pastor/home.html` (+ `_home_live.html`) | PASTOR, ADMIN | Pastor home: "Resumen general" |
| `E avance/` | `dashboards.progress` [297-303] | `events/pastor/progress.html` (+ `_progress_live.html`) | PASTOR, ADMIN | Pastor |
| `E seguimiento/` | `dashboards.follow_ups` [333-380] | `events/supervision/follow_ups.html` | LEADERS + COORDINATOR (rows scoped; row assign only if `can_manage_follow_up`) | Follow-ups |
| `E seguimiento/<id>/` | `dashboards.follow_up_detail` [383-407] | `events/supervision/follow_up_detail.html` | LEADERS + COORDINATOR; details only with `can_view_sensitive` | Follow-ups |
| `E seguimiento/<id>/asignar/` | `dashboards.follow_up_assign` [411-427] | — | `require_POST` + LEADERS + COORDINATOR + `can_manage_follow_up` | Row assignment |
| `E equipo/` | `dashboards.team` [430-454] | `events/supervision/team.html` | PASTOR, ADMIN (admin edits, pastor reads) | Team, join code, access requests |
| `E reportes/` | `reports.reports` [reports.py:41-67] | `events/reports/home.html` | LEADERS (scopes from `report_scopes()`) | Cutoffs |
| `E reportes/generar/` | `reports.report_generate` [71-85] | — | `require_POST` + LEADERS | "Generar corte ahora" |
| `E reportes/I` | `reports.report_detail` [97-112] | `events/reports/detail.html` | LEADERS + `can_view_snapshot` | Cutoff detail |
| `E reportes/I pdf/` | `reports.report_pdf` [115-129] | — (PDF) | LEADERS | "Descargar PDF" |
| `E reportes/I presentacion/` | `reports.report_presentation` [145-150] | `events/presentation/deck.html` | LEADERS | Deck of a saved cutoff |
| `E presentacion/` | `reports.presentation_live` [153-173] | `events/presentation/deck.html` (+ `_slides.html`) | LEADERS | Live deck |
| `E administracion/` | `dashboards.admin_home` [628-699] | `events/admin/home.html` (+ `_home_live.html`) | ADMIN | Admin home ("Inicio") |
| `E participantes/` | `admin.participants` [admin.py:161-208] | `events/admin/participants.html` | ADMIN, PASTOR, TUTOR | "Camperos" (whole event / team) |
| `E participantes/importar/` | `admin.participant_import` [211-246] | `events/admin/import.html` | ADMIN | CSV import |
| `E participantes/exportar/` | `admin.participant_export` [249-268] | — (CSV) | ADMIN | Export list |
| `E respuestas/exportar/` | `admin.answers_export` [277-302] | — (CSV, audited) | ADMIN + `can_view_sensitive` else 403 | Export answers |
| `E camperos/importar/ejemplo.csv` | `admin.sample_csv` [1126-1142] | — (CSV) | ADMIN | Import help |
| `E camperos/I datos/` | `admin.participant_data_export` [331-373] | — (**JSON**) [Verified admin.py:371] | ADMIN + `can_view_sensitive` | One person's data |
| `E camperos/I eliminar/` | `admin.participant_delete` [440-450] | `events/admin/participant_delete.html` | ADMIN | Erasure |
| `E equipo/agregar/` | `admin.member_add` [526-542] | `events/admin/member_form.html` → `member_created.html` | ADMIN | Team |
| `E equipo/<id>/` | `admin.member_edit` [645-682] | `events/admin/member_form.html` | ADMIN | Team |
| `E equipo/asignaciones/` | `admin.assignments` [685-718] | `events/admin/assignments.html` | ADMIN | "Asignar tutores" |
| `E solicitudes/<pk>/aprobar/` · `/rechazar/` | `admin.access_request_approve/reject` [550-612] | — | `require_POST` + ADMIN | Join requests |
| `E codigo/nuevo/` · `E codigo/nombre/` | `admin.join_code_regenerate` / `join_by_name_enable` [616-632] | — | `require_POST` + ADMIN | Join code |
| `E administracion/codigo/` | `admin.join_code_sign` [635-642] | `events/admin/join_code_sign.html` | ADMIN | Printable sheet |
| `E cuestionarios/` | `admin.questionnaires` [724-768] | `events/admin/questionnaires.html` | ADMIN | Questionnaires in use + versions |
| `E cuestionarios/nuevo/` | `admin.questionnaire_create` [811-830] | `events/admin/questionnaire_form.html` | ADMIN | New questionnaire |
| `E cuestionarios/<vid>/` | `admin.version_detail` [837-897] | `events/admin/version_detail.html` | ADMIN | Version editor (publish/use/stop/duplicate) |
| `E cuestionarios/<vid>/secciones/nueva/` | `admin.section_add` [911-922] | `events/admin/simple_form.html` | ADMIN (draft only) | Editor |
| `E cuestionarios/<vid>/secciones/<sid>/pregunta/` · `…/preguntas/<qid>/` | `admin.question_edit` [925-950] | `events/admin/question_form.html` | ADMIN (draft only) | Editor |
| `E cuestionarios/<vid>/preguntas/<qid>/opcion/` | `admin.option_add` [953-970] | `events/admin/simple_form.html` | ADMIN (draft only) | Editor |
| `E configuracion/` | `admin.settings_view` [976-1005] | `events/admin/settings.html` | ADMIN | Settings (6 sections) |
| `E configuracion/confidencial/` | `admin.confidential_level` [1008-1035] | `events/admin/confidential_level.html` | ADMIN, PASTOR | Who reads "Confidencial" |
| `E configuracion/computadoras/` | `admin.stations` [1039-1049] | — | `require_POST` + ADMIN | Stations toggle/add |
| `E configuracion/actividad/` | `admin.audit_log` [1052-1066] | `events/admin/audit.html` | ADMIN | Activity log |
| `E configuracion/sesiones/` | `admin.sessions` [1069-1096] | `events/admin/sessions.html` | ADMIN | "Computadoras en uso" (release / close screen) |
| `E administracion/sedes/` · `nueva/` · `<id>/` · `<id>/borrar/` | `admin.sedes` / `sede_edit` / `sede_delete` [1151-1209] | `events/admin/sedes.html`, `sede_form.html` | ADMIN; editing a sede additionally requires church admin or superuser (`_manages_sedes`) [Verified admin.py:1145-1148] | Sedes |

### 1.4 `events/kiosk_urls.py` — campero on a shared computer [Verified events/kiosk_urls.py:8-14; events/views/kiosk.py]

All seven are **PUBLIC** in the login sense: "The browser holds no login here. Every request is validated against the server-side `ParticipantSession`" [Verified kiosk.py:3-5]. `KioskConfinementMiddleware` keeps a browser that holds `KIOSK_KEY` inside `/estacion/` (plus `/static/`, `/health/`, the church logo) [Verified events/middleware.py:26-39].

| Path | View | Template | Gate | Journey |
|---|---|---|---|---|
| `estacion/` | `kiosk_home` [49-68] | `events/kiosk/welcome.html` / `handback.html` / `ended.html` | session state | Campero: "Hola, {first_name}." |
| `estacion/paso/<n>/` | `kiosk_step` [75-121] | `events/kiosk/step.html` | `State.ACTIVE` | One question per screen |
| `estacion/revisar/` | `kiosk_review` [124-144] | `events/kiosk/review.html` | `State.ACTIVE` | "Revisa tus respuestas" |
| `estacion/terminar/` | `kiosk_finish` [169-175] | — | `require_POST` | "Ya terminé" |
| `estacion/desbloquear/` | `kiosk_unlock` [178-204] | `events/kiosk/unlock.html` | coordinator password (5 attempts, 60 s cooldown) [Verified services/interviews.py:68-69] | "Recuperar la computadora" |
| `estacion/latido/` | `kiosk_heartbeat` [207-213] | — (204 / 410) | `require_POST` | JS keep-alive |
| `estacion/tiempo/` | `kiosk_timeout` [216-228] | — | — | Idle → `AWAITING_UNLOCK` after 60 s server-side idle |

### 1.5 Inventory observations

- **B-01 — Mixed URL language.** Discipleship paths are English (`/dashboard/`, `/setup/`, `/participant/`, `/mentor/`, `/church/…`, `/invite/`), camp paths are Spanish (`/eventos/`, `/cuenta/`, `/unirme/`, `/estacion/`, `/e/`) [Verified core/urls.py:9-42; emmaus/urls.py:12-37]. Users see the address bar; the printed join sheet relies on the Spanish one ("entra a /unirme/") [Verified emmaus/urls.py:35]. Severity: **Low** — cosmetic today, but the vision's Mi Iglesia would inherit `/participant/`. Recommendation: when Mi Iglesia pages are added, mount them under Spanish paths and keep the English ones as 301s; do not rename before the camp.
- **B-02 — Two parallel follow-up / question systems.** `events.FollowUp` (camp) and `core.FollowUpRequest` + `ParticipantQuestion` + `PastoralNote` (discipleship) have separate URLs, lists, states and navigation labels ("Seguimiento" in the event bar, "Seguimientos" in the church bar) [Verified events/urls.py:50-52; core/urls.py:22,30; common.py:81; context_processors.py:44,57,71]. State labels were unified by a decision ("Pendiente", "En proceso", "Atendido", "Cancelado") [Inferred: from docs/design-system.md §5, "LANG-20; en el discipulado, core/0009"]. Severity: **Medium** — this is exactly the seam J2 (camp → small group) must cross; a leader following one person after camp would use two inboxes. Recommendation: no merge before camp; in Phase 1 treat `FollowUp`/`FollowUpRequest` as one *concept* with two storages behind a single "Seguimiento" list in Liderazgo (read-only union first).
- **B-03 — "Participante" and "Campero" name different things.** A campero is an `EventParticipant` row without an account; a participante is a `User` with `Role.PARTICIPANT` and a `ParticipantProfile` [Verified docs/roles-and-permissions.md "Camperos are `EventParticipant` rows, not users"; core/models.py:24,202]. Yet the admin's "Camperos" link opens `…/participantes/` [Verified common.py:69; events/urls.py:63], and the church bar's "Participantes" opens `/church/participants/` [Verified context_processors.py:63]. `Event.Kind` also includes `"Programa de discipulado"` as an *event kind* while "discipulado" is the name of the other module [Verified events/models.py:120-125; core/models.py:44-48]. Severity: **Medium** — the vision's invariant "People are not roles" (§2.1) needs one word for the person; today the word changes with the module. Recommendation: pick the person noun now in the glossary (`docs/user-guide-es.md` §"Palabras que vas a ver" already defines **Campero**) and reserve "Participante" for the future `Person`; rename the event kind label to something that does not collide ("Curso" / "Programa") at the next content release.

---

## 2. Role journeys as implemented

### 2.1 Entry after login — the router [Verified core/views.py:221-244]

| Condition (evaluated in this order) | Destination |
|---|---|
| `accessible_events(user).exists()` **or** superuser **or** church role `church_admin` | `events:list` → auto-redirect to last/only active/only event [Verified home.py:24-33] → `events:home` → role home [common.py:51-58] |
| no church role, no events, no church in DB | `initial_setup` |
| no church role, no events | `events:list` → "Aún no formas parte de ningún evento" with a link to `/unirme/` [Verified events/events_list.html:35-40] |
| `participant` | `participant_dashboard` ("Mi camino") |
| `mentor` | `mentor_dashboard` |
| `pastor` of a church with `discipleship_enabled=False` | `events:list` |
| `pastor` / `church_admin` (only reachable by pastor, see first row) | `church_dashboard` ("Panorama del ministerio") |

- **B-04 — A church administrator always lands in the camp, never in the church.** The first rule fires for every `church_admin`, so the discipleship "Inicio" (`church_dashboard`) is reachable only through "Discipulado" in the event bar [Verified events/rotulo/shell_leader.html:8] or the church bar on the event list [Verified core/context_processors.py:60-73]. The two homes are both labelled "Inicio" [Verified common.py:68; context_processors.py:62]. Severity: **Medium** — one person, two "Inicio", and the one they land on depends on whether any event exists. Recommendation: before camp nothing; for Phase 2 the router becomes "Mi Iglesia first, Liderazgo by choice" behind a per-church flag (pattern already exists: `Church.discipleship_enabled`).

### 2.2 Shells and bars

| Shell | Used by | Bar content | Evidence |
|---|---|---|---|
| `layout/skeleton.html` | everything | `lang="es-MX"`, viewport, `noindex`, Figtree preload, `rotulo.css`, church theme tokens, `emmaus.js defer`, skip link "Saltar al contenido" | [Verified templates/layout/skeleton.html:1-23] |
| `base.html` | login, landing, errors, event list, join pages, discipleship | signed-in: `partials/ui/bar.html` with church brand, church name + church role, "Campamentos" button when `show_events_link`, nav `app_nav_items` labelled "Discipulado"/"Principal", "Ayuda", Menú, "Cerrar sesión"; signed-out: wordmark + "Entrar" | [Verified templates/base.html:14-35; partials/ui/bar.html:20-35] |
| `events/rotulo/shell_leader.html` | pastor, administrador, tutor (via `rotulo_shell`) | brand → event home, event name + inline "Campamentos" + role line, nav, "Discipulado" (admin, module on), "Ayuda"; stacked rows when > 5 links | [Verified shell_leader.html:7-11; events/context_processors.py:23] |
| `events/rotulo/shell_coordinator.html` | coordinador | same bar + "Estás en **Computadora N**" | [Verified shell_coordinator.html:9-12; bar.html:24] |
| `events/shell_focus.html` | assisted interview, "Antes de empezar", review, done, handoff, conflict, discard | no nav; `focusbar` with "**{full_name}** #{code}, {room}" and 1–2 actions ("Guardar y salir", "Volver", "Cancelar") | [Verified shell_focus.html:10-21; interview/step.html:4-11] |
| `events/shell_kiosk.html` | campero | church mark + station; footer "Tus respuestas se guardan cada vez que avanzas." / "Si necesitas ayuda, levanta la mano."; no idle-logout attributes | [Verified shell_kiosk.html:14-29] |
| `events/presentation/deck.html` | projector | no bar; controls "Salir · Anterior · 1 de N · Siguiente · Pantalla completa · Modo noche · En vivo" | [Verified presentation/deck.html:10-21] |

Responsive behaviour of the bar: one row above 1280 px; links on their own row 761–1280 px; ≤ 760 px links and logout move into the "Menú" dialog (`role="dialog"`, `aria-modal`, `inert` on the rest, Escape closes) [Verified rotulo.css:385-399; partials/ui/menu_panel.html:8-22; emmaus.js:21-95]. ≤ 600 px the event-name/role line is hidden; context survives only in the Menú "who" line ("{name}, {role}, en {station}, con acceso confidencial") [Verified rotulo.css:401-406; menu_panel.html:20].

Tests pin this: the role is said under the event in the bar and once on each home ("Tu rol: …") [Verified events/tests/test_rol_visible.py:49-135]; every screen of every role is on the Rótulo/Atrio shell with one heading [Verified events/tests/test_rotulo.py:159,221; test_rotulo_leaders.py:22].

### 2.3 Journeys

#### Campero (no account) — three modes

| Mode | Entry | Screens | Ends |
|---|---|---|---|
| **Asistida** (`Interview.Mode.ASSISTED`, "Entrevista asistida") | coordinator picks it on "Antes de empezar" [Verified interview/start.html:72-86] | the coordinator's `shell_focus` steps; the campero never touches the screen | coordinator's review "Revisa y finaliza" → "¿Necesita seguimiento?" → `done.html` "Entrevista completada" [Verified interview/review.html:34-59; done.html] |
| **A solas / supervisada** (`Mode.SUPERVISED`, "Entrevista a solas") | coordinator → "Entrega la computadora a {first_name}" (3 promises, warning "Tu sesión se cerrará en esta computadora") → POST logs the staff out and opens `/estacion/` [Verified interview/handoff.html:5-22; interview.py:452-481] | `welcome.html` "Hola, {first_name}." + intro + promises ("Son N preguntas, una por pantalla", "Puedes elegir “Prefiero no responder”", "Si quieres parar, toca “Parar por ahora”") + "¿Quién lee tus respuestas?" → `step.html` (trail "Pregunta X de Y", "Continuar"/"Revisar respuestas", header button "Parar por ahora" saves without validation) → `review.html` ("Seguir contestando" when something is missing, "Ya terminé") [Verified kiosk/welcome.html:5-19; kiosk/step.html:5-6; _step_form.html:40-46; kiosk/review.html:5-27; kiosk.py:92-109] | `handback.html` "¡Gracias!" / "La pantalla se cerró" + "Soy coordinador: recuperar la computadora" → `unlock.html` (password; "Soy otro coordinador" asks e-mail too) → coordinator's review [Verified kiosk/handback.html:4-13; kiosk/unlock.html; kiosk.py:178-204] |
| **Kiosk timeouts** | client `data-kiosk-idle="300"` → `<dialog>` "¿Sigues aquí?" one minute before; server `KIOSK_IDLE_MINUTES = 10`, `KIOSK_MAX_MINUTES = 60`; `kiosk_timeout` flips to `AWAITING_UNLOCK` after 60 s idle | | `ended.html` "No hay una entrevista abierta en esta computadora" + "Entrar a Emmaus" when no session is left [Verified kiosk/welcome.html:3,21-26; services/interviews.py:65-67; kiosk.py:216-228; kiosk/ended.html] |

Empty/error states: "Esta pantalla ya se cerró. Avisa a tu coordinador." (`KIOSK_CLOSED_MESSAGE`), "La contraseña no es correcta. Revísala e inténtalo otra vez." [Verified services/interviews.py:79-80]. A stale revision silently redisplays the step (`StaleRevision` → redirect to the same step) [Verified kiosk.py:97-98].

#### Coordinador

- Entry: `events:list` → event → `coord_home` ("Mi inicio"): `h1` "Hola, {first_name}.", "Tu rol: coordinador.", "Todavía no eliges computadora. Elegir computadora" when no station, the "sign" panel with exactly one next action (resume the open interview / search "¿A quién vas a entrevistar?" / "¡Terminaste!" / "Todavía no tienes camperos asignados" → "Registrar campero"), then the live region (20 s) with "Llevas N de M", "Por entrevistar", "Esperan algo de ti" [Verified coordinator/home.html:5-52; coordinator/_home_live.html].
- Nav: "Mi inicio", "Mis camperos", "Entrevistas pendientes", "Mi avance", "Ayuda" [Verified common.py:93-98].
- Primary tasks: choose computer → search → "Antes de empezar con {first_name}" (3–4 numbered checks: identity, authorization with privacy notice fold, which questionnaire, mode) → steps → review → decision → done ("Entrevistar a otro campero", "Volver a mi inicio") [Verified interview/start.html:10-93; done.html:10-14]. Register a campero ("Guardar y empezar la entrevista" / "Guardar y registrar otro") with duplicate warning "Ya hay N camperos con este nombre" [Verified participant_form.html:21-46]. Resume ("Retomar"), take over ("Tomar el control aquí"), discard, correct with reason, report risk, decide "Por decidir" follow-ups.
- Blockers surfaced on the home: "Todavía no se pueden iniciar entrevistas: la administración debe confirmar los procedimientos." [Verified coordinator/home.html:14-16] (prior UX-10 closed).
- Ends: "¡Terminaste! Las N entrevistas de tus camperos están completas. Gracias por tu tiempo." [Verified coordinator/home.html:36-38]; idle logout after 30 min with "Por seguridad cerramos tu sesión después de un tiempo sin actividad. Vuelve a entrar." and a "Seguir aquí" notice two minutes before [Verified middleware.py:59; emmaus.js:292-297].

#### Tutor

- Entry: `tutor_home` ("Mi equipo"): `h1` "{team_name}, con {first_name}." or "Hola, {first_name}."; sign only when an interview stalled ("{name} tiene N entrevistas interrumpidas … Llevan 20 minutos o más sin cambios"); "Tu equipo lleva N de M", rows per coordinator, "Ritmo del equipo" charts, side: "Seguimientos del equipo", "Por decidir", "Actividad reciente", "Cortes" [Verified tutor/home.html; tutor/_home_live.html:9-116].
- Nav: "Mi equipo", "Coordinadores", "Entrevistas", "Pendientes", "Reportes", "Ayuda" [Verified common.py:85-92]. Shell = leader shell (tutor is not coordinator) [Verified events/context_processors.py:23].
- Scope: only coordinators with a `TutorAssignment` to this tutor; reports only `equipo:<id>` scope [Verified events/access.py:99-109,241-242].
- Empty states: "Tu equipo todavía no tiene camperos.", "Aún no tienes coordinadores asignados. La administración del evento te los asigna.", "No hay seguimientos abiertos. Cuando un campero pida acompañamiento aparecerá aquí." [Verified tutor/_home_live.html:40,45,73].
- Ends: nothing explicit; the camp closes and reports remain readable.

#### Pastor (event role or church-wide `pastor`)

- Entry: `pastor_home` ("Resumen general"): report-as-questions — "¿Cómo va el campamento?", "¿Qué hay que coordinar?" (rule-based agenda with "No hay inteligencia artificial ni juicios sobre las personas."), "¿Qué equipos necesitan apoyo?", "¿Cuánto falta?", "¿Qué necesidades hay?" (+ folded "Lo que los camperos quisieron compartir" with k-anonymity text "Solo totales … se redondean de k en k") [Verified pastor/_home_live.html:15-147]. Actions: "Mostrar presentación", "Reportes" [Verified pastor/home.html:13-16].
- Nav: "Resumen general", "Avance del campamento" (`f"Avance {event.of_kind}"`), "Seguimiento", "Reportes", "Equipo", "Ayuda" [Verified common.py:77-84].
- Cannot register, interview, or read confidential answers without the per-membership `can_view_sensitive` flag; a church-wide pastor has no membership and therefore never has it [Verified events/access.py:69-71,267-268].
- Empty state: "Aún no hay camperos registrados. La administración los registra o importa." and only the first question renders [Verified pastor/_home_live.html:7-8,36-39].

#### Administración (event `administrador`, church `church_admin`, superuser)

- Entry: `admin_home` ("Inicio"): `h1` = event name; one sign for the first blocker in order (procedures → questionnaire → no camperos → access requests → unassigned camperos); "Llevamos N de M"; figures (Camperos, Entrevistas a solas, Seguimientos abiertos, Situaciones de riesgo); "Preparación" checklist "k de 6 pasos listos" (procedimientos, cuestionario, camperos, equipo, computadoras, cortes) with pending first; "Actividad reciente"; "Descargas recientes" [Verified admin/_home_live.html:12-125; dashboards.py:514-625].
- Nav: "Inicio", "Camperos", "Equipo", "Cuestionarios", "Reportes", "Configuración", (+"Discipulado"), "Ayuda" → stacked bar (> 5) [Verified common.py:66-76; shell_leader.html:9].
- Primary tasks: create event (`event_create` seeds 10 stations + the church's latest published questionnaire or `DEFAULT_CAMP_QUESTIONNAIRE`) [Verified home.py:48-67]; confirm procedures; import CSV; add team / approve requests / assign tutors; set confidential level (shared with pastor); cutoffs schedule; export; release stuck interviews ("Computadoras en uso"); sedes.
- Ends: event status "Terminado"/"Archivado" (`Event.Status`) — archived events disappear from the list except for superusers [Verified events/models.py:127-131; events/access.py:287].

#### Church admin (discipleship)

- Entry: see B-04 (camp first). Church bar: "Inicio", "Participantes", "Invitaciones", "Rutas", "Preguntas", "Seguimientos", "Configuración" when the module is on; only "Configuración" when off [Verified context_processors.py:46-73].
- `church_dashboard` "Panorama del ministerio" is five questions ("¿Qué preguntas esperan respuesta?", "¿Qué seguimientos siguen pendientes?", "¿Quién se quedó callado?", "¿Quién espera un mentor?", "¿Cómo va el discipulado?", "¿Qué anotaron los líderes?") with a sign only when escalated questions wait [Verified core/church_dashboard.html:12-185].
- Settings: name, welcome text, contact e-mail, participant notice, logo (light/dark), `self_registration_enabled`, `leaders_can_invite_participants`, `discipleship_enabled`, 8 colour fields with contrast validation [Verified core/church_settings.html:11-67; core/test_audit_a11y_ui.py:82-135].

#### Discipleship participant / mentor

- Participant entry: `invite/<token>/` → "Acepta tu invitación a Emmaus" with two consent checkboxes ("Acepto que mi iglesia guarde en Emmaus…", "Entiendo que Emmaus no es un servicio de emergencias…") → "Crear mi cuenta" → `participant_dashboard` "Hola, {name}." which auto-enrols in the church's default path [Verified core/accept_invitation.html; core/forms.py:423-428; core/views.py:373-384]. Nav: "Mi camino", "Lección actual" (only while one is open), "Preguntas", "Pedir una conversación" [Verified context_processors.py:23-39]. Lesson screen reuses the interview trail ("Lección N de M") and ends with "Marcar lección como terminada" / "Guardar reflexión" [Verified core/participant_lesson.html:8-53]. Ends: "Terminaste la ruta. Si quieres platicar, pide una conversación." [Verified core/participant_dashboard.html:20].
- Mentor entry: `mentor_dashboard` "¿Quién necesita atención hoy?" with sign "{name} espera tu respuesta", then "Esperan tu respuesta", "Seguimientos pendientes", "Mis participantes" [Verified core/mentor_dashboard.html:6-105]. Nav: "Inicio", "Seguimientos" [Verified context_processors.py:40-45]. Question detail offers "Responde personalmente", "Pasar al pastor" and an AI draft section ("Borrador con IA") [Verified core/question_detail.html:50-103] — note the camp module states "No external AI in the camp module" [Verified docs/privacy-and-safeguarding.md heading §"No external AI in the camp module"] while the discipleship module has one.

### 2.4 Journey-level findings

- **G-01 — The event list is a pass-through, and "Campamentos" is the only way back.** `events_home` redirects to the remembered/only active event unless `?todos=1`, which only the bar's "Campamentos" link appends [Verified home.py:25-33; bar.html:23,29]. For a church running one camp this is right; for the multi-event church in the vision it hides choice and the label is wrong for "Retiro", "Conferencia", "Otra actividad" [Verified events/models.py:120-125]. Severity: **Low** now, **Medium** at Phase 2. Recommendation: keep; when Mi Iglesia exists, the list becomes "Mis eventos" inside it and the auto-redirect is limited to the kiosk/coordinator context.
- **G-02 — A 403 inside an event drops the event bar.** `event_view` raises `PermissionDenied` before `request.access` is set, so `event_context` returns `{}` and `errors/403.html` (which extends `base.html`) renders with the church bar or none [Verified common.py:30-36; events/context_processors.py:9-11; errors/403.html:1]. The page says "pide a la administración del evento que te dé acceso" and offers "Volver a mi inicio" → `dashboard` → back into the event list [Verified errors/403.html:5-6]. Prior UI-35 remains open. Severity: **Low** — permissions are correct; orientation is lost. Recommendation: pass `event`/`access` into the 403 context when the slug resolves (cheap, test-covered), after the camp.
- **G-03 — Coordinator pages outside the four nav items have no `aria-current` and no "where am I".** "Ver todos los seguimientos" from "Mi inicio" opens `events:follow_ups` under the coordinator shell, but `follow_ups` is not in any coordinator nav item's `names`, so no link is marked current [Verified coordinator/_home_live.html:87; common.py:93-98]. Same for `backup_list`, `participant_detail` reached from search. Severity: **Low**. Recommendation: add those names to the nearest item's `also` tuple (one-line change, covered by `test_rol_visible`).

---

## 3. Forms and workflows

### 3.1 Workflow map

| Workflow | Entry → steps → exit | Evidence | Notes |
|---|---|---|---|
| **Registration (one campero)** | "Registrar campero" → `ParticipantForm` (`full_name`, `age`, `room`, `church_group` with datalist, `sede` if the church has sedes, `coordinator` admin-only, `guardian_authorization` "Ya entregó la autorización firmada…", `not_duplicate` "Sí, es otra persona con el mismo nombre") → "Guardar y empezar la entrevista" / "Guardar y registrar otro" / "Guardar" → message "{name} quedó registrado con el número {code}." | [Verified events/forms.py:191-215; participant_form.html; coordinator.py:161-188] | Lede "Solo pedimos lo necesario." is true: 4 required fields. |
| **CSV import** | "Importar camperos" → `ImportForm` ("Archivo CSV", help "Columnas: Nombre, Edad, Cuarto, Iglesia o grupo. Opcionales: Coordinador…, Autorización (sí/no) y Sede.") → preview "Revisa antes de importar" with figures (Filas / Listas para importar / Con errores / Posibles repetidos) and a 9-column table with per-row checkbox → "Importar filas marcadas" / "Cancelar y subir otro archivo" / "¿Otro archivo?" | [Verified events/forms.py:500-503; admin/import.html:5-82] | Says "CSV (UTF-8)" and "Archivo CSV" (LANG-16 open, see §4). The browser's own file button is in the browser's language (comment acknowledges it) [Verified import.html:56]. |
| **Questionnaires and versions** | "Cuestionarios" → "En este evento" (ordered list; "Subir"/"Bajar"/"Quitar"; checkbox "Los camperos los responden en este orden" with ≥ 2) + "Versiones" per questionnaire ("Usar en este evento", status marks) → version page: draft = editable (sections, questions, options, "Datos técnicos" fold), "Publicar versión" (confirm: "Al publicar ya no podrás cambiar esta versión"), published = "Usar…", "Dejar de usar…", "Crear versión nueva a partir de esta" | [Verified admin/questionnaires.html:6-65; admin/version_detail.html:17-63] | Conditions are typed as option codes: help text "Los identificadores internos de esas respuestas, separados por comas. Están en “Datos técnicos”…" [Verified events/forms.py:526-536] — expert-only (G-05). |
| **Interview — modes, locks, resume** | `interview_start`: live interview → resume; all questionnaires done → detail with "ya tiene su entrevista completada"; otherwise consent requirements + mode + (questionnaire) → `start_interview`. Claims: `CLAIM_MINUTES = 15`; a step POST with a lost claim → `interview_conflict` ("Esta entrevista está abierta en otra computadora" / "La administración liberó esta entrevista") with "Tomar el control aquí"; `interview_resume` re-opens on purpose (`reopen=True`) and lands on the first unanswered step or the review | [Verified interview.py:103-118,163-292; services/interviews.py:65] | Admin can release from "Computadoras en uso" ("Liberar", "Cerrar la pantalla") [Verified admin/sessions.html:7-38]. |
| **Interview — review, decision, done** | `review.html`: answers as `summary-list` with "Cambiar" (`?volver=revision` returns to the review), hidden-answer notes per confidential level, "¿Necesita seguimiento?" radios (`Se requiere seguimiento` / `No se requiere seguimiento` / `Por decidir`) with "Lo que {name} pidió" reminder, "Finalizar entrevista"; "Si algo no salió como se esperaba": "Reportar una situación de riesgo" (danger outline), "Descartar esta entrevista" | [Verified interview/review.html:15-67; events/models.py:862-865] | Prior UX-07 closed by `RETURN_TO_REVIEW` [Verified kiosk.py:71-72,89-90; interview.py L? (same marker)]. |
| **Follow-ups** | created by the decision or a safety report → `follow_ups` list with status filters (Abiertos / Pendiente / En proceso / Atendido / Cancelado / Todos) and responsible filters (Todos / Sin asignar / Míos), row-level `<select>` + "Asignar" when `can_manage_follow_up`, "Mostrando 200 de N" cap → detail: status, "Líder responsable", `resolution_note` (details only with `can_view_sensitive`; "Confidencial. Solo el equipo de cuidado autorizado puede leer los detalles."); safety kind shows "Sigue el procedimiento de protección" + `safeguarding_instructions` | [Verified supervision/follow_ups.html:6-42; follow_up_detail.html:6-32; dashboards.py:333-427] | Prior UX-15 closed (filters, row assignment, count line). |
| **Cutoffs / reports / PDF / deck / export** | Settings "Cortes": time zone, `reports_enabled`, interval ("Cada hora"/"Cada 2 horas"), window start/end → scheduler `run_report_cutoffs` stores `ReportSnapshot` per scope; "Reportes" shows scope filters (`Todo el campamento`, each `team_label`), "Generar corte ahora", "Mostrar presentación", latest cutoff, history with quiet stretches folded ("Sin cambios", "Mostrar los N cortes"), 12 per page → detail (+ "Comparar con el corte anterior") → "Descargar PDF" (reportlab, Figtree) → deck (6 or 5 slides, "Modo noche", full screen, 60 s live refresh, "Sesión terminada: vuelve a entrar para ver datos actuales") | [Verified admin/settings.html:58-67; events/models.py:133-135; reports/home.html:5-82; services/reports.py:3-6,263-279; presentation/deck.html; presentation/_slides.html:18] | Admin home also watches the scheduler: "El programador de cortes no ha corrido desde …" [Verified dashboards.py:479-511]. Exports: list CSV (admin), answers CSV (admin + confidential), one person JSON (admin + confidential) [Verified admin.py:249-302,331-373]. |
| **Team and tutor assignment** | "Equipo": join-code block, "Solicitudes de acceso" (role `<select>` + "Agregar al equipo" / "Rechazar"), team grid per tutor, "Coordinadores sin tutor", "Pastores y administración"; "Agregar persona" (`MemberForm`: name, e-mail "Lo usará para entrar a Emmaus.", role radios, "Nombre del equipo" "Solo para tutores.", "Tutor que lo supervisa" "Solo para coordinadores.", "Puede leer información confidencial" "Solo para el equipo de cuidado autorizado. Cada consulta queda registrada."); "Asignar tutores" = table of coordinators with a native `<select>` each | [Verified supervision/team.html:5-130; events/forms.py:396-410; admin/assignments.html:6-26] | Password-set link shown on screen when no e-mail backend ("No hay correo configurado: comparte este enlace…") [Verified docs/roles-and-permissions.md §"Joining with the event code"]. |
| **Join code** | printed sheet "Código para unirse" → `/unirme/` ("Código o nombre del evento", "Buscar el evento") → `/e/<code>/` ("Pide acceso al equipo. La administración del evento revisará tu solicitud.", name, e-mail, "¿Qué vas a hacer en el evento?" tutor/coordinador, "Algo que la administración deba saber") → "Solicitud enviada" (one answer for every case) | [Verified join/join.html; join/entry.html; join/requested.html; events/forms.py:426-462] | Throttles and the 50-pending cap are server-side [Verified join.py:25-28]. |
| **Church settings / logo** | one multipart form, four sections; logo preview "Tu logotipo de ahora, sobre blanco." / "Tu versión en blanco, sobre fondo oscuro."; colour fields with plain names ("Color de “estás aquí”", …) and server-side contrast refusal | [Verified core/church_settings.html; core/test_audit_a11y_ui.py:82-135] | Field `public_welcome_text` ("Mensaje de bienvenida") exists but the landing page does not render it [Verified core/models.py:30; core/landing.html:12-23] — a ready hook for Explorar. |
| **Discipleship paths / lessons / notes** | "Rutas" → `PathForm` (title, description, target audience, active, default) → lessons (`order`, `title`, `scripture_references`, `body`, `practical_step`, `is_active`) → reflection questions; notes `PastoralNoteForm` (body, "¿Quién puede ver esta nota?") | [Verified core/forms.py:454-503,593-597; core/urls.py:36-42] | No versioning of lessons (contrast with questionnaire versions). |

### 3.2 Findings on workflows

- **G-04 — Settings is still the densest administrative screen.** Six sections, three independent POST forms plus one form per station row ("Poner fuera de uso"), two `data-guard`ed forms that warn on leaving; the Cortes + Privacidad pair saves together under one button "Guardar cortes y privacidad" [Verified admin/settings.html:27-111; comment at 20-26]. Prior UX-14/UI-23 are closed (one form per section, index, warning), so what remains is length: 184 visible words and 11 fields across two scroll screens (word count is a static proxy, see §3.3). Severity: **Low** for the camp (done once per event). Recommendation: none before camp; when sedes/tenant settings arrive, split "Cortes" and "Privacidad y protección" into their own pages rather than growing this one.
- **G-05 — The questionnaire editor is expert-only by construction.** Conditions require typing option codes from "Datos técnicos"; the published-version lock means a wording error (e.g. prior LANG-10 "¿Te consideras salvo?", still the default first question [Verified events/services/questionnaire.py:343-344]) needs a new version and a switch in "Cuestionarios" [Verified admin/version_detail.html:19-25]. Severity: **Medium** — a church without a technical volunteer cannot adapt the form; the vision wants "Configuration before custom code" (§2.12) and configurable `FormDefinition`s (§5.2). Recommendation: do not touch before camp (interviews are keyed to versions); afterwards replace the code field with option checkboxes (the help text already describes that target) and keep the version model.
- **G-06 — "Descargar todos sus datos" delivers raw JSON.** `participant_data_export` returns `application/json` [Verified admin.py:371] from a link that reads as a family-facing export [Verified participant_detail.html:66]; prior LANG-29 open. Severity: **Medium** — this is the erasure/portability path for a minor's record (vision §5.4) and nobody non-technical can read it. Recommendation: keep JSON as the machine copy; add a PDF/CSV rendering from the same dict after the camp.
- **G-07 — Two ways to reach the same follow-up with different "back" behaviour.** From "Mi inicio"/"Mi equipo"/"Pendientes" the detail carries `?siguiente=` and returns there; from `participant_detail` it returns to the record; from `follow_ups` to the filtered list [Verified coordinator/_home_live.html:80; tutor/_home_live.html:64; participant_detail.html:57; follow_ups.html:24]. This is deliberate (prior UX-21 closed) and consistent; recorded here as **no finding** so the synthesis does not re-open it.
- **G-08 — Dead ends found: none in the camp; one for anonymous users.** The error pages link "Volver a mi inicio" → `dashboard` → login for someone signed out [Verified errors/403.html:6; errors/404.html:6; settings.py:154-155] (prior LANG-51 open). The 403→dashboard→event→403 loop is **not** possible: `current_event` is only remembered after access passes [Verified common.py:38-39], and `accessible_events` excludes events with a deactivated membership [Verified events/access.py:281-286]. Severity: **Low**. Recommendation: label "Ir a Emmaus" when `user.is_anonymous`.

### 3.3 Text density (static proxy)

Words of visible text per template after stripping tags, template tags and comments (median across 138 templates: 34). Top of the list [Verified by the script in this audit; counts are of the template, so conditional branches add up]:

| Template | Words | Paragraphs | Observation |
|---|---|---|---|
| `events/ayuda.html` | 805 | 35 | By design: five tasks × four roles; only one role's five render (≈200 words) [Verified ayuda.html:13-160] |
| `core/church_dashboard.html` | 298 | 29 | Five question sections + figures; no fold |
| `events/pastor/_home_live.html` | 291 | 21 | Report-as-questions; the only fold is "Lo que los camperos quisieron compartir" (`data-fold-below="600"`) [Verified :122] |
| `events/backup_list.html` | 223 | 12 | Print sheet |
| `events/admin/import.html` | 222 | 8 | Two tables, four forms |
| `events/reports/detail.html` | 207 | 14 | |
| `events/admin/_home_live.html` | 203 | 17 | Sign + figures + 6-step checklist + two lists |
| `events/admin/settings.html` | 184 | 12 | G-04 |
| `events/interview/review.html` | 176 | 12 | Three alternative privacy notes (one renders) |
| `core/participant_dashboard.html` | 162 | 18 | Member home: lede of 24 words + four sections |

Hypothesis (not verified with people): the pastor summary and the church panorama are the two screens where phone reading would benefit from folding sections 3–5 by default, as the outcomes block already does. The coordinator and campero screens are short (home 102 words, kiosk welcome ≈ 90).

---

## 4. Language

### 4.1 What is in place [Verified]

- `LANGUAGE_CODE = "es-mx"`, `LANGUAGES = [("es-mx", "Español (México)")]`, `LocaleMiddleware`, `LOCALE_PATHS`, `TIME_ZONE` default `America/Mexico_City` [Verified emmaus/settings.py:53,126-131].
- `locale/es_MX/LC_MESSAGES/django.po`: 60 `msgid`s, all Django form/file/password messages rewritten in tú voice ("Escribe un valor válido.", "Usa solo letras sin acentos…") with a header explaining why ("Django's own es/es_MX catalogs use 'usted'"); a test fails if `.mo` and `.po` drift and checks the tú voice [Verified django.po:1-30; core/test_locale.py:47-78].
- Template strings: a scan for English UI nouns (dashboard, submit, login, settings, save, …) in visible text found none; the Spanish-page tests assert it per screen [Verified events/tests/test_ui.py:70-100,151; test_audit_lenguaje.py].
- Glossary with one word per thing in `docs/user-guide-es.md` §"Palabras que vas a ver" and the "Una palabra por cosa" rules in `docs/design-system.md` §5 (e.g. "Faltan" for interviews, "sin atender" for follow-ups; "Ya no participan"; "Campamentos" for the list) [Verified].

### 4.2 Residual inconsistencies (each is a `Low` unless stated)

| ID | Observation | Evidence | Status vs prior audit |
|---|---|---|---|
| **B-05** "Administrador" (role label) vs "administración" (prose) vs "Administración de la iglesia" (church role). The bar and the home say "Tu rol: administrador"; ~30 sentences say "la administración" ("pide a la administración", "La administración los registra o importa.") | [Verified events/models.py:364; core/models.py:21; events_list.html:17; coordinator/home.html:41; pastor/_home_live.html:38] | LANG-08 / decision D3 "Roles neutros en todas partes… no cambiaron" [Verified docs/design-direction.md:150] — open by decision |
| "Soy coordinador: recuperar la computadora" on the campero's hand-back screen; "Avisa a tu coordinador" | [Verified kiosk/handback.html:7,13] | LANG-09 open |
| "Tomar el control aquí" | [Verified interview/conflict.html:28; ayuda.html:138] | LANG-41 open |
| "¿Te consideras salvo?" is still the default first question of new churches | [Verified events/services/questionnaire.py:343-344] | LANG-10 open (published versions cannot change; new churches get it) |
| "teléfono" on the printed sheet vs "celular" in the church help page | [Verified admin/join_code_sign.html:16; core/ayuda.html:29] | LANG-50 partially: both words now coexist |
| "CSV", "CSV (UTF-8)", "Archivo CSV" (6 template hits) | [Verified admin/import.html:71; events/forms.py:502] | LANG-16 open |
| 24-hour times everywhere: 28 `H:i` uses | [Verified count; docs/design-direction.md:148 "siguen en 24 h… decisión D1… se cierra con voluntarios"] | LANG-06 open by decision → test task T4 |
| "Equipo" with four senses: nav "Equipo" (team page), "Mi equipo" (tutor home), "equipo de cuidado" (17 hits, the `can_view_sensitive` holders), "Nombre del equipo" (a tutor's label) | [Verified grep counts; forms.py:405] | LANG-55 open |
| "Inicio" ×3 homes, "Mi inicio", "Mi camino", "Mi equipo", "Resumen general" as home labels | [Verified common.py:68,94; context_processors.py:25,43,53,62] | new, Low: each is right for its role; matters for multi-role people (see §6) |
| "Campamentos" (bar) vs "Campamentos y eventos" (page) vs `Event.Kind` values Retiro/Conferencia/… | [Verified bar.html:23; events_list.html:3; events/models.py:120-125] | LANG-40 closed by decision; noted for Phase 2 |
| « » used for questionnaire titles («{{ title }}») while “ ” is used for quoted UI words | [Verified pending.html:16; welcome.html:10] | LANG-38 reversed into a convention; consistent |
| "Aviso para participantes" (church field) vs "aviso de privacidad" (interview fold "Leer el aviso de privacidad para explicárselo") | [Verified core/models.py:34; interview/start.html:35] | LANG-52 open |
| URL paths in English for the church module | [Verified core/urls.py] | B-01 |

**G-09 — No English leaks in authored strings; the leaks are native controls.** `type="date"` (event dates, follow-up due date) and `type="time"` (cutoff window) render in the browser's locale, as does the file picker ("Choose File") [Verified events/forms.py:53-54,127-128; core/forms.py:572; import.html:56]. Severity: **Low**, **Hypothesis** for effect (prior UI-43). Recommendation: verify once in Chrome/Edge set to es-MX during the test session (T5); if it leaks, label the control with the expected format in the hint, do not replace native inputs.

---

## 5. Accessibility and mobile — as written

### 5.1 Verified in templates, CSS and JS

| Area | What the code does | Evidence |
|---|---|---|
| Document | `lang="es-MX"`, `<meta viewport>`, skip link "Saltar al contenido" to `main#contenido[tabindex=-1]` on every shell | [Verified layout/skeleton.html:2-5,20; base.html:17; shell_leader.html:11; shell_focus.html:17; shell_kiosk.html:21; deck.html:5] |
| Headings | `_page_head.html` owns the `h1` (74 includes); pages with their own `h1` use `.voice`; the three templates with two `<h1>` are if/else branches; tests and e2e assert one `h1` | [Verified _page_head.html:8; interview/conflict.html:8,13; kiosk/handback.html:6,9; events/tests/test_rotulo.py:221; test_rotulo_leaders.py:22; docs/ux-review.md §Método] |
| Landmarks | `<nav aria-label="Principal">`, `role="region"` on scrollable tables (15), `aria-labelledby` on sections (181), `role="search"` (4) | [Verified bar.html:25; counts from grep] |
| Focus | `:focus { outline: none }` + `:focus-visible` 3 px ink ring with 2 px white halo; inside bars `outline-offset: -3px`; `main:focus` no ring | [Verified rotulo.css:131-140,351,379] |
| Sticky action bar | `html:has(.step .step__actions) { scroll-padding-bottom: calc(5.5rem + 1px) }` | [Verified rotulo.css:625] — closes prior A11Y-01 as written |
| Menú dialog | `role="dialog" aria-modal="true" aria-labelledby`, JS moves focus in, `inert` outside (fallback hides from AT), Tab cycles, Escape and "Cerrar menú" return focus to "Menú" | [Verified menu_panel.html:8-22; emmaus.js:21-95] — closes prior A11Y-02 |
| Live regions | time line `aria-live="off"`; separate `role="status" aria-live="polite" data-live-announce` only for lost connection / ended session; a test per dashboard | [Verified coordinator/home.html:47-48; pastor/home.html:10-11; emmaus.js:179-193; events/tests/test_audit_a11y_ui.py:298-313] — closes A11Y-07 |
| Idle logout | "Seguir aquí" notice two minutes before, countdown from the server's `last_seen`, typing pings at most every 2 min; kiosk dialog `<dialog>` with `showModal()` | [Verified emmaus.js:292-476; session.py:13-40; test_audit_a11y_ui.py:149-273] — closes A11Y-04 |
| Errors | `error_summary.html` with `role="alert"` and links; fields get `aria-describedby` for hint and error; `Error: ` prefix in `<title>` | [Verified partials/ui/field.html:12; interview/_question.html:3; _step_form.html:13-18; kiosk/unlock.html:2] |
| Targets | `--control: 3rem` (48 px); `.btn--small` 2.75 rem (44 px); primary/large 3.75 rem; inputs 3.5 rem; search input 3.75 rem; `.bar__event a` 24 px (WCAG 2.5.8 comment); sign links padded to 44 px | [Verified rotulo.css:88,186,199-204,222,334,423-425,428] |
| Motion | `@media (prefers-reduced-motion: reduce)` disables `@view-transition`, every animation and transition; `forced-colors` adds borders to marks; print hides bars and buttons | [Verified rotulo.css:935-947; test test_rotulo.py:461] |
| Colour | contrast tests for the Atrio palette and for church-chosen colours (server refuses unreadable ones with a message) | [Verified test_rotulo.py:419; core/test_audit_a11y_ui.py:82-135] — closes A11Y-06 |
| Copy feedback | `data-copy` buttons announce "Copiado" via a live region and revert after 3 s | [Verified emmaus.js:254-287] — closes A11Y-12 |
| Offline guard | step forms carry a hidden `[data-offline-notice]` "Sin conexión. Cuando vuelva la red, toca el botón otra vez. Lo que elegiste sigue aquí." | [Verified _step_form.html:39] — prior UX-05, [Unknown: the JS branch that shows it was not executed] |

### 5.2 Still open from the prior audit (verified as still absent) — all **Low** individually

| Prior ID | What is still missing | Evidence |
|---|---|---|
| A11Y-09 | no `aria-invalid` anywhere | [Verified grep over templates: 0 hits] |
| A11Y-10 | error-summary links to `#decision` (a `fieldset`) and `#campo-…` divs, not focusable | [Verified interview/review.html:7,37] |
| A11Y-13 | conditional reveals have no `aria-expanded`/`aria-controls` (only the menu and "Mostrar los N cortes" use them) | [Verified emmaus.js:195-225,482-487; _step_form.html:24-29] |
| A11Y-14 | PDF has no `lang`/tagging | [Verified events/services/pdf.py: no `lang`, no tagged flag] |
| A11Y-15 | `th` with `scope`: 16 of 79 | [Verified grep count] |
| A11Y-16 | deck live count "1 de N" without the slide title; slides do carry `aria-label="Diapositiva k de N"` | [Verified deck.html:16; _slides.html:13,23] |
| A11Y-05 | whether the kiosk `<dialog>` still closes on any key | [Unknown: emmaus.js:435-476 handles open/`data-idle-continue`; `cancel` handling not seen] |
| UI-25 | native `<select>`/checkbox in admin tables and rows (team, assignments, follow_ups, participants, interviews, audit) | [Verified 6 templates with `<select>`] |

### 5.3 Mobile as written

- Breakpoints: `600` (phone), `760` (menu), `900` (two-column → one), `1280` (bar rows); extra `min-width: 601/900/901` rules [Verified rotulo.css:338,390,396,401,713,754,788,1040,1324]. Type scale floor 16 px (`--t-small`), body 18 px, captions 15 px for chart text only [Verified rotulo.css:63-70].
- Phone-specific choices: `.search` stacks and buttons go full width; `.bar__event` hidden; `.split` and `.crew-board` collapse to one column at 900; figures `overflow-wrap: anywhere`; `data-fold-below="600"` folds the outcomes block [Verified rotulo.css:429-432,713,964,804; pastor/_home_live.html:122].
- **G-10 — Patterns that risk failure on phones (hypotheses, not measured here).** (a) Every data table is in `.table-wrap { overflow-x: auto }` — horizontal scrolling for import preview (9 columns), sessions, assignments, pending-by-coordinator, history [Verified rotulo.css:832; import.html:19-31; sessions.html:10-34]. (b) Rows that carry a `<select>` + button in `.row__end` (follow-ups, access requests) at 390 px [Verified follow_ups.html:31; team.html:49-58]. (c) The stacked leader bar (6–8 links) under 760 px is entirely inside "Menú": two taps per section change (prior UI-14 was answered with the 760 px step; the cost remains for admins). (d) The event name and role vanish ≤ 600 px, so a coordinator working from a phone sees only the church mark and "Estás en" [Verified rotulo.css:403]. Severity: **Medium** for the camp only if coordinators use phones (the design assumes shared computers, [Verified docs/interview-station-workflow.md; kiosk comments]); **Medium** for the vision's "mobile-first" members. Recommendation: measure in T2/T4 on real phones before changing anything; the prior e2e already checks no horizontal page overflow at 390 px for every URL (`e2e/review-all.mjs`), so what is unknown is *usability* of in-table scrolling, not layout breakage.
- The prior audit's own numbers cover the rest and are not repeated: reflow 65/65 pages, 0 axe violations across 347 runs, A11Y-11 (419 sub-44 px links at 390 px), UI-15 (chart text 12–14 px on phones) [docs/auditoria-ux-2026-10.md §7, §6].

---

## 6. Proposal — `Explorar`, `Mi Iglesia`, contextual `Liderazgo` on today's shells and URLs

### 6.1 What today's role-first shells already give (reuse, do not rebuild)

1. **A per-request shell switch** — `rotulo_shell` is chosen from `access.role`, and every event template extends `{% extends rotulo_shell %}` [Verified events/context_processors.py:23; tutor/home.html:1; admin/home.html:1]. This *is* "contextual Liderazgo": the same account gets a different bar inside `/eventos/<slug>/` and the church bar outside.
2. **One bar partial for all shells** with named slots: brand → `home_url`, `context_line` (event or church), `context_role`, inline/button `switch_url`, `nav`, `discipleship_url`, `help_url`, and the Menú "who" line [Verified partials/ui/bar.html:1-18]. Adding a second context (Mi Iglesia) is a matter of what the caller passes, not a new component.
3. **A router that already knows "do you have leadership contexts?"** — `show_events_link = role in (CHURCH_ADMIN, PASTOR) or accessible_events(user).exists()` [Verified core/context_processors.py:77] and `accessible_events` [events/access.py:272-290].
4. **Public surfaces that work without login and survive the kiosk confinement**: landing, `/unirme/`, `/e/<code>/`, church logo [Verified emmaus/urls.py:36-37; middleware.py:16-23,34-38].
5. **Church-level configuration hooks already modelled**: `public_welcome_text`, `contact_email`, `self_registration_enabled`, `leaders_can_invite_participants`, `discipleship_enabled`, theme fields, sedes with `active` and `order` [Verified core/models.py:27-70; events/models.py:76].
6. **Per-kind copy** — `kind_noun`, `of_kind`, `all_of_kind`, `FEMININE_KINDS` turn "campamento" into "la conferencia" across the pastor and tutor screens [Verified events/models.py:290-308]; the member-facing generalisation of "evento" is partly done.
7. **The member home pattern exists**: `participant_dashboard` is already "Hola, {name}." + one sign + "Alguien puede acompañarte" + "Tus preguntas son bienvenidas" [Verified core/participant_dashboard.html:7-61] — the calm, few-choices home the vision §4.2 asks for.

### 6.2 Mapping with minimal navigation burden

| Vision experience | Today | Proposal (incremental, flag-gated) | Navigation cost |
|---|---|---|---|
| **Explorar** (public) | `landing` with camp copy ("Platicar con cada joven, sin perder a nadie.", "Entrar a Emmaus", "¿Vas a ayudar en un evento? Escribe el código", three promises) [Verified core/landing.html:12-23] | Keep the page and the two entries. Render `Church.public_welcome_text`, `contact_email` and the active `Sede` list on it (fields exist, unused). Later add `/iglesia/<slug>/` for a second church. Public events = `Event.Status.ACTIVE/DRAFT` with a new `is_public` flag; `/e/<code>/` already shows name, dates and church for a code [Verified join.py:74-76,97]. | Zero new nav for signed-in users; public page gains at most "Horarios · Sedes · Eventos · Contacto". |
| **Mi Iglesia** (member) | `base.html` bar + `participant/*` (3–4 links) and the router | Make `base.html` the Mi Iglesia shell: nav `Inicio · Explorar · Mi actividad · Perfil` (vision §9, to be tested); "Mi actividad" = today's `participant_dashboard` sections (ruta, preguntas, conversaciones) plus "Mis eventos" (today's `events_list` rows, read from `accessible_events` and, later, `EventRegistration`). "Perfil" = contact preferences + consents (today only at invitation time [Verified core/forms.py:411-428]). The "Campamentos" button becomes **"Liderazgo"** and appears only when `show_events_link` or a church/leader role exists. Router: Mi Iglesia first when `Church.member_home_enabled` (new flag, default off), otherwise today's behaviour. | 4 member links + 1 conditional "Liderazgo" button + Ayuda. Nobody loses a link they have today. |
| **Liderazgo** (contextual) | event shells under `/eventos/<slug>/…`; church ops under `/church/…` with the same `base.html` bar | Leave the event shells and URLs **untouched**. Mount church operations (`church/*`) on a `shell_church_ops` that is `shell_leader.html` with `context_line = church.name`, `context_role = church role`, nav = today's church-admin/pastor items; brand → Mi Iglesia home; inline switch → "Mis eventos". Inside an event, add one link in the Menú "who" block back to "Mi Iglesia" (the bar's DOM order and whitespace are a test contract — add it in `menu_panel.html` after the nav, not in `bar.html`) [Verified bar.html:17-18; menu_panel.html:14-19]. | Entering a context swaps the bar (already true); leaving it is one link (already true: "Campamentos"). The only new element is a second context (church ops) using the same bar. |

Rule for multi-role people (J3): the account never switches; the *context* switches by URL. The bar's `context_line`/`context_role` already say which one you are in; the Menú "who" line already lists role, station and confidential access [Verified menu_panel.html:20]. What to add is only the list of contexts ("Mi Iglesia", "Discipulado de {church}", "{event} como {role}") in the Menú — one `nav` block, no new page.

### 6.3 What must not change for the camp

- Event URLs and names under `/eventos/<slug>/` and `/estacion/` (printed sheets, bookmarks on shared computers, `current_event` session key) [Verified emmaus/urls.py:33-36; common.py:38-39].
- `resolve_access` precedence and `event_view` (403 before any context), the kiosk confinement, the idle logout and "Seguir aquí", the CSP [Verified events/access.py:255-269; middleware.py].
- The four role navs and their labels (tests read them), the `_page_head` single-`h1` convention, and the byte-level bar/menu layout the tests assert [Verified bar.html:17-18; menu_panel.html:5-6; test_rol_visible.py].
- The router's "events first" rule until Mi Iglesia exists and is flag-gated per church.
- Questionnaire versions in use and `confidential_level` semantics (an interview keeps the level it started with) [Verified events/models.py:157-164].

---

## 7. Usability test plan (6–8 tasks)

Reference: the prior audit's §13 plan (four groups, one afternoon, synthetic data, fixed facilitator/observer protocol) remains valid for the *camp* tasks and is not repeated; tasks T1–T3 below cite it and only add what the code changed since. T4–T8 cover what §13 does not: phones, the two-homes seam, and the post-camp journey (paper/clickable prototype, since Mi Iglesia does not exist). Participants: 5 youths 14–17 with guardian consent, 5 coordinators/tutors (group leaders), 2–3 pastors, 2 church administrators, 3 volunteers 50+. Data: `seed_camp_demo` synthetic camp [Verified e2e/all_urls.py:2-3].

| # | Who | Task (said to the participant) | Success criterion | What to measure | Validates |
|---|---|---|---|---|---|
| T1 | Youth, kiosk | "Contesta esta conversación en la computadora; a la mitad, para por ahora; luego cambia una respuesta y termina." | ≥ 4/5 explain who reads their answers (coordinator + "equipo de cuidado", nobody else) and distinguish "Parar por ahora" from "Ya terminé" | completion, assisted completion, time, errors; comprehension probe after `welcome.html`; reaction to "Soy coordinador: …" | prior §13 group C; LANG-01/09/27; UX-06 closure; kiosk idle dialog |
| T2 | Coordinator, **phone 390 px** | "Entra, di en qué computadora estás, retoma la entrevista interrumpida y luego empieza una entrevista asistida." | ≤ 20 s to say the event and role (they are hidden ≤ 600 px); find "Retomar" in ≤ 2 taps; Menú used without help | first-glance orientation; Menú opens/closes count; mis-taps on 44 px links; keyboard not required | G-10(d), UI-14, A11Y-11 |
| T3 | Tutor | "Di qué coordinador necesita ayuda ahora, abre sus entrevistas interrumpidas y asigna un responsable a tres seguimientos sin abrir cada uno." | names the coordinator in < 30 s; 3 assignments in ≤ 9 taps using the row `<select>` | taps, errors with the native select on touch, whether "Míos/Sin asignar" filters are found | UX-15 closure, UI-25, LANG-55 ("equipo") |
| T4 | Pastor, phone + projector | "En tu celular, di cómo va, qué hay que coordinar y cuánto falta; luego muestra la presentación y di si los datos están al día después de 2 minutos." | three answers in < 90 s; reads "Solo totales … de k en k" correctly (no identification); detects "Sesión terminada" text in < 61 s when simulated | reading order on phone (fold usage), time, interpretation of 24-h times and "corte" (D1), deck keys after "Modo noche" | §13 group B; LANG-06; UX-04/03 closure; G-10 |
| T5 | Church administrator, first use | "Configura la iglesia, confirma los procedimientos e importa estos 20 camperos desde este archivo de Excel." | 4/5 complete without help; the CSV UTF-8 step is done or the error is understood; "Nombre corto para la dirección" is accepted without asking | screens, time, where they stop; whether the browser's native file/date controls appear in English (G-09); D6 wording | §13 group "Administrador"; LANG-16; G-04; UI-43 |
| T6 | Church administrator (same session) | "Activa el discipulado e invita a una persona; después vuelve al campamento y luego al panorama de la iglesia." | finds both homes in ≤ 2 wrong turns; can say which "Inicio" is which | path taken (Discipulado link vs Campamentos button), verbalised confusion, SEQ 1–7 | B-04, B-02, LANG-40 |
| T7 | Youth and group leaders, **prototype** (paper/clickable) | "Ya pasó el campamento. Desde la página de tu iglesia, encuentra un grupo y pide entrar. Como líder, acepta la solicitud." | ≥ 4/5 youths do it without creating a second account mentally ("¿necesito registrarme otra vez?"); leaders expect to see *no* camp answers | words used for group (grupo/célula/casa), expected login moment, expectation about visibility of camp answers (J2.7), expected notification channel | vision J2 steps 4–7, §6.2 Mi Iglesia nav labels (`Inicio · Explorar · Mi actividad · Perfil`) |
| T8 | A person with two roles (mentor + coordinator), current app + prototype | "Entra. Encuentra tus participantes de discipulado y luego tus camperos del campamento. Ahora en este boceto, haz lo mismo." | both found in ≤ 3 taps each; in the prototype the "Liderazgo"/contexts list is chosen over the brand link | time per switch, which link they try first, whether "Inicio" ambiguity is voiced | J3; §6.2 context list; B-05 |

Measures common to all: completion (unaided / aided / failed), time on task, error count, Single Ease Question (1–7) per task, verbatim quotes tied to a finding ID; a short post-session vocabulary check (corte, sede, seguimiento, equipo de cuidado, campero vs participante). Do not measure anything about a person's faith (vision §2.10). Threshold convention: the prior audit's (5 per group = signal, not statistics).

---

## 8. Findings register

| ID | Severity | Title | Rationale | Recommendation (incremental) |
|---|---|---|---|---|
| B-01 | Low | Mixed URL language (`/participant/` vs `/eventos/`) | visible, inherited by Mi Iglesia | Spanish mounts + 301s at Phase 2 |
| B-02 | Medium | Two follow-up/question systems and two "Seguimiento(s)" navs | the camp→group seam | Phase 1: one read-only union list; no merge before camp |
| B-03 | Medium | "Campero" vs "Participante" vs event kind "Programa de discipulado" | collides with "People are not roles" | fix glossary now, labels after camp |
| B-04 | Medium | Church admin always lands in the camp; two homes called "Inicio" | one person, ambiguous entry | flag-gated router change at Phase 2 |
| B-05 | Low | "Administrador" vs "administración" (D3 open) | consistency; gender | decide D3 with volunteers (T5/T6) |
| G-01 | Low→Medium | Event list auto-redirect and the label "Campamentos" for non-camp kinds | hides choice in multi-event churches | "Mis eventos" inside Mi Iglesia |
| G-02 | Low | 403 inside an event loses the event bar (UI-35 open) | orientation only | pass event into 403 context after camp |
| G-03 | Low | Coordinator pages outside the 4 nav items have no `aria-current` | orientation | add names to `also` tuples |
| G-04 | Low | Settings page density (6 sections, 5 forms) | once per event | split when tenant settings arrive |
| G-05 | Medium | Questionnaire editor requires typing option codes; default first question wording locked in published versions | churches cannot self-serve forms | option pickers after camp; new default version |
| G-06 | Medium | "Descargar todos sus datos" is raw JSON (LANG-29) | minors' data export unreadable by humans | add PDF/CSV rendering after camp |
| G-08 | Low | Error pages say "Volver a mi inicio" to anonymous users (LANG-51) | wording | conditional label |
| G-09 | Low (hypothesis) | Native date/time/file controls in the browser's language | may break es-MX | verify in T5 |
| G-10 | Medium (hypothesis) | Phone patterns: scrolling tables, select-in-row, all links in Menú, hidden event/role ≤ 600 px | vision is mobile-first; camp assumes shared computers | measure in T2/T4 before changing |
| §5.2 | Low ×8 | Open a11y leftovers (aria-invalid, summary anchors, reveal aria-expanded, PDF lang, th scope, deck announcement, dialog cancel, native controls) | each small | batch after camp; none blocks the event |

Verified-closed since the 2026-10-26 audit (for the synthesis, so they are not re-reported): UX-02, UX-04, UX-05 (template side), UX-06, UX-07, UX-10, UX-11, UX-13, UX-14, UX-15, UX-17, UX-19, UX-22, UI-03, UI-06, UI-14 (as 760 px step), UI-19/LANG-05, A11Y-01, A11Y-02, A11Y-04, A11Y-06, A11Y-07, A11Y-12, LANG-02, LANG-15, LANG-17 (field wording; D6 still open), LANG-19, LANG-20 (by decision), LANG-21, LANG-42, LANG-43, LANG-56 — evidence cited inline above.

---

## What I could not verify

- **Runtime behaviour**: no server or browser was run. Every "closes prior X" statement is about the code as written; the prior audit measured rendered pages at `4b6393b`, this one reads `6f4664f`. The camp runs on `main`, which was not audited; differences between `main` and `v2/foundation` are [Unknown].
- **JavaScript branches**: the offline notice, the kiosk `<dialog>` cancel behaviour (A11Y-05), deck key handling with focus on a button (UX-03), and the live-region announcements were read, not executed [Unknown: emmaus.js:435-476,497-585].
- **Assistive technology and real phones**: nothing here replaces NVDA/VoiceOver/TalkBack or touch testing (prior audit Anexo C says the same).
- **Django admin (`/admin/`)**: its strings come from Django's own es catalog (usted voice) and it is `is_staff`-gated; who holds `is_staff` in production is [Unknown].
- **E-mail bodies and management-command strings** (prior LANG-30, LANG-49): not read.
- **PDF accessibility**: inferred from `events/services/pdf.py` having no language/tagging calls; not opened in a reader.
- **Discipleship AI draft** (`core/question_detail.html` "Borrador con IA"): provider, prompt and data sent are outside B/G; flagged for workstream D.
- **Text-density numbers** are a static proxy (template words, all branches counted), not rendered reading time.
- **Whether `?todos=1` is discoverable** without the bar (phone, Menú) — hypothesis for T2.

## Top 5 for the synthesis

1. **The IA is already "contextual Liderazgo" inside events; the missing half is a member home.** `rotulo_shell` + one bar partial + `show_events_link` give the mechanism; Mi Iglesia can be `base.html` with four links and a conditional "Liderazgo" button, flag-gated per church, without touching `/eventos/…` [§6].
2. **The camp→group seam is a vocabulary and inbox problem before it is a schema problem**: Campero vs Participante, two "Seguimiento(s)" systems, an event kind called "Programa de discipulado" (B-02, B-03). Decide the person noun and the single follow-up list first.
3. **One person, two "Inicio"**: the router sends every church administrator into the camp and hides the church home behind "Discipulado"/"Campamentos" (B-04, T6). Any multi-role design (J3) must fix the entry rule, not add links.
4. **Self-service forms and human-readable exports are the two medium product gaps inside the camp code**: option-code conditions and locked default wording in the questionnaire editor (G-05); JSON as the only per-person export for minors' data (G-06). Both are post-camp, both are small.
5. **Nothing in B/G blocks the camp.** Accessibility and Spanish are strong and test-pinned; what remains is a batch of Low a11y leftovers (§5.2), three wording decisions the owner already parked (D1 24 h, D3 role gender, D6 "Nombre corto"), and phone usability that must be *measured* (T2, T4) before anyone redesigns for "mobile-first".
