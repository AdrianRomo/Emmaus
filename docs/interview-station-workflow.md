# Interview station workflow

This page describes how an interview runs on a shared "computadora de entrevistas" (an `InterviewStation`), from the code in `events/services/interviews.py`, `events/views/interview.py`, `events/views/kiosk.py` and `events/middleware.py`.

There are two modes (`Interview.Mode`):

| Mode | Label in the UI | Who types |
|---|---|---|
| `asistida` | "Entrevista asistida" ("Tú haces las preguntas y anotas las respuestas.") | The coordinador, logged in |
| `supervisada` | "Captura supervisada" ("El campero responde solo. Necesitarás tu contraseña para recuperar la computadora.") | The campero, on an anonymous kiosk session |

Only coordinadores (for their own active camperos) and administradores (for any active campero) can run interviews.

## Preconditions

`event_blockers(event)` must return nothing, otherwise the start button is disabled and the reason is shown:

1. The event status is "En curso" (`activo`).
2. The event has a published questionnaire version (`Event.questionnaire_version`).
3. An administrador has confirmed in "Configuración" that the church approved its authorization, child-protection and risk procedures (`procedures_approved_at`). Until then: "La administración aún no confirma que la iglesia aprobó sus procedimientos de autorización y protección."

Registering camperos does not need these; interviewing does.

## Station selection

* "Crear campamento" creates ten stations, "Computadora 1" to "Computadora 10". Administrators add more or take one out of use in "Configuración" → "Computadoras de entrevista".
* The first time a browser opens "Iniciar entrevista" while active stations exist, it is sent to "¿En qué computadora estás?". The choice is stored in the browser's session (`session["stations"][event_id]`) and can be changed with "Cambiar" on the search page.
* The station is recorded on the interview (and updated on take-over) so supervisors see where it was opened. It is informational; it does not restrict anything.
* Hand-over to a campero flushes the session, so after recovering the computer the coordinador is asked to choose the station again on the next "Iniciar entrevista".

## Consent and assent

The "Antes de empezar" screen (`interview/start.html`) has three parts: "¿Es la persona correcta?", "Autorización" (with "Leer el aviso de privacidad para explicárselo") and "¿Cómo se va a responder?". The required checkboxes come from `consent_requirements(participant)`:

| Campero | Required |
|---|---|
| Minor (`age < event.adult_age`, default 18) and the event requires guardian authorization | **Guardian authorization**, unless the latest `ConsentRecord` of kind guardian is "Otorgado". If missing, the coordinador must tick "Tengo la autorización firmada de su padre, madre o tutor legal" ("Confírmalo solo si viste el formato firmado...") |
| Any minor | **Assent**: "Le expliqué el aviso de privacidad a {nombre} y está de acuerdo en conversar" |
| Adult | **Consent**: "Le expliqué el aviso de privacidad a {nombre} y acepta participar" |

On start, each newly confirmed item becomes a `ConsentRecord`: guardian authorization with method `papel` and no interview link; assent or consent with method `verbal`, linked to the interview, recorded by the coordinador. Guardian authorization can also be recorded beforehand: on registration ("Ya entregó la autorización firmada..."), in the CSV import (column "Autorización"), or with "Marcar como recibida" on the campero page.

## Assisted interview, step by step

1. "Iniciar entrevista" → search by name or number → "Entrevistar" (or "Continuar" if one is in progress).
2. "Antes de empezar": check the data, tick the authorizations, choose "Entrevista asistida", "Comenzar entrevista".
3. `start_interview` locks the campero row, refuses if a live interview exists, creates the `Interview` with the event's current questionnaire version, sets a 15-minute claim, records the consents, sets the campero to "En curso" and writes the audit row `entrevista_iniciada`.
4. One question per screen ("Pregunta N de M", "Sección X de Y"). A conditional follow-up question of the same section appears on the same screen and is revealed when a matching option is chosen (it is also visible without JavaScript, with a hint). "Prefiero no responder" and other exclusive options sit after an "o bien" divider; choosing one clears the others.
5. Buttons: "Continuar" (or "Revisar respuestas" on the last step), "Atrás", and "Guardar y salir" in the top bar. All three save the current screen first. Validation runs on every save; the default questionnaire has no required questions.
6. "Revisa las respuestas de {nombre}": every visible question with "Cambiar". Then "¿Necesita seguimiento?" and "Finalizar entrevista".

## Supervised capture, step by step

1. Same search and "Antes de empezar", choosing "Captura supervisada". The coordinador still confirms the authorizations.
2. "Entrega la computadora a {nombre}" (`interview/handoff.html`): three reminders, then "Entregar la computadora". "Mejor hago yo las preguntas" switches to assisted mode, allowed only while no answer was typed by the campero (`switch_to_assisted`). "Cancelar" in the top bar opens the discard screen.
3. `hand_over_to_participant` verifies the claim, closes any older capture session, creates a `ParticipantSession` (random token, only its hash stored; expires in 60 minutes), sets a 60-minute claim whose raw token is kept only in the kiosk session, sets mode `supervisada`, audits `captura_iniciada`, then **calls `logout()`**. The browser keeps only `session["kiosk"]`.
4. The campero sees "Hola, {nombre}", three reassurances and "Empezar". Steps look like the coordinador's, with "Terminar por ahora" in the top bar and "Tus respuestas se guardan cada vez que avanzas." Answers are stored with `answered_by_participant=True`.
5. "Revisa tus respuestas" (all their answers, with "Cambiar") → "Terminar". The session moves to `por_desbloquear`.
6. The campero sees "¡Gracias, {nombre}! Ya terminaste. Avisa a tu coordinador para que recupere la computadora." and the button "Soy coordinador: recuperar la computadora".
7. "Recuperar la computadora": the coordinador types their password (or "Soy otro coordinador" to use another account's e-mail). On success the browser is logged in again, the claim is handed back, and the coordinador lands on the review screen: "Recuperaste la computadora. Revisa y finaliza la entrevista."
8. On review, confidential rows show "Respuesta confidencial registrada" and cannot be changed. The coordinador chooses the follow-up decision and finalizes.

```mermaid
sequenceDiagram
    autonumber
    actor C as Coordinador
    participant B as Shared browser
    participant S as Django
    participant DB as PostgreSQL
    actor P as Campero
    C->>B: Antes de empezar, mode Captura supervisada
    B->>S: POST camperos/{id}/entrevista/
    S->>DB: lock EventParticipant, insert Interview with claim hash
    S-->>B: redirect to entregar/ (claim token kept in session)
    C->>B: Entregar la computadora
    B->>S: POST entrevistas/{id}/entregar/
    S->>DB: lock Interview, check claim, new 60 min claim, insert ParticipantSession
    S->>S: logout() flushes the session, store kiosk token and claim only
    S-->>B: redirect to /estacion/
    loop each step
        P->>B: answer, Continuar
        B->>S: POST /estacion/paso/N/ with revision
        S->>DB: check kiosk token, idle and expiry, lock Interview, check claim and revision, save answers
    end
    P->>B: Terminar
    B->>S: POST /estacion/terminar/
    S->>DB: ParticipantSession state por_desbloquear
    S-->>B: Gracias screen
    C->>B: Soy coordinador, password
    B->>S: POST /estacion/desbloquear/
    S->>S: authenticate, resolve_access, can_interview (5 failures then 60 s wait)
    S->>DB: close ParticipantSession (desbloqueada), hand claim to coordinador
    S->>S: login() rotates the session key
    S-->>B: redirect to entrevistas/{id}/revision/
    C->>B: follow-up decision, Finalizar entrevista
    B->>S: POST revision/
    S->>DB: lock Interview, completada, FollowUp if required
```

## Claims, locks and "Tomar el control"

* A claim is a random token. Its SHA-256 is stored on the interview row; the token itself lives only in the server-side session of the browser that holds it. Every write locks the row and checks claim and `revision`.
* A staff claim lasts 15 minutes (`CLAIM_MINUTES`) and each save extends it. Once a claim has expired, whoever opens the interview next (the same browser or another one) gets it without a prompt, audited as `entrevista_retomada`.
* If another browser holds an active claim, opening the interview shows "Esta entrevista está abierta en otra computadora" with the station, holder and last activity, and the warning "Si tomas el control aquí... La otra computadora ya no podrá guardar cambios." The button "Tomar el control aquí" replaces the claim, closes any capture session on the other computer (`retomada`) and is audited as `entrevista_control`.
* A tab with an old `revision` cannot overwrite newer answers: "Las respuestas cambiaron desde que abriste esta pantalla. Te mostramos la versión más reciente."
* Administrators can clear claims and capture sessions in "Configuración" → "Sesiones abiertas" ("Liberar", "Cerrar").

## Interrupted and abandoned interviews

* Nothing is lost: every step is saved when the coordinador or campero moves on.
* "Interrumpidas" are interviews in progress with no saved activity for 20 minutes or more (`STALE_MINUTES`). They appear on the tutor's "Mi equipo" and "Pendientes" screens, in the recommendations ("Próximos pasos") of dashboards and cutoffs, and in the presentation. The coordinador sees open interviews under "Continúa donde te quedaste" on "Mi inicio" and in "Entrevistas pendientes".
* "Continuar" goes to the first visible step whose main question has no stored answer, otherwise to the review. A text question left blank is not stored, so resuming can land on that blank question again.
* The scheduler clears expired claims and moves capture sessions idle for 10 minutes to `por_desbloquear` on every pass (`run_report_cutoffs`).

## Kiosk confinement and timeouts

* `KioskConfinementMiddleware`: while the session holds `kiosk`, every path outside `/estacion/` (and `/static/`, `/health/`) redirects to the kiosk home. The login page, the back button and typed URLs all land there. There is no coordinador privilege left in the browser to abuse.
* Every kiosk request validates the token against the `ParticipantSession` (hash comparison), its state, the interview state, the 10-minute idle limit (`KIOSK_IDLE_MINUTES`) and the 60-minute absolute limit (`KIOSK_MAX_MINUTES`).
* In the browser, the step and review screens use a 5-minute idle timer (`data-kiosk-idle="300"`). One minute before, a dialog asks "¿Sigues aquí?" with "Sí, sigo aquí". When it runs out, the page goes to `/estacion/tiempo/`, which locks the session if the server saw no activity for over 60 seconds. Any key press, tap, click or scroll sends a heartbeat to the server at most every 2 minutes, so a campero writing a long answer is not locked out.
* A locked session shows "La pantalla se cerró... Lo que respondiste está guardado." and needs the coordinador's password.
* Closing the browser normally ends the kiosk session, because the session cookie expires with the browser (`SESSION_EXPIRE_AT_BROWSER_CLOSE`). Some browsers restore such cookies when they reopen previous tabs, so do not rely on this alone. The interview stays in progress; the coordinador continues it or takes control from any computer.
* If the session was revoked or taken over, the kiosk shows "No hay una entrevista abierta en esta computadora" with "Entrar a Emmaus".

## Unlock

`kiosk_unlock` authenticates the password of the coordinador who handed over the computer, or of the e-mail typed after "Soy otro coordinador". The account must resolve to a role that `can_interview` this campero: the assigned coordinador or an administrador. Failed attempts are counted in the kiosk session: after 5 failures there is a 60-second wait ("Demasiados intentos. Espera N segundos e inténtalo otra vez."), then the counter resets. The counter is per browser session and is not protected against parallel requests.

## Review, finalize and the follow-up decision

* "Sugerencia según sus respuestas" lists deterministic hints from options marked `suggests_follow_up` ("Pidió hablar con un líder", "Pidió apoyo en un área de su vida", "Tomó una decisión espiritual"), with "Tú decides. Emmaus solo te recuerda lo que el campero pidió." If there are hints, "Se requiere seguimiento" is preselected.
* Choices: "Se requiere seguimiento", "No se requiere seguimiento", "Seguimiento pendiente de evaluación". One is mandatory.
* `finalize` is idempotent, checks claim and revision, refuses if a visible required question is empty, marks the interview and campero "Completada", clears the claim, closes capture sessions and creates one care `FollowUp` when the decision is "required" (reason taken from the first hint, else "A criterio del coordinador").
* "Entrevista completada" offers "Continuar con otro campero" and shows how many are left.
* An interview left "pendiente de evaluación" can be resolved later on its detail page with "Sí requiere seguimiento" / "No requiere seguimiento".

## Corrections

On a completed interview, the assigned coordinador or an administrador sees "Corregir" next to each answer they can read. The correction screen asks "¿Por qué corriges esta respuesta?" (required). `correct_step` checks permission and revision, then for every value that actually changed stores an `AnswerRevision` with the previous option codes or text, the reason, who and when. It increments `correction_count` and audits `entrevista_corregida` (count only, no content). A correction never creates a second interview, so the metrics do not double count. The "Historial de correcciones" list is shown to administrators and `can_view_sensitive` users.

## Discard

"Descartar esta entrevista" (from the review screen) asks for a reason ("Se abrió la entrevista de otro campero por error", "El campero decidió no participar por ahora", "Hay que empezar de nuevo", "Otro motivo") and "Sí, descartar y borrar respuestas". `discard` deletes all answers of that interview (data minimization), marks it `descartada`, closes capture sessions and puts the campero back to "Sin entrevista". The partial unique index then allows a new interview. Only interviews in progress can be discarded.

## Safety reports during an interview

"Reportar una situación de riesgo" (review screen, campero page) first shows "Primero, actúa" with the event's safeguarding instructions, then "¿Qué pasó?" and "Enviar reporte". See `privacy-and-safeguarding.md`.

## Load and concurrency testing

**Threaded PostgreSQL tests** (`events/tests/test_concurrency.py`, skipped on SQLite):

1. Ten stations start, answer three steps and finalize in parallel: no failures, 10 completed interviews.
2. Five threads race to start the same campero: exactly one interview is created, four get `AlreadyInProgress`.
3. Four simultaneous finalizes of one interview: all return the interview, one follow-up, one completion.
4. Ten parallel registrations: participant numbers stay unique.

**HTTP load test** (`scripts/simulate_camp_day.py`): each station logs in as a different coordinador, chooses its computer and completes the assisted interview of every pending campero assigned to it through the real forms, while two pastor pollers fetch the live dashboard fragment every 2 seconds. The script then checks the database for duplicates and prints latency percentiles. It exits non-zero on any HTTP error or duplicate.

Result recorded on 2026-10-23, gunicorn (3 workers x 4 threads) on local PostgreSQL 16:

| Measure | Result |
|---|---|
| Stations | 10 |
| Interviews completed | 321 in 30.4 s |
| HTTP errors | 0 |
| Duplicate interviews | 0 |
| p95 step save | 165 ms |
| p95 start | 153 ms |
| p95 finalize | 147 ms |
| p95 live pastor fragment | 194 ms |
| Dashboard payload for 400 camperos | 50 ms, 32 queries |
| 5 cutoff scopes generated | 133 ms |

How to repeat it is in `deployment.md`.

## Known limitations

* After recovering the computer the coordinador must pick the station again.
* An interview started in "Captura supervisada" but never handed over (for example after "Cancelar" then "No, volver a la entrevista") continues in coordinador screens with mode `supervisada`; confidential steps then bounce to the review with "Esa pregunta es confidencial y la respondió el campero." There is no link back to the hand-over screen.
* The review screen's "Guardar y salir" always links to the coordinador home, which administrators cannot open.
* The kiosk welcome screen has no browser idle timer; the server's 10-minute limit still applies.
