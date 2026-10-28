"""Exports read by a person in a spreadsheet: every campero of an event and their answers, one row each.

``answers_rows`` builds the table behind "Descargar las respuestas" (views.admin.answers_export) with a fixed number of
queries for the whole event: never one per campero or per answer. The list without answers (participant_export) and
one person's JSON (participant_data_export) stay in the views; they share ``safe_cell``.
"""

from __future__ import annotations

from django.utils import timezone

from ..models import Answer, ConsentRecord, EventParticipant, Interview
from . import event_questionnaires as eq
from .metrics import local_tz
from .questionnaire import all_questions

DATE_FORMAT = "%d/%m/%Y %H:%M"
REGISTRY_HEADER = [
    "Número",
    "Nombre",
    "Edad",
    "Cuarto",
    "Iglesia o grupo",
    "Sede",
    "Coordinador",
    "Participa",
    "Autorización",
    "Registro",
]
NO_INTERVIEW = "Sin entrevista"  # else the interview's own state: "En curso" or "Completada"


def safe_cell(value) -> str:
    """Spreadsheets run cells that start with = + - @ as formulas; neutralize them."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def answers_rows(event, links=None) -> tuple[list[str], list[list[str]]]:
    """(header, rows) of "Descargar las respuestas", every cell already through ``safe_cell``. ``links``: the event's
    (``event_questionnaires.links_for``), when the caller already read them.

    One row per campero of the event, by number, also those who no longer take part ("Participa": "No"). After the
    registry columns, for each questionnaire the event uses, in the event's order and named by the title of the version
    it uses: the state of the campero's interview, when it was completed, and one column per question of that version
    in questionnaire order. A cell holds what ``describe_answer`` writes (option labels joined with ", ", or the text),
    or nothing when the question was not answered.

    An interview answered on another version of the same questionnaire fills the columns of the questions with the
    same code; its answers to questions the version in use no longer has are left out. Discarded interviews hold no
    answers and count as none; interviews of a questionnaire the event no longer uses are not read.
    """
    zone = local_tz(event)
    links = eq.links_for(event) if links is None else links
    columns = []  # per link: (questionnaire_id, version_id, {question_id: index}, {code: index}, width)
    header = list(REGISTRY_HEADER)
    for link in links:
        title = link.version.title
        questions = all_questions(link.version)
        columns.append((
            link.questionnaire_id,
            link.version_id,
            {question.id: index for index, question in enumerate(questions)},
            {question.code: index for index, question in reversed(list(enumerate(questions)))},  # first one wins
            len(questions),
        ))
        header += [f"{title}: estado", f"{title}: fecha en que se completó", *(f"{title}: {q.text}" for q in questions)]

    linked = [link.questionnaire_id for link in links]
    interviews = {}  # (participant_id, questionnaire_id) -> Interview; at most one live per pair (database constraint)
    for interview in (
        Interview.objects.filter(event=event, questionnaire_id__in=linked)
        .exclude(status=Interview.Status.DISCARDED)
        .only("id", "participant_id", "questionnaire_id", "version_id", "status", "completed_at")
    ):
        interviews[(interview.participant_id, interview.questionnaire_id)] = interview

    answers = {}  # interview_id -> list[Answer]
    for answer in (
        Answer.objects.filter(interview__event=event, interview__questionnaire_id__in=linked)
        .exclude(interview__status=Interview.Status.DISCARDED)
        .select_related("question")
        .prefetch_related("options")
    ):
        answers.setdefault(answer.interview_id, []).append(answer)

    guardian = {}  # participant_id -> status of the latest guardian record (services.participants.latest_consent)
    for participant_id, status in (
        ConsentRecord.objects.filter(participant__event=event, kind=ConsentRecord.Kind.GUARDIAN)
        .order_by("participant_id", "-recorded_at", "-id")
        .values_list("participant_id", "status")
    ):
        guardian.setdefault(participant_id, status)

    rows = []
    participants = EventParticipant.objects.filter(event=event).select_related("coordinator__user", "sede").order_by("number")
    for p in participants:
        if event.requires_guardian_authorization and p.age < event.adult_age:  # is_minor, and only when the event asks for it
            authorized = "Sí" if guardian.get(p.id) == ConsentRecord.Status.GRANTED else "No"
        else:
            authorized = ""
        row = [
            p.code,
            p.full_name,
            p.age,
            p.room,
            p.church_group,
            p.sede.name if p.sede else "",
            p.coordinator.display_name if p.coordinator else "",
            "Sí" if p.is_active else "No",
            authorized,
            timezone.localtime(p.created_at, zone).strftime(DATE_FORMAT),
        ]
        for questionnaire_id, version_id, by_id, by_code, width in columns:
            cells = [""] * width
            interview = interviews.get((p.id, questionnaire_id))
            if interview is None:
                row += [NO_INTERVIEW, "", *cells]
                continue
            same_version = interview.version_id == version_id
            for answer in answers.get(interview.id, ()):
                index = by_id.get(answer.question_id) if same_version else by_code.get(answer.question.code)
                if index is not None:
                    cells[index] = _answer_text(answer)
            completed = timezone.localtime(interview.completed_at, zone).strftime(DATE_FORMAT) if interview.completed_at else ""
            row += [interview.get_status_display(), completed, *cells]
        rows.append([safe_cell(value) for value in row])
    return [safe_cell(value) for value in header], rows


def _answer_text(answer: Answer) -> str:
    """As ``describe_answer``: the chosen options' labels in their order, joined with ", ", or the text; "" when empty.

    The labels are the answer's own options (its version's), so an answer given on an older version keeps the words
    the campero chose, and "Prefiero no responder" is written as its label, never as its code.
    """
    if answer.question.is_choice:
        return ", ".join(option.label for option in answer.options.all())
    return answer.text_value if answer.text_value.strip() else ""
