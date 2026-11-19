# Wave 10: updates of one event by e-mail, for people without an account

Issue #96, second part (context: #59 phase A, decisions 0010, 0012 and 0017). Branch
`v2/wave-10-suscripciones`. Code: `agenda/subscriptions.py` (rules), `agenda/subscription_mail.py`
(the two e-mails), `agenda/subscription_views.py` (the three public pages), migration
`agenda/0008_suscripciones_por_correo`.

## What was built

- **The option**, per event and off by default: `EventPage.notice_mail`, "Avisos por correo para quien
  no tiene cuenta", on the event's page form. A copy of an event and a date of a series start with it off.
- **The public page** (`/evento/<slug>/`) shows a short section with the purpose and a link when the
  option is on and this server can send. It still has no form and sets no cookie.
- **The form** (`/evento/<slug>/avisos-por-correo/`): one field, the address. The answer is one redirect
  to one text, whatever happened.
- **Double opt-in**: the scheduled pass sends one e-mail with a signed link (48 hours). The link opens a
  page with a button; only the button confirms.
- **One e-mail per new public notice** to each address confirmed before the notice was published, sent
  by the scheduled pass, with once-only rows, attempts and waits (5 min, 30 min, 3 h, 12 h; five tries).
- **Unsubscribe**: a signed link in every notice e-mail and the `List-Unsubscribe` /
  `List-Unsubscribe-Post` headers. The address is deleted at once.
- **Automatic endings**: an unconfirmed request after 48 hours; every address 14 days after the event's
  last day; all of them with the event.
- **The staff screen of public notices** says whether publishing sends e-mail, to how many (only from the
  church's minimum reporting size up), and why not when it does not.
- **A scheduler job**, `suscripciones_evento`, the sixth of `run_scheduled_jobs`.

## Routes and who may use them

| Route | Who | Rule |
|---|---|---|
| `GET/POST /evento/<slug>/avisos-por-correo/` | anybody, while the offer is open | `subscription_views.subscribe` → `subscriptions.offered_page`; else the public page's 404 |
| `GET/POST /evento/avisos-por-correo/confirmar/<token>/` | whoever holds the link | `subscriptions.for_confirmation` (signature, 48 h, row, event still takes subscriptions) |
| `GET/POST /evento/avisos-por-correo/baja/<token>/` | whoever holds the link | `subscriptions.for_unsubscribe` (signature, row exists) |
| `…/mi-iglesia/eventos/<slug>/pagina/` (the option) | church administration | existing `church_admin_view` on `agenda.views.page_edit` |
| `…/mi-iglesia/eventos/<slug>/avisos-publicos/` (the state) | church administration | existing `church_admin_view` on `agenda.views.public_notices` |

No new permission was invented. The three public pages never look at who is signed in.

## Decisions taken, and why

1. **The form is on its own page, not on the public page.** A form needs a CSRF token, and rendering one
   sets a cookie. The public page's promise is that reading it leaves no cookie, so it carries a link.
   The form's page uses the ordinary CSRF check, as the sign-in page does.
2. **The unsubscribe POST is exempt from the CSRF check.** RFC 8058 one-click unsubscribe is a POST from
   the mail program, without cookies. Nothing there is authorized by a cookie: the signed token in the
   address is the whole authorization. The confirm POST keeps the ordinary check.
3. **Opening the confirmation link confirms nothing.** Mail scanners follow links; a GET that confirmed
   would defeat the double opt-in.
4. **No e-mail is handed over inside a request.** The pass sends the confirmation (within the pass's
   interval, five minutes by default). So the request's duration says nothing about the address, and a
   slow mail server cannot hold a public page. Cost: a few minutes of delay, also for a notice.
5. **One confirmation e-mail per address and event every 48 hours**, stricter than the one per day that
   was asked: an unconfirmed row is never mailed twice and lives 48 hours. Also at most 3 unconfirmed
   requests per address per day across all events, and 30 requests per event per hour
   (`events.services.throttle`, keyed by the event: it fits anonymous use because it needs no session and
   its window is the hour its purge assumes). Over the event's limit, requests are dropped for an hour
   with the same answer; somebody can so block new subscriptions to one event for a while.
6. **The links carry a random key of the row, signed; never the address, and no slug.** An unsubscribe
   link must work whatever becomes of the event's address or page. A link of one event cannot touch a row
   of another because the key names one row.
7. **The event's name is in the e-mail; the notice's title and text never.** The subject is fixed and
   names nothing.
8. **These two e-mails do not go through `core.notify`**, and it was not changed. Its contract is two
   values and one link, its texts send the reader to their account, it carries no headers, and its sender
   logs the mail server's error, which can quote the recipient. `agenda/subscription_mail.py` applies the
   same discipline (fixed text, a strict list of four values, refusals that name keys only) and logs a
   failure by its class name. If the integrator prefers one path, the fold is: per-kind extra keys and
   headers in `core.notify`, and a sender that does not log the error's message.
9. **Whether a notice is mailed is decided when it is published** (`EventNotice.mail_subscribers`): the
   option on, the page public and the server able to send. A notice published while the screen said
   "nobody is mailed" is never mailed later. Only notices at most 3 days old are mailed.
10. **Who confirms after a notice is not mailed about it.**
11. **Staff see "direcciones", not "personas"**: an address is not a person. Below the minimum reporting
    size the words do not tell none from a few ("menos de 5 … puede no haber ninguna"), also for the
    count of e-mails of one notice.
12. **No audit row for subscribing, confirming or leaving.** There is no account to record, and the row
    could only hold what must not be kept. Turning the option on is audited as any change of the page.
13. **14 days is a constant** (`subscriptions.RETENTION_DAYS`). A church policy that shortens it needs a
    column in `policies`, which this slice does not own. Note: the public page stops when the event ends,
    so nothing is mailed in those 14 days; they only cover an event whose dates are corrected afterwards.
    The owner may prefer a shorter period.
14. **Confirming needs the event to still take subscriptions**; unsubscribing never does. The
    unsubscribe page names the event only while its public page answers.
15. **No seed flag and no Django admin page** for the new tables.
16. **One job, not two**: the deletions run first and always (every church, with or without e-mail); the
    e-mails only for churches using the module, with `EMMAUS_PUBLIC_BASE_URL` and a mail backend that
    delivers. Like the other community jobs it stops with `EMMAUS_SCHEDULED_COMMUNITY_JOBS=false`; then
    the form is not offered, and expired unconfirmed rows are still treated as absent.

## Storage

`EventSubscription`: event, address, keyed hash of the address (HMAC-SHA256 from `SECRET_KEY`, for
uniqueness lookups), a random key, dates, and the state of its confirmation e-mail. No person, no name,
no phone. `EventNoticeEmail`: notice, subscription, outcome, attempts; unique per notice and subscription.
Changing `SECRET_KEY` makes old hashes unfindable: an address could then be stored twice for one event.

## Not built

SMS, WhatsApp Business, push; subscriptions to a whole church; anything in `notices/`; a church policy
for the retention; a per-browser or per-IP throttle; a bounce or complaint handler (#52); sending straight
after publishing instead of on the next pass.

## Not verified

- PostgreSQL: only SQLite was run. The claims rely on conditional `UPDATE`s and unique constraints, and
  no `select_for_update` was added, but two real concurrent passes were not run; the test nests a second
  pass inside the first.
- A real mail server and a real mail program's one-click button.
- The `--postgres --e2e` gate (left to the integrator).
- Leadership approval of the contact purpose and retention, which #96 lists as a dependency: taken here
  under the delegation of decision 0017, for the owner to confirm.

## Screens looked at in a browser

Playwright, 390 px and 1440 px, synthetic seed with `--eventos`, axe with no violations and no horizontal
overflow: the public page with the section, the form, the form with an error, "Revisa tu correo", the
confirm page, "confirmado", the unsubscribe page, "te dimos de baja", the page for a link that no longer
works, the event's page form and the public notices screen for the administration. The screen after
publishing was looked at only at 1440 px. Not looked at: the staff screen's variants for a server without
e-mail or without a public address, and for a page that is not public (covered by tests only).

## Recommended steps for `e2e/community-flow.mjs`

1. As administration, turn on "Avisos por correo para quien no tiene cuenta" for a public event.
2. Signed out, open `/evento/<slug>/`: the section is there and the browser has no cookie.
3. Follow the link, send an address `@demo.invalid`, land on "Revisa tu correo"; audit both pages.
4. Open an unsubscribe address with a made-up token: "Este enlace ya no sirve", status 404; audit it.
5. As administration, open "Avisos públicos": it says "menos de 5 direcciones suscritas".
