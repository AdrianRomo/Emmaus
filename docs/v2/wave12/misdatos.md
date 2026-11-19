# Wave 12 — «Mis datos en Emmaus» and «Quién puede qué»

Branch `v2/wave-12-misdatos`, cut from `v2/wave-11-diseno`. High risk: an export and an authorization
review. First bounded parts of issue #98. Synthetic data only. One additive migration
(`cuenta_datos/0001`: `CreateModel RemovalRequest`).

## What was built

A new small app, `cuenta_datos` («Mi cuenta» itself lives in `core` and is served with the module off, so
it only gained one row that links here, inside its `ui_v2` branch).

**Part A, a person's own data**

- `cuenta_datos/registry.py`: ONE list of 15 categories. Each names the foreign keys it answers for,
  why Emmaus holds them (plain words), where they are seen or changed, and a collector that reads the
  church of the request and one person/account. Every other foreign key to `people.Person` or to the
  account model is in `NOT_THEIRS` with a written reason. `tests/test_registry.py` fails when a new
  foreign key is in neither (128 relations today).
- «Mis datos en Emmaus»: per category, the counts by kind and one way to its screen. Never a text.
- «Descargar mis datos»: one JSON file (attachment, `no-store`, nothing stored), or the same dictionary
  drawn as a page to print. Password again, 2 per day, audited with ids.
- «Pedir que borren mis datos»: one fixed choice → one `RemovalRequest` → one row in «Pendientes» for
  the administration → a task screen with counts and the existing tools. Nothing is deleted.

**Part B, «Quién puede qué»** (`cuenta_datos/powers.py`): read-only, by kind, from the tables
authorization reads; flags; superusers as a count.

## Routes and who may use each

All under `/mi-iglesia/cuenta/`, module `community` on (404 otherwise), through
`groups.views.community_view` (`groups/views.py:55`: signed in, a person of the request's church).

| Route | Name | Who | Rule |
|---|---|---|---|
| `mis-datos/` | `cuenta_datos:home` | any person of the church, about themself only | `community_view`; no id in the address |
| `mis-datos/descargar/` | `download` | the same, not a known minor, with the password | `views.download`; `services.may_download` (imports `oracion.access.known_minor`); `services.check_password`; `services.build_download` |
| `mis-datos/pedir-borrado/` | `ask_removal` | the same, not a known minor (403) | `views.ask_removal` |
| `mis-datos/pedir-borrado/<id>/retirar/` | `withdraw` (POST, in place) | the account that asked, in that church (else 404) | `views.withdraw`, `services._close(own_only=True)` |
| `solicitudes/`, `solicitudes/<id>/`, `…/atendida/` (POST) | `requests`, `request`, `request_done` | church administration; another church's id → 404 | `groups.views.church_admin_view` (`groups/views.py:68`, `has_scope` church manage) |
| `quien-puede-que/` | `powers` | administration and pastors of THIS church; anybody else 404 | `views.powers` → `overview.access.can_see_overview` |

## Product decisions taken (the conservative option each time)

1. **The church is the request's.** The page shows one church. A two-church account reads a line that
   says so and switches church in «Mi cuenta»; the download of A holds nothing of B (tested).
2. **Always the password**, never "a recent sign-in": `last_login` belongs to the account, not to the
   session, so a fresh sign-in on another device would have opened the download on a forgotten one.
   An account without a usable password cannot download and is told how to create one. Wrong passwords:
   5, then a block that doubles (`events.services.throttle`, the sign-in's own mechanism).
3. **Two per day per account and church**, counted from the audit rows inside the transaction that
   writes the new one, after locking the account row. The page to print counts as a download.
4. **The request names the ACCOUNT, not the person record** (`RemovalRequest.user`). So it needed no
   decision in `people/merge.py` and no migration of `people`; the record is looked up when the task opens.
5. **A known minor** reads the page (it is their own screens' data) and gets neither the download nor
   the request: both say the guardian asks the administration directly.
6. **What the administration sees on the task**: a count only for categories whose rows it can already
   see elsewhere (profile, groups, sign-ups, serving, forms, bookings, camp links, roles, account).
   Attendance, notices read, reading, prayer and the discipleship path are named and never counted:
   prayer's own rule is that not even the number leaves its readers' screens.
7. **Left out of the download on purpose** (and said on the page): a pastor's reply to a prayer request;
   to whom a restricted request was addressed (another person's name); the reason the administration
   gave for rejecting a booking; who decided, registered or added anything; stage, tags, follow-ups,
   notes; guardian authorizations (they name the guardian); answers to PUBLIC forms even when the
   administration later tied them to the record (anybody could have typed that contact); the text of the
   legacy discipleship questions and reflections (only counts and dates: the brief's two optional kinds
   of text are prayer and forms); the title of a moment of a service order (the coordinator's words).
8. **Included because the person wrote or chose it**: the title of a booking they asked for; their own
   prayer text and form answers, each behind its own box, default off.
9. **Attendance** is what the group page already shows (decision 0009, `groups.services.own_attendance`):
   the groups the person is in now, while names are kept. Only the meetings they came to are rows.
10. **«E-mail verified» does not exist in the schema.** The page and the file say what does: when the
    account was created, the last sign-in, whether it has a password.
11. **The directory** was built in parallel, inside the `people` app. Since the integration the category
    answers for `people.DirectoryEntry.person`: the page says in one sentence what the person chose and since
    when, with a link to «Aparecer en el directorio» while that page opens for them; the download holds the
    four choices and the date, never a value; the administration's task names the category and never counts
    it. `people.DirectoryPolicy.accepted_by` is the pastor's act for the church (`NOT_THEIRS`). Also since the
    integration: a booking that repeats is the requester's (`espacios.BookingSeries.requested_by`); what a
    person answered when signing up is in their registration's row (how many always, the words behind the box
    of the answers); a headcount and a template of an order are the church's.
12. **«Quién puede qué» change links only for the administration.** A pastor reads the same rows without
    them (those screens would answer 403), except «Personas con acceso», which pastors already open.
13. **Inactive accounts are not listed**: `has_scope` answers no for them. Participants (the church role
    that is a way to take part) are not a power either.
14. **Event teams**: names and role only, of every event of the church, archived ones marked.
    Confidential access is not shown here (the brief: "names and role only").
15. **"Administration that is also the only pastor".** `UserChurchRole` is unique per church and
    account, so one account cannot hold both roles: the literal condition cannot exist. The code checks
    it anyway (it lights up if the constraint ever changes) and flags what the wave-9 limit really is and
    the data can show: the church has exactly one pastor account and an accepted invitation of this
    church says the administration invited it. Without such an invitation nothing is claimed.
16. **Who granted a power**: group guide → `GroupMembership.added_by`; event role → `created_by`; form
    reader → `FormReader.added_by`; church role → the accepted `Invitation` of that e-mail and role when
    there is one; team coordinator → not stored, so nothing is said.
17. **Withdrawing one's request acts in place** (`form[data-inline="borrado"]`); asking has a field and a
    page of its own; «Marcar como atendida» leaves the task for the list, so it is a plain POST.
18. **Navigation**: one rail item «Quién puede qué» under «A tu cargo» for the two roles that read it;
    «Pendientes» stays marked on the task screens; «Mis datos en Emmaus» is reached from «Mi cuenta».

## Shared files touched (append-only)

`emmaus/settings.py` (app), `emmaus/urls.py` (one include before `groups.urls`), `people/audit.py` (four
labels, no new metadata key), `formularios/queue.py` (one source for the administration, in `rows` and
`count`), `core/shell.py` (two view sets, one item, one `or`), `templates/core/account.html` (one row in
the `ui_v2` branch), `static/css/santuario.css` (block `misdatos (wave 12)` before §15).
Not touched: `people/merge.py`, `tenant_integrity`, the seed, the scheduler, `e2e/`.

## Not built

- **Any deletion.** No tool in the repository deletes an account or a person record today (only the
  merge hides one); the task screen says so and sends the administration to the server's operator.
- Guardian-mediated requests for minors; named exports by the administration on a person's behalf.
- Export of «Quién puede qué» (on purpose); history of who held a power before.
- Retention of `RemovalRequest` rows and of the download's audit rows (#76 decides).
- `tenant_integrity` has no check for `RemovalRequest` (account belongs to the church): a candidate.

## Looked at in a browser, and not

With the synthetic seed (`--panorama --eventos`), Playwright with `bypassCSP`, at 390, 768 and 1440 px,
axe (WCAG 2.2 AA) at zero, no horizontal overflow, one `h1`, at most one filled button, no target under
44 px: «Mi cuenta» with its new row; «Mis datos en Emmaus» (a member with a pending group request, a
former camper with almost nothing, a guide); the download form, its wrong-password error and its
third-of-the-day refusal; the real JSON file (saved and parsed); the page to print (also with print
media); «Pedir que borren mis datos» empty, with its error, and the home with the open request;
«Pendientes» with the new row; the list of requests (one, and empty); the task; «Quién puede qué» as
administration and as pastor. Keyboard: «Ver para imprimir» and «Retirar solicitud» with Enter.
Offline: the request form is not sent and says so; «Retirar solicitud» in place restores the button
with its line.

Not looked at: the known-minor state of the page; long names (covered by tests for escaping only; the
rows use the wrapping row specification); a two-church account in the browser; the flags other than
«Nunca ha entrado» (the seed's accounts never signed in, so almost every row carries it: in a real
church that line is rare); the no-pastor and lone-pastor notices; dark/reduced-motion; PostgreSQL (the
integrator's gate) — `select_for_update` on the account and on the request only does its work there.

Known rough edges: the generated line says «hora de America/Mexico_City» (the zone's key); a category
with one fixed row reads «Tu cuenta para entrar: 1».

## Journey steps recommended for `e2e/community-flow.mjs` (not edited)

1. Member: «Mi cuenta» → «Mis datos en Emmaus»: axe and widths; the page holds no other person's name.
2. Member: «Descargar mis datos» with a wrong password stays on the form with the error at the field;
   with the right one the response is an attachment named `mis-datos-<slug>-<date>.json`.
3. Member: «Pedir que borren mis datos» → choose → the home shows the open request; «Retirar solicitud»
   in place leaves the trace line.
4. Administration: «Pendientes» shows «1 solicitud sobre datos personales» → the task → «Marcar como
   atendida» → the empty list.
5. Pastor: «Quién puede qué» opens (axe, widths); the guide gets 404 at the same address.

## What a deletion engine would need, table by table

From the registry and from each column's `on_delete` as the models declare it today. "own" rows are
the person's (a deletion engine would delete or anonymise them, per the retention the leadership sets in
#76); "church's" rows must survive with the person removed from them, or be covered by a stated
exception. Before anything: (1) a policy per category from #76 (what is kept, how long, legal holds);
(2) the camp record's own rule (`purge_expired_event_data`, the family export) stays separate;
(3) `PersonMerge` PROTECTs both records, so a merged person cannot be deleted until its merge rows are
resolved; (4) most "church's" columns CASCADE from the account today, which would silently delete
church records (an event membership's rows, notes a pastor authored): an engine has to re-point or null
those first, not rely on the cascade; (5) backups (#45) need their own purge statement;
(6) `ChurchAuditEvent.summary` may carry a name: anonymising it is a policy decision;
(7) `Submission.contact_*` and `EventRegistration.guest_*` hold contact data with no foreign key at all
and are outside this inventory.

| Table.column | Today: on delete of the person/account | Decision in the registry |
|---|---|---|
| `access.RoleAssignment.subject_user` | CASCADE | own: `roles` |
| `admin.LogEntry.user` | CASCADE | church's: technical |
| `agenda.EventNotice.created_by` | SET_NULL | church's: staff act |
| `agenda.EventPage.created_by` | SET_NULL | church's: staff act |
| `agenda.EventRegistration.checked_in_by` | SET_NULL | church's: staff act |
| `agenda.EventRegistration.person` | CASCADE | own: `eventos` |
| `agenda.EventRegistration.registered_by` | SET_NULL | church's: staff act |
| `agenda.EventSeries.created_by` | SET_NULL | church's: staff act |
| `agenda.GuardianConsent.recorded_by` | SET_NULL | church's: staff act |
| `agenda.GuardianConsent.revoked_by` | SET_NULL | church's: staff act |
| `agenda.SeriesOccurrence.cancelled_by` | SET_NULL | church's: staff act |
| `auth.User_groups.user` | CASCADE | church's: Django mirror |
| `auth.User_user_permissions.user` | CASCADE | church's: Django mirror |
| `care.FollowUp.created_by` | SET_NULL | church's: staff act |
| `care.FollowUp.owner` | SET_NULL | church's: about the person |
| `care.FollowUp.person` | CASCADE | church's: about the person |
| `care.FollowUpStep.by` | SET_NULL | church's: about the person |
| `core.AIDraft.generated_by` | CASCADE | church's: staff act |
| `core.DiscipleshipPath.created_by` | SET_NULL | church's: staff act |
| `core.Enrollment.participant` | CASCADE | own: `discipulado` |
| `core.FollowUpRequest.assigned_to` | SET_NULL | church's: staff act |
| `core.FollowUpRequest.created_by` | SET_NULL | church's: staff act |
| `core.FollowUpRequest.participant` | CASCADE | church's: about the person |
| `core.Invitation.assigned_mentor` | SET_NULL | church's: names another person |
| `core.Invitation.invited_by` | SET_NULL | church's: staff act |
| `core.OnboardingState.user` | CASCADE | own: `cuenta` |
| `core.ParticipantProfile.assigned_mentor` | SET_NULL | church's: names another person |
| `core.ParticipantProfile.user` | CASCADE | own: `perfil` |
| `core.ParticipantQuestion.participant` | CASCADE | own: `discipulado` |
| `core.PastoralNote.author` | CASCADE | church's: staff act |
| `core.PastoralNote.participant` | CASCADE | church's: about the person |
| `core.QuestionResponse.responder` | CASCADE | church's: staff act |
| `core.UserChurchRole.user` | CASCADE | own: `roles` |
| `cuenta_datos.RemovalRequest.closed_by` | SET_NULL | church's: staff act |
| `cuenta_datos.RemovalRequest.user` | CASCADE | own: `cuenta` |
| `espacios.Booking.decided_by` | SET_NULL | church's: staff act |
| `espacios.Booking.requested_by` | SET_NULL | own: `espacios` |
| `espacios.Space.created_by` | SET_NULL | church's: staff act |
| `events.AccessRequest.decided_by` | SET_NULL | church's: staff act |
| `events.Answer.answered_by` | SET_NULL | church's: camp record |
| `events.AnswerRevision.changed_by` | SET_NULL | church's: camp record |
| `events.AuditEvent.actor` | SET_NULL | church's: audit |
| `events.ConsentRecord.recorded_by` | SET_NULL | church's: camp record |
| `events.Event.created_by` | SET_NULL | church's: staff act |
| `events.Event.procedures_approved_by` | SET_NULL | church's: staff act |
| `events.EventMembership.created_by` | SET_NULL | church's: staff act |
| `events.EventMembership.user` | CASCADE | own: `roles` |
| `events.EventParticipant.created_by` | SET_NULL | church's: camp record |
| `events.EventParticipant.updated_by` | SET_NULL | church's: camp record |
| `events.FollowUp.assigned_to` | SET_NULL | church's: camp record |
| `events.FollowUp.created_by` | SET_NULL | church's: camp record |
| `events.FollowUpDecisionChange.changed_by` | SET_NULL | church's: camp record |
| `events.FollowUpDecisionChange.continued_by` | SET_NULL | church's: camp record |
| `events.Interview.conducted_by` | SET_NULL | church's: camp record |
| `events.Interview.discarded_by` | SET_NULL | church's: camp record |
| `events.Interview.lock_holder` | SET_NULL | church's: camp record |
| `events.MembershipAlias.created_by` | SET_NULL | church's: staff act |
| `events.ParticipantSession.started_by` | CASCADE | church's: camp record |
| `events.QuestionnaireVersion.created_by` | SET_NULL | church's: staff act |
| `events.QuestionnaireVersion.published_by` | SET_NULL | church's: staff act |
| `events.ReportSnapshot.created_by` | SET_NULL | church's: camp record |
| `events.TutorAssignment.created_by` | SET_NULL | church's: staff act |
| `feedback.FeedbackGiven.person` | CASCADE | church's: unused column |
| `formularios.Form.created_by` | SET_NULL | church's: staff act |
| `formularios.Form.long_text_ack_by` | SET_NULL | church's: staff act |
| `formularios.Form.task_owner` | SET_NULL | church's: staff act |
| `formularios.FormReader.added_by` | SET_NULL | church's: staff act |
| `formularios.FormTask.closed_by` | SET_NULL | church's: staff act |
| `formularios.FormTask.owner` | SET_NULL | church's: staff act |
| `formularios.FormVersion.published_by` | SET_NULL | church's: staff act |
| `formularios.Submission.person` | SET_NULL | own: `formularios` |
| `groups.Group.created_by` | SET_NULL | church's: staff act |
| `groups.GroupAttendance.person` | CASCADE | own: `asistencia` |
| `groups.GroupJoinRequest.decided_by` | SET_NULL | church's: staff act |
| `groups.GroupJoinRequest.person` | CASCADE | own: `grupos` |
| `groups.GroupMeeting.recorded_by` | SET_NULL | church's: staff act |
| `groups.GroupMembership.added_by` | SET_NULL | church's: staff act |
| `groups.GroupMembership.person` | CASCADE | own: `grupos` |
| `notices.Announcement.created_by` | SET_NULL | church's: staff act |
| `notices.AnnouncementRead.person` | CASCADE | own: `avisos` |
| `notices.EmailPreference.person` | CASCADE | own: `avisos` |
| `notices.NoticeEmail.person` | CASCADE | own: `avisos` |
| `oracion.PrayerPolicy.accepted_by` | SET_NULL | church's: staff act |
| `oracion.PrayerRequest.author` | CASCADE | own: `oracion` |
| `oracion.PrayerRequest.only_reader` | SET_NULL | church's: names another person |
| `oracion.PrayerRequest.person` | CASCADE | own: `oracion` |
| `oracion.ReaderPreference.user` | CASCADE | own: `oracion` |
| `people.CampusAffiliation.person` | CASCADE | own: `perfil` |
| `people.ChurchAuditEvent.actor` | SET_NULL | church's: audit |
| `people.DuplicateDismissal.dismissed_by` | SET_NULL | church's: merge tool |
| `people.DuplicateDismissal.person_a` | CASCADE | church's: merge tool |
| `people.DuplicateDismissal.person_b` | CASCADE | church's: merge tool |
| `people.EventParticipantLink.linked_by` | SET_NULL | church's: staff act |
| `people.EventParticipantLink.person` | CASCADE | own: `campamentos` |
| `people.PeopleImport.imported_by` | SET_NULL | church's: staff act |
| `people.PeopleImport.undone_by` | SET_NULL | church's: staff act |
| `people.Person.birth_date_verified_by` | SET_NULL | church's: staff act |
| `people.Person.merged_into` | SET_NULL | church's: merge tool |
| `people.Person.user` | SET_NULL | own: `perfil` |
| `people.PersonMerge.absorbed` | PROTECT | church's: merge tool |
| `people.PersonMerge.merged_by` | SET_NULL | church's: merge tool |
| `people.PersonMerge.survivor` | PROTECT | church's: merge tool |
| `people.PersonMerge.undone_by` | SET_NULL | church's: merge tool |
| `people.PersonTag.added_by` | SET_NULL | church's: staff act |
| `people.PersonTag.person` | CASCADE | church's: about the person |
| `people.Tag.created_by` | SET_NULL | church's: staff act |
| `resources.ContentApproval.approved_by` | SET_NULL | church's: staff act |
| `resources.ContentApproval.requested_by` | SET_NULL | church's: staff act |
| `resources.ContentApproval.returned_by` | SET_NULL | church's: staff act |
| `resources.Devotional.created_by` | SET_NULL | church's: staff act |
| `resources.DevotionalRead.person` | CASCADE | own: `lectura` |
| `resources.MediaItem.created_by` | SET_NULL | church's: staff act |
| `resources.ReadingPlan.created_by` | SET_NULL | church's: staff act |
| `resources.ReadingPlan.rights_confirmed_by` | SET_NULL | church's: staff act |
| `resources.ReadingPlanProgress.person` | CASCADE | own: `lectura` |
| `serving.Assignment.person` | CASCADE | own: `servir` |
| `serving.PlanItem.responsible` | SET_NULL | own: `servir` |
| `serving.RotationEntry.person` | CASCADE | own: `servir` |
| `serving.ServiceDate.created_by` | SET_NULL | church's: staff act |
| `serving.ServingPreference.person` | CASCADE | own: `servir` |
| `serving.SwapRequest.decided_by` | SET_NULL | church's: staff act |
| `serving.SwapRequest.from_person` | CASCADE | own: `servir` |
| `serving.SwapRequest.to_person` | CASCADE | own: `servir` |
| `serving.Team.created_by` | SET_NULL | church's: staff act |
| `serving.TeamMembership.person` | CASCADE | own: `servir` |
| `serving.Unavailable.person` | CASCADE | own: `servir` |
| `sitio.PublicHome.published_by` | SET_NULL | church's: staff act |
| `sitio.PublicMedia.published_by` | SET_NULL | church's: staff act |
