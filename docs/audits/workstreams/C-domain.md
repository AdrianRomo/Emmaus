# Emmaus audit — Workstream C: data and domain model

Repository: `<checkout>`, branch `v2/foundation`, commit `6f4664f` (read-only).
Date: 2026-10-28. Author: audit agent (workstream C of `docs/EMMAUS_AUDIT_PROMPT.md`).

Evidence conventions: `[Verified path:line]` = read in the code at that location; `[Inferred: …]` = deduced, not
directly observed; `[Unknown: …]` = could not be established. Line numbers are of this commit.

Method. Read `core/models.py`, `events/models.py`, all 22 migration files, `core/access.py`, `events/access.py`,
`core/services.py`, `events/services/{participants,audit,interviews,event_questionnaires,questionnaire,team,
metrics,exports}.py`, the management commands, the forms and views that create accounts or events, and
`docs/{architecture,privacy-and-safeguarding,roles-and-permissions,EMMAUS_PRODUCT_VISION}.md`. Ran
`manage.py migrate` against an empty scratch SQLite database (`DATABASE_URL=sqlite:///<audit-tmp>/c.sqlite3`)
and a shell script that printed every model's fields, FKs, constraints, indexes and choices
(`<audit-tmp>/introspect.txt`, 38 models: `auth.User`, 15 `core`, 22 `events`);
`makemigrations --check` reports "No changes detected" [Verified: scratch run]. No `.env`, dump, `media/` or
production database was read. No runtime system was inspected: static code does not prove runtime behaviour.

---

## 0. Executive summary

* Two apps, one `auth.User`. `core` = church + church-wide roles + discipleship (people WITH accounts);
  `events` = the camp (people WITHOUT accounts). The two "participant" concepts (`core.ParticipantProfile`,
  `events.EventParticipant`) share nothing but the word [Verified core/models.py:202-235; events/models.py:524-599].
* There is no `Person`. A campero exists only as one `EventParticipant` row per event, with a per-event number and a
  global `public_id`; nothing links the same child across two camps, nothing records a guardian's identity
  [Verified events/models.py:524-559, 602-635].
* Tenant = `Church`; isolation inside `events` is **implicit** (`event.church_id`), enforced by `resolve_access`
  and by a handful of `questionnaire__church=event.church` lookups, not by the schema. 17 of 22 camp tables carry no
  church FK [§4]. Single-tenant assumptions survive in the landing page, `/setup/` and event creation (`churches[0]`)
  [Verified core/views.py:195,200; events/views/home.py:47].
* Versioning is sound where it matters: published questionnaire versions are immutable at the model layer,
  interviews pin their version and copy the event's confidentiality level, corrections leave `AnswerRevision`s,
  cutoffs refuse mutation once `listo`. Guards are model/service-level, bypassable by queryset/`/admin/` [§6].
* Migration history is additive except one planned contract (`events_event.questionnaire_version_id` still in
  PostgreSQL, already gone on SQLite) and six irreversible `RunPython` data steps [§7].
* The smallest evolution that keeps every camp table and flow intact is: additive `Person` (+ nullable
  `EventParticipant.person`), `AuditEvent.church`, consent provenance fields, a projected `RoleAssignment`, per-church
  boolean flags — eleven steps, each with a rollback, no rewrite of `EventMembership`/`Interview` [§8].

---

## 1. Entity inventory

Scope column: **G** global, **C** church (tenant), **E** event, **U** user. "Audit" = written to `events.AuditEvent`
by the code paths I read (the discipleship module writes no audit rows at all — `AuditEvent` is only imported in
`events` [Verified: grep `AuditEvent` in `core/` returns nothing]).

### 1.1 `auth` and `core`

| Model (table) | Purpose | Scope | Identity | Lifecycle / flags | Timestamps | Audit |
|---|---|---|---|---|---|---|
| `auth.User` (`auth_user`) | Django stock user. **No custom user model** (`AUTH_USER_MODEL` absent from `emmaus/settings.py`) [Verified: grep]. | G | `id`; `username` UNIQUE (= e-mail, see §3); `email` NOT unique at DB level [Verified introspect: `email: EmailField` without UNIQUE] | `is_active` (demo accounts deactivated by `deactivate_demo_accounts`), `is_staff`, `is_superuser` | `date_joined`, `last_login` | no |
| `core.Church` | Tenant: name, slug, welcome text, theme colours, two logo files, module switches | G | `slug` UNIQUE global [core/models.py:29] | `active` [:33]; `self_registration_enabled`, `leaders_can_invite_participants`, `discipleship_enabled`, `custom_theme_enabled` [:42-53] | `created_at` | no |
| `core.UserChurchRole` | One church-wide role per user per church (`church_admin`, `pastor`, `mentor`, `participant`) | C×U | `unique_together (church, user)` [:136] | role string; no start/end, no status | `created_at` | no |
| `core.Invitation` | E-mail invitation to a church role (+ optional mentor, path) | C | `token` UNIQUE, **stored in clear** [:176] | `expires_at` (14 d), `accepted_at`; `can_accept` property [:189-199] | `created_at` | no |
| `core.ParticipantProfile` | Discipleship participant WITH an account | C×U | `user` 1:1 [:210]; `church` FK [:211] | `consent_accepted_at`, `emergency_disclaimer_accepted_at` (set at acceptance [core/views.py:297-298]) | `date_joined` | no |
| `core.DiscipleshipPath` | A programme of lessons | C | `(church, title)` not unique | `is_active`, `is_default` (one per church, enforced in `save()` [:262-267]) | `created_at` | no |
| `core.Lesson` | Ordered lesson | C via path | `unique_together (path, order)` | `is_active` | — | no |
| `core.ReflectionQuestion` | Question of a lesson | C via path | `unique_together (lesson, order)` | — | — | no |
| `core.Enrollment` | User ↔ path | C via path; subject is `User`, not profile [:305] | `unique_together (participant, path)` | `completed_at` | `started_at` | no |
| `core.LessonProgress` | Per-lesson completion | via enrollment | `unique_together (enrollment, lesson)` | `completed_at` | `last_viewed_at` auto_now | no |
| `core.ReflectionAnswer` | Participant's free text per reflection question | via enrollment | `unique_together (enrollment, question)` | — | `created_at`, `updated_at` | no |
| `core.ParticipantQuestion` | Question raised by a participant | C (`church` FK [:367]) + `participant`→User | — | `sensitivity_level`; `status` open/answered/escalated/closed [:361-365] | `created_at`, `updated_at` | no |
| `core.QuestionResponse` | Leader's reply | via question | — | — | `created_at`, `edited_at` | no |
| `core.FollowUpRequest` | Church-level pastoral follow-up | C (`church` FK [:428]) | — | `status` open/in_progress/completed/cancelled; `source` [:416-426] | `created_at`, `completed_at`, `due_date` | no |
| `core.PastoralNote` | Note about a participant | C | — | `visibility` mentor_only/pastor_only/pastor_and_mentor | `created_at` | no |
| `core.AIDraft` | LLM draft answer | C | — | `status` draft/approved/rejected/edited | `created_at`, `reviewed_at` | no |

Observations (core):
* Every discipleship table that holds personal content carries a `church` FK **and** a `participant`/`user` FK; the
  pair is not constrained to agree with `UserChurchRole.church` or `ParticipantProfile.church` [Inferred: no
  constraint in introspect output]. Consistency relies on `core/access.py` filtering by `role_record.church`
  [Verified core/access.py:65,87,111,127].
* No audit coverage and no soft delete anywhere in `core`; `PastoralNote.author`, `QuestionResponse.responder`,
  `AIDraft.generated_by`, `Enrollment.participant` are `CASCADE` on the user, so deleting a leader's account deletes
  pastoral history [Verified core/models.py:305,401,468,495]. App flows never delete users (they deactivate), only
  `purge_demo_data` does, for demo-marked accounts [Verified events/management/commands/purge_demo_data.py:159-160,248].

### 1.2 `events` (the camp)

| Model | Purpose | Scope | Identity | Lifecycle / flags | Timestamps | Audit action(s) |
|---|---|---|---|---|---|---|
| `Sede` | Church campus | **C** (`church` FK [events/models.py:80]) | `UniqueConstraint (church, slug)` `sede_slug_per_church` [:89] | `active` (deactivate, never delete once used; `EventParticipant.sede` is `PROTECT` [:536]) | — | `evento_configurado` (from the event where edited) [docs/roles-and-permissions.md:186] |
| `Event` | The camp / retreat / … | **C** (`church` FK [:140]) | `slug` UNIQUE **global** [:142]; `join_code` partial-unique where not blank [:228] | `status` borrador/activo/cerrado/archivado [:127-131], chosen freely in `EventForm` (`fields=[…,"status"]` [events/forms.py:51]) — **no transition guard**; `procedures_approved_at/by`; `data_purged_at` [:217]; `confidential_level` [:175-181] | `created_at`, `updated_at` | `evento_creado`, `evento_configurado`, `procedimientos_aprobados` |
| `Event.sedes` (M2M) | Sedes taking part; empty = whole church | E | join table | — | — | — |
| `EventMembership` | A user's role in ONE event (administrador/pastor/tutor/coordinador) + `can_view_sensitive`, `team_name` | **E** | `UniqueConstraint (event, user)` `membership_one_role_per_event` [:397] | `is_active` (deactivate = revoke) | `created_at`, `updated_at` | `integrante_agregado/modificado`, `acceso_confidencial` |
| `TutorAssignment` | tutor → coordinador supervision | E | `coordinator` OneToOne [:420]; same-event + role check in `clean()` run by `save()` [:431-441] | — | `created_at` | `asignacion_tutor` |
| `AccessRequest` | Public "join the team" request (name + e-mail, no account) | E | partial unique `(event, email)` while `pendiente` [:480-482]; `email` lower-cased in `save()` [:492] | `status` pendiente/aprobada/rechazada, set in views [events/views/admin.py:573,587,606]; `membership` link after approval | `created_at`, `decided_at` | `solicitud_acceso` (actor "Sistema", summary has the requester's full name [events/views/join.py:135-136]), `solicitud_aprobada/rechazada` |
| `InterviewStation` | Kiosk computer | E | `(event, number)` unique [:505] | `is_active` | `created_at` | — |
| `EventParticipant` | **Campero**: no account | **E** only (`event` FK [:525]) | `public_id` UUID UNIQUE global [:526]; `number` unique per event [:564]; `normalized_name` (accent/case/space-folded, duplicate warnings only) [:42-46,583] | `interview_status` pendiente/en_curso/completada — **derived and stored** by `event_questionnaires.derive_interview_status` [events/services/event_questionnaires.py:189-219]; `is_active` + `withdrawn_reason` (soft withdrawal); `sede` nullable PROTECT; `coordinator` SET_NULL | `created_at`, `updated_at`, `created_by`, `updated_by` | `campero_creado/modificado/reasignado/baja/eliminado`, `importacion` |
| `ConsentRecord` | Guardian authorisation / self consent / minor's assent, **append-only history** | via participant (no event FK) | — | `kind` × `status` otorgado/negado/revocado × `method` papel/verbal/digital [:603-616]; latest row per kind decides [events/services/participants.py:195-200] | `recorded_at` | `autorizacion_registrada/retirada` |
| `Questionnaire` | Form definition | **C** (`church` FK [:644]) | no slug; `(church, name)` not unique | none (no active flag, no retire) | `created_at` | `cuestionario` |
| `QuestionnaireVersion` | Form version | C via questionnaire | `(questionnaire, number)` unique [:680] | `status` borrador/publicada/**retirada** — `RETIRED` is never assigned anywhere in code [Verified: grep `RETIRED|retirada` hits only the enum, a template tag and an unrelated audit label]; `is_editable` = draft [:688] | `created_at`, `published_at` | `cuestionario` |
| `EventQuestionnaire` | Which version an event uses, in which order | E | `(event, questionnaire)` unique [:704]; `questionnaire` copied from `version` in `save()` [:712-717] | `order` | — | `cuestionario` |
| `Section` / `Question` / `QuestionOption` | Form structure | C via version | `Question.code`, `QuestionOption.code` slugs, **not unique** at DB level [:769,827] | `_DraftOnlyMixin`: `save()`/`delete()` raise `ImmutableRecordError` unless the version is a draft (`allow_published=True` escape hatch) [:720-734] | — | — |
| `Interview` | One campero × one questionnaire, pinned to a version | E (`event` FK [:868]) | `public_id` UUID UNIQUE; partial unique `(participant, questionnaire)` where not `descartada` [:916-920] | `status` en_curso/completada/descartada — transitions only in `events/services/interviews.py` under `select_for_update` (`start_interview` [:294-370], `finalize` [:518-578], `discard` [:579-608]); `mode`; `confidential_level` copied from the event at start [:355]; claim lock (`lock_token_hash`, `lock_holder`, `lock_expires_at`); `revision` optimistic counter; `follow_up_decision`; `correction_count`; `discarded_*` | `started_at`, `last_activity_at`, `completed_at`, `last_corrected_at`, `discarded_at` | `entrevista_iniciada/retomada/control/completada/corregida/descartada` |
| `Answer` | Current value per question | via interview | `(interview, question)` unique [:973]; `question` PROTECT; `options` M2M | `answered_by`, `answered_by_participant` | `created_at`, `updated_at` | (no answer content ever) |
| `AnswerRevision` | Previous value of a corrected answer | via interview | — | `reason` (≤200) | `changed_at` | `entrevista_corregida` |
| `ParticipantSession` | Account-less kiosk session | via interview | `public_id` UUID; `token_hash`, `lock_token_hash` (SHA-256, never raw [:53,1013-1014]) | `state` activa/por_desbloquear/cerrada; `end_reason` [:998-1008]; `expires_at` | `created_at`, `last_activity_at`, `ended_at` | `captura_iniciada/desbloqueada`, `sesion_revocada` |
| `FollowUp` | Care referral or safety report | E (`event` FK [:1057]) + participant | partial unique `(interview, confidential)` for open care [:1084-1088] | `kind` seguimiento/seguridad; `status` pendiente/en_proceso/atendido/cancelado (views [events/views/dashboards.py:398-425]); `confidential` db_default False | `created_at`, `updated_at`, `resolved_at` | `seguimiento`, `reporte_riesgo` |
| `ReportSnapshot` | Cutoff ("corte") | E | `public_id` UUID; `(event, scope_key, cutoff_at, trigger)` unique [:1135-1137] | `status` listo/fallido; **immutable once `listo`** (`save()` re-reads stored status and raises; `delete()` needs `purge=True`) [:1143-1153] | `created_at`, `cutoff_at` | `corte_generado` |
| `AuditEvent` | Who did what | E (`event` **nullable** [:1162]); no church FK | — | append-only by convention only (no DB guard) | `created_at` db_index | — |
| `AuthThrottle` | Login/reset/unlock attempt counters | **G** | `key` UNIQUE = `login:<e-mail typed>`, `reset:<e-mail typed>`, `unlock:<session public_id>` [docs/architecture.md:174] | `attempts`, `strikes`, `blocked_until` | `window_started_at` | — |

Naming inconsistency (Low): active flags are `active` on `Church`/`Sede`, `is_active` on `EventMembership`/
`EventParticipant`/`InterviewStation`/`DiscipleshipPath`/`Lesson`/`User` [Verified: introspect].

### 1.3 State machines and where they are enforced

| State field | Transitions | Enforced in | DB support |
|---|---|---|---|
| `Event.status` | any → any | nowhere (form choice) [events/forms.py:51]; consumers: `event_blockers` [events/services/interviews.py:275-292], `accessible_events` hides `archivado` [events/access.py:287], automatic purge needs `cerrado`/`archivado` [events/management/commands/purge_expired_event_data.py:9,40] | none |
| `Interview.status` | `en_curso` → `completada` (finalize) / `descartada` (discard); completed → corrected (status unchanged, `revision`+1) | service, row lock, revision check [interviews.py:415-431,518-608,669-760] | partial unique "one live per questionnaire" |
| `EventParticipant.interview_status` | derived: live interview → `en_curso`; all linked questionnaires completed → `completada`; else `pendiente` | service in the same transaction [interviews.py:351,546,602; event_questionnaires.py:177-219]; recomputed by migration 0011 | index only |
| `QuestionnaireVersion.status` | `borrador` → `publicada` (`publish_version`) ; `retirada` unreachable | service [events/services/questionnaire.py:248-259]; child immutability in model mixin [events/models.py:720-734] | none |
| `ParticipantSession.state` | `activa` → `por_desbloquear` → `cerrada`; `activa` → `cerrada` (revoke/takeover/discard) | service [interviews.py:200-205,834-932] | index `(state, expires_at)` |
| `FollowUp.status` | free among four | views via forms [dashboards.py:398-425] | partial unique open-care |
| `AccessRequest.status` | `pendiente` → `aprobada`/`rechazada`, never re-decided | views [admin.py:562-610] | partial unique pending |
| `ConsentRecord` | append-only; latest by `(-recorded_at, -id)` wins | service [participants.py:131-200] | index `consent_lookup_idx` |
| `ReportSnapshot.status` | `listo` final; `fallido` may be re-saved | model `save()`/`delete()` [models.py:1143-1153]; queryset/cascade bypass [docs/architecture.md:181] | none |
| `Invitation` | pending → accepted (`accepted_at`) / expired (`expires_at`) | properties [core/models.py:189-199]; acceptance view [core/views.py:272-300] | none |
| `ParticipantQuestion.status`, `FollowUpRequest.status`, `AIDraft.status` | free | core views [Unknown: not read in depth for this workstream] | none |

---

## 2. The ER diagram in `docs/architecture.md` (lines 47-173) against the code

Every drawn relationship matches a FK/M2M/O2O in `events/models.py` (checked one by one: `CHURCH→EVENT` [:140],
`CHURCH→QUESTIONNAIRE` [:644], `CHURCH→SEDE` [:80], `EVENT↔SEDE` M2M [:155], `SEDE→EVENT_PARTICIPANT` nullable PROTECT
[:536], `USER→EVENT_MEMBERSHIP` [:374], `EVENT→EVENT_MEMBERSHIP` [:373], `TUTOR_ASSIGNMENT` tutor FK [:419] and
coordinator O2O [:420], `EVENT→INTERVIEW_STATION` [:497], `EVENT→EVENT_PARTICIPANT` [:525],
`EVENT_MEMBERSHIP→EVENT_PARTICIPANT` SET_NULL [:537], `EVENT_PARTICIPANT→CONSENT_RECORD` [:618],
`QUESTIONNAIRE→QUESTIONNAIRE_VERSION` [:664], the three `EVENT_QUESTIONNAIRE` FKs [:695-698], `VERSION→SECTION→QUESTION→OPTION`
[:738,767,825], `QUESTION` self-condition [:777], `EVENT_PARTICIPANT→INTERVIEW` [:869], `QUESTIONNAIRE→INTERVIEW` [:872],
`VERSION→INTERVIEW` PROTECT [:870], `STATION→INTERVIEW` [:889], `INTERVIEW→ANSWER` [:961], `ANSWER↔OPTION` M2M [:963],
`INTERVIEW→ANSWER_REVISION` [:981], `INTERVIEW→PARTICIPANT_SESSION` [:1011], `INTERVIEW→CONSENT_RECORD` nullable [:619],
`EVENT_PARTICIPANT→FOLLOW_UP` [:1058], `INTERVIEW→FOLLOW_UP` nullable [:1059], `EVENT→REPORT_SNAPSHOT` [:1114],
`EVENT_MEMBERSHIP→REPORT_SNAPSHOT` nullable [:1117], `EVENT→AUDIT_EVENT` nullable [:1162]). **Confirmed: no drawn edge
is wrong.** [Verified]

What the diagram omits (corrections to add, none contradicts the code):

| Missing | Code | Why it matters |
|---|---|---|
| `ACCESS_REQUEST` entity entirely (`EVENT ||--o{ ACCESS_REQUEST`, `EVENT_MEMBERSHIP |o--o{ ACCESS_REQUEST`) | [events/models.py:448-493] | It holds names + e-mails of people who are not users; relevant to retention and tenancy |
| `EVENT ||--o{ INTERVIEW` (denormalised `Interview.event`) | [:868] | Two paths to the event (direct and via participant) with **no constraint that they agree** |
| `EVENT ||--o{ FOLLOW_UP` (denormalised `FollowUp.event`) | [:1057] | same |
| `QUESTION ||--o{ ANSWER` (PROTECT) and `QUESTION ||--o{ ANSWER_REVISION` (PROTECT) | [:962,982] | Deleting a question is impossible once answered — a property the purge relies on |
| `INTERVIEW_STATION |o--o{ PARTICIPANT_SESSION` | [:1012] | — |
| `USER` edges: `Interview.conducted_by`, `lock_holder`, `Answer.answered_by`, `ParticipantSession.started_by` (**CASCADE**), `ConsentRecord.recorded_by`, `created_by`/`updated_by`/`procedures_approved_by`/`published_by` everywhere | [:886,899,965,1016,626,209,219,552-556,670-675] | `ParticipantSession.started_by` is the only CASCADE from `User` into camp data [:1016]; all others `SET_NULL` |
| The whole `core` app (`UserChurchRole`, `ParticipantProfile`, discipleship) and the `USER ||--o{ USER_CHURCH_ROLE }o--|| CHURCH` relation that `resolve_access` depends on | [core/models.py:127-140; events/access.py:260] | The identity model is split across two apps; the diagram shows only half |
| Attributes: `EVENT.join_code`, `join_by_name`, `data_purged_at`, `privacy_notice`; `EVENT_PARTICIPANT.sede`, `withdrawn_reason`; `INTERVIEW.questionnaire` | [:151-153,217,202,536,550,872] | Diagram's attribute blocks are illustrative, not complete |

Recommendation (Low): add `ACCESS_REQUEST`, the two denormalised event edges and a one-line note that `core`
identities are out of the picture; or state explicitly that the diagram is "events only, FKs to users omitted".

---

## 3. Identity and roles

### 3.1 The five identity-bearing tables and how they relate

```
auth.User (global, username == e-mail)
 ├─ core.UserChurchRole  (church, user) unique     → church-wide role: church_admin | pastor | mentor | participant
 ├─ core.ParticipantProfile  user 1:1, church FK  → discipleship participant (needs role 'participant')
 ├─ events.EventMembership  (event, user) unique  → event role: administrador | pastor | tutor | coordinador (+ can_view_sensitive)
 └─ events.AccessRequest.email  (no FK)           → matched to a User by e-mail at approval time
events.EventParticipant (event FK only; NO user, NO person)   → campero
```
[Verified core/models.py:127-140,202-235; events/models.py:370-401,448-493,524-559]

### 3.2 Which roles live where

| Level | Mechanism | Semantics | Evidence |
|---|---|---|---|
| Global | `User.is_superuser` | Administrador of **every** event and church; sees archived events; owns all churches (`administered_churches`) | [events/access.py:261-262,275-276,296-297] |
| Global (vestigial) | Django `Group` named after the role | `assign_role` does `user.groups.clear()` then adds ONE group = the last church role assigned. Groups are **not consulted** by any authorization code (`has_perm`/`permission_required` unused) | [core/services.py:58-68; Verified grep] |
| Per church | `UserChurchRole.role` | `church_admin` → Administrador in every event of that church, whatever the membership; `pastor` → Pastor in every event unless a membership of theirs was deactivated; `mentor`/`participant` → discipleship only | [events/access.py:260-268; core/access.py:16-57] |
| Per event | `EventMembership.role` + `can_view_sensitive` + `team_name`; `TutorAssignment` | "Roles never leak across events" [events/models.py:371]; confidential access is explicit per event, pastoral status alone is not enough | [events/access.py:69-71] |
| None | `EventParticipant` | camperos have no login; identified to staff by `#number` and to URLs by `public_id` | [events/models.py:524-526,594] |

Multi-church users today: `UserChurchRole` allows one row per church, so a user may hold roles in several churches.
But the discipleship side reads **only the first row by id** (`get_user_role` → `.order_by("id").first()`)
[Verified core/access.py:20-23] and every `core` view/queryset derives its church from it [core/access.py:60-132;
core/views.py:143,228,313,343,362,526,551,724]. The camp side is correct: it looks up the role **for the event's
church** [events/access.py:260]. Event creation, however, takes `churches[0]` of `administered_churches`
[Verified events/views/home.py:44-47], so a two-church admin can only create events in the first church.

**Finding C-02 — Severity: Medium (High once a second tenant exists).** Multi-church membership is representable
in the schema but not in the discipleship code path or event creation. Recommendation: no schema change; make
`get_user_role` take an explicit church (session-selected), and let `event_create` ask which church when
`len(churches) > 1`. Both are additive code changes.

### 3.3 E-mail as identity

* Accounts are created in four places, all with `username = e-mail`: initial setup (lower-cased)
  [core/services.py:96-98], invitation acceptance (lower-cased) [core/views.py:280-282], "Agregar persona"/access
  approval (`username=email` — already lower-cased by `MemberForm.clean_email` [events/forms.py:418-423] and
  `AccessRequest.save()` [events/models.py:491-493]) [events/services/team.py:86], and the demo seed
  [events/management/commands/seed_camp_demo.py:115-117].
* Lookups are `email__iexact` [events/services/team.py:49-50; core/forms.py:191,449]; login is by e-mail
  (`EmailAuthenticationForm`) [core/forms.py:111]. `auth_user.email` has **no unique constraint**; `username` is
  unique and case-sensitive on PostgreSQL [Inferred: Django default collation]. A race in `add_member` is caught via
  the `username` uniqueness [team.py:88-94].
* `AcceptInvitationForm` refuses an e-mail that already has an account ("Ya existe una cuenta con este correo")
  [core/forms.py:449-450] — a camp coordinator (account created by `add_member`) **cannot** accept a discipleship
  invitation for the same church; the only path to add a church role to an existing account is church settings /
  `assign_role` [Inferred: no other call site of `assign_role` found besides setup, acceptance, tests].
* Demo marker: the e-mail suffix `@demo.invalid` / `@example.org` is the **only** thing that distinguishes demo
  accounts (`events/checks.py:16`, `deactivate_demo_accounts.py:13`, `purge_demo_data.py:67`); demo churches are
  the slugs `iglesia-demo`, `emmaus-demo` [purge_demo_data.py:66]. Superusers with such e-mails are skipped, never
  deleted [purge_demo_data.py:159-166].

**Finding C-01 — Severity: Low today / Medium for SaaS.** E-mail is the identity key but is not unique at the DB
level and is duplicated into `username`; `Invitation.token` is stored unhashed [core/models.py:176] while every
camp token is SHA-256 hashed [events/models.py:53]. Recommendation (additive): a data check for mixed-case
duplicates, then a partial unique index on `LOWER(email)`; hash invitation tokens in a later release (new column,
dual-read).

### 3.4 What couples event participation to permanent identity

* Staff: `EventMembership.user` → `User`. The same person across events = the same `user_id`; `accessible_events`
  unions church roles and active memberships [events/access.py:272-290]. This is the only cross-event identity in
  the system.
* Camperos: **nothing**. The same child at two camps = two `EventParticipant` rows with different `number`s and
  `public_id`s; `possible_duplicates` compares `normalized_name` **within one event only** and only warns
  ("nothing is ever merged") [Verified events/services/participants.py:25-39]. The per-campero JSON export is per
  event [events/views/admin.py:345-370]. There is no contact field (no phone, e-mail, address) on
  `EventParticipant`; `church_group` and `sede` are free text / FK describing origin, not identity
  [events/models.py:534-536].
* Discipleship participants: `ParticipantProfile.user` 1:1 — permanent, church-scoped, never linked to a campero.

**Finding C-07 — Severity: High for the product direction (§5 Person), Info for the running camp.** No `Person`
exists; a campero's identity is per event, a staff member's identity is a global `User`. Any "camp → small group"
journey (vision J2) needs a tenant-scoped person record and an explicit, human-confirmed link. See §8 steps S3-S5.

---

## 4. Scoping today (feeds workstream D)

| Church FK present (direct tenant column) | Event FK only (tenant implicit via `event.church_id`) | Neither (reachable only through a chain) | Global |
|---|---|---|---|
| `core.*` content tables: `UserChurchRole`, `Invitation`, `ParticipantProfile`, `DiscipleshipPath`, `ParticipantQuestion`, `FollowUpRequest`, `PastoralNote`, `AIDraft` [core/models.py:128,152,211,239,367,428,466,493] ; `events.Sede` [:80], `events.Event` [:140], `events.Questionnaire` [:644] | `EventMembership` [:373], `TutorAssignment` [:418], `AccessRequest` [:460], `InterviewStation` [:497], `EventParticipant` [:525], `EventQuestionnaire` [:695], `Interview` [:868], `FollowUp` [:1057], `ReportSnapshot` [:1114], `AuditEvent` (**nullable** event) [:1162] | `ConsentRecord` (participant→event) [:618]; `QuestionnaireVersion`→questionnaire→church [:664]; `Section`/`Question`/`QuestionOption` (→version) [:738,767,825]; `Answer`, `AnswerRevision`, `ParticipantSession` (→interview) [:961,981,1011]; `core.Lesson`/`ReflectionQuestion`/`Enrollment`/`LessonProgress`/`ReflectionAnswer`/`QuestionResponse` | `auth.User`, `AuthThrottle` [:1179-1190], Django `Session`, `admin.LogEntry` |

Where isolation is enforced and how:

| Boundary | Enforcement | Evidence | Gap |
|---|---|---|---|
| Who may open an event | `resolve_access`: superuser, church_admin of `event.church_id`, active membership, or church pastor | [events/access.py:255-269] | server-side, tested (`test_another_churchs_admin_has_no_access_here_and_can_be_added` [events/tests/test_join_code.py:815]) |
| Event ↔ questionnaire of the same church | `use_version` raises "Ese cuestionario es de otra iglesia." [events/services/event_questionnaires.py:71-72]; admin lookups `questionnaire__church=event.church` [events/views/admin.py:731,747,834] | tested [events/tests/test_multi_questionnaires.py:471] | **no DB constraint**: `Interview.version` / `EventQuestionnaire.version` may point at another church's version if written by code that skips the service (`/admin/`, scripts) [Inferred] |
| Event ↔ sede of the same church | forms filter `Sede.objects.filter(church_id=event.church_id)` [events/forms.py:617,668]; views `get_object_or_404(Sede, …, church_id=event.church_id)` [admin.py:1181,1199] | tested [events/tests/test_sedes.py:612] | `EventParticipant.sede` has no cross-check against `event.church` at DB level |
| Participant ↔ coordinator of the same event | form queryset [events/forms.py:222]; `limit_choices_to={"role": COORDINATOR}` (role only) [models.py:544] | — | no DB constraint on event equality (**C-06**) |
| `Interview.event` vs `participant.event`; `FollowUp.event` vs `participant.event`; `ReportSnapshot.tutor.event`; `ConsentRecord.interview.participant` | set by services | [interviews.py:349-357; 536-553] | no DB constraint (**C-06**) |
| Church logo | **public by church slug**, no auth, served for the sign-in page | [core/views.py:164-183] | by design; the slug enumerates tenants |
| `AuditEvent` | `event` nullable; `AuditEvent.actor_label` and summaries name staff; no church column | [events/models.py:1156-1176] | a null-event row has no tenant; rows survive the event's purge by design [purge_expired_event_data.py:31] |
| `AuthThrottle.key` | contains the e-mail **as typed** at login/reset | [docs/architecture.md:174] | global table with personal data (typos included), purged by the scheduler when windows expire [docs/roles-and-permissions.md:184] |
| Landing / setup | landing shows the first active church by `created_at`; `/setup/` only while no church exists | [core/views.py:195,200] | single-tenant assumption |

**Finding C-05 — Severity: Medium (security feed).** Church isolation in `events` is implicit and correct in the
paths read, but the schema cannot refuse a cross-church `Interview.version`, `EventQuestionnaire.version`,
`EventParticipant.sede` or `coordinator` written outside the services. Recommendation (incremental): (1) a
read-only "tenant integrity" management check that counts mismatches (`Interview.version.questionnaire.church !=
event.church`, `participant.sede.church != event.church`, `coordinator.event != event`, `Interview.event !=
participant.event`), run in CI and nightly; (2) later, `CheckConstraint`s cannot express cross-table rules in
Django, so keep the service layer the chokepoint and add the `church` column to `AuditEvent` (S1) so audit rows
can be scoped without an event.

**Finding C-06 — Severity: Medium.** Four denormalised FK pairs without agreement constraints (listed above).
Recommendation: same integrity check; no schema change for the camp.

**Finding C-04 — Severity: Medium for a second tenant.** `landing`, `initial_setup`, `event_create` assume one
church [core/views.py:195,200; events/views/home.py:47]. Recommendation: church-aware landing by host or path
(F workstream), church choice on event creation (S, code only).

---

## 5. Minors, guardians, consent, retention

### 5.1 What exists

| Concern | Implementation | Evidence |
|---|---|---|
| Who is a minor | `EventParticipant.is_minor = age < event.adult_age` (default 18, per event) | [events/models.py:198,597-599] |
| Guardian requirement | `Event.requires_guardian_authorization` (default True) → a minor cannot start an interview without a GRANTED guardian `ConsentRecord` (recorded on registration, CSV import, "Marcar como recibida", or ticked at interview start) | [events/models.py:199-201; interviews.py:218-273; participants.py:75-81,131-153] |
| Assent / consent | at interview start the coordinator ticks "assent" (minor) or "consent" (adult); recorded as `ConsentRecord(kind=asentimiento|consentimiento, method=verbal, interview=…)` | [interviews.py:233-273] |
| Withdrawal | `withdraw_guardian_authorization` appends a `revocado` row with a required reason (≤200), does not stop an interview in progress; audit names it | [participants.py:156-192] |
| Notice shown | `Event.privacy_notice` free text (defaults rewritten by migrations 0009/0010 when untouched) | [models.py:202; events/migrations/0009:1-27, 0010:1-27] |
| Guardian identity | **none** — `ConsentRecord` stores kind/status/method/`notes` (≤200 chars)/`recorded_by` (staff) | [models.py:618-629] |
| Consent provenance | **none** — no notice version/hash, no purpose, no basis | [models.py:602-635] |
| Retention | `Event.retention_days` 30-3650, default 365, counted from `ends_on`; event without `ends_on` never expires | [models.py:212-216; purge_expired_event_data.py:16-18] |
| Purge | manual `purge_expired_event_data --apply` and the scheduler (daily after 03:00 event-local) delete `event.participants.all()` (cascade: interviews, answers, revisions, sessions, consents, follow-ups) under a row lock; automatic run refuses unless `cerrado`/`archivado` and no open safety report / confidential referral; marks `data_purged_at`; writes `campero_eliminado` with the count | [purge_expired_event_data.py:28-50; run_report_cutoffs.py:37-62] |
| Kept after purge | event, team, questionnaires, cutoffs (aggregates), activity log (campero numbers, staff names), `AccessRequest` rows (requesters' names + e-mails) until `purge_decided_requests` [events/services/join.py:51] [Unknown: its window not read], `AuthThrottle` keys | [purge_expired_event_data.py:31; docs/privacy-and-safeguarding.md:66] |
| Per-campero erasure | `delete_participant_data`: audit row with `number` only, then `participant.delete()` (same cascade) | [participants.py:120-129] |
| Per-campero export | JSON with Spanish keys: event, number, name, age, room, group, registration date, interview(s), consents (type/status/date/**notes**) | [events/views/admin.py:345-384] |
| Demo purge | `purge_demo_data`: deletes demo churches (by slug), their events/interviews first (PROTECT order), then demo-marked non-superusers; refuses when real data references them | [purge_demo_data.py:66-67,208-250] |

### 5.2 Gaps against vision §5.4

**Finding C-08 — Severity: Medium.** A consent row cannot prove *what* was consented to: `Event.privacy_notice` is
editable after the fact and migrations have rewritten defaults twice [events/migrations/0009,0010]. Vision §5.2
asks for "purpose, basis/consent, version, timestamps, withdrawal". Recommendation (S, additive): add
`ConsentRecord.notice_sha256` (char 64, db_default ''), `notice_excerpt` (first 200 chars) and `purpose`
(char, db_default 'entrevista'), written by `_record_consents`/`record_guardian_authorization`; old rows stay blank.

**Finding C-13 — Severity: Medium.** `delete_participant_data` cascades into open `FollowUp(kind=seguridad)` rows
and confidential referrals without the check the automatic purge has [participants.py:120-129 vs
purge_expired_event_data.py:42-43]. A safeguarding report can vanish with the erasure it may concern.
Recommendation (S): refuse erasure while a safety report is open, or detach the safety follow-up (`participant`
SET_NULL is not available today — `FollowUp.participant` is CASCADE [models.py:1058]); minimal step: block and
message, as the purge does.

**Finding C-12 — Severity: Low.** Retention is per event, from `ends_on`; an event never given an end date is never
purged [purge_expired_event_data.py:18]; team accounts, `AccessRequest` rows and audit rows naming staff have no
retention at all. Recommendation: `check --deploy`-style warning for active/closed events without `ends_on`; a
retention statement for staff data in `privacy-and-safeguarding.md`.

Guardians/households (vision §5.4): nothing to reuse. A guardian today is a paper form plus a staff attestation.
The minimal additive shape is a `Person` row for the guardian with a `Guardianship(child_person, guardian_person,
relationship, verified_by, verified_at)` table — **after** `Person` exists (S3) and only behind a flag (§8).

---

## 6. Versioning and history

| Mechanism | Verified behaviour | Bypass / limit |
|---|---|---|
| Questionnaire versions | `QuestionnaireVersion(number)` per questionnaire; `publish_version` flips `borrador`→`publicada` and stamps `published_at/by` [questionnaire.py:248-259]; `duplicate_version` creates the next draft [:278]; editing a published version = duplicate → publish → `use_version`, which replaces the link in place and recomputes statuses [event_questionnaires.py:68-89] | `allow_published=True` kwarg on `save()` and queryset `.update()` skip the mixin [models.py:727]; `/admin/` edits [Unknown: whether admin registers Section/Question] |
| Version pinning on interviews | `Interview.version` PROTECT + `Interview.questionnaire` copied in `save()`; `EventQuestionnaire.version` PROTECT | [models.py:870-874,933-941,698] — a version with interviews can never be deleted |
| Confidentiality pinning | `Interview.confidential_level` = event's level at start; effective = stricter of interview and event now | [models.py:877-885,943-946; interviews.py:355] |
| Answer history | `AnswerRevision(previous_option_codes, previous_text, reason, changed_by, changed_at)` per corrected or hidden answer; `Interview.revision` optimistic lock, `correction_count`, `last_corrected_at` | [interviews.py:669-760; models.py:978-992]. Previous values are stored but shown only as a list of (question, date, who, reason) to `can_view_sensitive` [docs/privacy-and-safeguarding.md:13] |
| Cutoff immutability | `ReportSnapshot.save()` re-reads the stored status and raises once `listo`; `delete()` needs `purge=True` | [models.py:1143-1153]; cascades and queryset deletes bypass [docs/architecture.md:181] |
| Cutoff contents | `data` = full metrics payload: counts, timeline, `teams` and `coordinators` with **staff display names**, `follow_ups` summary, `attention` counts, and `outcome_counts` = **raw, unsuppressed** per-option counts per question (suppression `suppress()` is applied at render, `released` block) [metrics.py:735-756,556-560,540-553] | no campero names or answers in `data` [Verified metrics.py:521-534,740-756] |
| Audit payloads | `AuditEvent.metadata` keys observed across all 44 call sites: `station` (name), `decision`, `role` [before, after], `active` [before, after], `fields` (changed field *names*), `count`, `sin_sede`, `number`, `interview` (public_id), `kind`, `form`, `level`, `status`, `reactivada` [Verified: grep of `audit.record(` in events/*; e.g. participants.py:109-116,128; admin.py:673-677; interviews.py:646; team.py:127-128]. Summaries contain staff full names, campero `#numbers`, and the requester's full name for `solicitud_acceso` [join.py:135-136]. **No answer text, no tokens, no e-mails** in metadata; `actor_label` stores the actor's name | `/admin/` changes are not audited and can delete rows [docs/privacy-and-safeguarding.md:143]; no DB-level append-only guard; no hash chain |

**Finding C-11 — Severity: Medium (security feed).** Snapshots persist raw per-option counts below the suppression
threshold; the k-anonymity promise is a rendering rule. Anyone with DB or `/admin/` read access to
`events_reportsnapshot.data` sees exact small counts. Recommendation: store `outcome_counts` already suppressed, or
keep raw counts in a separate `ReportSnapshotRaw` readable only by the release job (M, additive; current readers
use the `released` block [docs/architecture.md:183]).

**Finding C-14 — Severity: Low.** `QuestionnaireVersion.Status.RETIRED` is dead; `Questionnaire` has no active
flag, no owner, no unique name per church. Recommendation: either wire "Retirar" in the UI (hides from
"Cuestionarios" lists) or drop the enum value in the next contract release.

---

## 7. Migration history risks

22 migrations: `core` 0001-0010, `events` 0001-0012 [Verified: `ls`]. History spans 2026-08-03 (MVP) to 2026-10-26
[Verified: git log]. `makemigrations --check`: clean [Verified: scratch run].

| Class | Where | Risk | Expand/contract implication |
|---|---|---|---|
| **No** `RenameField`, `RenameModel`, `DeleteModel`, `AlterField` that changes a column type | anywhere [Verified: grep of operations across both dirs] | — | The history is additive in schema terms |
| `AlterField` label/choices-only | core 0003 (26×), 0008 (2×), 0009 (1×); events 0004 (3×), 0009 (11×), 0010 (2×) | none on PostgreSQL (metadata) [Inferred: docs/architecture.md:270-283 + unchanged column types in the model; core/0003 contents not read line by line] | — |
| `AlterField` NOT NULL after backfill | events 0011: `Interview.questionnaire` null→required [0011:177-185] | `ALTER COLUMN SET NOT NULL` scans the table; fine at camp sizes; must run after the `RunPython` in the same migration (it does) | done correctly, with `SET CONSTRAINTS ALL IMMEDIATE` to avoid "pending trigger events" [0011:107-112] |
| `RemoveConstraint`/`AddConstraint` swaps | events 0002 (`one_open_care_follow_up_per_interview` widened) [0002:30-41]; events 0011 (`one_live_interview_per_participant` → `…_per_questionnaire`) [0011:187-199] | inside one transaction on PostgreSQL; the reverse of 0011 refuses when a campero has interviews of two questionnaires (`_refuse_reverse_with_multiple_live_interviews`) [0011:87-104,210] | reverse is a **manual** operation: discard extra interviews first |
| `SeparateDatabaseAndState` pending contract | events 0011 removes `Event.questionnaire_version` from the model, leaves `events_event.questionnaire_version_id` in the DB "for one release" [0011:4-5,200-208] | **PostgreSQL keeps the column (nullable, emptied); SQLite does not** — the scratch DB has no such column (`PRAGMA table_info(events_event)` → 30 columns, none named `questionnaire_version_id`) because a later table rebuild recreated the table from model state [Verified: scratch PRAGMA]. Dev and prod schemas differ; a future `RemoveField` database op must be written to tolerate a missing column on SQLite (`SeparateDatabaseAndState` + `RunSQL` with vendor check) | **Contract step still owed** (S10 in §8) |
| `RunPython` with `noop` reverse (irreversible data) | core 0004 (discipleship flag on, `participant@example.org` deactivated), 0006 (English notice cleared), 0010 (flag on where in use); events 0006 (join codes), 0009 and 0010 (default privacy-notice text replaced) [Verified each file] | reversing the schema leaves the data changes; the two notice rewrites alter a consent-relevant text without a record of the previous text per event (ties to C-08) | acceptable, documented in `docs/architecture.md:265-287` |
| `RunPython` with a real reverse | events 0011 (`restore_single_version`) | restores the lowest-order link only; other links are dropped silently on reverse [0011:75-84] | — |
| `db_default` discipline | `FollowUp.confidential` (0003), `AuthThrottle.strikes` (0005), `Event.join_code/join_by_name` (0006), `Sede.city/order/active` (0008), `Event.questionnaires_in_order`, `confidential_level` ×2 (0011/0012), `Church.logo*` (core 0007) | older code can insert during rolling deploys — the right pattern for expand | keep using it for every new column (§8) |
| Dependency across apps | events 0001 depends on core 0003; events 0002 on `AUTH_USER_MODEL` | cannot migrate `events` without `core` ≥0003 | — |

**Finding C-15 — Severity: Medium.** One contract migration is owed (the orphan column) and the dev database
(SQLite) no longer matches production (PostgreSQL) for it; the next `events` migration that touches `Event` on
SQLite will succeed while the PostgreSQL column keeps existing until explicitly dropped. Recommendation: write
`events/0013` as `SeparateDatabaseAndState(database_operations=[RunSQL("ALTER TABLE events_event DROP COLUMN IF EXISTS questionnaire_version_id", reverse_sql="ALTER TABLE events_event ADD COLUMN questionnaire_version_id bigint NULL")])`
guarded by vendor; rollback = the reverse SQL (column back, empty).

---

## 8. Minimum viable domain evolution — PROPOSAL (not a mandated schema)

Principles: additive only; every camp table and every service/view path untouched by default; new behaviour behind
per-church boolean flags with `db_default=False` (the pattern `Church.discipleship_enabled` already uses
[core/models.py:44-48]; there is **no** other flag mechanism in the repo [Verified: settings env names; grep
FEATURE/flags]); never auto-link people across churches by name/e-mail/phone; a human confirms every link; each
step has a rollback that loses no camp data.

### 8.1 Mapping: vision §5.2 concept → existing model

| Target concept | Existing | Verdict | Notes |
|---|---|---|---|
| `Tenant` | `core.Church` | **reuse as is** | slug global-unique, `active` flag, settings live here already |
| `Campus` | `events.Sede` | **reuse** (later move to `core` by `db_table` alias, not now) | `(church, slug)` unique ✔; `active` ✔ |
| `AuthIdentity` | `auth.User` | **reuse**; harden e-mail uniqueness (S11) | no custom user model needed yet; e-mail = username |
| `Person` | — (`EventParticipant` is per event; `ParticipantProfile` is per account) | **new** `core.Person` | tenant-scoped, account optional |
| `IdentityLink` | — | **new**, minimal form = `Person.user` nullable FK with `unique (church, user)` | explicit table only when one account must map to several persons in one church (not a known need) |
| `CampusAffiliation` | `EventParticipant.sede` (per event) | **new** later; interim `Person.primary_sede` nullable | — |
| `RoleAssignment` | `UserChurchRole` (church scope) + `EventMembership` (event scope) + `TutorAssignment` | **adapt**: keep both tables as the sources; add a **projected** `RoleAssignment` (S7) | `resolve_access` stays the single authorization chokepoint [events/access.py:255-269] |
| `Group` / `GroupMembership` | — | **new** (S8) | subject = `Person`, not `User` |
| `Ministry` / `TeamMembership` | — | **new**, phase 3 | — |
| `Event` / `EventRegistration` | `events.Event` / `events.EventParticipant` | **reuse**; add nullable `EventParticipant.person` (S4) | the campero row stays the registration record |
| `EventAssignment` | `EventMembership` + `TutorAssignment` | **reuse as is** | — |
| `FormDefinition` / `FormVersion` / `Submission` | `Questionnaire` / `QuestionnaireVersion` (+ `Section`/`Question`/`QuestionOption`) / `Interview` + `Answer` (+ `AnswerRevision`) | **reuse** — the camp form engine *is* the form engine | later: allow `Interview` without an `EventParticipant` by a nullable `person`/`context` (XL, not now) |
| `Program` / `Enrollment` | `DiscipleshipPath` / `Enrollment` | **reuse**; later re-point `Enrollment.participant` to `Person` via an additive nullable FK | — |
| `ContentItem` / `Series` | — | **new**, out of scope | — |
| `Workflow` / `WorkflowTask` | `FollowUp` (event), `FollowUpRequest` (church) | **adapt** later; not needed for the camp | — |
| `ConsentRecord` | `events.ConsentRecord` | **adapt** (S6): provenance + nullable `person`/`guardian_person` | keep the append-only history semantics |
| `AuditEvent` | `events.AuditEvent` | **adapt** (S1): nullable `church` FK, backfilled | later `scope_type/scope_id` |

### 8.2 Steps, in order, with rollback and effort

| # | Step (additive) | Flag | Backfill | Rollback | Risk to the camp | Effort |
|---|---|---|---|---|---|---|
| S1 | `AuditEvent.church` nullable FK (`db_default` NULL) + write-through in `audit.record` (`event.church_id` when `event` is given) | none | `UPDATE … SET church_id = event.church_id` in a `RunPython` (idempotent) | drop column (`RemoveField`); readers never filter on it until S7 | none (nullable, no reader) | S |
| S2 | `Church.people_enabled`, `Church.groups_enabled` booleans `db_default=False` | they are the flags | none | drop columns | none | S |
| S3 | `core.Person`: `church` FK, `full_name`, `normalized_name` (reuse `events.models.normalize_name` [events/models.py:42-46]), `birth_date` null, `email` blank, `phone` blank, `primary_sede` null FK, `user` null FK SET_NULL, `created_at`, `updated_at`, `merged_into` null self-FK (tombstone, never physical merge). Constraints: `unique (church, user)` where user not null; index `(church, normalized_name)` | S2 | none (table starts empty) | `DeleteModel` | none (no camp code reads it) | M |
| S4 | `EventParticipant.person` nullable FK SET_NULL + index `(event, person)`; `ParticipantProfile.person` nullable FK | S2 | none automatic | drop columns | none when NULL; `purge`/`erasure` unchanged (SET_NULL means the `Person` survives a campero's deletion — **decide**: erasure must also delete or tombstone the `Person` when it has no other links; add that rule to `delete_participant_data` in the same PR) | M |
| S5 | Linking flow behind `people_enabled`: on registration/import, "¿Es la misma persona que …?" lists candidates **from the same church only** (`Person.church == event.church`) by `normalized_name` token match, requires a click, writes `persona_vinculada` audit with `person` id; "Crear persona" otherwise. Never by e-mail/phone, never across churches. Export/erasure extended to the `Person` | S2 | an optional one-off dry-run report of same-church, same-normalized-name camperos across events (counts only) for the owner to judge; **no automatic linking** | flag off; data stays, nullable | low: new branch in `create_participant`/`apply_import` only when flag on | M |
| S6 | `ConsentRecord` additive: `person` null FK, `guardian_person` null FK, `notice_sha256` char(64) `db_default ''`, `notice_excerpt` char(200) `db_default ''`, `purpose` char(32) `db_default 'entrevista'`; `_record_consents` and `record_guardian_authorization` fill the hash from `event.privacy_notice` | none (hash) / S2 (person) | none; old rows blank | drop columns | none (defaults) | S |
| S7 | `RoleAssignment` **projection**: `subject_user` FK, `church` FK, `scope_type` ('church'|'event'), `scope_id`, `role`, `status`, `start_at`, `end_at`, `source_table`, `source_id`; filled by a management command and by post-save hooks of `UserChurchRole`/`EventMembership`; **read by nothing in authorization** | none | full rebuild command (idempotent) | drop table; `resolve_access` untouched | none | M |
| S8 | `Group` (`church` FK, `sede` null FK, name, `active`) and `GroupMembership` (`group`, `person`, role participant/leader/co_leader, `start_at`, `end_at`) behind `groups_enabled`; "camp → group" = pick `Person`s linked in S5 | S2 | none | `DeleteModel` ×2 | none | L |
| S9 | Discipleship on `Person`: `Enrollment.person` nullable FK alongside `Enrollment.participant` (User); readers prefer `person` when set | S2 | none; optional backfill `person = ParticipantProfile.person` | drop column | none to camp; low to discipleship | M |
| S10 | **Contract** owed by events/0011: drop `events_event.questionnaire_version_id` with vendor-guarded SQL (`IF EXISTS`) | none | — | reverse SQL re-adds a nullable column | none (column is unread since 0011) | S |
| S11 | Identity hardening: report mixed-case duplicate e-mails (read-only command), then partial unique index on `LOWER(auth_user.email)` where email ≠ ''; hash `Invitation.token` into a new `token_hash` column with dual-read for 14 days (the invitation lifetime [core/models.py:147-148]) | none | one-off report | drop index / drop column | none to camp; invitations in flight keep working (dual-read) | M |
| S12 | Later, only if needed: make `RoleAssignment` the source for `UserChurchRole` (dual-write, then switch `get_user_role`), tenant-prefixed URL space for `Event.slug` (global-unique today [events/models.py:142]), `Sede` moved to `core` | per church | — | flag off | medium — not part of the minimum | XL |

Expand/contract pattern for every step: expand (new nullable/defaulted column or new table, `db_default`) → deploy
code that writes both → verify with a read-only integrity command → only then any contract (S10 is the sole
contract in this plan). Every step is one migration file plus one PR; rollback = `migrate <app> <previous>` for
S1/S3/S4/S6/S7/S8/S9 (all additive), the reverse SQL for S10, index drop for S11.

### 8.3 Identity-linking risks to state up front

* The `Person` table must be **church-scoped**; `IdentityLink` (= `Person.user`) must be unique per church, so one
  account can be one person per church and a person in two churches is two rows — exactly the vision's "never
  auto-merge across churches" [docs/EMMAUS_PRODUCT_VISION.md:136].
* `normalized_name` is a warning aid, not a key [events/services/participants.py:28]; minors' names collide;
  `age` is a snapshot, not a birth date — do not infer identity from it.
* `EventParticipant` rows are purged by retention; a linked `Person` must not silently keep the camp's personal
  data alive past the event's retention: the purge should null the link (SET_NULL does) and the `Person` must carry
  its own retention/erasure rule (S4 note).
* `AccessRequest.email` and `AuthThrottle.key` are typed strings, not identities — never link from them.
* `assign_role`'s `user.groups.clear()` [core/services.py:65] must not be reused for any `RoleAssignment` logic;
  Django Groups should be left alone or removed in a contract step.

---

## 9. Findings register

| ID | Severity | Finding | Evidence | Recommendation (incremental) |
|---|---|---|---|---|
| C-01 | Low (Medium SaaS) | E-mail is identity but not DB-unique; `username=email`; `Invitation.token` in clear | core/models.py:176; introspect `auth_user.email`; core/services.py:97; events/services/team.py:86 | S11 |
| C-02 | Medium | Multi-church users: discipleship reads first `UserChurchRole` by id; Groups overwritten; event creation uses `churches[0]` | core/access.py:20-23; core/services.py:65; events/views/home.py:44-47 | church-aware helpers (code only) |
| C-04 | Medium | Single-tenant assumptions in landing/setup | core/views.py:195,200 | F workstream |
| C-05 | Medium | Church isolation in `events` is implicit (event FK) and service-enforced; schema cannot refuse cross-church versions/sedes | §4 table; event_questionnaires.py:71-72; admin.py:834 | integrity check command; S1 |
| C-06 | Medium | Denormalised FK pairs without agreement constraints | events/models.py:868/869, 1057/1058, 537, 1117 | same check |
| C-07 | High (direction) / Info (camp) | No `Person`; camperos have per-event identity only; no guardian identity | §3.4, §5 | S3-S5 |
| C-08 | Medium | Consent rows lack notice version/hash/purpose; notice text rewritten by migrations | events/models.py:602-635; migrations 0009/0010 | S6 |
| C-11 | Medium | Snapshots persist raw sub-threshold counts | metrics.py:556-560,752 | suppress at write or split table |
| C-12 | Low | Events without `ends_on` never expire; staff/requester data has no retention | purge_expired_event_data.py:18 | check + policy text |
| C-13 | Medium | Manual erasure cascades into open safety reports | participants.py:120-129; models.py:1058 | refuse while a safety report is open |
| C-14 | Low | `RETIRED` dead state; `Questionnaire` has no active flag/unique name | grep; models.py:643-655 | wire or drop |
| C-15 | Medium | Owed contract (`questionnaire_version_id`); SQLite/PostgreSQL schema divergence | 0011:200-208; scratch PRAGMA | S10 |
| C-16 | Info | No feature-flag mechanism beyond per-church booleans and env vars | settings grep; core/models.py:44-53 | keep the per-church boolean pattern (S2) |
| C-17 | Low | `Event.slug` and `Church.slug` global-unique; `Question.code` not unique per version | events/models.py:142,769; core/models.py:29 | defer (S12) |
| C-18 | Low | `ParticipantSession.started_by` and several core authorship FKs CASCADE on user delete | events/models.py:1016; core/models.py:305,401,468,495 | SET_NULL in a later additive `AlterField` (no data change) |
| C-19 | Info | Two unrelated "participant" concepts; `core` tables carry redundant `church` FKs unconstrained against the user's role | core/models.py:202-235,367,428,466 | S3/S9 unify on `Person` |

---

## What I could not verify

* Runtime: nothing was run against production or the sandbox; the PostgreSQL schema (in particular whether
  `events_event.questionnaire_version_id` is still present there) is **inferred** from migration 0011, not observed.
* Contents of `core/migrations/0003`'s 26 `AlterField`s were not read line by line; "labels only" rests on
  `docs/architecture.md:270` and on the model's unchanged column types [Inferred].
* Whether the Django admin registers `Section`/`Question`/`QuestionOption` (i.e. whether the published-version
  immutability can be bypassed from `/admin/`): `events/admin.py` not read for this workstream [Unknown].
* The retention window of `purge_decided_requests` (`events/services/join.py:51`) [Unknown: not read].
* Enforcement of `ParticipantQuestion`/`FollowUpRequest`/`AIDraft` status transitions in `core/views.py`
  [Unknown: out of this workstream's depth].
* Whether any test exercises one user holding `UserChurchRole`s in two churches (the grep found files that call
  `assign_role` but I did not confirm a two-church case) [Unknown].
* Tool limitation during the session: the harness's temp volume (`/tmp/claude-1000`, tmpfs) hit its quota
  mid-audit, so the last greps were redirected to files under the audit directory and read back; no repository file
  was modified and nothing was deleted.

## Top 5 for the synthesis

1. **No `Person`, no guardian identity** — the vision's J2 (camp → group) and §5.4 cannot be built on
   `EventParticipant`; the additive path is `core.Person` + nullable `EventParticipant.person` + human-confirmed,
   same-church-only linking (S3-S5). (C-07)
2. **Tenant isolation in the camp is implicit and service-enforced**; 17 of 22 camp tables have no church column,
   `AuditEvent` has none and a nullable event, and single-tenant assumptions remain in landing/setup/event creation.
   Add `AuditEvent.church` and a read-only integrity check before any second tenant. (C-04, C-05, C-06)
3. **Consent has no provenance**: the notice text consented to is not recorded and has been rewritten by two
   migrations; add hash/excerpt/purpose columns (S6). Manual erasure can delete an open safety report (C-13).
4. **Roles are three disjoint mechanisms** (superuser, `UserChurchRole`, `EventMembership`), correct for one church
   per user; the discipleship side picks the first church by id and `assign_role` wipes Django groups. Project a
   `RoleAssignment` table rather than replacing `EventMembership` (S7). (C-02)
5. **Migrations are additive and well-disciplined** (`db_default`, backfills, reverse guards) with one owed contract
   and a dev/prod schema divergence on it; snapshots store raw sub-threshold counts. (C-11, C-15)
