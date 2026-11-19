from django.conf import settings
from django.db import models

from core.models import Church
from resources.models import MediaItem


class PublicHome(models.Model):
    """What a church says about itself to anybody (``/iglesia/<slug>/``). One row per church, created
    by the first save. Nothing here is shown while ``published`` is off. The e-mail of the page is
    the church's own ``Church.contact_email``: it is not copied here."""

    WELCOME_MAX = 280
    # A limit of what is saved from now on, not of the column: a longer address written before keeps
    # showing and is shortened the next time somebody saves it (sitio.services.clean_address).
    ADDRESS_MAX = 160

    church = models.OneToOneField(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="public_home")
    published = models.BooleanField("Publicada", default=False, db_default=False)
    welcome = models.CharField("Bienvenida", max_length=WELCOME_MAX, blank=True, default="", db_default="")
    address = models.TextField("Dirección", blank=True, default="", db_default="")
    map_url = models.URLField("Enlace al mapa", max_length=500, blank=True, default="", db_default="")
    contact_phone = models.CharField("Teléfono de contacto", max_length=30, blank=True, default="", db_default="")
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "página pública"
        verbose_name_plural = "páginas públicas"

    def __str__(self) -> str:
        return f"Página pública de {self.church}"


class ServiceTime(models.Model):
    """One time the church meets every week: "Domingos, 11:00, Reunión general, Templo principal"."""

    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Lunes"
        TUESDAY = 1, "Martes"
        WEDNESDAY = 2, "Miércoles"
        THURSDAY = 3, "Jueves"
        FRIDAY = 4, "Viernes"
        SATURDAY = 5, "Sábado"
        SUNDAY = 6, "Domingo"

    # Limits of what is saved from now on (sitio.services.save_time), tighter than the columns: a time is a
    # fact (when, what, where), not a place for a text. Rows written before keep showing as they are.
    NAME_MAX = 40
    PLACE_MAX = 80
    PER_CHURCH = 12

    church = models.ForeignKey(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="service_times")
    weekday = models.PositiveSmallIntegerField("Día", choices=Weekday.choices)
    time = models.TimeField("Hora")
    name = models.CharField("Qué reunión es", max_length=80)
    place = models.CharField("Lugar", max_length=120, blank=True, default="", db_default="")
    position = models.PositiveSmallIntegerField("Orden", default=0, db_default=0)

    class Meta:
        verbose_name = "horario de reunión"
        verbose_name_plural = "horarios de reunión"
        ordering = ["position", "weekday", "time", "id"]

    def __str__(self) -> str:
        return f"{self.get_weekday_display()} {self.time:%H:%M}, {self.name}"


class PublicMedia(models.Model):
    """The decision, resource by resource, that anybody may see it. A resource is on the public page
    only while this row exists, the resource is published in the library and both belong to the
    church of the page. ``church`` repeats the resource's church on purpose: the page filters by
    both, and ``tenant_integrity`` counts a row where they disagree."""

    item = models.OneToOneField(MediaItem, verbose_name="Recurso", on_delete=models.CASCADE, related_name="public_mark")
    church = models.ForeignKey(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="+")
    published_at = models.DateTimeField(auto_now_add=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "recurso público"
        verbose_name_plural = "recursos públicos"

    def __str__(self) -> str:
        return f"Público: {self.item}"
