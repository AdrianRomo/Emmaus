# Emmaus audit — Workstream D: security, privacy and authorization

Repository: `<checkout>`, branch `v2/foundation`, commit `6f4664f` (read-only; `main` runs the camp and was not audited). Method: static tracing of every URL in `emmaus/urls.py`, `events/urls.py`, `events/kiosk_urls.py`, `core/urls.py`, plus reading the tests. Two existing suites were run read-only as evidence: `events/tests/test_access.py` + `events/tests/test_security_review.py` → **69 passed in 6.83 s**; a `-k` selection of the confidential-level, core-scoping and cross-church tests → **28 passed** (`DEBUG=true`, SQLite test DB under the audit tmp dir). No `.env`, dumps, `media/` or `db.sqlite3` were read; no running system was touched.

Tags: `[Verified path:line]` = read in the code; `[Inferred: …]` = follows from code but not directly asserted; `[Unknown: …]` = could not be established statically.

---

## 0. Summary of the authorization design as implemented

- One decision point per module: `events/access.py` (`resolve_access` → `EventAccess`) and `core/access.py` (`get_user_role` → `visible_*` querysets). Every event view goes through `@event_view(*roles)` which resolves the event by **global slug**, resolves access, enforces the role allow-list, and passes `(event, access)` to the view `[Verified events/views/common.py:24-44]`. Every core view goes through `@role_required(*roles)` `[Verified core/views.py:139-153]`.
- Role resolution `[Verified events/access.py:255-269]`: superuser or `church_admin` of the event's church → `ADMIN` (membership kept if active, so its `can_view_sensitive` still counts; `via_church_role=True` otherwise); a **deactivated** membership → no access even for a church `pastor`; active membership → its role; church `pastor` → `PASTOR` with no membership (so never `can_view_sensitive`); else `None` → 403.
- Scope is a queryset, never a URL parameter: `participants()` (whole event / supervised coordinators / own) `[Verified events/access.py:131-141]`, `coordinators()` `[111-121]`, `tutors()` `[123-129]`, `interviews()` `[143-146]`, `follow_ups()` `[148-159]`, `snapshots()` `[250-252]`; object checks `can_view_participant` `[162-165]`, `can_view_answer` `[175-209]`, `can_view_follow_up` `[218-219]`, `can_view_snapshot` `[245-248]`.
- `can_view_sensitive` is true only for an **active membership** with the flag `[Verified events/access.py:69-71]`; administrative or pastoral status alone never sets it (tested `test_pastor_status_does_not_grant_sensitive_access`, `test_admin_status_alone_is_not_enough_to_read_confidential_text`).
- The suites prove the role/scope model holds for the fixture (one church, two events). What they do **not** prove is covered in §8.

---

## 1. Permission matrix (role × action × resource × scope) as implemented

### 1.1 Event module (camp). Roles are per event; "church_admin" and "pastor" here mean the church role resolved into an event role.

| Resource / action | Superuser | church_admin (no membership) | Event ADMIN (membership) | Event PASTOR / church pastor | TUTOR | COORDINATOR | Anonymous | Kiosk session |
|---|---|---|---|---|---|---|---|---|
| Open any page of an event (`event_view`) | all events `[access.py:261,275-276]` | events of own church `[access.py:260-262]` | this event | this event (church pastor: unless a deactivated membership exists `[263-264]`) | this event | this event | 302 login `[common.py:28]` | 302 `kiosk:home` `[middleware.py:36-38]` |
| Create event | yes, in `Church.objects.all()[0]` (alphabetical) `[home.py:44-47; access.py:296-297]` | yes, first administered church `[home.py:44-47]` | **no** (not church admin) `[home.py:44-46]` | no | no | no | no | no |
| Event settings, procedures, stations, audit log, sessions | ADMIN role `[admin.py:976,1038,1052,1069]` | ADMIN | ADMIN | 403 | 403 | 403 | — | — |
| "Respuestas confidenciales" (event level) | yes | yes | yes | **yes** `[admin.py:1008]` | 403 | 403 | — | — |
| Team: add/edit members, assignments, approve/reject requests, regenerate code | ADMIN `[admin.py:526,645,685,549,598,615]` | ADMIN | ADMIN | read-only team page `[dashboards.py:430-441]` | 403 | 403 | — | — |
| Grant `can_view_sensitive` | to others only `[admin.py:529-530,658-661]` | same | same | no | no | no | — | — |
| Participants list (`/participantes/`) | whole event | whole event | whole event | whole event `[admin.py:161-163]` | team (`access.participants`) | 403 (own list is `/mis-camperos/`) `[coordinator.py:65-69]` | — | — |
| Participant detail (`@event_view()`) | scope | scope | scope | scope | team `[coordinator.py:230-232]` | own | — | — |
| Register / edit campero | any coordinator `[coordinator.py:161-167,263-267]` | same | same | 403 | 403 | own (coordinator forced to self `[coordinator.py:167]`; edit = own only `[access.py:167-170]`) | — | — |
| Withdraw / reassign campero (`ParticipantAdminForm`) | ADMIN `[coordinator.py:270]` | ADMIN | ADMIN | no | no | no | — | — |
| Guardian authorization record/withdraw | `can_edit_participant` `[coordinator.py:283-287]` | same | same | 403 | 403 | own | — | — |
| CSV import, campero list CSV, sample CSV | ADMIN `[admin.py:211,249,1126]` | ADMIN | ADMIN | 403 | 403 | 403 | — | — |
| **Answers CSV (every answer)** | ADMIN **and** `can_view_sensitive` `[admin.py:277-282]` → superuser/church admin without membership: **403** | 403 | only with flag | 403 | 403 | 403 | — | — |
| One campero's JSON export | ADMIN and flag `[admin.py:331-341]` | 403 | with flag | 403 | 403 | 403 | — | — |
| Delete campero data | ADMIN `[admin.py:440-442]` | yes | yes | 403 | 403 | 403 | — | — |
| Start / resume / take over / step / review / discard / hand over interview | `can_interview` (admin: any active campero) `[access.py:172-173; interview.py:163-167,301-307,369-377,452-456,484-489]` | same | same | 403 | 403 | own | — | — |
| Interview detail (read) | field rules `[interview.py:510-512; access.py:175-209]` | same | same | same, team/whole | team | own | — | — |
| Correct completed interview / decide "Por decidir" | `can_correct` `[access.py:211-216; interview.py:547-551; interviews.py:627]` | yes | yes | 403 | 403 | own | — | — |
| Correction history | `can_view_sensitive` only `[interview.py:541]` | no | with flag | with flag | with flag | with flag | — | — |
| Safety report (file) | anyone who can view the campero (`@event_view()`) `[interview.py:598-600]` | yes | yes | yes | team | own | — | — |
| Follow-ups list / detail | `access.follow_ups()`: whole / team / own `[dashboards.py:333-339,383-385]` | yes | yes | yes | team | own | — | — |
| Follow-up details & closing note | `can_view_sensitive` `[access.py:230-231; dashboards.py:388-389; forms.py:379-380; follow_up_detail.html:17-22]` | no | flag | flag | flag | flag | — | — |
| Safety reports: see existence / manage | admin or flag (`follow_ups` + `can_manage_follow_up`) `[access.py:155-158,224-225]`; reporter sees own exists | yes (no details) | yes | flag | flag | flag (reporter sees own) | — | — |
| Confidential referrals (from confidential answers) | flag only `[access.py:152-154]` (created_by is None `[interviews.py:564-572]`) | no | flag | flag | flag | flag | — | — |
| Reports: list, generate, detail, PDF, presentation | `LEADERS`; scopes: whole+every team / own team `[reports.py:41,70,97,115,145,153; access.py:234-243]` | event+teams | event+teams | event+teams | own team | 403 (`coord_progress` only `[coordinator.py:220-227]`) | — | — |
| Aggregate spiritual outcomes (released cutoffs) | `can_see_aggregate_outcomes` (admin/pastor) `[access.py:94-96; reports.py:239]` | yes | yes | yes | no | no | — | — |
| Backup paper list (registry + blank questionnaire) | `@event_view()` scope `[coordinator.py:120-128]` | whole | whole | whole | team | own | — | — |
| Questionnaires (church-wide): create, edit draft, publish, use | ADMIN; versions scoped `questionnaire__church=event.church` `[admin.py:724,811,833-834,837,911,925,953]` | yes | yes | 403 | 403 | 403 | — | — |
| Sedes of the church: add/edit/delete | `administered_churches(user).filter(pk=event.church_id)` `[admin.py:1145-1148,1177-1181,1193-1199]` | yes | **no** (403) | 403 | 403 | 403 | — | — |
| Sedes taking part in the event | ADMIN `[admin.py:1151-1158; forms.py:653-668]` | yes | yes | 403 | 403 | 403 | — | — |
| `/unirme/`, `/e/<code>/` | public; signed-in with access → role home `[join.py:192,218-235]` | — | — | — | — | — | yes | 302 kiosk |
| Kiosk `/estacion/*` | only with `KIOSK_KEY` in session `[kiosk.py:31-36; interviews.py:822-843]` | — | — | — | — | — | "No hay una entrevista abierta" | own interview: read steps, write answers while ACTIVE, review own answers, finish, heartbeat, timeout, unlock (needs the password of a user with `can_interview`) `[interviews.py:875-900]` |
| Church logo image | public, by church slug `[core/views.py:163-187]` | — | — | — | — | — | yes | yes (middleware exemption `[middleware.py:19-23,34-35]`) |

### 1.2 Core module (church / discipleship). The role is the **first** `UserChurchRole` of the user by id `[Verified core/access.py:20-23]`; the church is that row's church.

| Resource / action | church_admin | pastor | mentor | participant | superuser without church role | anonymous |
|---|---|---|---|---|---|---|
| Church settings (logo, notice, theme, module flags) | yes `[core/views.py:360-362]` | 403 | 403 | 403 | redirect landing with "no vinculada" `[core/views.py:144-146]` | 302 |
| Users list (names, e-mails, roles of this church) | yes | yes `[783-788]` | 403 | 403 | redirect | 302 |
| Participants list / detail (profile, phone, notes, follow-ups) | whole church `[core/access.py:60-72; views 548-550,765-767]` | whole church | assigned only | own dashboard only (`participant_detail` 403 `[core/tests.py:233-241]`) | redirect | 302 |
| Pastoral notes | all of church `[core/access.py:106-113]` | all | assigned participants and visibility `mentor_only`/`pastor_and_mentor` `[114-118]` | none `[109-110]` | — | — |
| Questions / answer / escalate / AI draft | church `[core/access.py:82-103]` | church | assigned | own | — | — |
| Invitations | all roles except admin `[core/forms.py:386-395,403-404]` | participants only if `leaders_can_invite_participants` `[core/views.py:315-316]` | same | 403 | — | token page public `[272-275]` |
| Paths / lessons | yes | yes `[861-938]` | 403 | own enrolled lessons `[411-421]` | — | — |
| Follow-up requests | church `[815-837]` | church | assigned/own participants | create own `[493-509]` | — | — |
| Django admin `/admin/` | `is_staff=True` → **login allowed**, no model permissions `[core/services.py:102; core/views.py:286; core/services.py:53-68]` | `is_staff=True` (via invitation) `[core/views.py:286]` | no | no | everything, every church `[core/admin.py:32-84; events/admin.py:17-77]` | login page |

Cells with **no code evidence** (no enforcement exists, vision only): "sede A vs sede B" (a `Sede` is a label on `EventParticipant` `[events/models.py:536]`; no queryset anywhere filters by sede), "group leader" (no group concept), "campero requests a group membership" (camperos have no account `[events/models.py:524-559]`), "event privilege with start/end dates" (only `is_active` `[events/models.py:387]`).

---

## 2. End-to-end traces

Format: entry link → URL → decorator → service → queryset (event? church? assignment?) → template (hide vs deny).

| Endpoint | Decorator / role check | Lookup and scope | Template behaviour |
|---|---|---|---|
| Participant detail `events:participant_detail` `/eventos/<slug>/camperos/<uuid>/` | `@event_view()` (any resolved role) `[coordinator.py:230]` | `get_object_or_404(access.participants(include_inactive=True), public_id=…)` → filtered by `event` and by assignment for tutor/coordinator `[coordinator.py:232; access.py:131-141]`; follow-ups through `access.follow_ups()` `[239]`; consents listed without their notes `[258; participant_detail.html has no `.notes`]` | server denies (404 on scope miss; tested `test_tutor_cannot_open_unassigned_participant_by_url`, `test_coordinator_cannot_open_other_coordinators_camper`, `test_cross_event_object_is_not_reachable_through_another_event_url`) |
| Interview detail / answers `events:interview_detail` | `@event_view()` `[interview.py:510]` | `_scoped_interview`: `Interview` by `public_id` **and** `event=access.event`, then `can_view_participant` else 404 `[interview.py:85-93]`; rows via `review_rows(access, interview)` applying `can_view_answer` per question, hiding conditional rows whose parent is hidden `[interviews.py:1020-1051]`; `consulta_confidencial` audited when a confidential answer is shown `[interview.py:526-531]` | per-row: hidden rows render only the sensitivity label, same whether answered or not `[detail.html:36]`; history only with flag `[interview.py:541]` and shows question/date/reason, no previous values `[detail.html:47-56]` |
| Interview step / review (capture) | `@event_view(COORDINATOR, ADMIN)` + `can_interview` + claim token `[interview.py:301-314,369-384]` | `_scoped_interview`; `save_step`/`finalize` under `select_for_update` with claim + revision checks `[interviews.py:415-430,441-476,518-576]`; confidential question already answered by the campero → redirect `[interview.py:322-326]` | deny |
| Answers CSV `events:answers_export` | `@event_view(ADMIN)` + `access.can_view_sensitive` else 403 `[admin.py:277-282]` | `answers_rows(event, links)`: **every** campero of the event (inactive too), every answer of every linked questionnaire, guardian flag, coordinator name `[exports.py:96-127]`; cells neutralised `[exports.py:33-36]`; audited with counts only `[admin.py:292-301]` | n/a |
| One campero JSON `events:participant_data_export` | ADMIN + flag `[admin.py:331-341]` | `access.participants(include_inactive=True)` then full record incl. consents' `notes` (withdrawal reasons) `[admin.py:344-372]`; audited | n/a |
| Report list / detail / PDF / presentation | `@event_view(*LEADERS)` `[reports.py:41,97,115,145]` | `report_scopes()` → `evento` + each team for admin/pastor, own team for tutor `[access.py:234-243]`; `_snapshot_or_404`: `public_id` + `event=access.event` + `can_view_snapshot` (scope **now**) `[reports.py:88-94]`; detail/PDF payload trimmed by `visible_payload` (safety/confidential counts, outcomes, tutor's coordinators) `[reports.py:230-245]`; presentation gets raw `snapshot.data` but only through `build_deck`, a whitelist serializer with no names/answers `[reports.py:145-150; presentation.py:1-7,63-155]`; live presentation `build_payload(scope)` with `include_safety=False` `[reports.py:163; metrics.py:735]` | deny (404); tested `test_tutor_cannot_read_event_or_other_team_snapshots`, `test_saved_report_does_not_outlive_access`, `test_tutor_old_snapshot_does_not_show_coordinator_moved_out_of_scope` |
| Follow-ups list / detail / assign | `@event_view(*LEADERS, COORDINATOR)` `[dashboards.py:333,383,411]` | `access.follow_ups()` (event + assignment; confidential and safety filtered by flag/admin/reporter) `[access.py:148-159]`; `can_manage_follow_up` for POST `[dashboards.py:391-392,416-417]`; form drops `resolution_note` unless `can_read` `[forms.py:379-380]`; assignee queryset limited to active non-coordinators + the campero's coordinator `[forms.py:358-366]` | details block rendered only `{% if can_read %}` `[follow_up_detail.html:17-22]`; list templates never print `details` (grep) |
| Coordinator "Mis camperos" | `@event_view(COORDINATOR, ADMIN)` `[coordinator.py:65]` | `access.participants()` → `coordinator=self.membership` `[access.py:139-140]`; search filters applied on top `[coordinator.py:69-80]` | — |
| Tutor team views (`tutor_home`, `coordinators`, `coordinator_detail`, `interviews`, `pending`) | `TUTOR` / `LEADERS` `[dashboards.py:90,113,127,144,189]` | `scope_for_access` → `team_scope(tutor)` `[metrics.py:243-248,219-220]`; `coordinator_detail`: `get_object_or_404(access.coordinators(), pk=…)` `[dashboards.py:129]`; `?coordinador=` filter only narrows `access.participants()` `[dashboards.py:154-155]` | deny; tested `test_tutor_cannot_open_unassigned_coordinator_by_url`, `test_tutor_filter_cannot_widen_scope` |
| Admin team / role change / `can_view_sensitive` grant | `@event_view(ADMIN)` `[admin.py:526,645]` | `member_edit`: `EventMembership` by pk **and** `event=event` `[admin.py:647]`; self-deactivation and self-grant refused `[658-661]`; `member_add` self-grant refused by e-mail match `[529-530]`; `add_member` reuses any account by e-mail (any church) `[team.py:79-97]`; audit `integrante_modificado` / `acceso_confidencial` `[673-679; team.py:124-130]`; tutor querysets event-scoped `[forms.py:415,493]` | deny; tested `test_admin_handlers_reject_other_event_ids` |
| Kiosk start / answer / unlock / finish | hand-over: ADMIN/COORDINATOR + claim `[interview.py:452-480]`; kiosk views: no login, server-side `ParticipantSession` `[kiosk.py:31-36]` | hand-over logs the coordinator out (`logout()` flushes), then stores `{session public_id, token, claim}` in a fresh anonymous session `[interviews.py:794-799]`; every kiosk request re-reads the `ParticipantSession`, compares SHA-256 hashes with `compare_digest`, applies 10-min idle / 60-min max `[interviews.py:822-843; models.py:1027-1028,956-957]`; writes require `ACTIVE` state and the kiosk's claim `[interviews.py:846-865]`; unlock throttled per session in the DB (5/h, 60 s doubling to 15 min), then `authenticate(username or started_by)` and `resolve_access(...).can_interview(participant)` `[interviews.py:883-900]`, `login()` rotates the key `[911]`; middleware confines the browser to `/estacion/` `[middleware.py:26-39]` | welcome shows only the first name `[kiosk/welcome.html:6]`; tested (`HeldKioskTests`, `KioskRateLimitRaceTests`, `test_participant_mode_cannot_reach_privileged_pages`) |
| Join request `/unirme/`, `/e/<code>/` | none (public) `[join.py:192,218]` | `find_event_by_key`: code, or slug/name while `join_by_name` `[join.py:25-48]`; archived never; throttle keys `join:<session_key>` and `join-request:<session_key>` with the session **created on demand** `[join.py:172-176,196,227,243]`; one pending request per e-mail, 50 per event, event row locked `[join.py:254-279]`; no account created | page shows event name, church, dates `[join.py:213-215,236]` |
| Church settings and logo | `@role_required(CHURCH_ADMIN)` → `get_user_role(user).church` `[core/views.py:360-362]`; logo validated by bytes (PNG only, ≤512 KB, chunk walk, CRC, no acTL, Pillow verify+load), renamed `[core/logos.py:72-179; core/forms.py:298-309]`; served only by `church_logo`, `MEDIA_ROOT` never static `[core/views.py:163-187; settings.py:137-139]`; Django admin excludes the logo fields `[core/admin.py:36-38]` | public image by slug (by design) | — |
| Discipleship pastoral notes / participant data | `participant_detail`: `visible_participant_profiles` (church + role) `[core/views.py:548-550; core/access.py:60-72]`; notes via `visible_notes` `[584; core/access.py:106-119]`; `assign_mentor`, paths, lessons: `church=get_user_role(...).church` `[793,883,897,904,918,929]`; `complete_follow_up`: church + mentor check `[840-846]` | deny (404); tested `test_mentor_can_only_see_assigned_participants`, `test_participant_cannot_see_pastoral_notes_or_ai_drafts` |
| Django admin `/admin/` | Django's own `AdminSite.login` (not `ThrottledLoginView`) `[emmaus/urls.py:11 vs 12]`; `is_staff` required | **no tenant scoping** in any `ModelAdmin`; `core.admin` registers `PastoralNote`, `ReflectionAnswer`, `ParticipantQuestion`, `QuestionResponse`, `AIDraft`, `UserChurchRole` with full CRUD `[core/admin.py:41-84]`; `EventMembershipAdmin` edits `can_view_sensitive` **without** `audit.record` `[events/admin.py:33-36]`; `EventAdmin` edits `confidential_level`, `privacy_notice`, `retention_days` and can link a draft questionnaire of **any** church `[events/admin.py:17-30]`; `Answer` is not registered and `InterviewAdmin` is read-only `[events/admin.py:47-54]`; `AuditEventAdmin` blocks add/change but not delete `[68-77]` | — |

---

## 3. Object-level access (IDOR) — every id/public_id/slug lookup

| Lookup | Where | Scoped by | Verdict |
|---|---|---|---|
| `Event` by `slug` | `event_view` `[common.py:31]` | global slug (unique `[models.py:142]`), then `resolve_access` by the **event's** church `[access.py:260]` | OK (access decides) |
| `EventParticipant` by `public_id` | `coordinator.py:232,265,284`; `interview.py:165,600`; `admin.py:339,442` | `access.participants(...)` (event + assignment) | OK |
| `Interview` by `public_id` | `_scoped_interview` `[interview.py:85-93]`; `admin.sessions` `[1085]` | `event=` + `can_view_participant` / `event=event` | OK |
| `EventMembership` by pk | `dashboards.py:129` (`access.coordinators()`), `admin.py:647` (`event=`), `metrics.scope_from_key` `[227-240]` (`event=`), assignments `[admin.py:690,699]` (event dict) | event | OK (tested `HeldIdorTests`) |
| `FollowUp` by pk | `dashboards.py:385,415` | `access.follow_ups()` | OK |
| `ReportSnapshot` by `public_id` | `reports.py:89-91` | `event=` + scope today | OK |
| `AccessRequest` by pk | `admin.py:558,602` | `event=` | OK (tested `test_a_request_of_another_event_is_not_reachable_from_this_one`) |
| `InterviewStation` by pk | `admin.py:1046`; `current_station` `[common.py:105]` | `event=` / `event.stations` | OK |
| `ParticipantSession` by `public_id` | `admin.py:1081`; kiosk `[interviews.py:826-831]` | `interview__event=` / token hash | OK |
| `QuestionnaireVersion`, `Section`, `Question`, `QuestionOption` | `admin.py:834,875,881,931,934,956`; `forms.py:545` | `questionnaire__church=event.church`, `section__version=version` | OK (tested `test_delete_question_with_foreign_id_is_a_clean_rejection`, `test_another_churchs_questionnaire_cannot_be_used`) |
| `Sede` by pk | `admin.py:1181,1199`; `forms.py:234-244,668`; import `[participants.py:278,373]` | `church_id=event.church_id` / `event.allowed_sedes()` | OK (tested `test_another_churchs_sede_is_not_found`, `test_confirm_takes_only_a_sede_of_this_church`) |
| `User` by e-mail (`account_for_email`) | `team.py:49-50`, used by `add_member`, `describe_account`, approvals | **global** | By design (invite by e-mail); see F7/F8 |
| Core: `ParticipantProfile`, `ParticipantQuestion`, `PastoralNote`, `AIDraft`, `FollowUpRequest`, `DiscipleshipPath`, `Lesson` by pk | `core/views.py:550,613,661,793,843,883,897,904,918,929` | church of the user's first role (+ assignment for mentor/participant) | OK |
| `Invitation` by token | `core/views.py:273` | global secret token (`token_urlsafe(32)`, 14 days, single use `[core/models.py:143-199]`) | OK |
| `Church` by slug (logo) | `core/views.py:174` | global, public image | By design |
| `find_event_by_key` | `join.py:25-48` | global, public, archived excluded | By design |

**No unscoped id lookup was found in the event or core views.** The IDOR surface is the public ones by design (logo, join code/slug, invitation token).

---

## 4. Cross-church (tenant) isolation

- Event pages: a church admin of church A gets `resolve_access(...) is None` → 403 on church B's events unless B's team added them by e-mail `[Verified access.py:260-269]`; `accessible_events` lists only own-church events + active memberships `[access.py:272-290]`. Tested for roles in one church (`test_roles_are_event_scoped`, `test_outsider_gets_403_for_event`) and for a foreign church admin being addable as tutor (`test_another_churchs_admin_has_no_access_here_and_can_be_added` `[test_join_code.py:815-823]`). **No test opens church B's event URL as church A's admin and asserts 403** (`[Inferred: from code, not asserted]`).
- Questionnaires, sedes, versions: scoped by `event.church` `[admin.py:834,1118,1163,1181; forms.py:668]`; `use_version` refuses another church's questionnaire (`test_another_churchs_questionnaire_cannot_be_used`).
- Team members: `add_member` reuses any account by e-mail, across churches `[team.py:80]`. The added user's name is **not** rewritten for an existing account (`full_name_to_parts` only on create `[team.py:83-86]`). A church A admin can therefore enrol church B's people into A's event (they gain A-scoped access only) and learn that the e-mail exists and belongs to "otra iglesia" `[team.py:61-69; admin.py:513-521]`.
- `/setup/` only while no `Church` exists `[core/views.py:199-201]`; `landing`/anonymous bar use `first_active_church` (branding of the oldest active church to everyone) `[core/views.py:195; core/context_processors.py:9-11,16-18]`. `[Inferred: a second tenant's visitors see tenant 1's logo/name on login.]`
- Superuser: ADMIN in every event, `administered_churches` = all `[access.py:261,275-276,296-297]`; `can_view_sensitive` only via a membership; Django admin unrestricted (see F1). A superuser **without** a `UserChurchRole` is redirected out of every core page `[core/views.py:144-146]`.
- Core module: the church context is the first role row by id `[core/access.py:20-23]`; all querysets filter `church=role_record.church` `[core/access.py:65,87,111,127]`. Isolation holds per request, but a two-church person is bound to one church with no way to switch (F4).
- Event creation: no church selector; `administered_churches(user)[0]` `[home.py:44-47]` (F3).

---

## 5. Sensitive data boundaries

- **Confidential answers (who reads)** `[Verified access.py:175-209]`: `general` → anyone who can see the campero; `sensible` → flag, or the assigned coordinator, or an admin who conducted that interview; `confidencial` → by `stricter(interview.confidential_level, event.confidential_level)`: `cuidado` → flag, or the assigned coordinator only `during_capture` of an assisted, in-progress interview; `coordinacion` → as `sensible`; `todo_el_equipo` → anyone who can see the campero. The interview copies the event's level when it starts `[interviews.py:343; models.py:879-885]`, so **widening reaches only later interviews and narrowing applies at once** — proven by `ConfidentialLevelTests` (`test_opening_the_level_does_not_reach_answers_given_before`, `test_closing_the_level_applies_at_once`, `test_an_interview_takes_the_events_level_when_it_starts` `[test_multi_questionnaires_admin.py:442-459]`, run: pass). Who may set the level: ADMIN **and PASTOR**, incl. a church pastor with no membership `[admin.py:1008]`, audited `acceso_confidencial` `[1020]`. The kiosk/handoff texts follow the interview's level `[kiosk/_who_reads.html; interview/_question.html:10,28; handoff.html:11]`, but the assisted-interview notice is the free-text `event.privacy_notice` `[interview/start.html:36; forms.py:123]` with only a reminder to align it `[admin.py:1021-1022]`.
- **Follow-ups**: `details` and `resolution_note` need the flag `[access.py:230-231]`; confidential referrals (from confidential answers) are created with `created_by=None` and `confidential=True` so only the flag sees them `[interviews.py:561-572; access.py:152-154]`; safety reports visible to flag/admin/reporter, details flag-only `[access.py:155-158,224-225]`; done page never mentions the confidential referral `[interview.py:428]` (tested).
- **Pastoral notes (core)**: visibility enum enforced in `visible_notes` `[core/access.py:106-119]`; participants never see notes or AI drafts (tested).
- **Minors / guardians**: `is_minor = age < event.adult_age` `[models.py:598-599]`; guardian authorization required to start when the event asks, assent recorded, consents are `ConsentRecord` rows with actor `[interviews.py:218-272]`; withdrawal keeps a reason on the record (export only) `[participants.py:159-191]`; kiosk shows first name only; `missing_guardian` is a count in payloads `[metrics.py:502-521]`. No public page lists participants (join pages show the event only). The authorization itself is an attestation, no document stored `[Verified by absence of a file field on ConsentRecord models.py:602-636]`.
- **Exports/PDF contents**: CSV of answers = everything (§2); campero list CSV = registry only (tested `test_admin_export_is_audited_and_has_no_answers`); JSON = registry + answers + consent notes; PDF = aggregates, team labels, coordinator names, recommendations, no answers `[pdf.py grep; presentation.py:1-7]`; deck = counts suppressed/rounded by `min_group_size` with released cells `[reports.py:36-47,126-135; metrics.py:540-553]` (tested `DifferencingTests`).
- **Audit log contents**: `summary` ≤300 chars with `#code`, member/sede names, requester names; `metadata` holds role transitions, status, counts, `fields` list, `level`, interview public_id — **never answers, e-mails or details** `[audit.py:8-22; all `audit.record` callers grepped]`; confidential rows masked as "Movimiento confidencial" for admins without the flag `[audit.py:66-93; admin.py:1061; dashboards.py:679]`.
- **Logs / exceptions**: console logging, `django.request` at WARNING (method/path/status) `[settings.py:171-178]`; request bodies never logged; snapshot failures store `str(exc)[:500]` on the row and log a traceback `[reports.py:136-153]` — could contain SQL/table names, not answers `[Inferred]`; `?q=` search terms (camper names) reach proxy logs (documented). E-mails carry no names/details `[notifications.py:26-38; core/tests.py:561-592]`. The AI draft sends a participant's **question text** to an external LLM when `LLM_ENABLED` (default off) `[core/services.py:279-320; settings.py:188-191]`.
- **Retention / deletion**: per-event `retention_days` (30–3650) `[models.py:212-216]`; scheduler purges finished events after 03:00 local, refusing while a safety report or confidential referral is open `[run_report_cutoffs.py:37-62; purge_expired_event_data.py:16-50]`; per-campero deletion by typed name, cascades answers/consents/follow-ups, audit keeps only the number `[admin.py:399-450; participants.py:120-129]`; discarded interviews delete answers `[interviews.py:593]`. Backups contain everything (documented). Import previews (names/ages, incl. minors) sit in the DB session store until confirm/cancel `[admin.py:213-231]`; expired sessions are deleted by the scheduler's `SessionStore.clear_expired()` on every pass `[Verified run_report_cutoffs.py:64-113]`, as `docs/privacy-and-safeguarding.md` says (an earlier draft of this report claimed otherwise; corrected in the synthesis).

---

## 6. Authentication and sessions

| Control | Implementation | Evidence / test |
|---|---|---|
| Login throttle | `ThrottledLoginView`: per lower-cased username, 8 per hour, blocks 5 min doubling to 60 min, DB row lock, fails closed `[core/views.py:941-961; throttle.py:21-50]` | `test_login_is_throttled_per_account`, `test_lockouts_escalate_instead_of_resetting`, `test_throttle_fails_closed_when_the_counter_cannot_be_written` |
| Django admin login | `admin.site.urls` → Django `LoginView`, **no throttle** `[emmaus/urls.py:11; grep: no override]` | none |
| Password reset | 3 per address per hour, same page either way; activation mail for accounts without a usable password, only when a real backend is configured `[core/views.py:964-1015]` | `core/test_camp_admin.py:69-125` |
| Password rules | Django's four validators `[settings.py:119-124]` | `core/test_audit_lenguaje.py:37-62` |
| Session cookie | DB sessions (default engine `[settings.py: no SESSION_ENGINE]`), `SESSION_EXPIRE_AT_BROWSER_CLOSE`, 12 h, `SameSite=Lax`, `Secure` unless DEBUG, HttpOnly (Django default) `[settings.py:160-162,194]` | — |
| Idle logout | `StaffIdleLogoutMiddleware` 30 min, background `X-Requested-With: fetch` not counted; `still_here` ping (GET, `login_required`) `[middleware.py:42-64; session.py:26-40]` | `test_dashboard_auto_refresh_does_not_keep_a_session_alive`, `test_audit_a11y_ui.py:167-273` |
| Kiosk tokens | `secrets.token_urlsafe(32)`, SHA-256 stored, `compare_digest`, 10 min idle / 60 min max, unlock throttle per session in DB, `login()` rotates key, old cookie dead `[models.py:49-54,1013,1027-1028; interviews.py:65-69,771-799,822-843,875-922]` | `HeldKioskTests`, `KioskRateLimitRaceTests`, `RevokedSessionUnlockTests`, `test_old_waiting_kiosk_stops_accepting_unlocks` |
| Kiosk confinement | middleware redirects every non-`/estacion/` path (incl. `/admin/`, logout) `[middleware.py:26-39]` | `test_kiosk_post_to_privileged_endpoint_is_confined`, `test_participant_mode_cannot_reach_privileged_pages` |
| CSRF | `CsrfViewMiddleware`, `CSRF_TRUSTED_ORIGINS` from env, custom failure page `[settings.py:55,35,196]` | `core/test_camp_flow.py:253` |
| Headers | CSP (`script-src 'self'`, `style-src 'unsafe-inline'`, `frame-ancestors 'none'`), `no-store`, `X-Frame-Options DENY`, `Referrer-Policy same-origin`, Permissions-Policy `[middleware.py:67-88; settings.py:164-165]` | `core/test_church_logo.py:385,535` |
| TLS settings | `SECURE_HSTS_SECONDS` default 0, `SECURE_SSL_REDIRECT` default False (deployment doc tells to set both), `SECURE_PROXY_SSL_HEADER` unconditional `[settings.py:166,193,197]`; `check --deploy` in the quality gate `[scripts/agents/quality-gate.sh:7]` | `[Unknown: production values live in .env, not read]` |
| Secret key / DEBUG | DEBUG default False; missing `SECRET_KEY` raises outside DEBUG/tests `[settings.py:25-32]` | — |
| Demo accounts | deploy check warns if `@example.org`/`@demo.invalid` accounts are active; `deactivate_demo_accounts` command `[events/checks.py:5-28; deactivate_demo_accounts.py:13-19]`; seed commands never print a given password `[seed_camp_demo.py:86-111; seed_dev_data.py:38-92]` | `core/tests.py:594-612`, `test_camp_ops.py` |
| Open redirects | `safe_next` / `allowlisted_next` `[common.py:123-138]` | `RedirectTests` |

---

## 7. Validation, upload and injection surfaces

- **CSV import**: ≤2 MB, `.csv/.txt` name check, UTF-8-sig then latin-1, `csv.Sniffer` on 2 KB, ≤1500 rows, per-row validation, coordinator/sede matched only inside the event/church, re-checked on confirm `[forms.py:500-513; participants.py:254-346,367-398]`. Formula cells neutralised on every CSV output `[exports.py:33-36; admin.py:261,1141]` (tested).
- **Logo**: byte-level PNG validation, size/dimension/ratio caps, animated PNG refused, trailing data refused, Pillow verify+load, renamed on store, never trusted on read `[core/logos.py:33-40,72-179,182-211]`; 66 tests in `core/test_church_logo.py` incl. hostile files and the Django-admin bypass (`test_the_django_admin_cannot_upload_around_the_checks`).
- **Forms**: all querysets for FK choices are event/church bound (`ParticipantForm` coordinator/sede `[forms.py:221-244]`, `MemberForm`/`MembershipEditForm` tutor `[415,493]`, `FollowUp*Form` assignee `[358-366]`, `EventSedesForm` `[668]`, `QuestionForm.condition_question` `[545]`, `ReportScopeForm` `[585]`, core `InvitationForm`/`FollowUpRequestForm`/`AssignMentorForm` `[core/forms.py:374-383,577-585,614]`). `EventForm` reserves `nuevo`/`cuenta` slugs `[forms.py:47,67]`. Integer parameters are checked with `isascii() and isdigit()` before `int()` `[e.g. admin.py:172,179,188; dashboards.py:154; metrics.py:231]`.
- **Injection**: no raw SQL, `.extra`, `RawSQL` or cursors `[Verified by grep]`; no `|safe` or `autoescape off` in HTML templates (only the plain-text reset e-mail `[password_reset_email.txt:1]`); `mark_safe` only on fragments built from integers/escaped strings `[events/templatetags/emmaus.py:137-159; events/charts.py:185-278 (labels through escape()); core/templatetags/brand.py:91-115 (format_html)]`; `format_html` in `dashboards.py:610-613`. PDF survives markup-like names (tested).

---

## 8. Findings

Severity rationale: Likelihood × impact on confidential participant data (the asset the invariants protect). Recommendations are incremental.

### F1 — Church admins and pastors are Django `is_staff`; the admin login is unthrottled and the admin is not tenant-scoped — **Severity: High**
- Evidence: `create_initial_church` sets `is_staff=True` `[Verified core/services.py:96-103]`; `accept_invitation` sets `is_staff` for `CHURCH_ADMIN` and `PASTOR` `[Verified core/views.py:280-287]`; `assign_role` puts users in a `Group` named after the role, and no code or migration grants any `Permission` `[Verified core/services.py:53-68; grep "Permission" → none]`; `/admin/` is Django's `AdminSite` with its own `LoginView`, outside `ThrottledLoginView` `[Verified emmaus/urls.py:11-12; grep: no override]`; no `ModelAdmin` scopes by church `[Verified core/admin.py:32-84; events/admin.py:17-77]`.
- Current behaviour: every church admin/pastor can sign in at `/admin/login/` (empty index today) and that form accepts **unlimited password guesses** against those accounts; e-mails are the usernames and are shown to teammates. Any permission later granted to the `church_admin`/`pastor` groups through the admin, or any superuser account, exposes **every church's** pastoral notes, questions, memberships (`can_view_sensitive` edits bypass the audit log), event levels and retention — the privacy doc acknowledges only the superuser case.
- Impact: takeover of a church-admin account ⇒ full church scope and, via a second account (F7), every confidential answer.
- Recommendation: (1) stop setting `is_staff` for church roles and clear it on existing non-superusers (data migration); (2) either put `ThrottledLoginView` in front of `admin:login` or block `/admin/` at the proxy `[Unknown: whether the proxy already does]`; (3) keep the admin superuser-only and write `audit.record` from the two sensitive `ModelAdmin` save paths. Tests: `test_church_roles_are_not_django_staff`, `test_admin_login_is_throttled_like_the_app_login`, `test_django_admin_edit_of_can_view_sensitive_is_audited`.

### F2 — A pastor (even a church pastor with no membership) can widen "Respuestas confidenciales" and read confidential answers of later interviews without `can_view_sensitive` — **Severity: Medium**
- Evidence: `confidential_level` is `@event_view(ADMIN, PASTOR)` `[Verified admin.py:1008-1035]`; at `todo_el_equipo` `can_view_answer` returns True for anyone who sees the campero `[Verified access.py:199-201]`; audited `[admin.py:1020]`; older interviews protected `[access.py:199; tests pass]`. The assisted-interview notice shown at start is free text (`event.privacy_notice`) that does not follow the level `[Verified interview/start.html:36; forms.py:123; admin.py:1021-1022]`, while the kiosk text does.
- Rationale: documented and audited, but it is a single-person, non-two-party path by which "pastoral status alone" reaches confidential data, contradicting invariant 4's spirit; the consent text can diverge from the real readers.
- Recommendation: require the ADMIN role (or a second person, as for the flag) to open the level; derive the "Confidencial" sentence of the start screen from the level (as `_who_reads.html` does) instead of relying on the free text. Tests: `test_a_church_pastor_without_membership_cannot_widen_the_level`, `test_start_screen_names_the_readers_of_the_current_level`.

### F3 — Event creation assigns the church implicitly (`churches[0]`) — **Severity: Medium (Low today, High with two tenants)**
- Evidence: `church = churches[0]` from `administered_churches(user)` ordered by `Church.Meta.ordering = ["name"]` `[Verified home.py:44-47; access.py:293-298; core/models.py:121]`; `EventForm` has no `church` field `[forms.py:51]`.
- Behaviour: a superuser, or an admin of two churches, creates every event in the alphabetically first church; its admins and pastors then own that camp's data.
- Recommendation: explicit church choice when `administered_churches().count() > 1`; test `test_event_create_asks_for_the_church_when_several_are_administered`.

### F4 — Core module binds a person to the first church role by id — **Severity: Medium (vision blocker, not a leak)**
- Evidence: `get_user_role` = `church_roles.order_by("id").first()` `[Verified core/access.py:20-23]`; every core view and `visible_*` queryset uses it `[core/access.py:60-131; core/views.py:139-153]`; `UserChurchRole` is unique per (church, user) `[core/models.py:136]` so several churches are possible.
- Behaviour: J3 ("one person, multiple roles") fails: a participant in church A later made admin of church B gets 403 on B's admin pages; a B-admin invited as participant in A keeps the B context. Only `user_list` was patched (`test_user_list_uses_this_churchs_role`).
- Recommendation: a session-held `current_church` validated against `church_roles` with a switcher; tests for two-church users on every core page.

### F5 — Public join endpoints are throttled per cookie that the server hands out on demand — **Severity: Low-Medium**
- Evidence: `_session_key` saves a new session when none exists and the key is `join:<session_key>` `[Verified join.py:172-176,196,227,243]`; names/slugs are matchable while `join_by_name` (default True) `[join.py:40-48; models.py:153]`; 50 pending requests cap per event `[join.py:259-260]`. Tests use one cookie jar (`test_searches_are_limited_per_browser_with_a_bounded_key`, `test_requests_are_limited_per_browser`).
- Behaviour: a cookie-less client gets a fresh budget per request: enumeration of event names/churches/dates, filling the request list (denial of the join feature), and unbounded `django_session` + `AuthThrottle` rows (`purge_expired` trims throttles; no `clearsessions` found).
- Recommendation: add an IP-keyed throttle (trusting the proxy's forwarded header) and avoid creating a session on misses; test `test_join_search_without_a_cookie_is_still_limited`.

### F6 — No IP-level rate limit anywhere; login throttle is per account — **Severity: Low-Medium** (documented)
- Evidence: `[Verified core/views.py:949-951]`; privacy doc "Riesgos que siguen". Combined with F1 this is the main brute-force surface. Recommendation: proxy or middleware limit per IP+path; keep the per-account lock.

### F7 — On-screen activation links let an event admin bootstrap a second identity that holds `can_view_sensitive` — **Severity: Medium (insider)**
- Evidence: `member_add` accepts `can_view_sensitive` for a new e-mail; `_member_added` renders the password-set link on screen when `is_new` or `_can_issue_activation_link` `[Verified admin.py:482-512,526-542,476-479]`; the self-grant guard only compares the typed e-mail with `request.user.email` `[529-530]`; `answers_export` needs only ADMIN + flag `[277-282]`.
- Behaviour: an admin creates `cuidado2@…`, grants the flag, opens the link, sets the password, logs in and reads/exports everything; audit shows the grant and the reads under the puppet account (attribution by inference only).
- Recommendation: refuse `can_view_sensitive` on accounts without a usable password (grant becomes possible only after the person set their own password from an e-mailed link), or require delivery by e-mail for flagged accounts; test `test_sensitive_access_cannot_be_granted_to_an_account_without_a_password`.

### F8 — Account-existence and other-church oracle for event admins — **Severity: Low** (by design)
- Evidence: `describe_account` returns name and "otra iglesia" `[Verified team.py:53-69]`; `_member_added` messages differ `[admin.py:513-523]`. Recommendation: in multi-tenant, show only "ya tiene cuenta" without the church.

### F9 — Archived events stay reachable by URL — **Severity: Low**
- Evidence: `event_view` never checks `event.status` `[Verified common.py:31-34]`; `accessible_events` and `find_event_by_key` exclude archived `[access.py:287; join.py:36]`. Recommendation: 404/closed page for archived events except superusers; test.

### F10 — TLS/proxy hardening depends on deployment values — **Severity: Low**
- Evidence: HSTS 0 and SSL redirect False by default, `SECURE_PROXY_SSL_HEADER` always trusted `[Verified settings.py:166,193,197]`; docs instruct the values `[docs/deployment.md:87-88,194-197]`. `[Unknown: production .env; whether gunicorn's port is reachable without the proxy]`. Recommendation: make the gate fail on `check --deploy` warnings W004/W008 in production mode.

### F11 — Import previews and kiosk claims live in the session store — **Withdrawn (synthesis correction)**
- The original finding said no session cleanup existed because `clearsessions` is not called. That was wrong: the scheduler's `run_once` calls `import_module(settings.SESSION_ENGINE).SessionStore.clear_expired()` on every pass `[Verified run_report_cutoffs.py:64-113; see A §1.11]`, which is what `clearsessions` does. The privacy doc is correct. Remaining recommendation (Low): a test that an expired `Session` row disappears after one scheduler pass, since the call has no coverage.

### F12 — Participant questions go to an external LLM when enabled — **Severity: Low** (opt-in)
- Evidence: `[Verified core/services.py:279-320]`; invariant 7 covers "answers"; a participant's question is comparably sensitive. Recommendation: church-level consent text and a per-church switch (today only server-level).

### F13 — Safety reports can be assigned to someone who cannot see them — **Severity: Low** (workflow)
- Evidence: assignee queryset = every active non-coordinator member `[Verified forms.py:347-366]`; visibility needs flag/admin/reporter `[access.py:155-158]`. Recommendation: restrict assignees of SAFETY/confidential rows to flag holders; test.

### Controls verified as sound (no finding)
Object scoping (§3), field-level answer rules and conditional-row hiding, confidential referrals, follow-up details gating, report scoping with "today's scope", presentation whitelist and k-suppression, kiosk token/claim model and confinement, unlock and login throttles (DB-backed, fail-closed), CSV formula neutralisation, logo validation, open-redirect guards, audit log contents and masking, per-campero deletion and retention purge, demo-account check.

---

## 9. Tests as evidence, and the missing ones

### 9.1 Existing proof (names; all in `events/tests/` unless noted)
- Role/scope: `test_access.py` (26: church admin everywhere, event-scoped roles, inactive membership, pastor/admin not sensitive, tutor/coordinator scopes, URL/filter widening, cross-event 404, role pages 403, snapshots scope, exports). `test_security_review.py` (43: account takeover via links, conditional-row leaks, supervised review leak, differencing, unlock race/cookies/revocation, redirects, CSV formulas, snapshot scope, IDOR on admin handlers, tutor cross-team, pastor safety signal, assignment lost update, login throttle, confidential referral, decision flip, malformed ids, handback name, done page, PDF markup, deactivated membership vs church pastor, throttle escalation, idle logout on auto-refresh, removed pastor not sent, fail-closed throttle, old kiosk).
- Confidential level: `test_multi_questionnaires_admin.py::ConfidentialLevelTests` (opening reaches later interviews only, closing at once, level copied at start, page for ADMIN/PASTOR only, labels).
- Cross-church: `test_sedes.py` (`test_another_churchs_sede_is_not_found`, `test_confirm_takes_only_a_sede_of_this_church`, `test_only_administrators`, `test_an_administrator_of_one_event_only_chooses_sedes_and_never_edits_them`); `test_multi_questionnaires.py::test_another_churchs_questionnaire_cannot_be_used`; `test_join_code.py` (`test_a_church_pastor_without_membership_is_not_demoted`, `test_a_superuser_is_not_touched`, `test_another_churchs_admin_has_no_access_here_and_can_be_added`, `test_a_request_of_another_event_is_not_reachable_from_this_one`, `test_only_the_administration_decides`); `test_church_logo.py::test_each_event_shows_its_own_church`; `core/test_rotulo_gestion.py::test_user_list_uses_this_churchs_role`.
- Core scoping: `core/tests.py:233-287` (participant own only, no notes/AI drafts, mentor assigned only, pastor whole church); e-mails without content `core/tests.py:561-592`.
- Kiosk: `test_interviews.py` (`test_hand_over_logs_the_coordinator_out`, `test_participant_mode_cannot_reach_privileged_pages`, `test_backup_list_is_scoped_and_has_no_answers`), `test_camp_interviews.py` (claims, liberar, discard), `test_security_review.py::HeldKioskTests`.
- Auth/session: `core/test_camp_admin.py:69-125` (reset throttle, activation mail, no log of links), `core/test_camp_flow.py:245-267` (bad host, CSRF page), `test_audit_a11y_ui.py:167-273` (idle logout/still_here).
- Upload: `core/test_church_logo.py` (66).

### 9.2 Controls with **no** test (and the exact test to add)
| Control / scenario | Proposed test (file: `events/tests/test_tenant_isolation.py` unless noted) | Asserts |
|---|---|---|
| Church A cannot reach church B by id/slug tampering (vision J6) | `test_church_a_admin_cannot_open_church_b_event_or_objects` | Fixture with church B, event B, participant B, membership B, sede B, version B. As A's church admin: GET `/eventos/<slugB>/` → 403; under `<slugA>`: `participant_detail(pB.public_id)` 404, `member_edit(mB.id)` 404, `sede_edit(sedeB)` 404, `version_detail(versionB)` 404, POST `sedes` with sedeB → not set, `coordinator_detail(mB)` 404; `accessible_events(adminA)` excludes event B; `answers_export` of slug B → 403. |
| Django admin not an entry point for church roles (F1) | `core/tests.py::test_church_roles_are_not_django_staff`, `test_admin_login_is_throttled` | setup admin and invited pastor have `is_staff is False`; 9 wrong passwords at `/admin/login/` then the right one → not logged in. |
| Sock-puppet confidential grant (F7) | `test_camp_admin.py::test_sensitive_access_cannot_be_granted_to_an_account_without_a_password` | `member_add` with `can_view_sensitive` for a new e-mail → form error; `member_edit` grant on an unusable-password account → error; after the person sets a password the grant succeeds and is audited. |
| Pastor widening the level (F2, decision needed) | `ConfidentialLevelTests::test_a_church_pastor_without_membership_cannot_change_the_level` (or the inverse, documenting the choice) | POST by a church pastor with no membership → 403 / 302 and the level unchanged. |
| Event creation church (F3) | `test_ux_admin_flujos.py::test_event_create_asks_for_the_church_when_several_are_administered` | superuser with two churches: GET shows a church field; POST without it → error; POST with church B → `event.church == B`. |
| One person, several roles (J3) | `core/test_multi_church.py::test_a_person_with_roles_in_two_churches_reaches_both_sets_of_tools` | participant in A (older row) + church_admin in B: B's `church_settings` → 200, A's `participant_dashboard` → 200, no page shows A's data under B's context. Today: fails (403) — mark `expectedFailure` until F4 is fixed. |
| Event privileges expire without removing affiliations | `test_access.py::test_deactivating_an_event_role_keeps_church_role_and_other_events` | church pastor with memberships in A and B; deactivate A → A 403, B 200, `user_list`/`church_dashboard` 200, `UserChurchRole` untouched. (Partly covered by `test_deactivated_membership_overrides_church_pastor_role`.) |
| Group leader cannot read camp answers by default | `test_access.py::test_a_church_mentor_without_membership_cannot_open_the_camp` and `test_a_tutor_reads_no_individual_answer_at_the_default_level` | core MENTOR → every event URL 403; tutor: `interview_detail` 200 with zero `row.visible` for sensible/confidential. |
| Sede A staff vs sede B | **not implementable**: no sede scope exists `[models.py:536]`; record as a vision gap; when implemented: `test_a_sede_scoped_leader_sees_only_their_sede` |
| Public visitors browse approved information only | `test_public_pages.py::test_anonymous_gets_redirected_everywhere_except_the_public_pages` | iterate `events.urls` + `core.urls` with sample args → 302 to login except `landing`, `health`, `church_logo`, `join`, `event_entry`, `accept_invitation`, auth views; `/e/<code>/` body contains no participant or member names. |
| Campero requests a group without a second account | **not implementable** (no campero account); record as gap. |
| Join throttle without cookies (F5) | `test_join_code.py::test_join_search_without_a_cookie_is_still_limited` | 11 cookie-less POSTs to `/unirme/` from one client → the 11th answers the wait message. |
| Archived events closed by URL (F9) | `test_access.py::test_archived_event_pages_are_closed` | member GET → 403/404; superuser 200. |
| Django-admin sensitive edits audited (F1) | `events/tests/test_admin_site.py::test_admin_site_change_of_can_view_sensitive_writes_an_audit_row` | superuser POST to `admin:events_eventmembership_change` → `AuditEvent(action="acceso_confidencial")` exists. |
| Safety assignment to a reader only (F13) | `test_audit_followup.py::test_a_safety_report_can_only_go_to_someone_who_can_read_it` | `follow_up_assign` with a tutor without the flag → form refused. |
| Session cleanup (F11, withdrawn: the call exists) | `test_camp_ops.py::test_the_scheduler_clears_expired_sessions` | an expired `Session` row disappears after one `run_report_cutoffs` pass (covers the existing `SessionStore.clear_expired()` call). |

---

## 10. What I could not verify
- Production configuration (`.env`): `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SECURE_HSTS_SECONDS`, `SECURE_SSL_REDIRECT`, `EMAIL_BACKEND`, `LLM_ENABLED`, whether `/admin/` is blocked at Traefik/Cloudflare, whether gunicorn's port is reachable without the proxy, proxy-level rate limits.
- Whether any `Permission` rows were attached to the role groups, or extra superusers exist, in the production database.
- Runtime behaviour of `main` (the camp) — only `v2/foundation` at `6f4664f` was read; the two branches may differ.
- PostgreSQL-specific behaviours (case folding of `email__iexact`, `select_for_update` semantics) — tests ran on SQLite only.
- The browser gates `e2e/station-flow.mjs` and `e2e/review-all.mjs` (not run).
- Whether the privacy notices of real events were aligned with their confidential level (data, not code).
- A disk-quota error in the Claude temp directory (`/tmp/claude-1000`, 16 GB used) swallowed one command's output during the audit; the test results above were re-captured and are complete.

## Top 5 for the synthesis
1. **Django admin exposure (F1)**: church admins/pastors are `is_staff`, the admin login is unthrottled, and the admin has no tenant scoping or audit — fix before any second tenant; a password-guessing surface today.
2. **"Administrative status alone" can still reach confidential answers through two sanctioned features**: a pastor-set `todo_el_equipo` level (F2) and the on-screen activation link for a flagged second account (F7). Both are audited; neither needs a second person.
3. **Multi-church identity is first-wins** (F3, F4): event creation picks `churches[0]`, core binds the first role row — J3 and J6 cannot pass until a `current_church` concept exists.
4. **Rate limiting is per account/cookie only** (F5, F6): public join endpoints and the app login have no IP dimension; join sessions are created on demand.
5. **Vision scenarios with no implementation and no tests**: sede-level scope, group leaders, campero accounts, dated event privileges; plus the missing cross-church-by-id test, which is the one test that would prove invariant 3 as written.
