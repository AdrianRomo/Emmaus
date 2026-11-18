import uuid

from django.db import models

from events.models import Event


class EventFeedback(models.Model):
    """One anonymous opinion about one event. It holds no person and only the day it was given, and
    its key is random: the order of this table does not follow the order of ``FeedbackGiven`` (who
    already answered, without when), so the two cannot be paired row by row."""

    class Rating(models.TextChoices):
        GREAT = "muy_bien", "Muy bien"
        GOOD = "bien", "Bien"
        FAIR = "regular", "Regular"
        POOR = "mal", "Mal"

    class Recommend(models.TextChoices):
        YES = "si", "Sí"
        MAYBE = "tal_vez", "Tal vez"
        NO = "no", "No"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="feedback")
    rating = models.CharField("¿Cómo estuvo?", max_length=10, choices=Rating.choices)
    recommend = models.CharField("¿Invitarías a alguien la próxima vez?", max_length=10, choices=Recommend.choices)
    comment = models.CharField("¿Qué mejorarías?", max_length=500, blank=True)
    given_on = models.DateField(auto_now_add=True)

    class Meta:
        verbose_name = "opinión de un evento"
        verbose_name_plural = "opiniones de los eventos"


class FeedbackGiven(models.Model):
    """That somebody already gave their opinion of an event, so nobody answers twice. It holds no
    person: ``token`` is a keyed hash of the event and the person (``feedback.services.token_for``)
    that cannot be read back into who it was. It has no date and no link to the opinion.

    The real guarantee, as the screens say it: the opinion is stored with nothing that ties it to
    the person. What remains knowable, only to somebody with the database AND the server's secret,
    is whether a given person answered; never what."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="feedback_given")
    token = models.CharField(max_length=64, default="", db_default="")
    # Not used any more: the first version of this table named the person. Migration 0002 turns each
    # of those rows into a token and empties this column; a later release drops it (decision 0013:
    # add, stop reading, drop later).
    person = models.ForeignKey("people.Person", on_delete=models.CASCADE, null=True, blank=True, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["event", "token"], name="feedback_given_once")]
