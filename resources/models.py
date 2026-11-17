from urllib.parse import urlsplit

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Church
from people.models import Person


def today():
    return timezone.localdate()


class Devotional(models.Model):
    """The devotional of one day of a church. ``body`` is plain text: escaped when shown, never
    copied to a log or an audit row. ``passage`` is only a reference ("Juan 15:1-8"): Emmaus never
    fetches or stores Bible text by itself."""

    BODY_MAX = 3000

    class Status(models.TextChoices):
        DRAFT = "borrador", "Borrador"
        PUBLISHED = "publicado", "Publicado"

    church = models.ForeignKey(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="devotionals")
    date = models.DateField("Día")
    title = models.CharField("Título", max_length=120)
    passage = models.CharField("Lectura", max_length=80, blank=True)
    body = models.TextField("Devocional")
    author_name = models.CharField("Quién lo firma", max_length=120, blank=True)
    status = models.CharField("Estado", max_length=12, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "devocional"
        verbose_name_plural = "devocionales"
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["church", "date"], name="devotional_one_per_day")]

    def __str__(self) -> str:
        return self.title

    @property
    def is_published(self) -> bool:
        return self.status == self.Status.PUBLISHED

    @property
    def is_readable(self) -> bool:
        """What every person of the church may read: published, and its day has come."""
        return self.is_published and self.date <= today()

    @property
    def state(self) -> str:
        """What the administration reads: "borrador", "programado" or "publicado"."""
        if not self.is_published:
            return "borrador"
        return "programado" if self.date > today() else "publicado"


class DevotionalRead(models.Model):
    """A person said "Ya lo leí". One row per person and devotional. It is shown only to that
    person: no list, count, export, audit row or administration screen reads this table for anybody else."""

    devotional = models.ForeignKey(Devotional, on_delete=models.CASCADE, related_name="+")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="+")
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "devocional leído"
        verbose_name_plural = "devocionales leídos"
        constraints = [models.UniqueConstraint(fields=["devotional", "person"], name="devotional_read_once")]


# The words of the link, by the site it goes to. Anything else is "Abrir el recurso".
LINK_WORDS = (
    (("youtube.com", "youtu.be"), "Ver en YouTube"),
    (("spotify.com",), "Escuchar en Spotify"),
)


class MediaItem(models.Model):
    """Something the church preached, sang or taught, kept as a link to the site that hosts it.
    Emmaus stores the address only: nothing is embedded, downloaded or fetched."""

    DESCRIPTION_MAX = 600

    class Kind(models.TextChoices):
        SERMON = "predicacion", "Predicación"
        MUSIC = "musica", "Música"
        STUDY = "estudio", "Estudio"
        OTHER = "otro", "Otro recurso"

    class Status(models.TextChoices):
        PUBLISHED = "publicado", "Publicado"
        RETIRED = "retirado", "Retirado"

    church = models.ForeignKey(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="media_items")
    kind = models.CharField("Tipo de recurso", max_length=12, choices=Kind.choices, default=Kind.SERMON)
    title = models.CharField("Título", max_length=160)
    speaker = models.CharField("De quién es", max_length=120, blank=True)
    series = models.CharField("Serie", max_length=120, blank=True)
    date = models.DateField("Fecha", default=today)
    url = models.URLField("Enlace", max_length=500)
    description = models.TextField("Descripción", blank=True)
    status = models.CharField("Estado", max_length=12, choices=Status.choices, default=Status.PUBLISHED)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "recurso"
        verbose_name_plural = "recursos"
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["church", "status", "date"], name="media_church_status_date_idx")]

    def __str__(self) -> str:
        return self.title

    @property
    def is_retired(self) -> bool:
        return self.status == self.Status.RETIRED

    @property
    def link_label(self) -> str:
        """Where the link goes, in words: "Ver en YouTube", "Escuchar en Spotify" or "Abrir el recurso"."""
        host = (urlsplit(self.url).hostname or "").lower()
        for domains, words in LINK_WORDS:
            if any(host == domain or host.endswith("." + domain) for domain in domains):
                return words
        return "Abrir el recurso"
