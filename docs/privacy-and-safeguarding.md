# Privacy and safeguarding

Emmaus Campamentos records spiritual and personal conversations with camperos, many of them minors. This page describes what the code stores, who can see it, and what the software does and does not do to protect it. It is not legal advice. The church remains responsible for its procedures and for complying with Mexican data-protection law, including the LFPDPPP; review that with legal counsel before using Emmaus with real people.

## Data inventory

| Model | Personal data | Who can see it in the app |
|---|---|---|
| `EventParticipant` (campero) | Full name, age, room, church or group, assigned coordinador, interview status, withdrawal reason | Administradores and pastores (whole event), tutores (their team), coordinadores (their own camperos) |
| `ConsentRecord` | Kind (guardian authorization, consent, assent), status, method (paper, verbal, digital), who recorded it, when, notes | Same as the campero (latest records on the campero page) |
| `Interview` | Questionnaire and version, mode, status, who conducted it, station, timestamps, follow-up decision, correction count, discard reason, the confidential level it started with | Same as the campero |
| `Answer` | The answers: selected options and free text | Per field, by sensitivity (below) |
| `AnswerRevision` | Previous value of a corrected answer, reason, who | Previous values are stored but not displayed; the history list (question, date, who, reason) is visible only to `can_view_sensitive` users |
| `FollowUp` | Reason, status, assigned leader, confidential details (safety reports), confidential closing note, `confidential` flag | Ordinary care follow-ups: everyone in scope; confidential support referrals: only `can_view_sensitive`; details and notes: only `can_view_sensitive`. Safety reports: see below |
| `ParticipantSession` | Which interview and station, who handed over, token hashes, timestamps | Administradores ("Computadoras en uso") |
| `EventMembership`, `TutorAssignment` | Staff name and e-mail (from `User`), role, team name, confidential-access flag | Administradores; pastores see the "Equipo" page |
| `AccessRequest` | Name, e-mail, requested role (tutor or coordinador) and an optional note typed on the event's public page; status, who decided and when, and why when the account already had access. At most 50 pending per event. Approved and rejected requests are deleted by the scheduler 30 days after the decision (the activity log keeps who decided); all are deleted with the event | Administradores ("Equipo" → "Solicitudes de acceso"); the note never goes to the activity log |
| `ReportSnapshot` | Aggregate counts, team labels, coordinador names; no campero names, no answers | Administradores, pastores, tutores (own team) |
| `AuditEvent` | Who did what and when; campero numbers (for example "#0042"), staff names; never answer contents | Administradores ("Registro de actividad") |
| `AuthThrottle` | The e-mail typed at login or in "¿Olvidaste tu contraseña?" (as part of the key) and attempt counts; kiosk unlock counters keyed by capture session; join searches and access requests keyed by browser session (`join:`, `join-request:`), never by what was typed. The scheduler deletes a row once its window (1 hour at most) and any block are over, so rows grow with recent activity only | Server operators only |
| Django sessions (database) | Login, current station, interview claim tokens, kiosk token, and during a CSV import the preview rows (names, ages, rooms) until confirmed or cancelled. Expired sessions are deleted by the scheduler on every pass | Server operators only |

Camperos have no accounts and no e-mail or phone fields.

## Sensitivity levels

Each question has a level, set in the questionnaire editor ("Nivel de privacidad"):

| Level | Meaning | Who reads individual answers |
|---|---|---|
| General | Operational, non-personal | Anyone who can see the campero |
| Sensible | Spiritual situation and decisions | The assigned coordinador, an administrador who conducted that interview, and members with "Puede leer información confidencial" |
| Confidencial | Intimate or free-text sharing | At the event's default level (below): only members with "Puede leer información confidencial". The coordinador sees it only while typing it in an assisted interview; never in supervised capture, never after finalizing |

In the default questionnaire, "¿Te consideras salvo?", "¿Tienes dudas acerca de tu fe?", "¿Tomaste alguna decisión espiritual...?" and the orientation question are `sensible`. The doubts detail, "¿Hay alguna situación en tu vida en la que te gustaría recibir apoyo?", its detail, and "¿Hay algo que te impida dar el siguiente paso en tu fe?" are `confidencial`. No question is `general`.

Confidential access (`can_view_sensitive`) is never automatic: administradores and pastores do not get it by role. It is granted per event, per person, by another administrador (nobody can grant it to themselves), and each grant or removal is audited. Each time someone opens an interview showing answered confidential questions, or a follow-up with details, an audit row `consulta_confidencial` is written.

Hidden answers are also hidden in the page structure: a row the viewer cannot read shows only "Confidencial" (answered or not), conditional questions that depend on a hidden answer are not listed, and the coordinador's follow-up hints use only questions they can read.

When a campero selects a support area in a confidential question (in the default questionnaire, "¿Hay alguna situación en tu vida en la que te gustaría recibir apoyo?"), finalizing the interview creates a confidential care referral, "Pidió apoyo en un área de su vida", visible only to the care team ("equipo de cuidado autorizado"). It is created whatever the coordinador decided, so a request made in private does not depend on someone who could not read it.

The interview screens show the "Confidencial" tag and "Solo el equipo de cuidado autorizado podrá leer esta respuesta." on confidential questions (at the other levels, "Esta respuesta la leen …" with who), and every question offers "Prefiero no responder".

**Each event decides who reads confidential answers.** On "Respuestas confidenciales" an Administrador (from "Configuración" → "Privacidad y protección") or a Pastor (from "Resumen general") chooses `Event.confidential_level`: `cuidado`, the default and the rule of every event before it existed; `coordinacion`, read like `sensible` answers; or `todo_el_equipo`, anyone who can see the campero. Each change is audited (`acceso_confidencial`). Opening it is not retroactive: an interview keeps the level its event had when it started, and the stricter of the two applies, so nobody reads under a wider rule what a campero said under a narrower promise. Closing it applies at once. A confidential answer someone may read carries its "Confidencial" tag on the interview's detail and review, and opening it is audited as before (`consulta_confidencial`), also when the correction screen shows it; an answer someone may not read shows only the tag of its own level ("Sensible" or "Confidencial"). The kiosk's "¿Quién lee tus respuestas?" sentence, the note under the question and the reminder on "Entrega la computadora" (which quotes the kiosk's sentence) follow the interview's level; the confidential note on the paper sheet follows the event's level, the one an interview typed in from it starts with. The question editor names no readers, because a questionnaire belongs to the church and several events may use it: it says each event decides and links to "Respuestas confidenciales". The event's "Aviso de privacidad" is free text and does not follow the level. After saving a level other than `cuidado`, Emmaus asks to check it ("Guardado. Revisa que el aviso de privacidad del evento diga lo mismo."): the administración must align it by hand, and the coordinador reads it to the campero before starting.

## Consent records

* An interview cannot start until the required confirmations are ticked: guardian authorization for minors (when the event requires it and none is on file), assent for minors, consent for adults. See `interview-station-workflow.md`.
* The coordinador's tick is the record. The app does not store the signed paper form; the church keeps it.
* Guardian authorizations are recorded on registration, in the CSV import, with "Marcar como recibida", or at the start of the interview. They are kept as a history; the latest record decides.
* Withdrawal: "Quitar la autorización" on the campero page (the campero's coordinador or an administrador) records a `revocado` entry with the reason, which is required (up to 200 characters). The reason stays on the record and in the campero's data export, not in the activity log (`autorizacion_retirada`, summary only). Completed interviews are not changed. An interview in progress is not stopped (the campero is already talking): the page says so, and the audit row carries that interview's id. Any later interview needs a new authorization.

## The church approves its procedures first

Interviews are blocked until an administrador ticks, in "Configuración" → "Procedimientos de la iglesia": "Confirmo que la iglesia definió y aprobó sus procedimientos de autorización, protección de menores y atención de situaciones de riesgo." The app records who confirmed and when. Until then every interview start shows "La administración todavía no confirma los procedimientos de autorización y protección de la iglesia. Avísale." The app cannot check that the procedures exist; the confirmation is the church's responsibility. There is no button to undo it.

Each event also stores its own "Aviso de privacidad" (read to the campero before starting) and "Qué hacer ante una situación de riesgo" (shown on every safety report). Both have defaults and should be adapted by the church.

The default notice (`events.models.DEFAULT_PRIVACY_NOTICE`) says who reads each level exactly as `EventAccess.can_view_answer` implements it (see "Sensitivity levels"): “Tus respuestas las lee tu coordinador y, las generales, también el equipo del campamento. Las de temas personales, solo quien te entrevistó, tu coordinador y el equipo de cuidado. Las marcadas “Confidencial”, solo el equipo de cuidado autorizado.” It also keeps the purpose, "Prefiero no responder", and that answers are not shared outside the church or analysed by AI. Migration `events/0010` replaces the previous default (it said "El equipo del campamento podrá leer tus respuestas", wider than the access rules) on every event still holding it word for word; a notice the church edited is left alone, and going back does not restore the old text.

## Retention

* "Días que se conservan las respuestas después del evento" (`retention_days`, 30 to 3650, default 365) counts from the event end date (`ends_on`).
* `python manage.py purge_expired_event_data` lists the events past their retention period and how many camperos would be deleted. It is a dry run by default. With `--apply` it deletes every campero of those events, which cascades to interviews, answers, correction history, consents, follow-ups and capture sessions, sets `data_purged_at` and writes an audit row.
* Kept after a purge: cutoffs (aggregates), the event, the team, the questionnaires and the activity log.
* Automatic for finished events: the `scheduler` service (`run_report_cutoffs`) runs the same purge once a day, after 03:00 in the event's time zone, for events marked Terminado or Archivado that have no open safety report or confidential referral (see "Retention" in `deployment.md`). An event still En curso or En preparación is purged only by hand. An event without an end date is never purged. Database backups are not affected; their retention is the operator's job.

## Export and deletion for one campero

On the campero page, administradores see:

* "Descargar todos sus datos" (only with confidential access): a JSON file, `campero-<número>.json`, meant to be handed to a family. Its keys are Spanish words, not identifiers: `evento`, `número de campero`, `nombre`, `edad`, `cuarto`, `iglesia o grupo`, `fecha de registro`; `entrevista` with `estado`, `versión del cuestionario`, `fecha en que se completó` and `respuestas` (each one `pregunta`, `respuesta`, `fecha`, the time it was last saved), the newest interview; when the campero has more than one (several questionnaires), also `entrevistas`, every one oldest first, each with its `cuestionario` (title) and the same keys; and `autorizaciones` (each one `tipo`, `estado`, `fecha`, `notas`; for a withdrawal, its reason). Dates are written out with seconds, in the event's time zone, which they name ("26 de octubre de 2026, 11:00:30 (hora de Ciudad de México)"; a zone outside the event settings' list is named by its key). It does not contain follow-ups, safety reports or the correction history. Audited as `exportacion`.
* "Eliminar sus datos": requires typing the full name, then deletes the campero with their interview, answers, authorizations and follow-ups. Audited as `campero_eliminado` with the number only. Cutoffs are not changed.

"Descargar la lista de camperos" exports registry data only (no answers), as CSV, and is audited. Cells that start with `=`, `+`, `-`, `@`, a tab or a carriage return are prefixed with `'` so a spreadsheet does not run them as formulas.

## Activity log

`AuditEvent` records who did what: event changes, procedure confirmation, team changes and confidential-access grants, campero registration, edits, import and deletion, authorizations, interview start, take-over, completion, correction and discard, hand-over and recovery of computers, session revocations, confidential views, follow-up updates, safety reports, cutoffs and downloads. The summary and metadata never contain answer contents. The app offers no way to edit or delete these rows (the Django admin makes them read-only for editing).

Some summaries name the campero next to something only the care team may know ("Consultó respuestas confidenciales del campero #0333", "Reportó una situación de riesgo del campero #0333"). On the activity log page ("Registro de actividad", `audit`), a viewer without confidential access sees those rows as "Movimiento confidencial", with who and when but not the campero: every `consulta_confidencial` and `reporte_riesgo` row, and `seguimiento` rows about a confidential referral, a safety report, or a follow-up that no longer exists (`audit.with_shown_summaries`). The stored rows are unchanged, and viewers with the flag see them whole.

## E-mail

* New safety report: sent to the event's care team (members with confidential access, administradores and church administrators). Subject "Emmaus: reporte urgente en {evento}". The body says a report exists and asks them to log in; it has no names and no details. The reporter is told about this e-mail only when it went: `notify_safety_report` returned True (there was at least one address and the backend accepted the message) and the backend delivers (not console, file or dummy).
* New team member: a link to create their password. It contains the staff member's first name and the event name. With the console, file or dummy backend nothing is sent (the link would only land in a log or a file on the server); the screen shows the link and says "No hay correo configurado".
* "¿Olvidaste tu contraseña?": for an active account that already has a password, Django's password reset e-mail ("Emmaus: crea una nueva contraseña"); for an active account that never set one (added to a team, link never used), "Emmaus: crea tu contraseña" with the link to create it. Both go only to the account's own address, with the person's first name, and match the address the way Django does (NFKC, then casefold, after the database's case-insensitive filter). At most 3 such e-mails per address per hour, counted on that normalized address, so other spellings of it share the limit; extra requests show the same "Revisa tu correo" page without sending anything, and so does an unknown address. With no e-mail configured, the "crea tu contraseña" message is not sent at all. Django's own reset still goes through whatever backend is configured. Administrators cannot generate a link for accounts that already have a password.
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

* An "Entrevista a solas" logs the coordinador out before the campero touches the computer. The browser is confined to `/estacion/` and holds only a random token for one interview. Recovering it needs a password; attempts are counted in the database (5, then a 60-second block that doubles each time up to 15 minutes), so clearing cookies or parallel requests do not help. A capture session closed by an administrador cannot be unlocked.
* The campero sees only their own answers, and is told on the welcome and review screens who reads them, in the words of the default privacy notice ("Tus respuestas las lee tu coordinador y, las generales, también el equipo del campamento. Las de temas personales, solo quien te entrevistó, tu coordinador y el equipo de cuidado. Las marcadas “Confidencial”, solo el equipo de cuidado autorizado.", the last sentence by the interview's confidential level); the screen locks after 5 minutes idle in the browser, 10 minutes on the server, 60 minutes at most. The final screen says "¡Gracias!" without the campero's name.
* The staff login allows 8 attempts per e-mail, then blocks that e-mail for 5 minutes, doubling up to 60 minutes if the failures continue.
* Staff logins end when the browser closes and after 30 minutes idle (`EMMAUS_IDLE_LOGOUT_SECONDS`). A dashboard's automatic refresh does not count as activity, so an unattended dashboard or live presentation is logged out too and then says "Tu sesión terminó...". The stored session also expires 12 hours after its last change (`SESSION_COOKIE_AGE`). Still, close the session with "Cerrar sesión" when you leave.
* Only one computer can edit an interview at a time; "Tomar el control aquí" is explicit and audited.
* The printable "Lista de respaldo" contains registry data and, after it, the event's questionnaires for interviewing on paper, one sheet each in the event's order ("Cuestionario para entrevistar en papel", UX-05). `backup_list` reads the structure of the versions the event uses (`build_steps`), never an interview, so the sheet prints no answers. It warns to keep it out of sight and destroy it afterwards. A filled-in copy holds the campero's answers, confidential ones included, in the coordinador's hands, as an assisted interview does while it is typed: the sheet says it is as private as the interview, to be captured in Emmaus as an assisted interview and destroyed once captured.

## Small-group suppression

Spiritual and support results are shown only as event-level totals, to administradores and pastores, and only from saved cutoffs, never live. Each cutoff stores a "released" value per cell that changes only when the underlying count moved by at least the minimum group size (k) since the previous release; the display then rounds to multiples of k and shows "Menos de k" for small counts and for zero. Projected presentations never show answer totals, and their follow-up tiles come from the latest cutoff's released values. Details are in `reporting.md` ("Small-group suppression"). The remaining risk: once k or more answers change, a new value is released at the next cutoff, so a group of k people can still be described.

## Safety escalation workflow

1. Anyone on the team who can see the campero opens "Reportar una situación de riesgo" (from the interview review or the campero page).
2. The first thing shown is "Primero, actúa" with the event's instructions. Default: "Si alguien está en peligro inmediato, llama al 911. Después avisa en persona a la persona responsable de protección del campamento. No investigues por tu cuenta ni prometas guardar un secreto que ponga en riesgo a alguien."
3. "¿Qué pasó?": a short description (up to 4000 characters), then "Enviar reporte". It creates a `FollowUp` of kind "Situación de riesgo", audits `reporte_riesgo` and, once that is stored, e-mails the care team without names or details.
4. The reporter reads only what Emmaus knows (`interviews.safety_report_result`). `report_safety_concern` sends the alert as soon as the report is stored and returns whether it went (`SafetyReport.notified`); called inside a caller's transaction, the alert waits for the commit and nothing is claimed. With at least one active member holding confidential access: "El reporte quedó guardado. El equipo de cuidado autorizado lo verá en Emmaus. Avísales también en persona." With nobody holding it: "El reporte quedó guardado. La administración lo verá en Seguimiento." plus the warning "Ahora mismo nadie en este evento tiene acceso confidencial: avisa a la administración." Either one adds "También les mandamos un aviso por correo, sin nombres ni detalles." only when the alert went and e-mail is configured (`team.email_is_configured`). The form itself promises no e-mail.
5. The report appears with an "Urgente" tag in "Seguimiento" for the care team (confidential access) and administradores, who assign a leader and update the status. Only confidential-access members read the details and the closing note.
6. The reporter sees that the report exists but not its content.

Confidential support referrals (created automatically, see above) are not urgent alerts and send no e-mail. The care team finds them in "Seguimiento" and as "Solicitudes de apoyo confidenciales abiertas" on "Resumen general".

Emmaus is a record and a notification, not an emergency service. The app does not call anyone and does not check that the e-mail was read; the church's procedure must cover the human steps.

## Known limitations

* Expired Django sessions are deleted only while the `scheduler` service (or its cron replacement) runs.
* The Django admin (`/admin/`) is a technical back office for superusers: it can change memberships without writing to the activity log and can delete activity-log rows.
* Backups contain everything, including answers.

## Revisión de seguridad

El 23 de octubre de 2026 una revisión adversarial (alguien intentando romper las reglas a propósito) encontró y corrigió:

* Toma de cuentas ajenas con enlaces para crear contraseña: ahora la administración solo puede generarlos para cuentas creadas en el evento que nunca han tenido contraseña; las demás usan "¿Olvidaste tu contraseña?", que llega al correo de la persona.
* Deducir respuestas ocultas por las filas que aparecían (preguntas condicionales y "sin responder"); ahora esas filas no se listan o dicen solo la etiqueta de su nivel ("Sensible" o "Confidencial"), igual si se respondieron o no.
* Comparar cifras redondeadas entre cortes para aislar a una persona; ahora las cifras solo cambian cuando se mueven al menos k respuestas y solo salen de cortes guardados.
* Sugerencias de seguimiento que revelaban respuestas confidenciales al coordinador.
* Administradores que podían darse a sí mismos acceso confidencial.
* Límite de intentos de desbloqueo que se podía saltar con solicitudes en paralelo o borrando cookies; ahora está en la base de datos.
* Redirecciones a sitios externos (parámetro `siguiente`).
* Fórmulas peligrosas en la lista de camperos descargada (CSV).
* Asignaciones de tutor que se perdían al guardar un formulario incompleto o desactualizado.
* Cortes antiguos que seguían mostrando a un tutor coordinadores que ya no son suyos.
* Desbloqueo de una pantalla de entrevista a solas que la administración ya había cerrado.
* Doble clic en "Generar corte ahora" que creaba dos cortes.
* Un error 500 con un identificador inválido.

Una segunda revisión, hecha al escribir esta documentación, corrigió también:

* La pantalla "Entrevista completada", que podía mostrar al coordinador la solicitud de apoyo confidencial.
* El PDF, que fallaba con nombres que parecían etiquetas (por ejemplo `<A>`).
* Una membresía desactivada que no quitaba el acceso a quien también era pastor de la iglesia.
* Límites de intentos que se reiniciaban después de cada bloqueo; ahora cada bloqueo dura el doble.
* Paneles que se actualizaban solos y nunca cerraban la sesión por inactividad.
* `DEBUG` encendido si no se configuraba, y la revisión de salud del contenedor que fallaba con HTTPS.

`events/tests/test_security_review.py` comprueba estos casos, salvo los dos de configuración del último punto.

Riesgos que siguen, dichos con honestidad:

* No hay límite de intentos por dirección IP. Como el límite es por correo, alguien que conozca el correo de otra persona puede bloquear su entrada por 5 minutos y, si insiste, hasta por 60.
* Los resúmenes de respuestas se vuelven a publicar en cada corte en cuanto cambian al menos k respuestas.
* La administración ve que existe un reporte de riesgo aunque no pueda leerlo.
* `/admin/` de Django es una oficina técnica: quien es superusuario puede ver y cambiar datos sin pasar por las reglas de la aplicación.

## Antes de usar Emmaus con menores de edad

- [ ] La iglesia definió y aprobó por escrito quién autoriza las entrevistas, cómo se recaban y guardan las autorizaciones firmadas de padres, madres o tutores legales, quién atiende las situaciones de riesgo y cuánto tiempo se conservan los datos.
- [ ] La iglesia revisó con asesoría legal sus obligaciones conforme a la LFPDPPP y su aviso de privacidad.
- [ ] Adaptaste en "Configuración" el "Aviso de privacidad" y "Qué hacer ante una situación de riesgo" con los nombres y teléfonos reales del equipo de protección.
- [ ] Revisaste la "Edad de mayoría", "Pedir autorización de padre, madre o tutor legal para menores", el "Mínimo de personas para mostrar un total" y los "Días que se conservan las respuestas".
- [ ] Revisaste los cuestionarios del evento, el nivel de privacidad de cada pregunta y quién lee las respuestas confidenciales; si no es solo el equipo de cuidado, el aviso de privacidad lo dice.
- [ ] Solo las personas del equipo de cuidado autorizado tienen "Puede leer información confidencial", y cada una sabe que sus consultas quedan registradas.
- [ ] Cada coordinador tiene su propia cuenta y contraseña, y sabe recuperar la computadora en una entrevista a solas.
- [ ] El servidor usa HTTPS, `DEBUG=false`, respaldos cifrados y `manage.py check --deploy` sin la advertencia `emmaus.W001` (cuentas de demostración).
- [ ] El servicio `scheduler` está corriendo (borra los datos vencidos de los eventos terminados o archivados, una vez al día), y al acabar el campamento alguien lo marca como "Terminado".
- [ ] Hiciste una prueba completa con datos ficticios antes del campamento.
- [ ] Solo entonces marcaste la casilla de procedimientos y pulsaste "Confirmar".
