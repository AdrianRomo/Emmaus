"""What the public home of a church shows, and every write to it.

Reading: ``public_home`` answers whether ``/iglesia/<slug>/`` exists for anybody (one rule, one
``None`` for everything else) and ``page_content`` gathers what the page shows. Both read the
church, its home, its times, its events and its resources, and no table of people.

Writing: each function is one transaction that saves and leaves one church audit row. An audit row
never carries a title, an address, a phone or the words of the welcome.

Callers have already answered who may write (``sitio.views``: the church's administration).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import Church
from events.models import Event
from people import flags
from people.audit import record_church
from resources.models import MediaItem

from .models import PublicHome, PublicMedia, ServiceTime

EVENT_LIMIT = 4
MEDIA_LIMIT = 4
# How a day reads on the page: every week, so in plural where Spanish has one.
DAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábados", "Domingos"]
HTTPS_ONLY = "El enlace debe empezar con https://. Copia la dirección completa desde tu aplicación de mapas, en «Compartir»."
BAD_URL = "Ese enlace no se entiende. Copia la dirección completa, por ejemplo https://maps.app.goo.gl/…"
BAD_PHONE = "Escribe el teléfono solo con números, espacios o guiones, con al menos 8 dígitos. Por ejemplo: 55 1234 5678."


class SiteError(ValidationError):
    """A rule of the public home refused the action; the message is ready for the screen."""


class StaleReview(Exception):
    """What would be public changed between the review and its confirmation."""


@dataclass
class Content:
    """Everything the public page shows of one church, and nothing else."""

    church: Church
    home: PublicHome
    days: list = field(default_factory=list)      # [{"day": "Domingos", "times": [ServiceTime, ...]}, ...]
    events: list = field(default_factory=list)    # events whose page is published and public, not ended
    media: list = field(default_factory=list)     # published resources marked as public, newest first

    @property
    def has_contact(self) -> bool:
        return bool(self.home.contact_phone or self.church.contact_email)

    @property
    def has_place(self) -> bool:
        return bool(self.home.address or self.home.map_url)

    def digest(self) -> str:
        """A fingerprint of what the review page listed. It leaves the server only as a hash."""
        parts = [
            self.church.name, self.church.contact_email, self.home.welcome, self.home.address, self.home.map_url,
            self.home.contact_phone,
            *(f"{t.pk}|{t.weekday}|{t.time}|{t.name}|{t.place}|{t.position}" for day in self.days for t in day["times"]),
            bool(getattr(self.church, "logo", None)),
            *(f"e{event.pk}|{event.name}|{event.starts_on}|{event.ends_on}|{event.location}|{getattr(getattr(event, 'page', None), 'starts_at', '')}"
              for event in self.events),
            *(f"m{item.pk}|{item.kind}|{item.title}|{item.speaker}|{item.series}|{item.date}|{item.url}" for item in self.media),
        ]
        return hashlib.sha256("\x1f".join(str(part) for part in parts).encode()).hexdigest()


# ---- reading ------------------------------------------------------------------------------------
def public_home(slug: str) -> PublicHome | None:
    """The home when anybody may read it: the church exists, is active, uses the module and
    published it. Everything else is ``None``, and the caller answers the same 404."""
    home = PublicHome.objects.filter(church__slug=slug, church__active=True, published=True).select_related("church").first()
    if home is None or not flags.enabled(home.church, "community"):
        return None
    return home


def is_live(church) -> bool:
    """Whether ``/iglesia/<slug>/`` answers for this church right now (for the administration's screens)."""
    return public_home(church.slug) is not None


def home_of(church) -> PublicHome:
    """The church's row, or an unsaved one with nothing in it: reading never writes."""
    return PublicHome.objects.filter(church=church).first() or PublicHome(church=church)


def service_days(church) -> list[dict]:
    """The times by day, in the order the administration gave them: consecutive times of one day share its name."""
    days: list[dict] = []
    # With the same order number, the week reads from Sunday: the day most churches meet.
    rows = sorted(ServiceTime.objects.filter(church=church), key=lambda t: (t.position, (t.weekday + 1) % 7, t.time, t.pk))
    for row in rows:
        if days and days[-1]["weekday"] == row.weekday:
            days[-1]["times"].append(row)
        else:
            days.append({"weekday": row.weekday, "day": DAYS[row.weekday], "times": [row]})
    return days


def public_events(church, today=None) -> list[Event]:
    """The church's events that have their own public page (``agenda.public.public_page``): active,
    page published and public, not over. Each one carries ``page``."""
    today = today or timezone.localdate()
    events = (
        Event.objects.filter(church=church, status=Event.Status.ACTIVE, page__published=True, page__public=True)
        .filter(Q(ends_on__gte=today) | Q(ends_on__isnull=True, starts_on__gte=today))
        .select_related("page").order_by("starts_on", "name", "pk")
    )
    return list(events[:EVENT_LIMIT])


def public_media_items(church):
    """Published in the library AND marked as public AND of this church, newest first."""
    return MediaItem.objects.filter(
        church=church, status=MediaItem.Status.PUBLISHED, public_mark__church=church
    ).order_by("-date", "-id")


def page_content(church, home: PublicHome | None = None) -> Content:
    home = home or home_of(church)
    return Content(
        church=church, home=home, days=service_days(church), events=public_events(church),
        media=list(public_media_items(church)[:MEDIA_LIMIT]),
    )


def public_ids(items) -> set[int]:
    """Which of these resources carry the mark (for the administration's list)."""
    return set(PublicMedia.objects.filter(item__in=[item.pk for item in items]).values_list("item_id", flat=True))


# ---- writing ------------------------------------------------------------------------------------
def _line(text: str) -> str:
    return " ".join((text or "").split())


def _text(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def clean_map_url(url: str) -> str:
    """Empty, or an ``https://`` address with a host."""
    url = (url or "").strip()
    if not url:
        return ""
    if urlsplit(url).scheme.lower() != "https":
        raise SiteError(HTTPS_ONLY, code="map_url")
    try:
        URLValidator(schemes=["https"])(url)
    except ValidationError:
        raise SiteError(BAD_URL, code="map_url") from None
    if len(url) > 500:
        raise SiteError("Ese enlace es demasiado largo. Usa el enlace corto que da el mapa en «Compartir».", code="map_url")
    return url


def clean_phone(phone: str) -> str:
    phone = _line(phone)
    if not phone:
        return ""
    if len(phone) > 30 or not re.fullmatch(r"\+?[0-9 ()\-]+", phone) or len(re.sub(r"\D", "", phone)) < 8:
        raise SiteError(BAD_PHONE, code="contact_phone")
    return phone


def _locked_home(church) -> PublicHome:
    home, _ = PublicHome.objects.select_for_update().get_or_create(church=church)
    return home


def save_home(church, actor, *, welcome: str = "", address: str = "", map_url: str = "", contact_phone: str = "") -> PublicHome:
    """Save what the church says about itself. It does not publish: that is ``publish``."""
    welcome, address = _line(welcome), _text(address)
    if len(welcome) > PublicHome.WELCOME_MAX:
        raise SiteError(f"La bienvenida puede tener hasta {PublicHome.WELCOME_MAX} caracteres y esta tiene {len(welcome)}. Acórtala.", code="welcome")
    if len(address) > PublicHome.ADDRESS_MAX:
        raise SiteError(f"La dirección puede tener hasta {PublicHome.ADDRESS_MAX} caracteres. Acórtala.", code="address")
    map_url, contact_phone = clean_map_url(map_url), clean_phone(contact_phone)
    with transaction.atomic():
        home = _locked_home(church)
        wanted = {"welcome": welcome, "address": address, "map_url": map_url, "contact_phone": contact_phone}
        if all(getattr(home, name) == value for name, value in wanted.items()):
            return home
        for name, value in wanted.items():
            setattr(home, name, value)
        home.save(update_fields=[*wanted, "updated_at"])
        record_church(church, actor, "sitio_modificado", "Modificó los datos de la página pública de la iglesia", home)
    return home


def save_time(church, actor, *, weekday: int, time, name: str, place: str = "", position: int = 0,
              row: ServiceTime | None = None) -> ServiceTime:
    name, place = _line(name), _line(place)
    if weekday not in ServiceTime.Weekday.values:
        raise SiteError("Elige el día de la reunión.", code="weekday")
    if time is None:
        raise SiteError("Escribe la hora de la reunión.", code="time")
    if not name:
        raise SiteError("Escribe qué reunión es, por ejemplo «Reunión general».", code="name")
    if len(name) > 80 or len(place) > 120:
        raise SiteError("Ese texto es demasiado largo. Acórtalo.", code="name")
    with transaction.atomic():
        if row is None:
            row, words = ServiceTime(church=church), "Agregó un horario de reunión a la página pública"
        else:
            row = ServiceTime.objects.select_for_update().filter(pk=row.pk, church=church).first()
            if row is None:
                raise SiteError("Ese horario ya no existe.", code="missing")
            words = "Modificó un horario de reunión de la página pública"
        row.weekday, row.time, row.name, row.place, row.position = weekday, time, name, place, position
        row.save()
        record_church(church, actor, "sitio_modificado", words, row)
    return row


def delete_time(row: ServiceTime, actor) -> None:
    with transaction.atomic():
        church = row.church
        record_church(church, actor, "sitio_modificado", "Quitó un horario de reunión de la página pública", row)
        row.delete()


def publish(church, actor, reviewed: str) -> PublicHome:
    """Make the page public. ``reviewed`` is the fingerprint of what the review page listed: when what
    would be public is no longer that, ``StaleReview`` is raised and nothing changes."""
    with transaction.atomic():
        home = _locked_home(church)
        if page_content(church, home).digest() != reviewed:
            raise StaleReview
        if home.published:
            return home
        home.published, home.published_at, home.published_by = True, timezone.now(), actor
        home.save(update_fields=["published", "published_at", "published_by", "updated_at"])
        record_church(church, actor, "sitio_publicado", "Publicó la página pública de la iglesia", home)
    return home


def unpublish(church, actor) -> PublicHome:
    """Nobody without an account reads the page any more. Nothing is deleted. Idempotent."""
    with transaction.atomic():
        home = _locked_home(church)
        if not home.published:
            return home
        home.published = False
        home.save(update_fields=["published", "updated_at"])
        record_church(church, actor, "sitio_despublicado", "Dejó de publicar la página pública de la iglesia", home)
    return home


def _kind_words(item: MediaItem) -> str:
    return f"un recurso ({item.get_kind_display().lower()})"


def show_media(item: MediaItem, actor) -> bool:
    """Mark a published resource as one anybody may see. True when it changed."""
    with transaction.atomic():
        item = MediaItem.objects.select_for_update().select_related("church").get(pk=item.pk)
        if item.is_retired:
            raise SiteError("Este recurso está retirado: no se puede mostrar en la página pública.", code="retired")
        _, created = PublicMedia.objects.get_or_create(item=item, defaults={"church": item.church, "published_by": actor})
        if created:
            record_church(item.church, actor, "recurso_publico", f"Mostró {_kind_words(item)} en la página pública", item)
    return created


def hide_media(item: MediaItem, actor) -> bool:
    """Take the mark away: the resource stays in the library for the people of the church. True when it changed."""
    with transaction.atomic():
        item = MediaItem.objects.select_for_update().select_related("church").get(pk=item.pk)
        deleted, _ = PublicMedia.objects.filter(item=item).delete()
        if deleted:
            record_church(item.church, actor, "recurso_privado", f"Quitó {_kind_words(item)} de la página pública", item)
    return bool(deleted)
