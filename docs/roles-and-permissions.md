# Roles and permissions

All authorization for the camp module is in `events/access.py`. A view never decides access on its own: it resolves an `EventAccess` for `(user, event)` and asks it for querysets and object checks. Roles are per event and never leak to another event.

## The five roles

| Role | Internal value | Who it is |
|---|---|---|
| Administrador | `administrador` | Runs the event: settings, team, camperos, questionnaire |
| Pastor | `pastor` | Sees the whole event, reports and follow-ups |
| Tutor | `tutor` | Supervises a team of coordinadores |
| Coordinador | `coordinador` | Registers and interviews their own camperos |
| Campero | (no account) | Answers only their own interview on a supervised computer |

Camperos are `EventParticipant` rows, not users. In supervised capture the browser holds an anonymous session bound to one `ParticipantSession`.

## How the role is resolved

`resolve_access(user, event)` returns `None` (403 on every event page) for anonymous or inactive users. Otherwise, in this order:

1. **Superuser, or `church_admin` of the event's church** (`core.UserChurchRole`) → **Administrador**, whatever their membership says. If they also have an active `EventMembership`, it is kept, so its `can_view_sensitive` flag still applies.
2. **Deactivated `EventMembership`** for this event → **no access**. A deactivated membership overrides a church-wide `pastor` role for that event.
3. **Active `EventMembership`** for this event → that membership's role (which can itself be `administrador`).
4. **`pastor` of the event's church** in `core`, with no membership in this event → **Pastor**.
5. Otherwise no access.

`accessible_events(user)` lists events of churches where the user is church admin or pastor, plus events with an active membership, excluding archived events (superusers see every event, archived included). It applies the same rule 2: an event where the user has a deactivated membership is left out (unless they are a church admin), so nobody is sent to an event that would answer 403.

## One role per user per event

* `EventMembership` has the unique constraint `(event, user)`: one role per person per event.
* A coordinador is supervised by at most one tutor: `TutorAssignment.coordinator` is a one-to-one field. `TutorAssignment.clean()` requires the tutor to have role `tutor`, the coordinator role `coordinador`, and both to be in the same event.
* A tutor's scope comes only from explicit `TutorAssignment` rows ("Asignar tutores", or the "Tutor que lo supervisa" field when adding or editing a coordinador). Nothing is inferred from team names.
* A campero has at most one coordinador (`EventParticipant.coordinator`). A coordinador who registers a campero is always the one assigned; only administrators can choose another coordinador or leave it empty.
* When an administrator changes someone's role away from `coordinador`, their camperos become unassigned. Away from `tutor`, their assignments are deleted.

## Joining with the event code (access requests)

Every event has a `join_code` (6 characters from `ABCDEFGHJKMNPQRSTUVWXYZ23456789`, generated on save, unique among non-blank codes). The church prints it with "Equipo" → "Ver la hoja para imprimir". The code grants nothing by itself: it only finds the event.

* `/unirme/` takes the code or the event's name and redirects to `/e/<code>/`. A name is compared after `slugify` with both the slug and the event's real name, so accents, case, spaces, hyphens and underscores do not matter; an exact slug wins, other ties go to the oldest event. Names work only while `Event.join_by_name` is on: "Generar otro código" turns it off together with the old code (a leaked sheet stops working whole), and "Volver a permitir el nombre" turns it back on (Administrador only, audited `evento_configurado`). Searches are limited per browser session (`join:<session key>`, 10 per 10 minutes, then blocks starting at 60 s), and a miss on `/e/<x>/` spends the same budget; a hit costs nothing, but while the browser is blocked a hit answers exactly like a miss. A wrong code and a wrong name get the same answer. Archived events are never found; a closed event is found, and its page says "Este evento ya terminó." with no form. Only events in course or in preparation take requests.
* `/e/<code>/` (also `/e/<slug>/`) sends anyone who already has access to their role's home. Anyone else may file an `AccessRequest`: name, e-mail, `tutor` or `coordinador`, an optional note. A signed-in person's request always carries their account e-mail. There is one pending request per e-mail and event (a partial unique constraint); sending the form again shows the same page and creates nothing. Sending is limited per browser session (`join-request:<session key>`, 5 per hour, then blocks starting at 5 minutes), and an event holds at most 50 pending requests ("La lista de solicitudes está llena..."; the event row is locked while one is stored, so the cap holds under concurrency). Name and e-mail are capped at 150 characters, the most an account can store. The answer is one sentence for everyone, whatever the account, the team or the e-mail setup ("Cuando te den acceso podrás entrar con tu correo; si es tu primera vez, te darán un enlace para crear tu contraseña."). No account is created and nobody is signed in.
* **Only an Administrador of the event** sees the requests ("Equipo" → "Solicitudes de acceso") and decides them. Pastors see "Equipo" without the requests or the code. "Agregar al equipo" may give any of the four roles. It runs the same code as "Agregar persona" (`events/services/team.py:add_member`): reuse the account with that e-mail or create one without a password, plus the password link by e-mail and on screen. An account that already has access to the event through `resolve_access` (superuser, church admin or pastor of the event's church, active member) is never changed by a request: approval is refused with "X ya tiene acceso a este evento como <rol>. Si quieres cambiar su rol, hazlo en Equipo, Editar.", the request is closed as `rechazada` with `decision_note` "Ya tenía acceso como <rol>." and nothing is created. Otherwise a church pastor could be demoted to coordinador by a stranger who typed their e-mail, since an active membership outranks the church pastor role. The Equipo row shows, for an e-mail that has an account, "Ya tiene cuenta: <name>; acceso actual en este evento: <rol o ninguno>; iglesia: <church, otra iglesia o ninguna>". A deactivated membership is reactivated with the chosen role instead of a second one being created, and loses confidential access, which is granted apart on "Editar". An existing account that never set a password gets the password link like a new one (when `_can_issue_activation_link` allows it), on approval and on "Agregar persona" alike; an account managed elsewhere (a superuser, or one with a role or team in another church, with or without a password) and a staff account without a password get one message that does not say whether a password exists: "Esta cuenta ya existe y la administra otra iglesia o el sistema; pide a esa administración que le dé acceso." (its owner can use "¿Olvidaste tu contraseña?", which now also serves accounts without a password). "Le enviamos un correo" appears only when an e-mail could go out: with the console, file or dummy backend (`EMAIL_BACKEND`, the console one is the default) nothing is sent and the screen says "No hay correo configurado: comparte este enlace con la persona." A request already decided cannot be decided again. Approve, reject and "Generar otro código" are POST-only and Administrador-only (403 for every other role), and each is audited (`solicitud_aprobada`, `solicitud_rechazada`, `evento_configurado`). The request itself is audited as `solicitud_acceso` by "Sistema", never with the note.

## Permission matrix

"Own" means the camperos assigned to that coordinador; "team" means the camperos of the coordinadores assigned to that tutor.

| Action or screen | Administrador | Pastor | Tutor | Coordinador | Campero |
|---|---|---|---|---|---|
| Create an event ("Crear campamento") | Only superusers and church admins | No | No | No | No |
| Event settings, confirm procedures, computers | Yes | No | No | No | No |
| Add or edit team members, assign tutors | Yes | No | No | No | No |
| "Generar enlace" (password-set link for someone else) | Only for accounts that never set a password, are not staff or superuser, and have no role in another church; audited | No | No | No | No |
| Grant "Puede leer información confidencial" | To other people only | No | No | No | No |
| "Equipo" page | Yes | Read only | No | No | No |
| Register a campero | Yes (any coordinador) | No | No | Own (assigned to self) | No |
| Choose the campero's sede when registering or correcting (only if the church has active sedes) | Yes | No | No | Own | No |
| Import CSV (and download its example file), download the campero list | Yes | No | No | No | No |
| "Sedes de la iglesia": add, edit, deactivate or delete a sede | Only church admins and superusers (not an Administrador who is only a member of the event) | No | No | No | No |
| "Sedes de la iglesia": choose the sedes taking part in the event | Yes | No | No | No | No |
| Correct campero data | Yes | No | No | Own (no change of coordinador or withdrawal) | No |
| Withdraw a campero, reassign coordinador | Yes | No | No | No | No |
| Record guardian authorization ("Marcar como recibida": a confirmation page names the campero and who attests; audited `autorizacion_registrada`) | Yes | No | No | Own | No |
| Remove it ("Quitar la autorización", reason required and kept on the `ConsentRecord` and in the campero's export, not in the log; audited `autorizacion_retirada`, with `interview` metadata when one is in progress, which can still be finished; completed interviews unchanged) | Yes | No | No | Own | No |
| Campero list / detail | Whole event | Whole event | Team | Own | No |
| "Sede" filter on "Camperos", with counts | Whole event | Whole event | Team (counts too) | No (sede shown in "Mis camperos") | No |
| Start, continue, take over an interview | Any active campero | No | No | Own | No |
| Answer questions on a computer handed over ("Entrevista a solas") | No | No | No | No | Own interview only |
| Recover the computer (unlock) | Yes | No | No | Own camperos | No |
| Finalize, choose follow-up decision | Yes | No | No | Own | No |
| Discard an interview in progress | Yes | No | No | Own | No |
| Correct a completed interview | Yes | No | No | Own | No |
| Decide a "Por decidir" follow-up (once) | Yes | No | No | Own | No |
| "Historial de correcciones" | Only with `can_view_sensitive` | Only with `can_view_sensitive` | Only with `can_view_sensitive` | Only with `can_view_sensitive` | No |
| Read answers | Field rules below | Field rules below | Field rules below | Field rules below | Own answers while capturing |
| Follow-up list | Whole event | Whole event | Team | Own | No |
| Update a care follow-up (status, leader) | Yes | Yes | Team | Own | No |
| Read follow-up details and closing notes | Only with `can_view_sensitive` | Only with `can_view_sensitive` | Only with `can_view_sensitive` | Only with `can_view_sensitive` | No |
| Confidential support referrals (created from confidential answers) | Only with `can_view_sensitive` | Only with `can_view_sensitive` | Only with `can_view_sensitive` | Only with `can_view_sensitive` | No |
| Report a safety concern | Yes | Yes | Team | Own | No |
| See and update safety reports | Yes (details only with `can_view_sensitive`) | Only with `can_view_sensitive` | Only with `can_view_sensitive` | Only with `can_view_sensitive` | No |
| Live home | "Inicio" | "Resumen general" | "Mi equipo" | "Mi inicio" | No |
| Reports, cutoffs, PDF, presentation | Event and every team | Event and every team | Own team | No ("Mi avance" only) | No |
| Aggregate answer summaries | Yes | Yes | No | No | No |
| Printable backup list | Whole event | Whole event | Team | Own | No |
| Activity log, "Computadoras en uso" (without `can_view_sensitive`, rows about confidential information and safety reports read "Movimiento confidencial") | Yes | No | No | No | No |
| Download one campero's full data (JSON) | Only with `can_view_sensitive` | No | No | No | No |
| Delete one campero's data (the page counts the open follow-ups, open safety reports and interviews that go with it, never their content, and only what the viewer's `follow_ups()` shows: without `can_view_sensitive`, confidential referrals are not counted, and the constant sentence "Si existieran seguimientos confidenciales, también se borrarían." makes the page identical whether or not one exists; an open safety report needs an extra box) | Yes | No | No | No | No |
| Questionnaires (edit draft, publish, use) | Yes | No | No | No | No |

Whoever files a safety report always sees that it exists in their follow-up list, without being able to read or change it unless they have `can_view_sensitive`.

Some screens are reachable by URL but not in a role's menu. For example pastors and tutors can open `/participantes/` (read only, scoped) and the "Coordinadores" and "Entrevistas" lists.

## Field-level visibility of answers

Each question has a sensitivity level (`Question.sensitivity`). `EventAccess.can_view_answer(interview, question, during_capture=...)` decides per answer. The viewer must first be able to see the campero.

| Level | Who can read it |
|---|---|
| `general` | Anyone who can see the campero |
| `sensible` | The assigned coordinador; an administrador who conducted that interview; anyone with `can_view_sensitive` |
| `confidencial` | Anyone with `can_view_sensitive`. The assigned coordinador (or the administrador conducting it) only while recording it in an **assisted** interview that is **still in progress** |

Consequences:

* In an "Entrevista a solas" the coordinador never sees confidential answers, not even on the review screen.
* After an assisted interview is finalized, the coordinador can no longer read the confidential answers they typed.
* Hidden values do not leak through the layout (`review_rows`). A row the viewer cannot read shows only "Confidencial", the same whether or not it was answered. A conditional question whose controlling answer the viewer cannot read is not listed at all, because its presence would reveal that answer. The page then says "Algunas respuestas solo las puede leer el equipo de cuidado autorizado." (detail) or, on the coordinador's review, that the campero answered some confidential questions on their own and that the care team will learn of any request for support.
* Follow-up hints on the review screen ("Lo que {nombre} pidió") are computed only from non-confidential questions the coordinador can read.
* In the default questionnaire every question is `sensible` or `confidencial`. Tutors and pastors without `can_view_sensitive` therefore see no individual answers, only that a row exists.
* A correction is allowed only for questions the corrector can read now, so a coordinador cannot correct a confidential answer.
* The kiosk review screen shows the campero all of their own answers, and the welcome and review screens tell them who reads them, in the same words as the default privacy notice: “Tus respuestas las lee tu coordinador y, las generales, también el equipo del campamento. Las de temas personales, solo quien te entrevistó, tu coordinador y el equipo de cuidado. Las marcadas “Confidencial”, solo el equipo de cuidado autorizado.”
* Opening an interview that displays at least one answered confidential question is audited (`consulta_confidencial`), and so is opening a follow-up with details.
* On the activity log page (`audit`, Administrador only), an administrador without `can_view_sensitive` does not read which campero a confidential view, a safety report, or an update of a confidential referral or a safety report was about: `consulta_confidencial` and `reporte_riesgo` rows, and `seguimiento` rows about a confidential referral, a safety report or a deleted follow-up, show "Movimiento confidencial" instead of their summary (`audit.with_shown_summaries`). With the flag, the summaries are shown whole. (Safety reports themselves stay listed in "Seguimiento" for administradores; see "Known gaps".)

### `can_view_sensitive`: explicit, per event

`EventAccess.can_view_sensitive` is true only when the user has an **active** `EventMembership` in this event with `can_view_sensitive=True`. Being an administrador or a pastor is not enough. A church admin or pastor who needs it must be added to the event team ("Agregar persona al equipo") with "Puede leer información confidencial" checked.

Administrators grant and remove the flag. Nobody can grant it to themselves: checking it on your own membership, or adding yourself with it, fails with "Otra persona de la administración debe otorgarte este acceso." Every change is audited as `acceso_confidencial`. It grants:

* `sensible` and `confidencial` answers of every campero the user can see;
* the correction history of interviews;
* confidential support referrals and the "Solicitudes de apoyo confidenciales abiertas" count;
* follow-up details and closing notes ("Nota de cierre (confidencial)");
* reading and managing safety reports;
* the "Situaciones de riesgo abiertas" figure in dashboards and PDFs (administrators also see this figure without the flag);
* for administrators, "Descargar todos sus datos".

## Follow-ups and safety reports

`EventAccess.follow_ups()` is scoped through the camperos the user can see. Then:

* **Care follow-ups** (`kind=seguimiento`) are listed for everyone in scope. Administrators, pastors, tutors, the assigned coordinador and anyone with `can_view_sensitive` can change status and leader. The closing note field is shown only to users with `can_view_sensitive`.
* **Confidential support referrals** (`kind=seguimiento`, `confidential=True`) are created by `finalize` when the campero selected a support option (one marked "Sugiere seguimiento") in a confidential question, whatever follow-up decision the coordinador chose. The reason is always "Pidió apoyo en un área de su vida", never which area. They are visible only to users with `can_view_sensitive` (administrators need the flag too), and they are left out of the ordinary follow-up counts. No e-mail is sent for them.
* **Safety reports** (`kind=seguridad`) are listed only for users with `can_view_sensitive`, for administrators, and for the person who created the report. Only `can_view_sensitive` users and administrators can update them. Only `can_view_sensitive` users can read the details. An administrator without the flag sees that a report exists and can assign it, but not what it says.
* E-mail alerts for a new safety report go to active members with `can_view_sensitive`, active members with role `administrador` and the church's `church_admin` users. The message has no names or details.

## Reports and snapshots

* `report_scopes()`: administrators and pastors get `evento` plus one `equipo:<tutor id>` per tutor; a tutor gets only their own `equipo:<id>`; coordinadores get none.
* **Access to a saved cutoff uses the viewer's current scope.** `can_view_snapshot` checks the snapshot's `scope_key` against `report_scopes()` at the time of the request. A tutor removed from the event, or moved to another team, loses access to the old team's cutoffs. Having been able to see a report once grants nothing later.
* The stored payload is trimmed per viewer (`visible_payload`): the safety and confidential-referral figures are removed unless the viewer is an administrador or has `can_view_sensitive`; answer aggregates are shown only to administradores and pastores, and only as released values; a tutor sees only the rows of coordinadores currently assigned to them, even in an older cutoff. The PDF is built from the trimmed payload.

## Revoking access

| To stop... | Do this | Effect |
|---|---|---|
| A team member working in one event | "Equipo" → "Editar" → uncheck "Puede entrar y trabajar en este evento" | `resolve_access` ignores inactive memberships, so the next request gets 403. No session needs to be killed. |
| Confidential access | Uncheck "Puede leer información confidencial" | Takes effect on the next request; audited |
| A campero still answering on some computer | "Configuración" → "Computadoras en uso" → "Cerrar la pantalla" | `ParticipantSession` closes (`revocada`); the kiosk shows "No hay una entrevista abierta en esta computadora"; the interview stays in progress |
| An interview held by another computer | "Computadoras en uso" → "Liberar", or the coordinador's own "Tomar el control aquí" | The claim is cleared or replaced; the old computer can no longer save |
| A person everywhere | Deactivate the Django user (Django admin) | Inactive users are anonymous on their next request |

Passwords: a person who already has a password uses "¿Olvidaste tu contraseña?" on the login page; the link goes only to their own e-mail. An active account that never set a password gets, from the same page, the link to create one (Django's own reset form skips such accounts), under the same limit of 3 per address per hour and with the same answer whether or not the address has an account. "Generar enlace" on the member page is offered only for accounts created from an event team that never set a password, are not staff or superuser, and have no role or membership in another church. Generating it is audited.

Limits of membership deactivation:

* A church `pastor` loses access to the event as soon as their membership in it is deactivated. A church pastor who has no membership can be kept out of one event by adding them to its team and then deactivating that membership. A church admin or superuser is always Administrador, whatever their membership says; remove their `UserChurchRole` (church settings) or deactivate the user instead. The "Computadoras en uso" page says the same.
* Claims expire on their own after 15 minutes without saves; kiosk sessions after 10 minutes idle or 60 minutes in total.

## Known gaps

* A **required confidential question** left blank in an "Entrevista a solas" is a dead end for a coordinador without `can_view_sensitive`. When the campero taps "Ya terminé" without answering it, `finalize` refuses ("Falta responder “{pregunta}”. Toca “Cambiar” junto a ella."), but the review offers no "Cambiar" on a confidential row and `interview_step` sends that screen back to the review ("Esa pregunta es confidencial y la respondió el campero."), because `can_view_answer` lets the coordinador read a confidential question only while typing it in an assisted interview. The same happens with any required question left blank on a screen that also shows a confidential follow-up (for example "¿Tienes dudas acerca de tu fe?" made required): the review does show "Cambiar" on that row, but the whole screen is refused and the link lands back on the review. Two ways out: someone who can open the interview and has `can_view_sensitive` (an Administrador of the event, or the assigned coordinador, with "Puede leer información confidencial") takes control ("Tomar el control aquí") and completes that screen; or the coordinador hands the computer back to the campero (the "Entregar la computadora" screen, `…/entrevistas/<id>/entregar/`, which no button links to once a capture has happened), the campero answers on the kiosk and taps "Ya terminé" again, and the coordinador recovers the computer and finalizes. A pastor with the flag cannot do it: interview screens are for coordinadores and administradores. The question editor warns about it: on a question that is confidential and required, "Obligatoria" says "Si el campero no la contesta, solo el equipo de cuidado autorizado podrá completarla." (`QuestionForm`; shown on the edit screen and when the form comes back with an error, not after a successful save).
* Administrators without confidential access still see that a safety report exists (and can assign it), though not what it says.
* The "Actividad reciente" list on the administrador's home (its last 8 activity rows) does not go through `audit.with_shown_summaries` yet: there, an administrador without `can_view_sensitive` still reads summaries such as "Consultó respuestas confidenciales del campero #0333". The full activity log page does.
* Administrators can add any existing account to their event by e-mail. That account does not get a password link from them (see above), but it does get the role.
* An access request's e-mail is whatever the person typed; nothing verifies it before an Administrador approves. The password link still goes only to that address (or is handed over in person), so a wrong e-mail gives the requester nothing, but the Administrador must know who they are approving. The per-session limits (searches and requests) slow down one browser; a client that drops its cookies starts a new session each time, so the bound that holds against a script is the cap of 50 pending requests per event. Each anonymous search or request leaves a session row (deleted by the scheduler when it expires: `SESSION_COOKIE_AGE` is 12 hours, and `SESSION_EXPIRE_AT_BROWSER_CLOSE` drops the cookie, not the row) and an `AuthThrottle` row (deleted by the scheduler once its window and any block are over, `throttle.purge_expired`).
* Changes made in the Django admin (for example to `EventMembership.can_view_sensitive`) are not written to the activity log. `/admin/` is a technical back office for superusers.
* Sedes belong to the church, but "Sedes de la iglesia" lives inside an event (`/eventos/<slug>/administracion/sedes/`). Adding, editing, deactivating and deleting a sede (`sede_add`, `sede_edit`, `sede_delete`) need a church-admin `UserChurchRole` for the event's church, or a superuser (`administered_churches`); an Administrador who is only a member of one event gets 403 there, because a rename or a deactivation would change the camperos of the church's other events. That Administrador still sees the page (without "Agregar sede" or "Editar", with "Las sedes las administra la iglesia.") and chooses which sedes take part in their own event. A sede with camperos in any event cannot be deleted (`PROTECT`), and every change is audited in the event where it was made (`evento_configurado`).
