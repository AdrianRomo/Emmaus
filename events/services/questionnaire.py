"""Questionnaire structure, conditional logic, and answer validation.

Published versions are immutable, so their step structure is cached in-process.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from ..models import (
    Answer,
    Interview,
    Question,
    QuestionOption,
    Questionnaire,
    QuestionnaireVersion,
    Section,
)


@dataclass
class Step:
    number: int
    section: Section
    section_number: int
    question: Question
    children: list[Question] = field(default_factory=list)

    @property
    def questions(self) -> list[Question]:
        return [self.question, *self.children]


@dataclass
class AnswerValue:
    codes: list[str] = field(default_factory=list)
    text: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.codes and not self.text.strip()


_structure_cache: dict[int, list[Step]] = {}
_cache_lock = threading.Lock()


def build_steps(version: QuestionnaireVersion) -> list[Step]:
    if version.status != QuestionnaireVersion.Status.DRAFT:
        cached = _structure_cache.get(version.id)
        if cached is not None:
            return cached
    sections = list(version.sections.prefetch_related("questions__options").order_by("order", "id"))
    steps: list[Step] = []
    step_for_question: dict[int, Step] = {}
    for section_number, section in enumerate(sections, start=1):
        questions = sorted(section.questions.all(), key=lambda q: (q.order, q.id))
        for question in questions:
            parent_step = step_for_question.get(question.condition_question_id) if question.condition_question_id else None
            if (
                parent_step is not None
                and parent_step.section.id == section.id
                and parent_step.question.id == question.condition_question_id
                and parent_step.question.is_choice
            ):
                parent_step.children.append(question)
                step_for_question[question.id] = parent_step
                continue
            step = Step(len(steps) + 1, section, section_number, question)
            steps.append(step)
            step_for_question[question.id] = step
    if version.status != QuestionnaireVersion.Status.DRAFT:
        with _cache_lock:
            _structure_cache[version.id] = steps
    return steps


def all_questions(version: QuestionnaireVersion) -> list[Question]:
    return [question for step in build_steps(version) for question in step.questions]


def options_by_code(question: Question) -> dict[str, QuestionOption]:
    return {option.code: option for option in question.options.all()}


def load_answers(interview: Interview) -> dict[int, AnswerValue]:
    values: dict[int, AnswerValue] = {}
    for answer in interview.answers.prefetch_related("options"):
        values[answer.question_id] = AnswerValue(
            codes=sorted((o.code for o in answer.options.all()), key=lambda c: c),
            text=answer.text_value,
        )
    return values


def is_visible(question: Question, answers: dict[int, AnswerValue], questions_by_id: dict[int, Question]) -> bool:
    seen: set[int] = set()
    current = question
    while current.condition_question_id:
        if current.id in seen:  # defensive: a cycle hides the question
            return False
        seen.add(current.id)
        parent_answer = answers.get(current.condition_question_id)
        if not parent_answer or not set(parent_answer.codes) & set(current.condition_option_codes or []):
            return False
        current = questions_by_id.get(current.condition_question_id)
        if current is None:
            return False
    return True


def visible_steps(version: QuestionnaireVersion, answers: dict[int, AnswerValue]) -> list[Step]:
    by_id = {q.id: q for q in all_questions(version)}
    return [step for step in build_steps(version) if is_visible(step.question, answers, by_id)]


def first_unanswered_step(version: QuestionnaireVersion, answers: dict[int, AnswerValue]) -> Step | None:
    for step in visible_steps(version, answers):
        if step.question.id not in answers:
            return step
    return None


def neighbor_steps(version, answers, current_number: int) -> tuple[Step | None, Step | None]:
    steps = visible_steps(version, answers)
    previous = next((s for s in reversed(steps) if s.number < current_number), None)
    following = next((s for s in steps if s.number > current_number), None)
    return previous, following


# ---- validation -------------------------------------------------------------------------


EXCLUSIVE_ERROR = "Si eliges “{label}”, no marques otras opciones."


def parse_question(question: Question, data) -> tuple[AnswerValue, str | None]:
    field_name = f"q_{question.id}"
    if question.is_choice:
        valid = options_by_code(question)
        if question.kind == Question.Kind.MULTIPLE:
            raw = [code for code in data.getlist(field_name) if code]
        else:
            raw = [data.get(field_name)] if data.get(field_name) else []
        codes = []
        for code in raw:
            if code not in valid:
                return AnswerValue(), "Elige una de las opciones que aparecen en pantalla."
            if code not in codes:
                codes.append(code)
        exclusive = [code for code in codes if valid[code].is_exclusive]
        if exclusive and len(codes) > 1:
            return AnswerValue(codes=codes), EXCLUSIVE_ERROR.format(label=valid[exclusive[0]].label)
        value = AnswerValue(codes=codes)
    else:
        text = (data.get(field_name) or "").strip()
        if len(text) > question.max_length:
            return AnswerValue(text=text), f"Escribe máximo {question.max_length} caracteres (llevas {len(text)})."
        value = AnswerValue(text=text)
    if question.required and value.is_empty:
        if question.is_choice:
            return value, "Elige una respuesta para continuar."
        return value, "Escribe una respuesta para continuar."
    return value, None


def validate_step(step: Step, data, existing: dict[int, AnswerValue]) -> tuple[dict[int, AnswerValue | None], dict[int, str]]:
    """Return {question_id: value or None (= delete)} and {question_id: error}."""
    by_id = {q.id: q for q in step.questions}
    merged = dict(existing)
    cleaned: dict[int, AnswerValue | None] = {}
    errors: dict[int, str] = {}
    value, error = parse_question(step.question, data)
    if error:
        errors[step.question.id] = error
    cleaned[step.question.id] = None if value.is_empty else value
    if value.is_empty:
        merged.pop(step.question.id, None)
    else:
        merged[step.question.id] = value
    for child in step.children:
        lookup = {**by_id, step.question.id: step.question}
        if is_visible(child, merged, lookup):
            child_value, child_error = parse_question(child, data)
            if child_error:
                errors[child.id] = child_error
            cleaned[child.id] = None if child_value.is_empty else child_value
        else:
            # Hidden answers are not kept: a changed parent must not leave stale private text.
            cleaned[child.id] = None
    return cleaned, errors


def write_answers(interview: Interview, cleaned: dict[int, AnswerValue | None], *, actor, by_participant: bool) -> dict[int, AnswerValue]:
    """Persist validated values. Caller holds the interview row lock.

    Returns the answers ``_prune_hidden`` removed because they are no longer visible, so a
    correction can record them.
    """
    questions = {q.id: q for q in all_questions(interview.version)}
    for question_id, value in cleaned.items():
        question = questions[question_id]
        if value is None:
            Answer.objects.filter(interview=interview, question=question).delete()
            continue
        answer, _ = Answer.objects.update_or_create(
            interview=interview,
            question=question,
            defaults={
                "text_value": value.text if question.is_text else "",
                "answered_by": None if by_participant else actor,
                "answered_by_participant": by_participant,
            },
        )
        if question.is_choice:
            option_map = options_by_code(question)
            answer.options.set([option_map[code] for code in value.codes])
    return _prune_hidden(interview)


def _prune_hidden(interview: Interview) -> dict[int, AnswerValue]:
    """Delete answers whose questions are no longer visible (e.g. the parent changed); return them."""
    answers = load_answers(interview)
    by_id = {q.id: q for q in all_questions(interview.version)}
    hidden = {qid: value for qid, value in answers.items() if qid in by_id and not is_visible(by_id[qid], answers, by_id)}
    if hidden:
        Answer.objects.filter(interview=interview, question_id__in=list(hidden)).delete()
    return hidden


def describe_answer(question: Question, value: AnswerValue | None) -> str:
    if value is None or value.is_empty:
        return ""
    if question.is_choice:
        chosen = set(value.codes)
        return ", ".join(option.label for option in question.options.all() if option.code in chosen)
    return value.text


# ---- version management ----------------------------------------------------------------


@transaction.atomic
def publish_version(version: QuestionnaireVersion, user) -> QuestionnaireVersion:
    if version.status != QuestionnaireVersion.Status.DRAFT:
        return version
    if not version.sections.filter(questions__isnull=False).exists():
        raise ValueError("Agrega al menos una pregunta antes de publicar.")
    for question in Question.objects.filter(section__version=version):
        if question.is_choice and not question.options.exists():
            raise ValueError(f"La pregunta “{question.text}” necesita opciones de respuesta.")
    version.status = QuestionnaireVersion.Status.PUBLISHED
    version.published_at = timezone.now()
    version.published_by = user
    version.save(update_fields=["status", "published_at", "published_by"])
    return version


@transaction.atomic
def create_questionnaire(church, *, name: str, title: str = "", intro_text: str = "", actor=None) -> QuestionnaireVersion:
    """"Nuevo cuestionario": the questionnaire and its version 1, an empty draft to fill in the editor."""
    questionnaire = Questionnaire.objects.create(church=church, name=name)
    return QuestionnaireVersion.objects.create(
        questionnaire=questionnaire,
        number=1,
        status=QuestionnaireVersion.Status.DRAFT,
        title=title or name,
        intro_text=intro_text,
        created_by=actor,
    )


@transaction.atomic
def duplicate_version(source: QuestionnaireVersion, user) -> QuestionnaireVersion:
    """New editable draft with the same content. Old interviews keep pointing at ``source``."""
    last = source.questionnaire.versions.order_by("-number").first()
    draft = QuestionnaireVersion.objects.create(
        questionnaire=source.questionnaire,
        number=(last.number if last else 0) + 1,
        title=source.title,
        intro_text=source.intro_text,
        created_by=user,
    )
    question_map: dict[int, Question] = {}
    pending_conditions: list[tuple[Question, int, list]] = []
    for section in source.sections.prefetch_related("questions__options").order_by("order"):
        new_section = Section.objects.create(
            version=draft, order=section.order, title=section.title, description=section.description
        )
        for question in section.questions.all().order_by("order"):
            new_question = Question.objects.create(
                section=new_section,
                order=question.order,
                code=question.code,
                text=question.text,
                help_text=question.help_text,
                kind=question.kind,
                required=question.required,
                sensitivity=question.sensitivity,
                max_length=question.max_length,
            )
            question_map[question.id] = new_question
            if question.condition_question_id:
                pending_conditions.append((new_question, question.condition_question_id, question.condition_option_codes))
            for option in question.options.all().order_by("order"):
                QuestionOption.objects.create(
                    question=new_question,
                    order=option.order,
                    code=option.code,
                    label=option.label,
                    is_exclusive=option.is_exclusive,
                    is_decline=option.is_decline,
                    suggests_follow_up=option.suggests_follow_up,
                )
    for new_question, old_parent_id, codes in pending_conditions:
        new_question.condition_question = question_map.get(old_parent_id)
        new_question.condition_option_codes = codes
        new_question.save(update_fields=["condition_question", "condition_option_codes"])
    return draft


# ---- default camp questionnaire ---------------------------------------------------------

DECLINE = {"code": "prefiero_no", "label": "Prefiero no responder", "is_exclusive": True, "is_decline": True}

DEFAULT_CAMP_QUESTIONNAIRE = {
    "name": "Conversación espiritual del campamento",
    "title": "Conversación espiritual",
    "intro_text": (
        "Gracias por darte este tiempo. No hay respuestas correctas o incorrectas. "
        "Puedes elegir “Prefiero no responder” en cualquier pregunta."
    ),
    "sections": [
        {
            "title": "Situación espiritual",
            "description": "Lo que tú piensas hoy. Nadie va a calificar tus respuestas.",
            "questions": [
                {
                    "code": "salvo",
                    "text": "¿Te consideras salvo?",
                    "help_text": "Responde con lo que tú piensas.",
                    "kind": "unica",
                    "sensitivity": "sensible",
                    "options": [
                        {"code": "si", "label": "Sí"},
                        {"code": "no", "label": "No"},
                        {"code": "no_seguro", "label": "No lo tengo claro"},
                        DECLINE,
                    ],
                },
                {
                    "code": "dudas",
                    "text": "¿Tienes dudas acerca de tu fe?",
                    "kind": "unica",
                    "sensitivity": "sensible",
                    "options": [{"code": "si", "label": "Sí"}, {"code": "no", "label": "No"}, DECLINE],
                },
                {
                    "code": "dudas_detalle",
                    "text": "¿Qué dudas te gustaría compartir?",
                    "help_text": "Escribe solo lo que quieras compartir.",
                    "kind": "texto_largo",
                    "sensitivity": "confidencial",
                    "condition": ("dudas", ["si"]),
                },
            ],
        },
        {
            "title": "Áreas en las que te gustaría apoyo",
            "description": "Esto no es un diagnóstico. Solo nos ayuda a saber cómo acompañarte.",
            "questions": [
                {
                    "code": "apoyo",
                    "text": "¿Hay alguna situación en tu vida en la que te gustaría recibir apoyo?",
                    "help_text": "Puedes elegir varias.",
                    "kind": "multiple",
                    "sensitivity": "confidencial",
                    "options": [
                        {"code": "familia", "label": "Familia", "suggests_follow_up": True},
                        {"code": "bienestar_emocional", "label": "Bienestar emocional", "suggests_follow_up": True},
                        {"code": "vida_personal", "label": "Vida personal", "suggests_follow_up": True},
                        {"code": "relaciones", "label": "Relaciones", "suggests_follow_up": True},
                        {"code": "sexualidad", "label": "Sexualidad", "suggests_follow_up": True},
                        {"code": "otra", "label": "Otra situación", "suggests_follow_up": True},
                        {"code": "ninguna", "label": "Por ahora no", "is_exclusive": True},
                        DECLINE,
                    ],
                },
                {
                    "code": "apoyo_detalle",
                    "text": "Si quieres, cuéntanos un poco más",
                    "help_text": "Escribe solo lo que quieras compartir.",
                    "kind": "texto_largo",
                    "sensitivity": "confidencial",
                    "condition": (
                        "apoyo",
                        ["familia", "bienestar_emocional", "vida_personal", "relaciones", "sexualidad", "otra"],
                    ),
                },
            ],
        },
        {
            "title": "Decisiones y seguimiento",
            "description": "Ninguna decisión es obligatoria.",
            "questions": [
                {
                    "code": "decision",
                    "text": "¿Tomaste alguna decisión espiritual durante esta conversación?",
                    "help_text": "Puedes elegir más de una.",
                    "kind": "multiple",
                    "sensitivity": "sensible",
                    "options": [
                        {"code": "recibir_cristo", "label": "Decidí recibir a Cristo", "suggests_follow_up": True},
                        {"code": "consagrarse", "label": "Deseo consagrarme o renovar mi compromiso", "suggests_follow_up": True},
                        {"code": "seguir_aprendiendo", "label": "Quiero seguir aprendiendo"},
                        {"code": "hablar_lider", "label": "Me gustaría hablar con un líder", "suggests_follow_up": True},
                        {"code": "ninguna", "label": "No tomé una decisión por ahora", "is_exclusive": True},
                        DECLINE,
                    ],
                },
                {
                    "code": "orientacion",
                    "text": "Si decidiste recibir a Cristo, ¿recibiste orientación o discipulado?",
                    "kind": "unica",
                    "sensitivity": "sensible",
                    "condition": ("decision", ["recibir_cristo"]),
                    "options": [
                        {"code": "si", "label": "Sí"},
                        {"code": "no", "label": "No"},
                        {"code": "pendiente", "label": "Pendiente"},
                        {"code": "no_aplica", "label": "No aplica"},
                    ],
                },
                {
                    "code": "impedimento",
                    "text": "¿Hay algo que te impida dar el siguiente paso en tu fe?",
                    "help_text": "Puedes dejarlo en blanco.",
                    "kind": "texto_largo",
                    "sensitivity": "confidencial",
                },
            ],
        },
    ],
}


@transaction.atomic
def create_questionnaire_from_definition(church, definition: dict, user=None, publish: bool = True) -> QuestionnaireVersion:
    questionnaire = Questionnaire.objects.create(church=church, name=definition["name"])
    version = QuestionnaireVersion.objects.create(
        questionnaire=questionnaire,
        number=1,
        title=definition["title"],
        intro_text=definition.get("intro_text", ""),
        created_by=user,
    )
    by_code: dict[str, Question] = {}
    for s_index, section_def in enumerate(definition["sections"], start=1):
        section = Section.objects.create(
            version=version, order=s_index, title=section_def["title"], description=section_def.get("description", "")
        )
        for q_index, q_def in enumerate(section_def["questions"], start=1):
            condition = q_def.get("condition")
            question = Question.objects.create(
                section=section,
                order=q_index,
                code=q_def["code"],
                text=q_def["text"],
                help_text=q_def.get("help_text", ""),
                kind=q_def["kind"],
                required=q_def.get("required", False),
                sensitivity=q_def.get("sensitivity", Question.Sensitivity.SENSITIVE),
                condition_question=by_code[condition[0]] if condition else None,
                condition_option_codes=list(condition[1]) if condition else [],
            )
            by_code[question.code] = question
            for o_index, o_def in enumerate(q_def.get("options", []), start=1):
                QuestionOption.objects.create(
                    question=question,
                    order=o_index,
                    code=o_def["code"],
                    label=o_def["label"],
                    is_exclusive=o_def.get("is_exclusive", False),
                    is_decline=o_def.get("is_decline", False),
                    suggests_follow_up=o_def.get("suggests_follow_up", False),
                )
    if publish:
        publish_version(version, user)
    return version
