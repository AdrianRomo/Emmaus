"""What a person may read of the church's devotionals and resources, and every write to one.
Each write is one transaction that saves the row and one church audit row. An audit row names a
devotional by its day and a resource by its kind; it never carries a title, a body or an address.

``DevotionalRead`` is private to its person: the functions here answer "did *this* person read
it" and nothing else. There is no count, list or total of readers, and none may be added.

Callers have already answered who may write (``resources.views``: the church's administration);
these functions enforce what holds whoever asks.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone

from people.audit import record_church

from .models import Devotional, DevotionalRead, MediaItem

FALLBACK_DAYS = 7
DAY_TAKEN = "Ya hay un devocional para ese día. Elige otro día o edita el que ya existe."
HTTPS_ONLY = "El enlace debe empezar con https://. Copia la dirección completa del sitio donde está el recurso."
BAD_URL = "Ese enlace no se entiende. Copia la dirección completa, por ejemplo https://www.youtube.com/watch?v=…"


class ResourceError(ValidationError):
    """A rule of the devotionals or the resources refused the action; the message is ready for the screen."""


# ---- reading ------------------------------------------------------------------------------------
def readable_devotionals(church, today=None) -> QuerySet[Devotional]:
    """What every person of the church may read: published, up to today, newest first."""
    today = today or timezone.localdate()
    return Devotional.objects.filter(church=church, status=Devotional.Status.PUBLISHED, date__lte=today).order_by("-date", "-id")


def is_read(devotional: Devotional, person) -> bool:
    return person is not None and DevotionalRead.objects.filter(devotional=devotional, person=person).exists()


def today_devotional(church, person, today=None) -> Devotional | None:
    """The published devotional of today, or else the newest published one of the last seven days.
    It carries ``is_today`` and ``is_read`` (for ``person`` only)."""
    today = today or timezone.localdate()
    since = today - timedelta(days=FALLBACK_DAYS)
    devotional = readable_devotionals(church, today).filter(date__gte=since).first()
    if devotional is None:
        return None
    devotional.is_today = devotional.date == today
    devotional.is_read = is_read(devotional, person)
    return devotional


def past_devotionals(church, person):
    """The readable ones, newest first, each with ``is_read`` for ``person``. A queryset: the screen pages it."""
    mine = DevotionalRead.objects.filter(devotional=OuterRef("pk"), person=person)
    return readable_devotionals(church).annotate(is_read=Exists(mine))


def first_free_day(church, today=None):
    """The first day from today that has no devotional, draft or published."""
    day = today or timezone.localdate()
    taken = set(Devotional.objects.filter(church=church, date__gte=day).values_list("date", flat=True))
    while day in taken:
        day += timedelta(days=1)
    return day


def published_media(church) -> QuerySet[MediaItem]:
    return MediaItem.objects.filter(church=church, status=MediaItem.Status.PUBLISHED).order_by("-date", "-id")


def latest_media(church, limit: int = 3) -> list[MediaItem]:
    """The newest published resources of the church."""
    return list(published_media(church)[:limit])


def mark_read(devotional: Devotional, person) -> bool:
    """The person said "Ya lo leí". Idempotent; True the first time."""
    if person is None or person.church_id != devotional.church_id or not devotional.is_readable:
        raise ResourceError("Ese devocional no está publicado.", code="unreadable")
    try:
        with transaction.atomic():
            _, created = DevotionalRead.objects.get_or_create(devotional=devotional, person=person)
    except IntegrityError:  # two tabs at once: the other one wrote it
        return False
    return created


# ---- writing ------------------------------------------------------------------------------------
def _line(text: str) -> str:
    return " ".join((text or "").split())


def _text(text: str) -> str:
    """A browser sends each line break as two characters; a person counts one."""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _fits(value: str, limit: int, message: str, code: str) -> str:
    if len(value) > limit:
        raise ResourceError(message, code=code)
    return value


def clean_url(url: str) -> str:
    """An address that opens on another site: ``https://`` and a host, or a ``ResourceError``."""
    url = (url or "").strip()
    if not url:
        raise ResourceError("Pega el enlace del recurso.", code="url")
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise ResourceError(HTTPS_ONLY, code="url")
    try:
        URLValidator(schemes=["https"])(url)
    except ValidationError:
        raise ResourceError(BAD_URL, code="url") from None
    if len(url) > 500:
        raise ResourceError("Ese enlace es demasiado largo. Usa el enlace corto que da el sitio en «Compartir».", code="url")
    return url


def _numeric_day(day) -> str:
    return day.strftime("%d/%m/%Y")


def save_devotional(
    church, actor, *, date, title: str, body: str, passage: str = "", author_name: str = "",
    publish: bool = False, devotional: Devotional | None = None,
) -> Devotional:
    """Write a new devotional, or correct ``devotional``. One per church and day. With ``publish``
    the people of the church read it from its day on; without it, only the administration sees it."""
    title, passage, author_name, body = _line(title), _line(passage), _line(author_name), _text(body)
    if date is None:
        raise ResourceError("Elige el día del devocional.", code="date")
    if not title:
        raise ResourceError("Escribe un título para el devocional.", code="title")
    _fits(title, 120, "El título puede tener hasta 120 caracteres. Acórtalo.", "title")
    _fits(passage, 80, "La lectura puede tener hasta 80 caracteres. Escribe solo la cita, por ejemplo Juan 15:1-8.", "passage")
    _fits(author_name, 120, "El nombre de quien lo firma puede tener hasta 120 caracteres.", "author_name")
    if not body:
        raise ResourceError("Escribe el devocional.", code="body")
    _fits(
        body, Devotional.BODY_MAX,
        f"El devocional puede tener hasta {Devotional.BODY_MAX} caracteres y este tiene {len(body)}. Acórtalo.", "body",
    )
    with transaction.atomic():
        if devotional is None:
            devotional, created, was_published = Devotional(church=church, created_by=actor), True, False
        else:
            devotional = Devotional.objects.select_for_update().filter(pk=devotional.pk, church=church).first()
            if devotional is None:
                raise ResourceError("Ese devocional ya no existe.", code="missing")
            created, was_published = False, devotional.is_published
        if Devotional.objects.filter(church=church, date=date).exclude(pk=devotional.pk).exists():
            raise ResourceError(DAY_TAKEN, code="date")
        devotional.date, devotional.title, devotional.passage = date, title, passage
        devotional.body, devotional.author_name = body, author_name
        devotional.status = Devotional.Status.PUBLISHED if publish else Devotional.Status.DRAFT
        try:
            with transaction.atomic():
                devotional.save()
        except IntegrityError:  # somebody else took the day between the question and the write
            raise ResourceError(DAY_TAKEN, code="date") from None
        day = _numeric_day(date)
        if publish and not was_published:
            record_church(church, actor, "devocional_publicado", f"Publicó el devocional del {day}", devotional)
        elif created:
            record_church(church, actor, "devocional_modificado", f"Guardó un borrador del devocional del {day}", devotional)
        elif was_published and not publish:
            record_church(church, actor, "devocional_modificado", f"Dejó de publicar el devocional del {day}", devotional)
        else:
            record_church(church, actor, "devocional_modificado", f"Modificó el devocional del {day}", devotional)
    return devotional


def _kind_words(item: MediaItem) -> str:
    return f"un recurso ({item.get_kind_display().lower()})"


def save_media(
    church, actor, *, kind: str, title: str, url: str, date=None, speaker: str = "", series: str = "",
    description: str = "", item: MediaItem | None = None,
) -> MediaItem:
    """Publish a new resource, or correct ``item``. A retired one is not edited."""
    title, speaker, series, description = _line(title), _line(speaker), _line(series), _text(description)
    if kind not in MediaItem.Kind.values:
        raise ResourceError("Elige qué tipo de recurso es.", code="kind")
    if not title:
        raise ResourceError("Escribe el título del recurso.", code="title")
    _fits(title, 160, "El título puede tener hasta 160 caracteres. Acórtalo.", "title")
    _fits(speaker, 120, "Ese nombre puede tener hasta 120 caracteres.", "speaker")
    _fits(series, 120, "El nombre de la serie puede tener hasta 120 caracteres.", "series")
    _fits(
        description, MediaItem.DESCRIPTION_MAX,
        f"La descripción puede tener hasta {MediaItem.DESCRIPTION_MAX} caracteres y esta tiene {len(description)}. Acórtala.",
        "description",
    )
    url = clean_url(url)
    date = date or timezone.localdate()
    with transaction.atomic():
        if item is None:
            item, created = MediaItem(church=church, created_by=actor), True
        else:
            item = MediaItem.objects.select_for_update().filter(pk=item.pk, church=church).first()
            if item is None:
                raise ResourceError("Ese recurso ya no existe.", code="missing")
            if item.is_retired:
                raise ResourceError("Este recurso ya está retirado. Agrega uno nuevo.", code="retired")
            created = False
        item.kind, item.title, item.url, item.date = kind, title, url, date
        item.speaker, item.series, item.description = speaker, series, description
        item.save()
        if created:
            record_church(church, actor, "recurso_publicado", f"Publicó {_kind_words(item)}", item)
        else:
            record_church(church, actor, "recurso_modificado", f"Modificó {_kind_words(item)}", item)
    return item


def retire_media(item: MediaItem, actor) -> MediaItem:
    """Nobody finds it in Recursos any more. Nothing is deleted. Idempotent."""
    with transaction.atomic():
        item = MediaItem.objects.select_for_update().select_related("church").get(pk=item.pk)
        if item.is_retired:
            return item
        item.status = MediaItem.Status.RETIRED
        item.save(update_fields=["status", "updated_at"])
        record_church(item.church, actor, "recurso_retirado", f"Retiró {_kind_words(item)}", item)
    return item
