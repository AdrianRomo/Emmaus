# Reporting

One function, `build_payload(scope)` in `events/services/metrics.py`, produces the operational numbers Emmaus shows: live dashboards, saved cutoffs ("cortes"), PDFs and presentations. A cutoff is that payload frozen at a point in time, plus a "released" block (below). The definitions are metrics version `2` (`METRICS_VERSION`), stored on every snapshot. Version 2 came with several questionnaires per event: a campero's "entrevista" is all of the event's questionnaires, and the answer counts are kept per questionnaire.

## Metric definitions

These are the `DEFINITIONS` printed at the end of every PDF ("Cómo se calcula"), copied from `metrics.py`:

| Metric | Definition (as shown to users) |
|---|---|
| Camperos | Camperos registrados que siguen participando en el evento. |
| Completadas | Camperos que completaron la entrevista (todos los cuestionarios del evento). Las correcciones no cuentan como una entrevista nueva. |
| En curso | Entrevistas iniciadas que todavía no se finalizan. |
| Pendientes | Camperos que aún no completan todos los cuestionarios, incluidos los que tienen una entrevista en curso. |
| Avance | Completadas entre camperos. Si no hay camperos no se calcula y se muestra "—", nunca 0 %. |
| Ritmo | Camperos que completan la entrevista por hora, en promedio, durante las últimas 2 horas. |
| Seguimientos abiertos | Seguimientos en estado pendiente o en proceso. |
| Por decidir | Entrevistas completadas que esperan decidir si necesitan seguimiento. |
| Interrumpidas | Entrevistas en curso sin cambios desde hace 20 minutos o más. |

Two more, `SEVERAL_QUESTIONNAIRE_DEFINITIONS`, follow them in the PDF only when the cutoff counts several questionnaires (its `progress.questionnaires` has rows), so a one-questionnaire PDF keeps the glossary above:

| Metric | Definition (as shown to users) |
|---|---|
| A medias | Camperos que completaron algún cuestionario del evento, pero no todos, y no tienen una entrevista en curso. |
| Por cuestionario | Camperos que completaron cada cuestionario del evento. Un campero cuenta en cada cuestionario que terminó. |

How they are computed:

* Counts come from `EventParticipant` rows with `is_active=True` in the scope, using `interview_status`. Withdrawn camperos drop out of every figure.
* `interview_status` is derived from the campero's interviews (`events/services/event_questionnaires.py`): `completada` when every questionnaire the event uses is completed, `en_curso` while one is in progress (at most one at a time), `pendiente` otherwise. With one questionnaire these are the version 1 figures.
* "Sin iniciar" (meters, PDF legend) is camperos minus completadas minus en curso minus "a medias" (`partial`): pendientes with no interview in progress who completed at least one of the event's questionnaires. With one questionnaire `partial` is always 0.
* "Por cuestionario" (`questionnaires`) counts, for each questionnaire the event uses and in its order, the scope's camperos who completed it and the ones answering it now. A campero counts in every questionnaire they finished.
* "Por decidir" counts interviews, so one per questionnaire.
* "Avance" is `round(100 * completadas / camperos)`. With zero camperos it is `None`, shown as "—", never 0 %.
* "Ritmo" (`per_hour`) is camperos who completed the interview in the last 2 hours divided by 2, each at the moment their last questionnaire was finalized (`campero_completions`), so it is in the same unit as "Pendientes"; `last_hour` is also stored. The ETA is `ceil(pendientes / ritmo * 60)` minutes, empty when the rate is 0.
* The timeline groups those same completions (camperos, never above their total) in the event's time zone into 60-minute buckets (120 above 30 hours of span, 240 above 60 hours), at most 96, with a cumulative line.
* Follow-up counts cover **ordinary** care follow-ups only (`kind=seguimiento`, `confidential=False`): pendiente, en proceso, atendido. `open_total = pendiente + en proceso`; `requested_total = open_total + atendido`. Confidential support referrals (`confidential_open`) and safety reports (`safety_open`) are counted separately, and only in payloads meant for viewers allowed to see them.
* Attention counters: interrumpidas, camperos without coordinador, active coordinadores without tutor, and minors not yet completed whose latest guardian record is not "Otorgado" (only when the event requires guardian authorization).

## Scopes

| Scope key | Meaning | Who reads it |
|---|---|---|
| `evento` | Every active campero of the event ("Todo el campamento", "Toda la conferencia"…) | Administradores, pastores |
| `equipo:<tutor membership id>` | Camperos of the coordinadores assigned to that tutor (label: the team name, or "Equipo de {first name}") | Administradores, pastores, that tutor |
| `coordinador:<membership id>` | One coordinador's camperos | Live only ("Mi avance", coordinator detail); never saved as a cutoff |

The event payload also lists teams (least progress first, plus "Sin equipo asignado") and coordinadores.

`progress`, every `teams[]` row and every `coordinators[]` row carry `eligible`, `completed`, `in_progress`, `pending`, `not_started` and `percent`, and two keys for several questionnaires:

* `progress.partial`: the camperos "a medias" (0 with one questionnaire);
* `progress.questionnaires`: `[{id, title, order, eligible, completed, in_progress, percent}]`, one row per questionnaire in the event's order (`title` is the version's title, the one every screen shows; `[]` with one questionnaire).

Cutoffs saved before them lack both keys. Every screen, the PDF and the deck read them with defaults (0 and no rows), so those cutoffs render as they always did. The reports history folds a quiet stretch only when completed, pending and each questionnaire's completed count are all unchanged (`_run_key`).

## Automatic cutoffs

Each pass of `python manage.py run_report_cutoffs` (`events/services/reports.py`) does this:

1. Clears expired interview claims, moves capture sessions idle for 10 minutes to "waiting for unlock", and deletes expired login sessions.
2. For every event "En curso" with "Generar cortes automáticos" on, finds the latest due slot and stores one snapshot per scope: `evento`, plus one `equipo:<id>` per **active** tutor.

Slot rules:

* **Slots.** They start at "Primer corte del día" (default 08:00) and repeat every "Frecuencia de los cortes" (60, 120, 180 or 240 minutes; default 120) while the time is `<=` "Último corte del día" (default 22:00), in the event's time zone.
* **Days.** Slots run only between `starts_on` and `ends_on`, when those are set.
* **Grace window.** A slot is generated only if a pass runs within `max(10, min(30, interval / 2))` minutes after it, which is 30 minutes for every interval the UI offers. With a pass every 5 minutes, each slot gets about six chances.
* **Missed slots.** These are not back-filled, so later data is never labelled with an earlier time.
* **Idempotency.** The unique constraint `(event, scope_key, cutoff_at, trigger)` makes reruns and concurrent runs no-ops.
* **Failures.** If building the payload raises, a "Falló" snapshot with the error text (max 500 characters, never answers) is stored, and a later pass within the grace window replaces it with a good one. Administrators see "1 corte no se pudo generar." / "N cortes no se pudieron generar." on "Inicio" and the error in the history; others see "Genera uno nuevo con “Generar corte ahora”." A failed "Generar corte ahora" says "No se pudo generar el corte. Inténtalo otra vez; si sigue igual, avisa a la administración."

`next_slot(event)` ("Próximo corte") looks up to 7 days ahead. It is empty when cutoffs are off or the event is not in progress.

## Released values

Each snapshot stores `data["released"]` with two groups of raw counts:

* `follow_ups`: `requested_total`, `open_total`, `done`, `to_evaluate`;
* `outcomes`: for each questionnaire the event uses, keyed by its id (question codes can repeat between questionnaires), and each of its choice questions that is not `general`, the number of respondents and the count per option, from that questionnaire's completed interviews of active camperos (event scope only). A version 1 cutoff holds the counts unkeyed; they are read as the event's first questionnaire's (`keyed_outcomes`), on screen and as the previous release for the next cutoff.

`release_cells(current, previous, k)` works cell by cell against the previous ready snapshot of the same scope. A cell takes the new value only if it moved by at least `k` (the event's "Mínimo de personas para mostrar un total", default 5) since the last released value; otherwise the old released value is repeated. The first cutoff releases the current values. Comparing two consecutive releases can therefore never isolate fewer than `k` people. Raw outcome counts are not kept anywhere else in the snapshot.

Answer aggregates are **never computed live**. The pastor home shows those of the latest saved event cutoff (under "Lo que los camperos quisieron compartir", marked "corte de las HH:MM"; a cutoff's own page lists them under "Resumen de respuestas"), and the presentation's follow-up tiles use the latest cutoff of its scope. With two or more questionnaires, the summaries go under each questionnaire's title, in the event's order (cutoff page, pastor home and PDF).

## Manual cutoffs

"Generar corte ahora" (administradores, pastores, tutores) calls `manual_cutoff(access, scope_key)`. The scope must be one the user may read now. Under a lock on the event row, if a ready manual cutoff of the same scope was created in the last 60 seconds, that cutoff is returned instead of a new one; this covers double clicks and two people pressing at once. Otherwise a cutoff is stored with trigger "Manual" at the current second. Each request is audited (`corte_generado`).

## Immutability

* `ReportSnapshot.save()` refuses to modify a stored "Listo" snapshot, and `delete()` refuses to remove one. The Django admin shows snapshots read-only. Deleting the event, or a queryset `.delete()`, still removes them.
* Correcting an answer or a campero after a cutoff does not change that cutoff. Cutoffs keep only aggregate numbers plus team and coordinador names, so erasing a campero or purging an event leaves them in place.

## Comparison

`previous_snapshot` is the most recent "Listo" snapshot of the same event and scope with an earlier `cutoff_at` (automatic or manual). `compare(current, previous)` gives the change in camperos, completadas, en curso, pendientes, avance (percentage points), open follow-ups, and per-team avance. The report page shows it as "Comparado con el corte anterior". The pastor home compares the live numbers with the latest cutoff ("desde el corte de las HH:MM").

## Who sees what in a cutoff

* Access is checked against the viewer's **current** scopes (see `roles-and-permissions.md`). A failed snapshot returns 404.
* `visible_payload` does three things:
  * It removes the safety figure unless the viewer is an administrador or has `can_view_sensitive`, and the confidential-referral figure unless the viewer has `can_view_sensitive`.
  * It turns the released outcome counts into display rows (`present_outcomes`), only for administradores and pastores.
  * For a tutor, it keeps only the coordinadores currently assigned to them.

## PDF ("Descargar PDF")

Built with ReportLab from the trimmed payload (`events/services/pdf.py`), US Letter, and audited as `exportacion`. All text is escaped before it reaches ReportLab markup. Contents, in order:

1. Folio "{event} · {scope}", title "Corte de seguimiento de las HH:MM", "Datos al …", trigger and metrics version.
2. Participantes, Completadas, Pendientes, Avance; a completed / in-progress / remaining bar and the legend "Completadas · En curso · Sin iniciar". With several questionnaires the headline says "Llevábamos X de Y con los N cuestionarios.", the legend "N sin iniciar y M a medias" (when some are halfway), and a "Por cuestionario" table follows (Cuestionario, Completadas, En curso, Avance).
3. The change since the previous cutoff, when there is one, in the same sentence as every screen (`delta_sentence`): "+11 entrevistas y +3 % de avance desde el corte de las 20:00." When nobody finished an interview it still says what moved ("Ninguna entrevista nueva, pero se registraron +2 camperos y el avance cambió −1 % desde …"), or "Sin cambios desde …".
4. Rate sentence and ETA.
5. "Avance de entrevistas": cumulative line against the total of participants.
6. "Avance por equipo" (or "Avance por coordinador" for a team cutoff). With several questionnaires each name takes a second line: "«Primero»: 3 de 4, «Segundo»: 1 de 4."
7. "Seguimiento": pendientes, en proceso, atendidos, por decidir, and "Situaciones de riesgo abiertas" when allowed.
8. "Resumen de respuestas (sin datos individuales)" for event cutoffs viewed by administradores and pastores, from the released values, with the suppression note.
9. "Próximos pasos" (the recommendations stored with the cutoff).
10. "Cómo se calcula" (the definitions above; "A medias" and "Por cuestionario" only with several questionnaires).
11. Footer on every page: "Emmaus · Documento confidencial de uso interno. No reenviar." and the page number.

The PDF shows team labels and, for team cutoffs, coordinador names. It never shows campero names or individual answers.

## Presentation ("Modo presentación")

"Mostrar presentación" (pastor home and reports) opens the **live** deck for the viewer's own scope, refreshed every 60 seconds. From a team chosen in "Reportes" it carries `?alcance=` and shows that team; only one of the viewer's own scopes is honoured, anything else gives their whole scope, and the refresh keeps it. (A team cutoff was already a team report; only the live presentation used to ignore the choice.) "Presentación" in the history, or "Mostrar presentación" on a cutoff, opens that **saved** cutoff. Keyboard: arrows, PageUp/PageDown or space, Home/End, and F for full screen. Buttons: "Anterior", "Siguiente", "Pantalla completa", "Salir".

The template renders only the dict returned by `build_deck()` in `events/services/presentation.py`. It contains no campero, coordinador or tutor names, no answers or free text, no safety reports and no answer aggregates.

| Slide | Title | Content |
|---|---|---|
| 1 | "Así va nuestro {campamento}" | Participantes, Entrevistas completadas, Avance; completadas / en curso / sin iniciar bar |
| 2 | "Avance de entrevistas" | Cumulative chart; pending count, rate and ETA |
| 3 | "Avance por equipo" | Up to 8 teams with the least progress, labelled with their "Nombre del equipo" or "Equipo N" (numbered by tutor id, so stable across cutoffs) |
| 4 | "Necesidades de seguimiento" | Requested, waiting for a leader, attended, and to be decided, all from the **latest cutoff's released values**, suppressed. Shows "—" before the first cutoff ("Estas cifras aparecerán con el primer corte de seguimiento.") |
| 5 | "Próximos pasos" | Up to 5 deterministic steps |

With several questionnaires (`deck.progress.questionnaires`, copied by `build_deck` with only each row's title and counts, plus `partial`) the headline slide says "con los N cuestionarios" and its key "a medias", and a "Por cuestionario" slide follows it: one line and one ruler per questionnaire ("«Primer cuestionario»: 320 de 400."), the same foot as the others. The deck then has six slides; the slide numbers, the feet and the presenter's bar count them.

Participation counts on slides 1 to 3 are exact: they are operational, not personal. A team name typed by an administrador is shown as is, so it should not contain a person's name.

## Small-group suppression

`suppress(value, k)`:

| Value | Answer aggregates (`hide_zero`) | Presentation tiles |
|---|---|---|
| none yet | "—" | "—" |
| 0 | "Menos de k" | "0" |
| 1 to k-1 | "Menos de k" | "Menos de k" |
| k or more | "≈ N", nearest multiple of k | "≈ N", nearest multiple of k |

* Answer aggregates are shown only to administradores and pastores, only at event level, and only from released values. If fewer than `2k` people answered a question, its rows are withheld ("Aún no hay suficientes respuestas…"). Showing zero as "Menos de k" keeps a column of zeros from revealing that everyone gave the same answer.
* Staff dashboards and PDFs show exact operational and follow-up counts; the projector does not.

**Why release and round, rather than only round.** With plain rounding, someone who knows exactly who finished between two cutoffs could see a value flip at a rounding boundary and learn one person's answer. With per-cell release, a displayed value changes only when at least k answers changed.

**Remaining risk.** Once k or more answers change, a new value is released at the next cutoff, so a group of k people can still be described. Outcomes use the questions of the version each of the event's questionnaires uses **now**; interviews on an older version are not counted after the version changes, and a questionnaire the event stops using drops out of the summaries.

## Recommendations (deterministic)

`recommendations(payload)` builds "Próximos pasos" from fixed rules, each with an evidence line. There is no AI. In order:

1. `pendientes`: "Faltan N entrevistas de M." plus the ETA; or `completo`: "Todas las entrevistas están completas."
2. `equipo`: up to 3 teams at least 15 points below the overall avance with 5 or more pending: "… Podría necesitar otra computadora o más apoyo."
3. `interrumpidas`: interviews over 20 minutes without changes: "1 entrevista está interrumpida: … Hay que retomarla o descartarla." / "N entrevistas están interrumpidas: … Hay que retomarlas o descartarlas."
4. `sin_coordinador`: camperos without coordinador (event scope).
5. `sin_tutor`: coordinadores without tutor (event scope).
6. `autorizaciones`: minors without a registered guardian authorization.
7. `por_evaluar`: interviews waiting for the follow-up decision.
8. `seguimientos`: open ordinary follow-ups: "Asigna a un líder para cada uno."

The presentation uses its own shorter list (`build_deck`) with the same thresholds. Its follow-up steps appear only once a cutoff exists, and they use released, suppressed numbers.

## Running the scheduler

* Production: the `scheduler` service in `docker-compose.prod.yml` runs `python manage.py run_report_cutoffs --loop 300`. A failing pass is logged and the loop continues; restarting is safe.
* Cron instead: run `python manage.py run_report_cutoffs` (one pass) every 5 minutes, for example `*/5 * * * * cd /app && python manage.py run_report_cutoffs`.
* Development: `make cutoffs` runs one pass in the dev container.
