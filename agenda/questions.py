"""Questions at sign-up (issue #93, wave 12): what the administration asks whoever signs up for ONE event.

Up to ``MAX_QUESTIONS`` (5) per event, of three kinds: a short text (120 characters), one option of a list, yes or
no. The question itself is one line the administration types; it is shown to whoever can sign up (the people of
the church and, where the event takes them, visitors without an account), so it is short and has no format.

These are NOT the camp's questionnaires and not ``formularios``: nothing here is confidential pastoral data, and
the builder says so ("no preguntes por salud, situaciones familiares ni sobre menores de edad"). The answers are
read by the event's administration on "Inscritos" and nowhere else: no export, no report, no total, no e-mail,
no audit row carries one. They are deleted with their question, when the registration stops (cancelled, off the
list, annulled: ``services._drop_answers``) and when the event's retention ends (``visitors.delete_due``).

An event that protects minors asks no free-text question: ``asked`` leaves them out, the builder refuses a new
one, and turning the rule on deletes the free-text answers already given (``drop_free_text``). Ticket kinds are
not built: without payments (#86) a "kind" is a question with options.
"""

from __future__ import annotations

from django import forms
from django.db import transaction

from core.forms import TuErrorMessagesMixin
from events.models import Event
from people.audit import record_church

from . import services
from .models import EventPage, EventQuestion, EventRegistration, RegistrationAnswer

Kind = EventQuestion.Kind
MAX_QUESTIONS = 5
TEXT_MAX = 120
OPTION_MAX = 60
MAX_OPTIONS = 8
YES, NO = "Sí", "No"
PREFIX = "pregunta_"
SENSITIVE = (
    "No preguntes por salud, alergias, situaciones familiares ni nada sobre menores de edad: esas respuestas no se "
    "guardan aquí con el cuidado que necesitan. Pregunta solo lo que el evento ocupa para organizarse."
)
MINORS_NO_TEXT = "Este evento cuida a los menores de edad: sus preguntas solo pueden ser de opciones o de sí o no."


def protects_minors(event: Event) -> bool:
    return EventPage.objects.filter(event=event, requires_guardian_consent=True).exists()


def asked(event: Event, page: EventPage | None = None) -> list[EventQuestion]:
    """The questions a sign-up to ``event`` answers now, in order: every one, minus the free-text ones while the
    event protects minors."""
    rows = list(EventQuestion.objects.filter(event=event))
    minors = page.requires_guardian_consent if page is not None else protects_minors(event)
    return [row for row in rows if not (minors and row.kind == Kind.TEXT)]


def one_line(text: str) -> str:
    return " ".join(str(text).split())


def clean_options(text: str) -> list[str]:
    """The options as typed, one per line: trimmed, without empty or repeated lines."""
    seen, options = set(), []
    for line in str(text).splitlines():
        line = one_line(line)
        if line and line.casefold() not in seen:
            seen.add(line.casefold())
            options.append(line)
    return options


class QuestionForm(TuErrorMessagesMixin, forms.ModelForm):
    """One question, as the administration writes it. ``kind`` is chosen once: a question with answers does not
    change what its answers are."""

    class Meta:
        model = EventQuestion
        fields = ["text", "kind", "options", "required"]
        help_texts = {
            "text": "Una línea, por ejemplo: ¿Te quedas a comer?",
            "options": "Una opción por renglón, de 2 a 8. Solo para «Una opción de una lista».",
        }
        widgets = {"kind": forms.RadioSelect, "options": forms.Textarea(attrs={"rows": 4}), "text": forms.TextInput(attrs={"maxlength": TEXT_MAX})}

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.fields["kind"].choices = Kind.choices
        self.fields["text"].error_messages["required"] = "Escribe la pregunta."
        if self.instance.pk:
            del self.fields["kind"]

    def clean_text(self):
        text = one_line(self.cleaned_data["text"])
        if not services.visible_text(text):
            raise forms.ValidationError("Escribe la pregunta.")
        return text

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind") or self.instance.kind
        options = clean_options(cleaned.get("options", ""))
        if kind == Kind.CHOICE:
            if not 2 <= len(options) <= MAX_OPTIONS:
                self.add_error("options", f"Escribe de 2 a {MAX_OPTIONS} opciones, una por renglón.")
            elif any(len(option) > OPTION_MAX for option in options):
                self.add_error("options", f"Cada opción puede tener hasta {OPTION_MAX} caracteres.")
        else:
            options = []
        cleaned["options"] = "\n".join(options)
        if kind == Kind.TEXT and not self.instance.pk and protects_minors(self.event):
            self.add_error("kind", MINORS_NO_TEXT)
        return cleaned


def save_question(event: Event, actor, question: EventQuestion | None = None, *, text: str, options: str = "",
                  required: bool = False, kind: str | None = None) -> EventQuestion:
    """Add a question (at the end) or change one. The audit row says which question of which event, never its words."""
    with transaction.atomic():
        Event.objects.select_for_update().filter(pk=event.pk).first()  # two administrators adding the sixth: one wins
        created = question is None
        if created:
            if kind not in Kind.values:
                raise services.AgendaError("Elige el tipo de respuesta.", code="kind")
            if kind == Kind.TEXT and protects_minors(event):
                raise services.AgendaError(MINORS_NO_TEXT, code="minors_no_text")
            existing = EventQuestion.objects.filter(event=event)
            if existing.count() >= MAX_QUESTIONS:
                raise services.AgendaError(f"Un evento puede tener hasta {MAX_QUESTIONS} preguntas. Quita una para agregar otra.", code="too_many")
            last = existing.order_by("-position").values_list("position", flat=True).first() or 0
            question = EventQuestion(event=event, kind=kind, position=last + 1)
        else:
            question = EventQuestion.objects.select_for_update().filter(pk=question.pk, event=event).first()
            if question is None:
                raise services.AgendaError("Esta pregunta ya no existe.", code="gone")
        question.text = one_line(text)[:TEXT_MAX]
        question.options = "\n".join(clean_options(options)) if question.kind == Kind.CHOICE else ""
        question.required = bool(required)
        if not services.visible_text(question.text):
            raise services.AgendaError("Escribe la pregunta.", code="empty")
        question.save()
        verb = "Agregó una pregunta a la inscripción de" if created else "Modificó una pregunta de la inscripción de"
        record_church(event.church, actor, "evento_preguntas_inscripcion", f"{verb} {event.name}", question, event_id=event.pk)
    return question


def delete_question(question: EventQuestion, actor) -> int:
    """Take a question out, with every answer to it. Returns how many answers went."""
    event = question.event
    with transaction.atomic():
        answers = RegistrationAnswer.objects.filter(question=question).count()
        record_church(event.church, actor, "evento_preguntas_inscripcion",
                      f"Quitó una pregunta de la inscripción de {event.name}, con sus respuestas", question,
                      event_id=event.pk, count=answers)
        question.delete()
    return answers


def drop_free_text(event: Event) -> int:
    """The event protects minors now: what was typed in answer to its free-text questions is deleted."""
    return RegistrationAnswer.objects.filter(question__event=event, question__kind=Kind.TEXT).delete()[0]


class AnswersForm(forms.Form):
    """The event's questions as one form. ``answers()`` gives {question id as text: the answer as text}, only for
    what was answered, with every value checked against the question (an option that is not offered is refused)."""

    def __init__(self, *args, questions, **kwargs):
        super().__init__(*args, **kwargs)
        self.questions = list(questions)
        for question in self.questions:
            common = {"label": question.text, "required": question.required, "error_messages": {"required": "Responde esta pregunta."}}
            if question.kind == Kind.TEXT:
                field = forms.CharField(max_length=TEXT_MAX, **common)
                field.error_messages["max_length"] = f"Escribe {TEXT_MAX} caracteres o menos."
                field.widget.attrs["maxlength"] = TEXT_MAX
            else:
                options = [YES, NO] if question.kind == Kind.YES_NO else question.option_list
                field = forms.ChoiceField(choices=[(option, option) for option in options], widget=forms.RadioSelect, **common)
                field.error_messages["invalid_choice"] = "Elige una de las opciones."
            self.fields[f"{PREFIX}{question.pk}"] = field

    def answers(self) -> dict[str, str]:
        found = {}
        for question in self.questions:
            value = one_line(self.cleaned_data.get(f"{PREFIX}{question.pk}") or "")
            if value:
                found[str(question.pk)] = value[:TEXT_MAX]
        return found


def save_answers(registration: EventRegistration, answers: dict | None) -> int:
    """Store what one registration answered, replacing what it had. ``answers`` is what ``AnswersForm.answers`` gave,
    possibly a while ago (a visitor's request): every value is checked again against the question as it is NOW, and
    what no longer fits (the question was removed, the option is gone, free text in an event that now protects
    minors) is dropped without an error."""
    answers = answers if isinstance(answers, dict) else {}
    kept = []
    for question in asked(registration.event):
        value = answers.get(str(question.pk))
        if not isinstance(value, str) or not one_line(value):
            continue
        value = one_line(value)[:TEXT_MAX]
        if question.kind == Kind.YES_NO and value not in (YES, NO):
            continue
        if question.kind == Kind.CHOICE and value not in question.option_list:
            continue
        kept.append(RegistrationAnswer(registration=registration, question=question, value=value))
    with transaction.atomic():
        RegistrationAnswer.objects.filter(registration=registration).delete()
        RegistrationAnswer.objects.bulk_create(kept)
    return len(kept)


def answers_by_registration(event: Event) -> dict[int, list[RegistrationAnswer]]:
    """For "Inscritos": each registration's answers, in the order of the questions. One query."""
    found: dict[int, list[RegistrationAnswer]] = {}
    rows = RegistrationAnswer.objects.filter(registration__event=event).select_related("question").order_by("question__position", "question_id")
    for row in rows:
        found.setdefault(row.registration_id, []).append(row)
    return found
