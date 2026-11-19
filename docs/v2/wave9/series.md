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
