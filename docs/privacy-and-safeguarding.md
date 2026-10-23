# Privacy and safeguarding

Emmaus Campamentos records spiritual and personal conversations with camperos, many of them minors. This page describes what the code stores, who can see it, and what the software does and does not do to protect it. It is not legal advice. The church remains responsible for its procedures and for complying with Mexican data-protection law, including the LFPDPPP; review that with legal counsel before using Emmaus with real people.

## Data inventory

| Model | Personal data | Who can see it in the app |
|---|---|---|
| `EventParticipant` (campero) | Full name, age, room, church or group, assigned coordinador, interview status, withdrawal reason | Administradores and pastores (whole event), tutores (their team), coordinadores (their own camperos) |
| `ConsentRecord` | Kind (guardian authorization, consent, assent), status, method (paper, verbal, digital), who recorded it, when, notes | Same as the campero (latest records on the campero page) |
| `Interview` | Mode, status, who conducted it, station, timestamps, follow-up decision, correction count, discard reason | Same as the campero |
| `Answer` | The answers: selected options and free text | Per field, by sensitivity (below) |
| `AnswerRevision` | Previous value of a corrected answer, reason, who | Previous values are stored but not displayed; the history list (question, date, who, reason) is visible to administradores and `can_view_sensitive` users |
| `FollowUp` | Reason, status, assigned leader, confidential details (safety reports), confidential closing note | Care follow-ups: everyone in scope; details and notes: only `can_view_sensitive`. Safety reports: see below |
| `ParticipantSession` | Which interview and station, who handed over, token hashes, timestamps | Administradores ("Sesiones abiertas") |
| `EventMembership`, `TutorAssignment` | Staff name and e-mail (from `User`), role, team name, confidential-access flag | Administradores; pastores see the "Equipo" page |
| `ReportSnapshot` | Aggregate counts, team labels, coordinador names; no campero names, no answers | Administradores, pastores, tutores (own team) |
| `AuditEvent` | Who did what and when; campero numbers (for example "#0042"), staff names; never answer contents | Administradores ("Registro de actividad") |
| Django sessions (database) | Login, current station, interview claim tokens, kiosk token, and during a CSV import the preview rows (names, ages, rooms) until confirmed or cancelled | Server operators only |

Camperos have no accounts and no e-mail or phone fields.

## Sensitivity levels

Each question has a level, set in the questionnaire editor ("Nivel de privacidad"):

| Level | Meaning | Who reads individual answers |
|---|---|---|
| General | Operational, non-personal | Anyone who can see the campero |
| Sensible | Spiritual situation and decisions | The assigned coordinador, an administrador who conducted that interview, and members with "Puede leer información confidencial" |
| Confidencial | Intimate or free-text sharing | Only members with "Puede leer información confidencial". The coordinador sees it only while typing it in an assisted interview; never in supervised capture, never after finalizing |

In the default questionnaire, "¿Te consideras salvo?", "¿Tienes dudas acerca de tu fe?", "¿Tomaste alguna decisión espiritual...?" and the orientation question are `sensible`. The doubts detail, "¿Hay alguna situación en tu vida en la que te gustaría recibir apoyo?", its detail, and "¿Hay algo que te impida dar el siguiente paso en tu fe?" are `confidencial`. No question is `general`.

Confidential access (`can_view_sensitive`) is never automatic: administradores and pastores do not get it by role. It is granted per event, per person, and each grant or removal is audited. Each time someone opens an interview showing answered confidential questions, or a follow-up with details, an audit row `consulta_confidencial` is written.

The interview screens show "Confidencial: solo el equipo de cuidado autorizado podrá leerla" on confidential questions, and every question offers "Prefiero no responder".

## Consent records

* An interview cannot start until the required confirmations are ticked: guardian authorization for minors (when the event requires it and none is on file), assent for minors, consent for adults. See `interview-station-workflow.md`.
* The coordinador's tick is the record. The app does not store the signed paper form; the church keeps it.
* Guardian authorizations are recorded on registration, in the CSV import, with "Marcar como recibida", or at the start of the interview. They are kept as a history; the latest record decides.
* Withdrawal of guardian authorization (`revocado`, `negado`) exists in the model but there is no screen to record it.

## The church approves its procedures first

Interviews are blocked until an administrador ticks, in "Configuración" → "Procedimientos de la iglesia": "Confirmo que la iglesia definió y aprobó sus procedimientos de autorización, protección de menores y atención de situaciones de riesgo." The app records who confirmed and when. Until then every interview start shows "La administración aún no confirma que la iglesia aprobó sus procedimientos de autorización y protección." The app cannot check that the procedures exist; the confirmation is the church's responsibility. There is no button to undo it.

Each event also stores its own "Aviso de privacidad" (read to the campero before starting) and "Qué hacer ante una situación de riesgo" (shown on every safety report). Both have defaults and should be adapted by the church.

## Retention

* "Días que se conservan las respuestas después del evento" (`retention_days`, 30 to 3650, default 365) counts from the event end date (`ends_on`).
* `python manage.py purge_expired_event_data` lists the events past their retention period and how many camperos would be deleted. It is a dry run by default. With `--apply` it deletes every campero of those events, which cascades to interviews, answers, correction history, consents, follow-ups and capture sessions, sets `data_purged_at` and writes an audit row.
* Kept after a purge: cutoffs (aggregates), the event, the team, the questionnaire and the activity log.
* Not automatic: nothing schedules the purge. Run it by hand or with cron. An event without an end date is never purged. Database backups are not affected; their retention is the operator's job.

## Export and deletion for one campero

On the campero page, administradores see:

* "Descargar todos sus datos" (only with confidential access): a JSON file with the registry data, the interview status and version, every answer as question and text, and the authorization records. Audited as `exportacion`.
* "Eliminar sus datos": requires typing the full name, then deletes the campero with their interview, answers, authorizations and follow-ups. Audited as `campero_eliminado` with the number only. Cutoffs are not changed.

"Descargar lista de camperos" exports registry data only (no answers), as CSV, and is audited.

## Activity log

`AuditEvent` records who did what: event changes, procedure confirmation, team changes and confidential-access grants, campero registration, edits, import and deletion, authorizations, interview start, take-over, completion, correction and discard, hand-over and recovery of computers, session revocations, confidential views, follow-up updates, safety reports, cutoffs and downloads. The summary and metadata never contain answer contents. The app offers no way to edit or delete these rows (the Django admin makes them read-only for editing).

## E-mail

* New safety report: sent to the event's care team (members with confidential access, administradores and church administrators). Subject "Emmaus: reporte urgente en {evento}". The body says a report exists and asks them to log in; it has no names and no details.
* New team member: a link to create their password. It contains the staff member's first name and the event name.
* No e-mail ever contains answers or campero names. A sending failure is logged (subject and error) and does not block the action; the new-member screen always shows the link to copy.

## No external AI in the camp module

The camp module makes no outbound calls and uses no AI. Recommendations and follow-up suggestions are fixed rules ("Estas acciones salen de reglas fijas... No hay inteligencia artificial"). The only AI feature in the codebase is the optional reply-draft helper of the older discipleship module (`core`), off unless `LLM_ENABLED=true`, and it never reads camp data.

## Logs

Logging goes to the console. No handler prints request bodies, so answers are not logged. `django.request` logs only warnings and errors (method, path, status). Two caveats:

* Searches use the URL (`?q=`), so a campero name typed in a search appears in the URLs that a reverse proxy may log. gunicorn's access log is off in the shipped command.
* A failed cutoff stores the exception text (up to 500 characters) on the snapshot and logs a traceback.

## Browser and HTTP protections

Set on every non-static response by `PrivateResponseMiddleware` and Django settings:

* `Cache-Control: no-store, max-age=0` and `Pragma: no-cache`: confidential pages are not kept by the browser or a proxy, so the back button on a shared computer does not show them.
* Content-Security-Policy: `default-src 'self'`, `script-src 'self'` (no inline or third-party scripts), `frame-ancestors 'none'`, `form-action 'self'`, `object-src 'none'`, fonts only from the same site, images from the same site or `data:` URIs.
* `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()`, and `<meta name="robots" content="noindex, nofollow">`.
* Secure session and CSRF cookies by default when `DEBUG=false`; HSTS and HTTPS redirect are configurable (`deployment.md`).

## Shared-computer protections

* Supervised capture logs the coordinador out before the campero touches the computer. The browser is confined to `/estacion/` and holds only a random token for one interview. Recovering it needs a password, with a pause after 5 wrong attempts.
* The campero sees only their own answers; the screen locks after 5 minutes idle in the browser, 10 minutes on the server, 60 minutes at most.
* Staff logins end when the browser closes and after 30 minutes idle (`EMMAUS_IDLE_LOGOUT_SECONDS`). The stored session also expires 12 hours after its last change (`SESSION_COOKIE_AGE`), which in practice only matters for abandoned sessions. A dashboard left open keeps refreshing, and each refresh counts as activity, so close the session with "Cerrar sesión".
* Only one computer can edit an interview at a time; "Tomar el control aquí" is explicit and audited.
* The printable "Lista de respaldo" contains registry data only (no answers) and warns to keep it out of sight and destroy it afterwards.

## Small-group suppression

Spiritual and support results are shown only as event-level totals, to administradores and pastores, rounded to multiples of the minimum group size and hidden below it; projected presentations never show them and suppress follow-up counts. Details and limits are in `reporting.md` ("Small-group suppression"). In short, rounding lowers but does not eliminate re-identification risk, and a result where everyone answered the same way is visible as such.

## Safety escalation workflow

1. Anyone on the team who can see the campero opens "Reportar una situación de riesgo" (from the interview review or the campero page).
2. The first thing shown is "Primero, actúa" with the event's instructions. Default: "Si alguien está en peligro inmediato, llama al 911. Después avisa en persona a la persona responsable de protección del campamento. No investigues por tu cuenta ni prometas guardar un secreto que ponga en riesgo a alguien."
3. "¿Qué pasó?": a short description (up to 4000 characters), then "Enviar reporte". It creates a `FollowUp` of kind "Situación de riesgo", audits `reporte_riesgo` and, after the transaction commits, e-mails the care team without names or details.
4. The report appears with an "Urgente" tag in "Seguimiento" for the care team (confidential access) and administradores, who assign a leader and update the status. Only confidential-access members read the details and the closing note.
5. The reporter sees that the report exists but not its content.

Emmaus is a record and a notification, not an emergency service. The app does not call anyone and does not check that the e-mail was read; the church's procedure must cover the human steps.

## Known limitations

* Visibility rules hide values, not structure: someone without confidential access can see that a confidential question was answered, and the presence of a conditional follow-up question reveals the parent answer. Review-screen suggestions such as "Pidió apoyo en un área de su vida" come from confidential answers.
* Expired Django sessions stay in the database until `python manage.py clearsessions` is run; schedule it.
* The Django admin (for superusers) can change memberships without writing to the activity log, and can delete activity-log rows.
* Backups contain everything, including answers.

## Antes de usar Emmaus con menores reales

- [ ] La iglesia definió y aprobó por escrito quién autoriza las entrevistas, cómo se recaban y guardan las autorizaciones firmadas de padres, madres o tutores legales, quién atiende las situaciones de riesgo y cuánto tiempo se conservan los datos.
- [ ] La iglesia revisó con asesoría legal sus obligaciones conforme a la LFPDPPP y su aviso de privacidad.
- [ ] Adaptaste en "Configuración" el "Aviso de privacidad" y "Qué hacer ante una situación de riesgo" con los nombres y teléfonos reales del equipo de protección.
- [ ] Revisaste la "Edad de mayoría", "Pedir autorización de padre, madre o tutor legal para menores", el "Tamaño mínimo de grupo" y los "Días que se conservan las respuestas".
- [ ] Revisaste el cuestionario y el nivel de privacidad de cada pregunta.
- [ ] Solo las personas del equipo de cuidado tienen "Puede leer información confidencial", y cada una sabe que sus consultas quedan registradas.
- [ ] Cada coordinador tiene su propia cuenta y contraseña, y sabe recuperar la computadora en la captura supervisada.
- [ ] El servidor usa HTTPS, `DEBUG=false`, respaldos cifrados y `manage.py check --deploy` sin la advertencia `emmaus.W001` (cuentas de demostración).
- [ ] Hay una persona encargada de ejecutar `purge_expired_event_data --apply` y `clearsessions` cuando corresponda.
- [ ] Hiciste una prueba completa con datos ficticios antes del campamento.
- [ ] Solo entonces marcaste la casilla de procedimientos y pulsaste "Confirmar".
