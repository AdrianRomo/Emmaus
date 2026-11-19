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
- A throttle per browser session (6 valid submissions per hour) slows a person at the form; it creates a session
  cookie on that POST only. It is not what stops a script (a script drops the cookie): the bounds per event below do.

## Mail-bombing bounds, as numbers

Per address (as the limits read it), shared with subscriptions: 1 request per inbox and event; 2 unanswered
confirmation e-mails per event; 4 per church in 30 days; 3 events per day anywhere (silent: the page is the same).
Per event, whatever the session, address or network: 200 unconfirmed requests (`PENDING_PER_EVENT`) and 60 new
requests per hour (`NEW_PER_EVENT_PER_HOUR`); over either, nothing is stored and the form (GET and POST) says
«Por ahora no podemos recibir más inscripciones para este evento. Intenta más tarde o pregunta en la iglesia.»
for every address. Sending: 50 confirmations per pass, 60 per mail domain and event per hour, 300 per church per
day, taken in turns (one per church per round, one per event inside a church, oldest first; a church at its cap
takes no turn). Each request is mailed once (a failed hand-over is retried with the subscriptions' back-off, at
most 5 attempts). An offer e-mail goes once per offer.

## Review of wave 12 (branch `v2/wave-12-fix`, migration `agenda/0011_visitas_permiso_de_contacto`)

An independent review reproduced five defects here; each is now a test in `agenda/tests/test_visitors_review.py`
(and `espacios`, `cuenta_datos` for the other slices). Decisions taken (the owner delegated them; conservative):

- **A script cannot hold an event's form, nor another church's e-mails.** The bounds per event above; the at-cap
  page is about the event, so it reveals nothing about any address, and every other answer is still the same for
  every address. Requests that lapsed no longer count against the cap. The sending pass takes turns per church and
  per event (`visitors.send_due`), so junk in church A never delays church B. Two requests at the same instant may
  each read the count before the other's row: the bounds are bounds, not exact numbers (no lock was added).
- **«La iglesia puede guardar mi nombre y mi teléfono para invitarme a otras actividades.»** One optional box on
  the form, unticked. When the event does not ask a phone it reads «…guardar mi nombre…» (it never promises to keep
  what was not asked). The answer and its time (when the form was sent) are stored on the request and then on the
  registration (`visitor_contact_consent`, `visitor_contact_consent_at`; a NULL time: never asked).
  «Agregar a personas de la iglesia» is offered for a visitor who signed up alone only with a yes; the view answers
  404 and `services.promote_guest` refuses (`no_contact_consent`), also for recognizing an existing person. Guests
  the team wrote at the door keep today's way in. «Inscritos» says, on each such row, «Dio permiso de guardar su
  nombre y su teléfono…» or «No dio permiso de guardar sus datos: se borran N días después del evento.». The form's
  list says what is true in both cases. At retention nothing of a visitor stays unless they said yes AND the
  administration added them (then the person, with name and phone, and their registration stay; e-mail, key and
  answers go). Rows promoted before this change (only on a sandbox) are not touched: nothing deletes a `Person`.
- **Links follow the church and the event** (`visitors.closed`). With the church inactive, the `community` module
  off, the event not active, public and published, or the option off, the confirmation link is the dead link (GET
  and POST), and `register_visitor` refuses too (`sin_modulo`). The private link then shows «Tu inscripción» and
  nothing of the event or the church, and only lets the visitor cancel (asked first) or leave the waiting list; it
  never accepts an offered place and sends no e-mail. Turning the minors' rule on switches the option off, so it is
  one of these states: the visitor's private page no longer says «tiene que confirmar tu edad», it says to ask at the
  church (the team still sees the row under «Edad por confirmar»). A sign-up closed by its date and an event that
  ended keep their own honest page.
- **The log never holds what a visitor typed**: the authorization, its withdrawal and «Agregar a personas» (both
  its row and the `persona_creada` row of `people.services.save_person`, which gained `audit_name=`) say «una visita
  sin cuenta». After the administration added them, with their yes, they are a person of the church and later rows
  name them as any person.
- **Free text in a waiting request**: turning the minors' rule on also clears free-text answers inside unconfirmed
  `VisitorRequest.answers`.
- `visitors.request`'s docstring is now true: the answer is the same for every address; the database work is not
  (a new address is written), and nothing waits to hide it.
- Copy on the visitor's pages is now neutral for gender («Listo, ya tienes tu lugar», «No se hizo tu inscripción»,
  «Todavía no tienes un lugar en …»). `e2e/community-flow.mjs` step 20 reads none of the changed words: unchanged.
- The seed's first visitor («Visita Inscrita Demo») ticked the box, the others did not.
- Looked at in a browser (repo Playwright, `bypassCSP`, a scratch SQLite with `seed_community_demo --visitas`) at
  390 and 1440 px, axe at zero, no overflow, one `h1`, at most one filled button: the form with the box, its errors,
  «Revisa tu correo», «Listo, ya tienes tu lugar», the at-cap form, «Inscritos» with a visitor who said yes and two
  who did not, and the closed private link (its question, and after cancelling). Images opened and judged: the form
  (both widths), its errors, «Revisa tu correo», the result, «Inscritos» (both), the at-cap page (both), the closed
  private link, its question (1440) and after cancelling. Not looked at: 768 px, offline, keyboard by hand.

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
