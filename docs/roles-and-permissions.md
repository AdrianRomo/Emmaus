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
2. **Active `EventMembership`** for this event → that membership's role (which can itself be `administrador`).
3. **`pastor` of the event's church** in `core` → **Pastor**, with no membership.
4. Otherwise no access.

`accessible_events(user)` lists events of churches where the user is church admin or pastor, plus events with an active membership, excluding archived events (superusers see every event, archived included).

## One role per user per event

* `EventMembership` has the unique constraint `(event, user)`: one role per person per event.
* A coordinador is supervised by at most one tutor: `TutorAssignment.coordinator` is a one-to-one field. `TutorAssignment.clean()` requires the tutor to have role `tutor`, the coordinator role `coordinador`, and both to be in the same event.
* A tutor's scope comes only from explicit `TutorAssignment` rows ("Asignar tutores", or the "Tutor que lo supervisa" field when adding or editing a coordinador). Nothing is inferred from team names.
* A campero has at most one coordinador (`EventParticipant.coordinator`). A coordinador who registers a campero is always the one assigned; only administrators can choose another coordinador or leave it empty.
* When an administrator changes someone's role away from `coordinador`, their camperos become unassigned. Away from `tutor`, their assignments are deleted.

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
| Import CSV, download the campero list | Yes | No | No | No | No |
| Correct campero data | Yes | No | No | Own (no change of coordinador or withdrawal) | No |
| Withdraw a campero, reassign coordinador | Yes | No | No | No | No |
| Record guardian authorization | Yes | No | No | Own | No |
| Campero list / detail | Whole event | Whole event | Team | Own | No |
| Start, continue, take over an interview | Any active campero | No | No | Own | No |
| Answer questions on a supervised computer | No | No | No | No | Own interview only |
| Recover the computer (unlock) | Yes | No | No | Own camperos | No |
| Finalize, choose follow-up decision | Yes | No | No | Own | No |
| Discard an interview in progress | Yes | No | No | Own | No |
| Correct a completed interview | Yes | No | No | Own | No |
| Decide a "pendiente de evaluación" follow-up (once) | Yes | No | No | Own | No |
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
| Activity log, "Sesiones abiertas" | Yes | No | No | No | No |
| Download one campero's full data (JSON) | Only with `can_view_sensitive` | No | No | No | No |
| Delete one campero's data | Yes | No | No | No | No |
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

* In supervised capture the coordinador never sees confidential answers, not even on the review screen.
* After an assisted interview is finalized, the coordinador can no longer read the confidential answers they typed.
* Hidden values do not leak through the layout (`review_rows`). A row the viewer cannot read shows only "Confidencial", the same whether or not it was answered. A conditional question whose controlling answer the viewer cannot read is not listed at all, because its presence would reveal that answer. The page then says "Algunas respuestas solo las puede leer el equipo de cuidado autorizado." (detail) or, on the coordinador's review, that the campero answered some confidential questions on their own and that the care team will learn of any request for support.
* Follow-up hints on the review screen ("Sugerencia según sus respuestas") are computed only from non-confidential questions the coordinador can read.
* In the default questionnaire every question is `sensible` or `confidencial`. Tutors and pastors without `can_view_sensitive` therefore see no individual answers, only that a row exists.
* A correction is allowed only for questions the corrector can read now, so a coordinador cannot correct a confidential answer.
* The kiosk review screen shows the campero all of their own answers.
* Opening an interview that displays at least one answered confidential question is audited (`consulta_confidencial`), and so is opening a follow-up with details.

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
* **Confidential support referrals** (`kind=seguimiento`, `confidential=True`) are created by `finalize` when the campero selected a support option (one marked "Sugiere seguimiento") in a confidential question, whatever follow-up decision the coordinador chose. The reason is always "Pidió apoyo en un área de su vida", never which area. They are visible only to users with `can_view_sensitive`, administrators included, and they are left out of the ordinary follow-up counts.
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
| A campero still answering on some computer | "Configuración" → "Sesiones abiertas" → "Cerrar" | `ParticipantSession` closes (`revocada`); the kiosk shows "No hay una entrevista abierta en esta computadora"; the interview stays in progress |
| An interview held by another computer | "Sesiones abiertas" → "Liberar", or the coordinador's own "Tomar el control aquí" | The claim is cleared or replaced; the old computer can no longer save |
| A person everywhere | Deactivate the Django user (Django admin) | Inactive users are anonymous on their next request |

Passwords: a person who already has a password uses "¿Olvidaste tu contraseña?" on the login page; the link goes only to their own e-mail. "Generar enlace" on the member page is offered only for accounts created from an event team that never set a password, are not staff or superuser, and have no role or membership in another church. Generating it is audited.

Limits of membership deactivation:

* It does not remove access that comes from `core`. A church `pastor` whose membership is deactivated still resolves as **Pastor** through the church role, and a church admin or superuser is always Administrador. The "Sesiones abiertas" page says "quítale el acceso desde Equipo. Su sesión deja de funcionar de inmediato", which is not true for those users. Remove their `UserChurchRole` or deactivate the user instead.
* Claims expire on their own after 15 minutes without saves; kiosk sessions after 10 minutes idle or 60 minutes in total.

## Known gaps

* Administrators without confidential access still see that a safety report exists (and can assign it), though not what it says.
* Administrators can add any existing account to their event by e-mail. That account does not get a password link from them (see above), but it does get the role.
* Changes made in the Django admin (for example to `EventMembership.can_view_sensitive`) are not written to the activity log. `/admin/` is a technical back office for superusers.
