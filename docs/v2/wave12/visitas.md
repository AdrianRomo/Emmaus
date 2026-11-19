# Wave 12, slice «visitas»: sign-up without an account, questions at sign-up, headcounts

Issue #93, next part. Branch `v2/wave-12-visitas`, app `agenda`, migration `agenda/0010_visitas_preguntas_conteo`.
Everything is behind the `community` module and, for the visitor form, off per event.

## What was built

1. **Sign-up without an account** (`agenda/visitors.py`, `agenda/visitor_views.py`, `templates/agenda/visitor.html`).
   The administration turns «Inscripción para quien no tiene cuenta» on in the event's page. The public page then
   offers «Inscribirme» as a link (it still sets no cookie and reads no table of people). The form asks name,
   surname, e-mail, a phone only when the event asks, the event's questions and the required statement «Soy mayor
   de edad». Sending it stores a `VisitorRequest` and answers one page whatever the address. The scheduled pass
   sends one content-free e-mail (church, event name, signed link, 48 h). Opening the link shows a button; pressing
   it calls `agenda.services.register_visitor`, which creates a **guest** `EventRegistration` (no `Person`, no
   account) under the page lock, through the same capacity, waiting list and `standing` as every sign-up.
   The answer says honestly where the visitor stands (in, on the waiting list, or not signed up and why) and
   gives a private link (signed over a random key, no deadline) to see the registration, cancel until the event
   starts, accept an offered place, or leave the list.
2. **Questions at sign-up** (`agenda/questions.py`, `agenda/extras_views.py`): up to 5 per event; short text
   (120), one option (2 to 8 options of 60), yes/no. Members answer on a step page (`signup_step.html`) and
   visitors on their form. Answers are shown on «Inscritos» only.
3. **«¿Cuántos vinimos?»** (`agenda/headcounts.py`): one `EventHeadcount` per event (total, optional adults and
   children), typed by the administration from the event's first day. Per series: the last 12 counted dates as a
   plain table with the existing `.meter` bar, on the series' administration screen and in Panorama.
4. **Scheduler job `inscripciones_visitas`**, in `JOBS` and `DELETING_JOBS`: deletions always, e-mails only with the
   community jobs on.
5. **Seed**: `seed_community_demo --visitas`.

## Routes and who may use them

| Route | Who | Rule |
|---|---|---|
| `GET/POST /evento/<slug>/inscribirme/` | anybody, while offered; else the public page's 404 | `visitors.offered_page` |
| `GET/POST /evento/inscripcion/confirmar/<token>/` | whoever holds the signed link (48 h) | `visitors.for_confirmation` |
| `GET/POST /evento/inscripcion/mia/<token>/` | whoever holds the private link | `visitors.for_manage` |
| `…/eventos/<slug>/preguntas-de-inscripcion/` (+ `<id>/`, `<id>/quitar/`) | church administration | `groups.views.church_admin_view` |
| `…/eventos/<slug>/conteo/` | church administration | `church_admin_view` |
| Trend on `…/administrar/series/<id>/` | church administration | `church_admin_view` (existing view) |
| Trend in Panorama | church pastor and administration | `overview.access.can_see_overview` (existing) |

Events are always fetched inside `request.church`. The three public pages never look at who is signed in.

## Decisions taken (the brief's, and the ones it left open)

- **Adults only; never where the event protects minors.** `services.visitor_block` answers `menores` whatever
  the option says; `save_page` refuses turning both on (`minors_no_visitors`) and switches the option off when the
  rule is on. The public page of such an event says «Este evento cuida a los menores de edad: quien no tiene
  cuenta se inscribe en <iglesia>». Turning the rule on later: the form 404s, pending links sign nobody up, and
  `resettle_event` moves existing visitor registrations to «Edad por confirmar» (`guest_age_checked` is never set
  by a visitor), where the existing «Confirmar edad» of a visitor applies.
- **The waiting list takes visitors.** The brief asks for a waiting-list entry when full. The old check constraint
  (`agenda_reg_list_is_person`) forbade a guest on the list, so the migration **removes it and adds
  `agenda_reg_list_person_or_visitor`** (a person, or a row with a visitor key). It is a relaxation: no row can
  violate it, and a guest the team wrote at the door still cannot wait (tested). The migration-scope script
  passes; the integrator should know a `RemoveConstraint` is in it. An offer to a visitor is announced by one
  content-free e-mail to the private link, where it is accepted (`services.accept_offer`, generalised to rows
  with a visitor key).
- **Limits are shared with the e-mail subscriptions** (`SubscriptionSuppression`), so the two forms together
  cannot exceed the bounds below. Consequence: an address with two unanswered confirmations for an event (of
  either kind) gets no third, silently.
- **The church's log never holds a visitor's typed name.** Audit rows of a self-signed visitor say «una visita
  sin cuenta» and carry ids (`services._audit_name`); rows of people and of guests the team wrote are unchanged.
- **The visitor's e-mail is not copied to `Person`** by «Agregar a personas» (it was given for this event). The
  row keeps it, so the private link and offers keep working.
- **One place per inbox**, compared as the limits read an address (capitals, `+tag`, Gmail dots).
- **Answers end with the registration**: deleted on cancel, leaving the list, annulment, with their question,
  and at retention. In an event that protects minors free-text questions are not asked, cannot be added, and
  turning the rule on deletes free-text answers already given (a safeguard is never blocked by a question).
  A question's kind is fixed once created.
- **Retention**: the event's own `Event.retention_days` (default 365) after its last day: every sign-up answer,
  the registrations of self-signed visitors, and the e-mail and key of visitors who were added to people.
  Requests: 48 h after their e-mail was sent, confirmed or not; 2 days if never sent. Headcounts are kept
  (numbers, no person).
- **Headcounts are typed by the church administration only** (there is no usher role; conservative). The audit
  row says a count was written, never the numbers.
- **Children's number**: wherever a count is read (series table, Panorama) adults and children are exact only
  when both parts (typed, or what the total leaves) reach the church's minimum reporting size; otherwise the
  adults are hidden and a small children's number reads «Menos de N». The form shows the typed numbers back to the
  administration so they can be corrected. The total is always exact.
- **Panorama**: one section, one extra query (the cost tests now expect 12), no new audience.
- **Duplicating an event** copies none of the three new relations (questions included: they are read by the
  public, and nobody read them for the new event). Both duplicate maps say so.
- A throttle per browser session (6 valid submissions per hour) slows a script at the form; it creates a session
  cookie on that POST only.

## Mail-bombing bounds, as numbers

Per address (as the limits read it), shared with subscriptions: 1 request per inbox and event; 2 unanswered
confirmation e-mails per event; 4 per church in 30 days; 3 events per day anywhere. Per event: 500 unconfirmed
requests stored. Sending: 50 confirmations per pass, 60 per mail domain and event per hour, 300 per church per
day. Each request is mailed once (a failed hand-over is retried with the subscriptions' back-off, at most 5
attempts). An offer e-mail goes once per offer.

## Not built, on purpose

- **Ticket kinds**: without payments (#86) a kind is a question with options.
- **Transferring a place**: it would bypass the waiting list and the minors' rule.
- **Companions / family booking**: one person per form; waits for verified households (#59, #75).
- Export of answers, totals of answers, reordering questions, editing answers, staff cancelling a visitor's
  registration (as for a member's), a second e-mail carrying the private link, headcounts by ushers.

## Not verified

- PostgreSQL: the suite ran on SQLite only (the integrator runs `--postgres --e2e`). The locks use
  `select_for_update(of=("self",))` and the order request → page → registration; a test asserts the order of the
  queries, not real contention.
- Real mail delivery; only the locmem backend.
- Browser: looked at 390, 768 and 1440 px with axe (zero violations after one fix) for: the public page, the form
  (empty and with errors), «Revisa tu correo», the confirm button, the private page (confirmed, long name,
  waiting, asking to cancel), the dead link, «Inscritos», the event page form, the questions builder (many, empty,
  errors, edit, remove), the headcount form (filled, error, before the day), the series screen, the member's step
  (and its error) and Panorama as pastor and administration: 78 screen states measured (axe, overflow, one `h1`,
  one filled button, 44 px targets). **Images opened and judged by eye**: at 390 px the public page, the form and
  its errors, «Revisa tu correo», the confirm button, the private page in four states, the dead link, «Inscritos»,
  the questions builder (list, errors, edit, remove), the headcount form (three states), the member's step with
  an error and the series screen; at 1440 px «Inscritos» and Panorama. **Not opened by eye** (measured only): the
  event page form, the empty questions screen, Panorama at 390, every 768 px image. **Not looked at at all**: the
  result page right after confirming, «No quedaste inscrito», an offered place, a visitor pending for age. Two
  small changes were made after the last screenshots (no colon after a question on «Inscritos»; the options box
  hidden when editing a question that is not a list) and were not photographed again. No screen here has an
  action in place, so the keyboard/offline check of `data-inline` does not apply; offline was not tried.

## Recommended journey steps (for `e2e/community-flow.mjs`, with `--visitas`)

1. Anonymous: `/evento/desayuno-de-bienvenida-demo/` shows «Inscribirme», sets no cookie; axe.
2. Anonymous: the form; submit empty (errors, axe); submit valid → «Revisa tu correo».
3. Administration: «Inscritos» of that event shows «Se inscribió sin cuenta, correo confirmado», the answers and
   the count of unconfirmed requests; axe.
4. Administration: «Preguntas de la inscripción»: add a yes/no question, see it listed.
5. Member not signed up: «Inscribirme» leads to «Antes de inscribirte»; answer and land on the event.
6. Administration: «¿Cuántos vinimos?» on a past date of «Servicio dominical», then the series table; pastor:
   Panorama shows «Menos de 5» for the small children's count.
