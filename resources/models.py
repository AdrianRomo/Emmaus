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


class ReadingPlan(models.Model):
    """Several days of reading that the church itself prepared. A plan holds only Scripture
    *references* ("Juan 15:1-8") and what the church wrote: Emmaus never stores or fetches Bible
    text. The administration prepares it; it is published only when a pastor approves it
    (``resources.approvals``). ``rights_confirmed_by`` and ``rights_confirmed_at`` keep who published
    it last and when: the administration before approvals existed, the pastor who approved since."""

    DESCRIPTION_MAX = 400

    class Status(models.TextChoices):
        DRAFT = "borrador", "Borrador"
        PUBLISHED = "publicado", "Publicado"
        RETIRED = "retirado", "Retirado"

    church = models.ForeignKey(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="reading_plans")
    title = models.CharField("Título", max_length=120)
    description = models.TextField("De qué trata", blank=True)
    author_line = models.CharField("Quién lo preparó", max_length=120, blank=True)
    status = models.CharField("Estado", max_length=12, choices=Status.choices, default=Status.DRAFT, db_default=Status.DRAFT)
    rights_confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    rights_confirmed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "plan de lectura"
        verbose_name_plural = "planes de lectura"
        ordering = ["title", "id"]
        indexes = [models.Index(fields=["church", "status"], name="reading_plan_church_status_idx")]

    def __str__(self) -> str:
        return self.title

    @property
    def is_draft(self) -> bool:
        return self.status == self.Status.DRAFT

    @property
    def is_published(self) -> bool:
        return self.status == self.Status.PUBLISHED

    @property
    def is_retired(self) -> bool:
        return self.status == self.Status.RETIRED

    @property
    def was_published(self) -> bool:
        """It was published at least once: somebody may have started it, so its days are kept."""
        return not self.is_draft or self.rights_confirmed_at is not None


class ReadingPlanDay(models.Model):
    """One day of a plan, in order (``position`` 1..N). ``reference`` is a citation only; ``reflection``
    and ``question`` are the church's own words, plain text, escaped when shown. A person reads a day
    whenever they get to it: a day is never tied to a date."""

    REFLECTION_MAX = 1500

    plan = models.ForeignKey(ReadingPlan, on_delete=models.CASCADE, related_name="days")
    position = models.PositiveSmallIntegerField("Día")
    title = models.CharField("Título del día", max_length=120, blank=True)
    reference = models.CharField("Cita", max_length=80)
    reflection = models.TextField("Reflexión", blank=True)
    question = models.CharField("Pregunta para pensar", max_length=300, blank=True)

    class Meta:
        verbose_name = "día de un plan de lectura"
        verbose_name_plural = "días de un plan de lectura"
        ordering = ["plan_id", "position"]
        constraints = [models.UniqueConstraint(fields=["plan", "position"], name="reading_plan_day_position")]

    def __str__(self) -> str:
        return f"Día {self.position}"


class ReadingPlanProgress(models.Model):
    """A person started a plan. PRIVATE to that person, like ``DevotionalRead``: no list, count,
    export, audit row, administration screen or Django admin page reads this table for anybody
    else. Leaving the plan deletes the row and, with it, the person's marks."""

    plan = models.ForeignKey(ReadingPlan, on_delete=models.CASCADE, related_name="+")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="+")
    started_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "plan de lectura empezado"
        verbose_name_plural = "planes de lectura empezados"
        constraints = [models.UniqueConstraint(fields=["plan", "person"], name="reading_plan_progress_once")]


class ReadingPlanDayRead(models.Model):
    """The person said "Ya lo leí" on one day of a plan they started. As private as the progress it hangs from."""

    progress = models.ForeignKey(ReadingPlanProgress, on_delete=models.CASCADE, related_name="marks")
    day = models.ForeignKey(ReadingPlanDay, on_delete=models.CASCADE, related_name="+")
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "día leído de un plan"
        verbose_name_plural = "días leídos de un plan"
        constraints = [models.UniqueConstraint(fields=["progress", "day"], name="reading_plan_day_read_once")]


class ContentApproval(models.Model):
    """The pastoral approval of one piece of the church's own content (issue #99, decision 0014):
    preparing content is the administration's work, approving it for publication is a pastor's.

    One row per object. ``kind`` and ``object_id`` name it (a devotional, a reading plan, the public
    home); ``church`` is the tenant and every query filters by it. ``approved_digest`` is the
    fingerprint of the words that were approved, so a later edit is noticed instead of inheriting
    the approval. ``source_note`` and ``return_reason`` are short plain text for the staff only:
    escaped when shown, never e-mailed, never copied to an audit row.

    Nothing here is about readers: no reference to a person who reads, no count, no date of reading."""

    NOTE_MAX = 200
    REASON_MAX = 300

    class Kind(models.TextChoices):
        DEVOTIONAL = "devocional", "Devocional"
        PLAN = "plan", "Plan de lectura"
        HOME = "sitio", "Página pública de la iglesia"

    class State(models.TextChoices):
        PENDING = "pendiente", "Espera aprobación pastoral"
        APPROVED = "aprobado", "Aprobado"
        RETURNED = "devuelto", "Devuelto"
        LEGACY = "anterior", "Publicado antes de que existieran las aprobaciones"
        CHANGED = "cambio", "Cambió: necesita aprobación otra vez"

    class Use(models.TextChoices):
        OWN = "propia", "Obra propia de la iglesia"
        PERMISSION = "permiso", "Con permiso del autor"
        OPEN = "abierta", "Dominio público o licencia abierta"

    church = models.ForeignKey(Church, verbose_name="Iglesia", on_delete=models.CASCADE, related_name="+")
    kind = models.CharField("Tipo de contenido", max_length=12, choices=Kind.choices)
    object_id = models.PositiveBigIntegerField()
    state = models.CharField("Estado", max_length=12, choices=State.choices)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    requested_at = models.DateTimeField(null=True, blank=True)
    approved_digest = models.CharField(max_length=64, blank=True, default="", db_default="")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    permitted_use = models.CharField("Uso permitido", max_length=12, choices=Use.choices, blank=True, default="", db_default="")
    source_note = models.CharField("Nota sobre la fuente", max_length=NOTE_MAX, blank=True, default="", db_default="")
    returned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    returned_at = models.DateTimeField(null=True, blank=True)
    return_reason = models.CharField("Motivo", max_length=REASON_MAX, blank=True, default="", db_default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "aprobación pastoral de contenido"
        verbose_name_plural = "aprobaciones pastorales de contenido"
        constraints = [models.UniqueConstraint(fields=["kind", "object_id"], name="content_approval_one_per_object")]
        indexes = [models.Index(fields=["church", "state"], name="content_approval_state_idx")]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.object_id}: {self.get_state_display()}"

    @property
    def is_pending(self) -> bool:
        return self.state == self.State.PENDING

    @property
    def is_approved(self) -> bool:
        return self.state == self.State.APPROVED

    @property
    def is_returned(self) -> bool:
        return self.state == self.State.RETURNED

    @property
    def is_legacy(self) -> bool:
        return self.state == self.State.LEGACY

    @property
    def is_changed(self) -> bool:
        return self.state == self.State.CHANGED
