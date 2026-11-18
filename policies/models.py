from django.db import models

# Bumped by every write of a row. ``people.policy`` keeps what it read on the church object it was
# given (one query per request) and reads again when this number moved. It carries no data. Known
# limit: an object that read a row inside a transaction that was then rolled back keeps that answer
# until the next write or ``people.policy.forget(church)``; a request does not outlive its rollback.
_generation = 0


def generation() -> int:
    return _generation


def forget() -> None:
    """Make every reader ask the table again. ``save`` and ``delete`` call it; call it yourself
    after a queryset ``update``, which runs neither."""
    global _generation
    _generation += 1


class ChurchPolicy(models.Model):
    """A church's own choices (decision 0015). One row per church, created by the first change: a
    church with no row, and every empty column, follows the product default.

    Read only through ``people.policy`` (which clamps each value to the platform's floor and
    ceiling on every read) and written only through ``policies.services.save`` (which leaves one
    audit row per value changed). The numbers have no database constraint on purpose: the floors
    belong to the platform and may move, and the read path holds them whatever is stored.
    """

    church = models.OneToOneField("core.Church", verbose_name="Iglesia", on_delete=models.CASCADE, related_name="policy")
    min_reporting_size = models.PositiveSmallIntegerField("Mínimo de personas para mostrar un total", null=True, blank=True)
    feedback_open_days = models.PositiveSmallIntegerField("Días para dar una opinión después de un evento", null=True, blank=True)
    urgent_notice_pastors = models.BooleanField(
        "Los pastores también pueden publicar avisos urgentes", default=False, db_default=False
    )
    minors_need_consent_by_default = models.BooleanField(
        "Los eventos nuevos piden autorización para menores de edad", default=False, db_default=False
    )
    attendance_identifiable_months = models.PositiveSmallIntegerField(
        "Meses que se conserva quién asistió a cada reunión", null=True, blank=True
    )
    attendance_aggregate_months = models.PositiveSmallIntegerField(
        "Meses que se conservan los totales de cada reunión", null=True, blank=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "políticas de la iglesia"
        verbose_name_plural = "políticas de las iglesias"

    def __str__(self) -> str:
        return f"Políticas de {self.church}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        forget()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        forget()
        return result
