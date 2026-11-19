# Wave 10: private prayer requests (issue #97)

Branch `v2/wave-10-oracion`. **High risk**: the most sensitive free text in Emmaus. Synthetic data only.

Issue #97 asks that the church's leadership specify audience, access, response and escalation
before any text is collected. The owner delegated the product decision (decision 0017). Both are
honoured by building it so that **nothing is collected until a pastor of the church switches it on
after reading and accepting, on screen, what the church promises**. The acceptance is recorded with
the account and the moment. This is not the leadership's or a privacy reviewer's approval of the
wording: it is a default a church can read before it opts in, and #97 stays open for that review.

## What was built

A new app, `oracion`, under the `community` module **and** its own switch per church, off by default.

| Piece | Where |
|---|---|
| Models: `PrayerPolicy` (one row per church: on/off, who accepted, when, version of the promises, the church's own line, days kept), `PrayerRequest` (church, person, body ≤ 1,000, optional single reader, dates, one reply ≤ 500), `ReaderPreference` (a pastor's own e-mail choice) | `oracion/models.py`, migrations `oracion/0001_initial` (three `CreateModel`) and `0002` (`AddField author`, a `RunPython` that fills it, reversible) |
| Who writes, who reads | `oracion/access.py` |
| Every write, the switch, the retention | `oracion/services.py` |
| Screens | `oracion/views.py`, `templates/oracion/` |
| Platform bounds of the retention (90 days, floor 7) and its one read path | `people/policy.py::prayer_retention_days` |
| Scheduled deletion, job `oracion_retencion`; by hand `manage.py prune_prayer_requests [--apply]` | `core/scheduler.py`, `core/management/commands/run_scheduled_jobs.py`, `oracion/management/commands/` |
| Merge: nothing moves and nothing is refused | `people/merge.py` (`NOTHING_MOVES`) |
| Navigation | `core/shell.py`, `oracion/shell.py` |
| Content-free e-mail kind `peticion_oracion_nueva` | `core/notify.py`, `templates/notify/peticion_oracion_nueva.txt` |
| Tenant check `prayer_request_person_church` (its table size is never printed) | `people/management/commands/tenant_integrity.py` |
| Seed flag `--oracion` | `groups/management/commands/seed_community_demo.py` |

Not `care`: a follow-up is a task with a fixed reason and no words; this is words and no task. Neither
app imports the other, and a follow-up's owner reads nothing here (tested).

## Routes and who may use each

All under `/mi-iglesia/oracion/`, all but the first and the third behind `groups.views.community_view` (404 with the module off or
without a person in the church of the request). Everything is fetched inside `request.church`.

| Route | Name | Who | Rule |
|---|---|---|---|
| `` | `oracion:mine` | The account, about what it wrote (not behind the module) | `views.own_requests_view` + `access.own`: rows whose `author` is the account; 404 when there is none and it may not write |
| `nueva/` (GET, POST) | `oracion:new` | An adult person of the church, while the switch is on | `views._writer_or_404` → `services.is_on` and `access.may_write`; again under a lock in `services.create` |
| `<id>/borrar/` (GET confirm, POST) | `oracion:delete` | The account that wrote it (not behind the module) | `views.delete`: fetched from `access.own`, 404 otherwise |
| `recibidas/` | `oracion:inbox` | A pastor of this church | `views._reader_or_404` → `access.is_reader` (= `resources.approvals.can_approve`) |
| `recibidas/<id>/` | `oracion:read` | A pastor the request is addressed to | `access.readable` (the row filter) |
| `recibidas/<id>/leida/`, `responder/`, `hablamos/` (POST) | `oracion:mark_read`, `reply`, `mark_talked` | The same, while the switch is on | `services._locked`: the row again through `access.readable`, under `select_for_update(of=("self",))` |
| `recibidas/avisos/` (POST) | `oracion:reader_email` | A pastor of this church | `_reader_or_404`, and `services._pastor_or_refuse` |
| `politica/` (GET, POST) | `oracion:terms` | A pastor of this church | `_reader_or_404`, and `services._pastor_or_refuse` in each write |

Everybody who is not a reader gets **404 on every reader route**, the same answer as for an id that
does not exist: the administration, a group guide, the owner of a follow-up about the writer, a
superuser, a pastor of another church, a pastor who lost the role, the pastor who was not chosen, a
known minor, the writer. Signed out: the sign-in page. No rule looks at `is_superuser` or `is_staff`;
`has_scope` is not asked because no scope grants this. Nothing is registered in the Django admin.

## Product decisions taken here, and why

Given by the brief and implemented as given: off until a pastor accepts; only pastors read, named on
the form; optional single reader; one plain text, no title, category, attachment or urgency; "Lo leí",
one reply, "Hablamos en persona", no thread; content-free opt-in e-mail to readers only; the writer
deletes at any time; 90 days; a known minor does not see the feature. (The brief's "a merge is refused" was
replaced after review: see 14.)

Decided where the brief did not, always towards less data and fewer readers:

1. **The switch lives in `oracion.PrayerPolicy`, not in a new column of `policies.ChurchPolicy`.**
   The administration's form writes `ChurchPolicy`; this one must be writable only by a pastor, on
   another screen, with an acceptance. A separate table makes "administration alone cannot" a fact
   of the code and avoids a second migration in `policies` while other slices are built in parallel.
   The bounds still live in `people/policy.py`, read on every use and clamped whatever is stored. The
   administration's "Políticas de la iglesia" says whether it is on and that only a pastor changes it.
2. **A pastor is the church role, asked on every request** (`UserChurchRole`, one role per account
   and church, so an administrator is never also a pastor). With no active pastor the form is replaced
   by "Por ahora no está disponible" and nothing is stored.
3. **A pastor never reads their own request as a reader**; the form names the other pastors. The only
   pastor of a church has nobody to write to.
4. **Who reads is by role at the time of reading.** A person who becomes a pastor later can read what
   has not been deleted; the form says so before anything is written. A request for one reader never
   widens: if that pastor leaves the role or the account is deleted, nobody reads it and the writer is
   told ("Hoy nadie la puede leer"). The form carries a fingerprint of the readers it named
   (`services.readers_token`): if they changed while the person wrote, nothing is saved and the page is
   shown again with today's names. The switch does the same for the acceptance.
5. **Who marked or answered is not stored.** The writer is told "Leída" and the date. The name appears
   only when the writer chose one reader.
6. **Audit rows: only the pastors' own actions.** `oracion_politica` (on, off, terms changed),
   `oracion_abierta` (a reader opened the words) and `oracion_atendida` (read, replied, talked), with
   the request's id and a fixed word. **Writing and deleting a request leave no audit row**, and no row
   carries the writer's person id: otherwise the activity log would say who asked for prayer, to people
   who are not readers, long after the request was deleted.
7. **The retention date is fixed when the request is written** (`expires_at`). A later change of the
   church's number never moves an existing request, in either direction: the member's screen shows a
   date and that date holds. `prune` also deletes anything older than 90 days whatever its row says.
8. **The writer's own page and its delete answer whatever is switched off**: the church's prayer switch,
   its `community` module and `EMMAUS_V2_MODULES`. They are the only two routes outside `community_view`
   (`views.own_requests_view`): they show and delete only what the account of the request wrote, and offer
   nothing else. The form promised "lo puede borrar cuando quiera". Readers then read only (switch off) or
   not at all (module off).
9. **At most 5 stored requests per person** (a guard against a flood; shown only to that person), and
   **at most 6 e-mails an hour per reader**, counted by reader and church so the counter says nothing
   about who wrote.
10. **A pastor is shown no contact data** on the request's page: only the name. Whether pastors see
    people's contact data is an open question elsewhere (decision 0008, Q-C3).
11. **The church's own line** replaces only the second sentence, never the 911 one; it is stored as one
    line of words and numbers (no links, ≤ 160) and its words are never written to the audit.
12. **Copy says what the code does**: that the text sits in Emmaus's database until deleted, that no
    screen shows it to who operates the server, that nobody promises an answer, and that what was
    deleted may remain in the server's backups until they are renewed.
13. **Who counts as an adult**: by the date of birth when there is one (typed or verified, the same
    count as `agenda.services.age_on`). With none: an adult unless a camp link says under the event's
    adult age, a door of an event said "es menor de edad", or a guardian's authorization is on record.
14. **A request belongs to the account that wrote it, and a merge of two person records ignores it.**
    (Changed after review: refusing a merge told the administration who had written one.) `author` decides
    the writer's page and the delete; `person` only records which record the account was that day. A merge
    moves nothing, counts nothing, keeps nothing in its manifest and is never refused because of a request
    (`people.merge.NOTHING_MOVES`), so a preview is identical with and without one. Accounts are never
    joined, so the writer keeps it through a merge and its undo. Pastors see the name of the person that
    account is in the church today.
15. **Navigation**: "Pedir oración" for the member under "Para crecer", with no number; "Peticiones de
    oración" for a pastor under "A tu cargo", with the count of unread ones addressed to them. That
    number is a list of things that pastor can open, not a statistic about people, so the minimum
    reporting size does not apply; nobody else sees it and there are no totals, trends or exports.

16. **A request past its date is gone for every screen before the job deletes it** (`access.alive`: the
    lists, the request's page, the reader's actions, the writer's page, the counts and the links).
17. **The form's fingerprint covers what it stated**: the readers, the days and the church's own line. If
    any changed while the person wrote, nothing is saved and the form is shown again.
18. **A new version of the promises (`services.PROMISES_VERSION`) needs a pastor to accept again**: until
    then the church takes nothing new and what exists is read only.

## Emergency wording and its source

Fixed text, shown on the form, on the "not available" page and on the pastor's switch:

> Si tú o alguien más está en peligro en este momento, llama al 911.
> Si pasas por una crisis emocional, la Línea de la Vida te escucha gratis, a cualquier hora, todos los días: 800 911 2000.

Verified on 2026-11-18 against the Mexican government's own pages:

- <https://www.gob.mx/conasama/articulos/linea-de-la-vida-800-911-2000> (CONASAMA, Secretaría de Salud;
  the page is dated 23 October 2025): the name "Línea de la Vida", the number 800 911 2000, free, 24
  hours a day and 365 days a year, guidance on mental health and addictions.
- <https://www.gob.mx/911/articulos/que-es-el-911emergencias?idiom=es> (the government's 9-1-1 site; the
  page is dated 30 September 2016): 9-1-1 "es el Número Único de Llamadas de Emergencia en el país", for
  medical, security and civil-protection emergencies. The page is old; I found no official page that
  contradicts it, and did not look for a newer one beyond a search of gob.mx.

Both pages were opened and read through the session's web tool, which returns a model's reading of
the page, not the raw page: a person should open the two links once before release.

The number should be re-checked before each release that touches this screen.

## What is not built

- Minors: no access at all. Their pastoral care needs the safeguarding policy of #75.
- Requests on behalf of somebody else, anonymous or public requests, prayer walls, groups praying.
- Threads, edits of a request or of a reply, attachments, categories, urgency, search, exports, totals.
- Any escalation workflow beyond the fixed sentences: no flagging, no automatic detection of risk
  (that would be classifying the text, which is forbidden).
- Encryption of the text at rest in the application: it is plain text in the database.
- A pastor's bottom tab on phones: the inbox is reached from "Más".
- Changes to `e2e/`. Recommended journey steps for `e2e/community-flow.mjs` (seed with `--oracion`):
  1. As `guia.grupo@demo.invalid`: "Pedir oración" in the rail → the form names the pastor and shows
     the 911 sentence; axe at 390 and 1440.
  2. Submit a text; "Tus peticiones de oración" shows it with "Todavía no la han leído".
  3. As `admin.comunidad@demo.invalid`: `/mi-iglesia/oracion/recibidas/` answers 404 and the rail has
     no "Peticiones de oración".
  4. As `pastor.iglesia@demo.invalid`: the rail shows the unread number; open the request, "Lo leí",
     answer once; the reply form is gone.
  5. As the guide again: "Leída" with a date and the answer; "Borrarla ahora" → confirm → gone.
  6. As `valeria.soto@demo.invalid` (a campera of 17): `/mi-iglesia/oracion/` answers 404.

## What I could not verify

- **PostgreSQL**: the suite ran on SQLite only (the wave's integrator runs the PostgreSQL gate). The
  locks are `select_for_update(of=("self",))` on the request and a plain `select_for_update()` on the
  person and on the policy row; none was exercised against PostgreSQL, and no test runs two real
  concurrent transactions (the races are tested with stale copies and interleaved calls).
- **Backups**: deleting a row does not remove it from a database dump. The copy says so; when backups
  expire is host work (#45) and nothing here checks it.
- **E-mail delivery**: tested with Django's in-memory backend only.
- **The scheduler**: the deletion runs on every pass whatever `EMMAUS_SCHEDULED_COMMUNITY_JOBS`,
  `EMMAUS_V2_MODULES` and the churches' flags say (`core.scheduler.DELETING_JOBS`; tested through the
  command). It still needs the `scheduler` service to be running; the screens do not depend on it.
- **Error reports**: the two views that receive the words mark them with
  `sensitive_post_parameters`; I did not provoke a real 500 with error e-mails configured to read one.
- **Screens seen in a browser** (Playwright, bypassCSP, 390 and 1440, synthetic seed with `--oracion`,
  axe with zero violations and no horizontal scroll on each): the member's home, "Tus peticiones", the
  form and its error state, the delete confirmation, the pastor's home, the inbox on and off, a request
  before and after answering, the switch on and off, the writer's page with the switch off, and the
  administration's policies page. I looked at the images of the form (both widths), the inbox and the
  switch (1440), the answered request (both widths) and the writer's page with the switch off (390); the
  rest of that list was checked by axe, by the absence of horizontal scroll and by its headings only. **Not seen**: the "not
  available" page, the form with two pastors and its reader choice, a request addressed to one pastor,
  the page with five stored requests, and the switch page before it was ever on.

## Left alone in the existing code

- `people.merge.undo`: an undo of an older merge is not blocked by a prayer request the survivor wrote
  afterwards; the request stays with the survivor, which is the person who wrote it.
- A superuser can still give an account the pastor role through the Django admin of `core`
  (`UserChurchRole`), and would then be a reader. That is the role table's own exposure, not this app's.
