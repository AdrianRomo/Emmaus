# Wave 9 — series and the public calendar (issue #93, first slice)

## What was built

- **Series** (`agenda.EventSeries`): what a church does by the week. A template (name, days of the week, every N
  weeks, hour, duration, place, time zone, from/until, description, whether each date is published, public, takes
  sign-ups, and its places) plus **ordinary simple events**, one per date, tied to the series by
  `agenda.SeriesOccurrence`. Nothing is a recurrence worked out on the fly: capacity, offers, the waiting list,
  walk-ups, overrides, attendance and opinions are the existing code, per date, untouched.
- **Generation** (`agenda/series.py::generate`): idempotent, for a rolling window (`WINDOW_WEEKS = 8`), when a series
  is created, changed or resumed, and on every pass of the scheduler (job `series_eventos`, `generate_due`).
- **Exceptions**: a date is edited by itself (it then stops following the series) or called off ("Este día no hay").
- **Calendar files** (`agenda/calendar.py`): `/iglesia/<slug>/calendario.ics` (the church's public events) and
  `/evento/<slug>/calendario.ics` ("Agregar a mi calendario" on the public page of an event). RFC 5545 text written
  by hand (escaping, folding at 75 octets, CRLF); no dependency added.
- Members' list and the home show the next date of each series ("Se repite cada domingo. Ver las otras fechas");
  the administration's list groups the dates under their series.
- Seed: `seed_community_demo --eventos` adds the series «Reunión general» (Sundays 11:00), idempotently.

## Existing registration safeguards (documented, not rebuilt)

`agenda/services.py`: `register` (places counted with the page row locked), `standing` (minors, decision 0015),
the waiting list and offers (`_advance`, `accept_offer`, `expire_offers`, decision 0011), the door
(`register_walkup` with its audited overrides), `record_attendance`. A date of a series goes through all of them
because it is an `events.Event` with an `EventPage` like any other.

## Product decisions taken

1. **The link is a table in `agenda`, not a column on `events.Event`.** `SeriesOccurrence(series, day, event)` holds
   the unique constraint `(series, day)`; `day` is the slot the rule gave and never moves, so a date somebody moved
   by hand is still not written again. No migration in `events`.
2. **Defaults are closed**: a new series is not published, not public and takes no sign-up.
3. **A date takes less than a copy does** (`agenda.duplicate`): the event's basics and the page's configuration from
   the template; never people, registrations, authorizations, public notices, opinions, attendance, FAQs or programme.
   Unlike a copy, a date is published/public when the series says so (that is the series' purpose). The minors'
   authorization starts as the church's policy says, as for any new simple event, and a series never switches it off.
4. **"Changed by hand"** is any save of the date's «Página» or «Datos» (`SeriesOccurrence.edited_at`). There is no
   "follow the series again" action yet.
5. **Changing a series** touches only dates to come, not edited by hand, with nobody signed up or in line (checked
   with the page row locked, the same lock a sign-up takes). The others are listed under «Fechas que no cambiamos».
   If the rule no longer falls on an untouched date (another weekday), the date is not deleted: its page is
   unpublished and it is marked `fuera`; it comes back if the rule falls on it again.
6. **"Este día no hay"** unpublishes the date's page (so the members' list, new sign-ups, the public page and the
   calendar drop it by the existing rules) and keeps the row. Registrations are **not** touched and nobody is
   e-mailed: the screen tells the administration to tell them; who was signed up reads «Este día no hay» on the date.
   `Event.status` is not used: «Terminado» feeds the retention purge.
7. **Ending** a series stops new dates and leaves every existing one; it can be resumed.
8. **Time**: a date stores a local day, hour and zone, so 10:00 stays 10:00 across a clock change; the calendar says
   it as a UTC instant computed per date (tested with America/Tijuana, 25 Oct / 1 Nov 2026).
9. **Calendar contents**: name, start/end, place, the public URL. The page's description is left out although it is
   public (less data); never people, counts, capacity or ids (the UID is the public slug). A day with an hour is a
   timed entry (with an end only for a date of a series, from its duration); several days are an all-day entry.
10. **A church with no public event answers the same 404** as an address that does not exist (no oracle), so the feed
    address works from the first public event on. The administration reads the address in «Administrar los eventos»
    only while it answers.
11. **Audit rows** carry actions, ids and counts; never the series' name.
12. The seeded series is published but **not public**, so the demonstration's public home is not filled with Sundays.

## After the independent review (wave 9 integration)

- **"Sí hay" never puts a date back as it was.** Changes to a series skip a day that is called off, so on restore:
  a date nobody changed by hand and nobody signed up for takes what the series says *now* (place, hour, published,
  public) or is retired if the series left its day; a date changed by hand or with sign-ups is not rewritten and
  comes back **unpublished**, listed under «Fechas que no cambiamos» ("No está publicada: revísala").
- **Members read dates, never the template.** The member's series page takes its title from the next visible date
  and shows no description of the series; "Se repite…" (list, date page, series page) is said only while the series
  itself is published and active.
- `generate` refuses a church that is not active, as well as one without the module.
- A series that ended cannot be changed, by its form or its address (`SeriesEnded`): resume it first.
- Dates retired by a change of day (`fuera`) are listed on the series screen: «Fechas que la serie ya no incluye».
- Calendar: Unicode line breaks (U+2028, U+2029, U+0085, VT, FF) become the escaped line break and every control
  character (C0, DEL, C1) is dropped; ended events are left out by the query; an event with no first day answers
  the same 404. The two calendar answers carry `Cache-Control: public, max-age=300` through the existing
  `public_asset` mark of `events.middleware.PrivateResponseMiddleware` (the bytes are the same for every reader);
  a response that got a cookie, and every 404, stays `no-store`. A day called off can stay in a cache five minutes.

## Polish round (browser journey and a designer's read)

- **"Coming" is by the clock, in the series' zone** (`agenda.series.has_started`). Today's date is written only
  while its hour has not passed; the member's list, the home, the member's series page, «La que sigue» and «Fechas
  que vienen» skip a date that already began (on the series screen it becomes the first of «Las últimas que
  pasaron»). The rest of the agenda still counts by days, on purpose: the date's own page opens all day, and a date
  somebody signed up for keeps its row until the day ends, like any event.
- The window is eight whole weeks counting today (`horizon`), so a weekly series has exactly eight dates ahead.
- Series screen: a row is weekday, date and hour, a quiet «Abrir» (the date's page, which now links its sign-ups
  and its data) and one action, «Este día no hay». «Terminar la serie» is its own block at the end.
- Member's series page: the name and the place once, then a plain list of weekday, date and hour, each one the link
  to its date; a date says its own name or place only when it differs.

## Routes and permissions

| Route | Who | Rule |
|---|---|---|
| `mi-iglesia/eventos/administrar/series/nueva/`, `…/series/<id>/`, `…/<id>/editar/`, POST `…/<id>/terminar/`, `…/<id>/reanudar/`, `…/<id>/fechas/<n>/no-hay/`, `…/si-hay/` | church administration | `groups.views.church_admin_view`; the series is fetched inside `request.church` (`agenda/series_views.py::_series`, `_occurrence`) |
| `mi-iglesia/eventos/serie/<id>/` | a person of the church | `community_view`; 404 unless a date of it is visible to them (`services.upcoming_for`) |
| `iglesia/<slug>/calendario.ics`, `evento/<slug>/calendario.ics` | anybody | `agenda.public.public_pages` + not over + module on; else the one 404 |

Module off: every route above is 404. Scheduler job: skipped with the other community jobs; a church with the module
off is skipped and catches up later.

## Not built

Anonymous self-registration, ticket types, questions, payments, family booking, transfers, headcounts, room bookings.
Also not: monthly rules ("primer domingo"), deleting a series, "follow the series again", a notice to who signed up
when a day is called off, time zones outside Mexico in the form (the model and the generator accept any zone).

## Not verified

- PostgreSQL (the integrator's gate): the lock order series → page → event → occurrence and
  `select_for_update(of=("self",))` were written for it but ran only on SQLite.
- Two real concurrent passes; the tests use a stale read and the constraint.
- A real calendar client subscribing to the feed (Google, Apple, Outlook).

## For the integrator

- `sitio` lists public events on the church's public home: a public weekly series will fill that list with its
  dates. `agenda.series.collapse(events)` is the helper that keeps one per series.
- `core/test_scheduler.py` changed: four jobs instead of three.
