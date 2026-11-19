# Wave 10 — Formularios generales y «Pendientes» (issue #92)

New app `formularios`, under the `community` module flag. No new flag, no change to `people` models.
Nothing here reads the camp's questionnaires or their answers, and nothing is scored, calculated,
e-mailed or sent to any outside service.

## What was built

- **Forms** (`Form`): title, "Para qué lo pedimos", audience (people of the church with their
  account / anybody with the link), opens and closes days, retention (30 / 90 / 365 days after the
  last day, default 90), and whether each answer leaves a task.
- **Draft and versions**: the administration edits `Question` rows (the draft). Publishing writes an
  immutable `FormVersion` (title, purpose, questions as JSON) only when something changed. People
  always answer the last published version; an answer stores its version and the purpose it was shown.
- **Question kinds**: short text (200), long text (1000), one option, several options, yes/no, date,
  and for public forms one contact block (name, e-mail and/or phone, with its own consent sentence).
  One level of "only if that one-option or yes/no answer is X". 30 questions at most, 12 options each.
- **Answering**: `/mi-iglesia/formularios/` for people of the church; `/formulario/<token>/` for a
  public form, without an account.
- **Readers**: besides the administration, a form can name the guide of one active group or the
  coordinator of one active team (`FormReader`, a scope, never a list of names).
- **Task** `FormTask` ("Dar seguimiento"), at most one per answer: owner (an account that reads the
  form), due day, open/done, one short plain line of note.
- **"Agregar a personas"**: a manual, audited action of the administration on a public answer.
- **«Pendientes»** (`formularios/queue.py`): a read-only list of what waits for the signed-in person.
- **Retention**: `services.prune`, `manage.py prune_form_submissions [--apply]`, scheduler job
  `respuestas_formularios`.
- **Export**: one form's answers as CSV, administration only, audited, cells through
  `events.services.exports.safe_cell`.
- **Seed**: `seed_community_demo --formularios` (one form of the church, one public, five synthetic answers).

## Routes and who may use them

All under `community_view` (signed in, module on or 404, a person in the church of the request).
Forms are fetched inside `request.church` (`views._form`); answers, questions and tasks under their form.

| Route (`formularios:`) | Who | Rule |
|---|---|---|
| `list`, `fill`, `thanks` | any person of the church; `fill` only while the form is open, else 404 | `views.fill`, `services.is_open` |
| `queue` («Pendientes») | any person of the church; rows per source | `queue.rows` |
| `manage` | administration (all forms) or a reader (their forms); others 403 | `views.manage`, `access.readable_forms` |
| `create`, `loose_tasks` | administration | `church_admin_view` |
| `detail`, `edit`, `question_*`, `publish`, `close`, `readers`, `export`, `task_assign`, `add_person` | administration | `views._admin_form` |
| `submissions`, `submission`, `task` | administration and the form's readers | `views._readable_form` → `access.can_read` |
| `formulario:fill`, `formulario:thanks` (public) | anybody, only while that public form is open | `public.public_form` |

`access.can_read` asks `core.authz.has_scope(user, church, "group"|"ministry", id, "manage")` for
each reader row and that the group or team is active; the administration is `has_scope` church
`manage`. A pastor reads no form by being a pastor. Whoever answered reads nothing at the staff's addresses.

## Decisions taken (conservative option, each can be changed)

1. **No anonymous mode.** `feedback` covers anonymous opinions.
2. **Closing day is mandatory**, at most a year after opening, so retention always has an end.
   Closing by hand is final and counts retention from that day.
3. **After the first publication** the audience cannot change and retention can only be shortened:
   people were already told who reads and how long answers are kept.
4. **Admin-typed words shown without pastoral approval** (title, purpose, questions, options, consent
   sentence) are single-line, length-limited, and refuse links and `<` `>` (`services.clean_line`).
5. **Public long text** needs an administrator's explicit acknowledgement, stored with who and when;
   a draft that became public with a long question cannot be published without it.
6. **Contact block** only in public forms, once per form, never conditional; if any contact field is
   filled the name, one way to reach the person and the consent box are required.
7. **Conditional questions without new JavaScript**: the question is always drawn with a hint
   ("Responde solo si…"); the server drops its answer and never demands it when the condition fails.
8. **Duplicates**: the same person (church form), or the same e-mail or phone (public form, compared
   as HMAC keys of version + normalised contact, like `feedback`), answering the same version within
   24 hours keeps the first answer and makes no second task. The second send gets the same "gracias".
9. **Public POST**: Django's CSRF cookie is the only cookie; no session, no `messages`. Throttle is
   per form (20 valid answers per 10 minutes from everybody together), with no network address or
   session in the key. Honeypot field. A form keeps at most 2000 answers.
10. **Identical 404** for a public address that is unknown, a church form, a draft, not yet open,
    over, closed, module off or church inactive. Who is signed in is not looked at.
11. **Task owner is an account, not a person record**, because what reads a form is the account.
    The owner must read the form; when a reader is removed, or the owner's role ends, the task shows
    as "sin responsable" for the administration and leaves the owner's list.
12. **A task is deleted with its answer** when retention ends. The note never holds answers; the
    audit log never holds the note.
13. **"Agregar a personas"** needs the `people` module, goes through `people.services.save_person`,
    offers people with the same e-mail or phone first, refuses a new record when the e-mail is taken
    (the rule of `PersonForm`), gives no account, no group and sends nothing. Adults only: said on screen.
14. **Readers do not export, assign, or add people.** They read, write the note and mark done.
15. **«Pendientes» shows aggregated rows** for group requests, space requests, approvals and unowned
    items (a number and a link), and one neutral row per own follow-up or form task. It never shows
    who a follow-up is about, its reason, a booking's title, an answer or a contact.
16. **Not in «Pendientes»**: camp follow-ups and discipleship follow-ups (they stay behind their own
    confidential screens), and a member's own serving dates (not staff work).
17. **Merges**: `Submission.person` moves to the surviving record (`people/merge.py` TABLES).
18. **Retention job** runs for every church whatever its flags, because it only deletes.
19. Answer counts on staff screens are shown to people who can read each answer by name, so the
    minimum reporting size does not apply; no aggregate of answers is shown to anybody else.

## Shared files touched (append-only)

`emmaus/settings.py`, `emmaus/urls.py`, `people/audit.py` (10 actions, `form_id`), `core/shell.py`
(«Formularios», «Pendientes», «Respuestas de formularios», «Formularios de la iglesia»),
`people/merge.py`, `people/tests/merge_base.py`, `people/management/commands/tenant_integrity.py`
(2 checks) and its tests (31 tables), `core/scheduler.py`, `run_scheduled_jobs.py`,
`core/test_scheduler.py` (6 jobs), `seed_community_demo.py`, `static/css/santuario.css`.

## Not built

Anonymous forms; forms about minors; file uploads; scoring; e-mail of any kind; automatic creation
of people, memberships or groups; a task created by hand on an answer without one; repeating forms;
templates of forms; editing or deleting a single answer; hiding conditional questions in the browser;
a person's own history of what they answered; camp and discipleship tasks in «Pendientes».

## Not verified

- PostgreSQL: only SQLite was run. `select_for_update(of=("self",))` is used wherever a join is
  selected, but the integrator's `--postgres` gate is the proof.
- Two truly simultaneous public sends: the form row is locked, tested only with sequential calls and stale copies.
- The throttle under real load, and real spam against the honeypot.
- Screen readers by ear; axe-core reported no violation on the screens listed in the report.
