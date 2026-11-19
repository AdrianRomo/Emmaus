from django.conf import settings
from django.db import models

from core.models import Church
from people.models import Person

BODY_MAX = 1000
REPLY_MAX = 500
EMERGENCY_LINE_MAX = 160


class PrayerPolicy(models.Model):
    """Whether a church receives prayer requests, and who accepted its promises. One row per church,
    created by the first pastor who switches it on; no row is "off".

    Written only by ``oracion.services`` (a pastor of the church, never the administration). The
    retention is read through ``people.policy.prayer_retention_days``, which holds the platform's
    bounds whatever is stored."""

    church = models.OneToOneField(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="prayer_policy")
    enabled = models.BooleanField("Recibe peticiones de oración", default=False, db_default=False)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Quién aceptó", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    accepted_at = models.DateTimeField("Cuándo aceptó", null=True, blank=True)
    # The version of the promises that was on the screen when they were accepted (``services.PROMISES_VERSION``).
    promises_version = models.CharField(max_length=20, blank=True, default="", db_default="")
    # The church's own second line for an emotional crisis; empty: the platform's (Línea de la Vida).
    emergency_line = models.CharField("Línea de ayuda de la iglesia", max_length=EMERGENCY_LINE_MAX, blank=True, default="", db_default="")
    retention_days = models.PositiveSmallIntegerField("Días que se conserva una petición", null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "política de peticiones de oración"
        verbose_name_plural = "políticas de peticiones de oración"

    def __str__(self) -> str:
        return f"Peticiones de oración de la iglesia {self.church_id}"


class PrayerRequest(models.Model):
    """What one adult person of a church wrote about themselves, for its pastors.

    Privacy rules of the module (each one has a test in ``oracion/tests``):

    * read only through ``oracion.access.readable`` (the church's pastors now, or the one the writer
      chose) and by its writer; never by the administration, a guide or a superuser;
    * ``body`` and ``reply`` never leave this table: no log, audit row, e-mail, export, count or admin page;
    * it never moves to another person (``people.merge.REFUSED``) and is deleted, not archived: by its
      writer at any time, and by ``services.prune`` once ``expires_at`` passes.

    Who marked it or replied is not stored: the writer is told "Leída", never by whom.
    """

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="+")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="+")
    body = models.TextField("Tu petición", max_length=BODY_MAX)
    # ``restricted`` with no ``only_reader`` (the account is gone): nobody reads it; it never widens to every pastor.
    restricted = models.BooleanField(default=False, db_default=False)
    only_reader = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    # Fixed when it is written (what the form promised that day): a later change of the church's number never moves it.
    expires_at = models.DateTimeField()
    read_at = models.DateTimeField(null=True, blank=True)
    reply = models.TextField(max_length=REPLY_MAX, blank=True, default="", db_default="")
    replied_at = models.DateTimeField(null=True, blank=True)
    talked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "petición de oración"
        verbose_name_plural = "peticiones de oración"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["church", "read_at"], name="oracion_church_read_idx"),
            models.Index(fields=["expires_at"], name="oracion_expires_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(restricted=True) | models.Q(only_reader__isnull=True), name="oracion_reader_only_if_restricted"
            ),
        ]

    def __str__(self) -> str:  # never the words, never the person
        return f"Petición de oración {self.pk}"

    @property
    def state(self) -> str:
        if self.talked_at:
            return "hablada"
        if self.replied_at:
            return "respondida"
        return "leida" if self.read_at else "sin_leer"


class ReaderPreference(models.Model):
    """A pastor's own choice, per church: an e-mail that says a new request waits (never whose or what).
    No row is "no"."""

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    email_new = models.BooleanField(default=False, db_default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "aviso por correo de peticiones nuevas"
        verbose_name_plural = "avisos por correo de peticiones nuevas"
        constraints = [models.UniqueConstraint(fields=["church", "user"], name="oracion_one_preference_per_reader")]

    def __str__(self) -> str:
        return f"Aviso de peticiones {self.pk}"
