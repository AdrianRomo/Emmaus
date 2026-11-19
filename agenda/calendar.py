"""Calendar files (iCalendar, RFC 5545) of what the church made public (issue #93).

* ``/iglesia/<slug>/calendario.ics``: every public event of one church, to subscribe to from a calendar.
* ``/evento/<slug>/calendario.ics``: one public event, "Agregar a mi calendario".

Both serve exactly the events whose public page answers (``agenda.public.public_pages`` and the same two
checks: not over, the module on), and for each one only its name, when it starts and ends, the place and
the address of its public page. NEVER the description, a person, a count, the places or an internal id.
The view asks for the church, the pages with their events and, for a date of a series, how long it lasts;
no table of people. Everything else is the same 404 as an address that never existed, a church without a
public event included. Who is signed in is never looked at, nothing is written and no cookie is left.

The text is written here, with the standard's escaping and line folding, so no dependency is added.
"""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.http import Http404, HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_safe

from core.models import Church
from people import flags

from . import public, services
from .models import EventPage, SeriesOccurrence

# A calendar is not an archive: the events that have not ended, the nearest first, up to this many.
FEED_LIMIT = 200
CONTENT_TYPE = "text/calendar; charset=utf-8"
BACKSLASH, SEMICOLON = chr(92), chr(59)
# Seconds a browser or a proxy may keep a calendar: every reader gets the same bytes (nothing in them or in the
# view depends on who asks), so the answer is public. A day called off may be shown that much longer.
CACHE_SECONDS = 300


# Characters some readers take for a line break, besides the two the standard names.
LINE_BREAKS = ("\r\n", "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")


def escape(text: str) -> str:
    """A TEXT value (RFC 5545, 3.3.11): backslash, semicolon and comma escaped, every kind of line break
    (the Unicode ones too) as the two characters backslash and n, and no other control character (C0, DEL, C1)."""
    text = str(text)
    for mark in LINE_BREAKS:
        text = text.replace(mark, "\n")
    text = "".join(char for char in text if char == "\n" or unicodedata.category(char) != "Cc")
    for char in (BACKSLASH, SEMICOLON, ","):
        text = text.replace(char, BACKSLASH + char)
    return text.replace("\n", BACKSLASH + "n")


def fold(line: str) -> str:
    """One content line folded at 75 octets (RFC 5545, 3.1), never in the middle of a character."""
    chunks, current, size = [], "", 0
    for char in line:
        width = len(char.encode())
        if size + width > 75:
            chunks.append(current)
            current, size = " ", 1  # the continuation's leading space counts
        current, size = current + char, size + width
    chunks.append(current)
    return "\r\n".join(chunks)


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _duration(event) -> int | None:
    """Minutes a date of a series lasts; an event on its own has no written end hour."""
    try:
        return event.series_occurrence.series.duration_minutes
    except SeriesOccurrence.DoesNotExist:
        return None


def event_lines(page: EventPage, base_url: str) -> list[str]:
    event = page.event
    link = base_url.rstrip("/") + public.public_path(event)
    host = urlsplit(base_url).hostname or "emmaus"
    last = event.ends_on or event.starts_on
    lines = [
        "BEGIN:VEVENT",
        f"UID:{event.slug}@{host}",
        f"DTSTAMP:{_utc(max(event.updated_at, page.updated_at))}",
    ]
    if page.starts_at and last == event.starts_on:
        # The local hour of that day in the event's zone, said as an instant: 10:00 stays 10:00 where the
        # event is, whatever the clocks do between one date and the next.
        start = datetime.combine(event.starts_on, page.starts_at, tzinfo=public.notice_zone(event))
        lines.append(f"DTSTART:{_utc(start)}")
        minutes = _duration(event)
        if minutes:
            lines.append(f"DTEND:{_utc(start.astimezone(UTC) + timedelta(minutes=minutes))}")
    else:
        lines.append(f"DTSTART;VALUE=DATE:{event.starts_on:%Y%m%d}")
        lines.append(f"DTEND;VALUE=DATE:{last + timedelta(days=1):%Y%m%d}")
    lines.append(f"SUMMARY:{escape(event.name)}")
    if event.location:
        lines.append(f"LOCATION:{escape(event.location)}")
    lines += [f"DESCRIPTION:{escape('Más información: ' + link)}", f"URL:{link}", "END:VEVENT"]
    return lines


def render(name: str, pages: list[EventPage], base_url: str) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Emmaus//Calendario//ES", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{escape(name)}"]
    for page in pages:
        lines += event_lines(page, base_url)
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + "\r\n" for line in lines)


def church_pages(church) -> list[EventPage]:
    """The public pages of ``church`` that have not ended, by first day. Empty when the module is off. The
    database leaves out what is over and stops at ``FEED_LIMIT``: the feed costs the same in a church's tenth year."""
    if not flags.enabled(church, "community"):
        return []
    today = timezone.localdate()
    pages = (
        public.public_pages().filter(event__church=church, event__starts_on__isnull=False)
        .filter(services._not_ended(today, "event__"))
        .select_related("event", "event__series_occurrence__series")
        .order_by("event__starts_on", "event__pk")
    )
    return [page for page in pages[:FEED_LIMIT] if not services.has_ended(page.event, today)]


def feed_path(church) -> str:
    return reverse("calendario:feed", args=[church.slug])


def event_path(event) -> str:
    return reverse("evento:calendar", args=[event.slug])


def _base_url(request) -> str:
    return settings.EMMAUS_PUBLIC_BASE_URL or request.build_absolute_uri("/")


def _response(body: str, filename: str = "") -> HttpResponse:
    response = HttpResponse(body, content_type=CONTENT_TYPE)
    response["X-Robots-Tag"] = "noindex, nofollow"
    # events.middleware.PrivateResponseMiddleware keeps these headers for a response marked so (as it does for
    # the church's logo), unless something attached a cookie to it.
    response["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    response.public_asset = True
    if filename:
        response["Content-Disposition"] = f'attachment; filename="{filename}.ics"'
    return response


@require_safe
def feed(request, slug: str):
    church = Church.objects.filter(slug=slug, active=True).first()
    pages = church_pages(church) if church is not None else []
    if not pages:
        raise Http404
    return _response(render(church.name, pages, _base_url(request)))


@require_safe
def event(request, slug: str):
    page = public.public_page(slug)
    # No first day (only a last one was written): there is nothing to put in a calendar. The same 404.
    if page is None or page.event.starts_on is None:
        raise Http404
    return _response(render(page.event.name, [page], _base_url(request)), filename=page.event.slug)
