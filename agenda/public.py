"""The public page of one event: guardian access, phase A (decision 0010).

Anybody with the address reads what the event is, without an account: its name, what kind of event
it is, the dates, the hour, the place, the description, its public notices, the programme, the frequent questions,
whether people sign up from Emmaus, and the church's name to ask. NEVER anything about people: no
names, no counts, no places left, no waiting list, no internal ids. The view queries the event, its
page, its public notices, its questions and its programme, and no table of people.

One answer for everything that is not such a page (no event with that address, another status, no
page, not published, not public, over, the church not using the module): the same 404, so the
address is no oracle. Who is signed in is never looked at: the page is the same for everybody.

The page has no form: "Avisos por correo" (``agenda.subscriptions``, when the administration turned it on) and
"Inscribirme" for somebody without an account (``agenda.visitors``, likewise) are links to their own pages, so
that reading this one still sets no cookie.

No throttle: ``events.services.throttle`` counts attempts per browser session or account to slow
down guessing and repeated writes (a sign-in, a printed code, a sign-up). Here there is nothing to
guess that is not meant to be public, nothing is written, and counting would give every anonymous
reader a session cookie and a database write per view.
"""

from urllib.parse import quote
from zoneinfo import ZoneInfo

from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_safe

from events.models import Event
from people import flags

from . import services
from .models import EventFaq, EventPage


def public_pages():
    """The pages anybody may read, before the two checks ``is_served`` makes on each: the one set the public
    page of an event, its calendar file and the church's calendar feed (``agenda.calendar``) answer for."""
    return EventPage.objects.filter(published=True, public=True, event__status=Event.Status.ACTIVE,
                                    event__church__active=True)


def public_page(slug: str):
    """The event and its page when anybody may read them, else None."""
    page = public_pages().filter(event__slug=slug).select_related("event", "event__church").first()
    if page is None or services.has_ended(page.event) or not flags.enabled(page.event.church, "community"):
        return None
    return page


def is_public(event: Event, page: EventPage | None) -> bool:
    """Whether ``/evento/<slug>/`` answers for this event right now (for the administration's screen)."""
    return page is not None and page.public and public_page(event.slug) is not None


def public_path(event: Event) -> str:
    return reverse("evento:detail", args=[event.slug])


def notices_path(event: Event) -> str:
    """Where the event's public notices are read: the public page, at its «Avisos»."""
    return public_path(event) + "#avisos"


def subscribe_path(event: Event) -> str:
    """Where somebody leaves an e-mail address to hear about the event's public notices (``agenda.subscriptions``)."""
    return reverse("evento:subscribe", args=[event.slug])


def confirm_path(token: str) -> str:
    return reverse("evento:subscription_confirm", args=[token])


def unsubscribe_path(token: str) -> str:
    return reverse("evento:subscription_unsubscribe", args=[token])


def notices_share_url(church_name: str, link: str) -> str:
    """A WhatsApp message that says there is news and where: the church and the link. Never the notice's
    title or text, and not the event's name: the message travels through groups nobody controls."""
    text = f"{church_name}: hay un aviso nuevo de un evento. No necesitas cuenta para leerlo: {link}"
    return "https://wa.me/?text=" + quote(text, safe="")


def notice_zone(event: Event):
    """The time zone the hours of the notices are said in: the event's, or the server's if it names none that exists."""
    try:
        return ZoneInfo(event.time_zone)
    except (KeyError, ValueError, OSError):  # ZoneInfoNotFoundError is a KeyError; an empty name, a ValueError
        return timezone.get_current_timezone()


def share_url(church_name: str, link: str) -> str:
    """A WhatsApp message the administration sends: the church and where to read about an event.
    Not the event's name, and nothing about who goes."""
    text = f"{church_name}: aquí está la información de un evento. No necesitas cuenta para leerla: {link}"
    return "https://wa.me/?text=" + quote(text, safe="")


@require_safe
def event(request, slug: str):
    page = public_page(slug)
    if page is None:
        raise Http404
    event = page.event
    today = timezone.localdate()
    from . import subscriptions  # it imports this module

    # A link to the form, never the form: the form needs a CSRF cookie, and this page sets none.
    mail_offer = page.notice_mail and not subscriptions.not_sending()
    if not page.registration_open:
        sign_up = "no"
    elif page.registration_closes_on and page.registration_closes_on < today:
        sign_up = "closed"
    else:
        sign_up = "open"
    from . import visitors

    # Signing up without an account (issue #93): a link to its form, or the plain sentence of an event that
    # protects minors. Read from the page row and the settings: no query, no cookie.
    visitor = visitors.state_for_public(event, page) if sign_up == "open" else ""
    return render(
        request,
        "agenda/public.html",
        {
            "visitor": visitor,
            "visitor_path": reverse("evento:visitor_signup", args=[event.slug]) if visitor == "si" else "",
            "event": event,
            "page": page,
            "church": event.church,
            "sign_up": sign_up,
            "member_path": reverse("agenda:detail", args=[event.slug]),
            # "Agregar a mi calendario": this event as a calendar file (agenda.calendar), the same public facts.
            "calendar_path": reverse("evento:calendar", args=[event.slug]),
            # "Avisos por correo" (issue #96, second part): only when the administration turned it on and e-mail can go out.
            "subscribe_path": subscribe_path(event) if mail_offer else "",
            "faqs": EventFaq.objects.filter(event=event),
            # Public notices (issue #96): title, text and when. Never who wrote them.
            "notices": services.public_notices(event),
            "notice_zone": notice_zone(event),
            "schedule": services.schedule_by_day(event),
            # The page wears the event's church, whoever reads it: never the reader's own church or shell.
            "brand_church": event.church,
            "theme_church": event.church,
            "ui_v2": True,
        },
    )
