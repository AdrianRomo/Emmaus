import uuid

from django.db import models

from events.models import Event
from people.models import Person


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
    """That a person already gave their opinion of an event. No date and no link to the opinion."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="feedback_given")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="feedback_given")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["event", "person"], name="feedback_given_once")]
