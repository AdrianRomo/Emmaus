from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib import error, request

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import dateformat, timezone

from .models import (
    AIDraft,
    Church,
    DiscipleshipPath,
    Enrollment,
    FollowUpRequest,
    Invitation,
    LessonProgress,
    ParticipantProfile,
    ParticipantQuestion,
    QuestionResponse,
    Role,
    UserChurchRole,
)

logger = logging.getLogger(__name__)

AI_SYSTEM_PROMPT = """Estás ayudando a un líder de una iglesia cristiana.
No eres Dios, ni Jesús, ni el Espíritu Santo, ni pastor, terapeuta, médico o consejero.
No te atribuyas autoridad espiritual.
No des consejos de emergencia, médicos, legales ni de salud mental.
Anima al líder a responder de forma personal.
Sé amable, bíblico, humilde y breve.
Sugiere pasajes bíblicos, pero no cites pasajes largos.
En temas delicados, recomienda escalar el caso a un pastor o a un profesional calificado.
Escribe en español de México y dirígete a la persona de tú.
Lo que escribas es solo un borrador y un líder debe revisarlo antes de enviarlo."""


@dataclass
class ProgressSummary:
    enrollment: Enrollment | None
    total_lessons: int
    completed_lessons: int
    percent: int
    current_progress: LessonProgress | None


def ensure_role_groups() -> None:
    for role, label in Role.choices:
        Group.objects.get_or_create(name=role)


def assign_role(user: User, church: Church, role: str) -> UserChurchRole:
    ensure_role_groups()
    record, _ = UserChurchRole.objects.update_or_create(
        church=church,
        user=user,
        defaults={"role": role},
    )
    user.groups.clear()
    group, _ = Group.objects.get_or_create(name=role)
    user.groups.add(group)
    return record


def full_name_to_parts(name: str) -> tuple[str, str]:
    pieces = name.strip().split()
    if not pieces:
        return "", ""
    if len(pieces) == 1:
        return pieces[0], ""
    return pieces[0], " ".join(pieces[1:])


@transaction.atomic
def create_initial_church(
    *,
    church_name: str,
    church_slug: str,
    admin_name: str,
    admin_email: str,
    admin_password: str,
) -> tuple[Church, User]:
    first_name, last_name = full_name_to_parts(admin_name)
    church = Church.objects.create(
        name=church_name,
        slug=church_slug,
        contact_email=admin_email,
        public_welcome_text=f"Te damos la bienvenida a {church_name}.",
    )
    user = User.objects.create_user(
        username=admin_email.lower(),
        email=admin_email.lower(),
        password=admin_password,
        first_name=first_name,
        last_name=last_name,
        is_staff=True,
    )
    assign_role(user, church, Role.CHURCH_ADMIN)
    return church, user


def invite_url(request, invitation: Invitation) -> str:
    return request.build_absolute_uri(reverse("accept_invitation", kwargs={"token": invitation.token}))


def send_email_safely(subject: str, message: str, recipients: list[str]) -> bool:
    try:
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=False)
        return True
    except Exception:
        logger.exception("Email failed: %s", subject)
        return False


def notify_invitation(request, invitation: Invitation) -> bool:
    link = invite_url(request, invitation)
    expires = dateformat.format(timezone.localtime(invitation.expires_at), r"j \d\e F \d\e Y, H:i").lower()
    subject = f"Tu invitación a Emmaus de {invitation.church.name}"
    message = (
        f"Te invitaron a unirte a {invitation.church.name} en Emmaus con el rol de "
        f"{invitation.get_role_display()}.\n\nAcepta tu invitación aquí:\n{link}\n\n"
        f"Este enlace vence el {expires}. Si no esperabas esta invitación, puedes ignorar este correo."
    )
    return send_email_safely(subject, message, [invitation.email])


def notify_question_created(question: ParticipantQuestion) -> None:
    profile = question.participant.participant_profile
    recipients: list[str] = []
    if profile.assigned_mentor and profile.assigned_mentor.email:
        recipients.append(profile.assigned_mentor.email)
    recipients += list(
        User.objects.filter(
            church_roles__church=question.church,
            church_roles__role__in=[Role.CHURCH_ADMIN, Role.PASTOR],
            email__gt="",
        ).values_list("email", flat=True)
    )
    if recipients:
        send_email_safely(
            "Emmaus: hay una pregunta nueva",
            "Una persona de tu iglesia envió una pregunta en Emmaus. Entra a Emmaus para leerla.\n\n"
            "Por privacidad, este correo no incluye el nombre de la persona ni lo que escribió.",
            sorted(set(recipients)),
        )


def notify_question_response(response: QuestionResponse) -> None:
    participant = response.question.participant
    if participant.email:
        send_email_safely(
            "Emmaus: respondieron tu pregunta",
            "Un líder de tu iglesia respondió tu pregunta en Emmaus. Entra a Emmaus para leer la respuesta.",
            [participant.email],
        )


def notify_follow_up_created(follow_up: FollowUpRequest) -> None:
    recipients: list[str] = []
    if follow_up.assigned_to and follow_up.assigned_to.email:
        recipients.append(follow_up.assigned_to.email)
    recipients += list(
        User.objects.filter(
            church_roles__church=follow_up.church,
            church_roles__role__in=[Role.CHURCH_ADMIN, Role.PASTOR],
            email__gt="",
        ).values_list("email", flat=True)
    )
    if recipients:
        send_email_safely(
            "Emmaus: hay un seguimiento nuevo",
            "Se registró una solicitud de seguimiento en Emmaus. Entra a Emmaus para ver los detalles.\n\n"
            "Por privacidad, este correo no incluye el nombre de la persona ni el motivo.",
            sorted(set(recipients)),
        )


def notify_question_escalated(question: ParticipantQuestion) -> None:
    recipients = list(
        User.objects.filter(
            church_roles__church=question.church,
            church_roles__role__in=[Role.CHURCH_ADMIN, Role.PASTOR],
            email__gt="",
        ).values_list("email", flat=True)
    )
    if recipients:
        send_email_safely(
            "Emmaus: una pregunta necesita atención pastoral",
            "Una pregunta en Emmaus se pasó al pastor para recibir atención pastoral. Entra a Emmaus para leerla.\n\n"
            "Por privacidad, este correo no incluye el nombre de la persona ni lo que escribió.",
            sorted(set(recipients)),
        )


def enroll_participant_in_path(participant: User, path: DiscipleshipPath | None) -> Enrollment | None:
    if not path:
        return None
    enrollment, _ = Enrollment.objects.get_or_create(participant=participant, path=path)
    for lesson in path.lessons.filter(is_active=True):
        LessonProgress.objects.get_or_create(enrollment=enrollment, lesson=lesson)
    return enrollment


def default_path_for_church(church: Church) -> DiscipleshipPath | None:
    return (
        church.paths.filter(is_active=True, is_default=True).first()
        or church.paths.filter(is_active=True).order_by("created_at").first()
    )


def progress_for_participant(user: User) -> ProgressSummary:
    enrollment = (
        Enrollment.objects.filter(participant=user, path__is_active=True)
        .select_related("path")
        .prefetch_related("lesson_progress__lesson")
        .order_by("-started_at")
        .first()
    )
    if not enrollment:
        return ProgressSummary(None, 0, 0, 0, None)
    total = enrollment.lesson_progress.count()
    completed = enrollment.lesson_progress.filter(completed_at__isnull=False).count()
    current = (
        enrollment.lesson_progress.select_related("lesson")
        .filter(completed_at__isnull=True, lesson__is_active=True)
        .order_by("lesson__order")
        .first()
    )
    if not current:
        current = enrollment.lesson_progress.select_related("lesson").order_by("-lesson__order").first()
    percent = int((completed / total) * 100) if total else 0
    return ProgressSummary(enrollment, total, completed, percent, current)


def last_activity_at(user: User):
    latest_progress = (
        LessonProgress.objects.filter(enrollment__participant=user).order_by("-last_viewed_at").values_list("last_viewed_at", flat=True).first()
    )
    latest_question = (
        ParticipantQuestion.objects.filter(participant=user).order_by("-updated_at").values_list("updated_at", flat=True).first()
    )
    latest_answer = (
        user.enrollments.filter(reflection_answers__isnull=False)
        .order_by("-reflection_answers__updated_at")
        .values_list("reflection_answers__updated_at", flat=True)
        .first()
    )
    dates = [value for value in [latest_progress, latest_question, latest_answer, getattr(user, "date_joined", None)] if value]
    return max(dates) if dates else None


def create_inactivity_follow_up(profile: ParticipantProfile, days: int = 7) -> FollowUpRequest | None:
    last_activity = last_activity_at(profile.user) or profile.date_joined
    if last_activity and timezone.now() - last_activity < timezone.timedelta(days=days):
        return None
    duplicate = FollowUpRequest.objects.filter(
        church=profile.church,
        participant=profile.user,
        source=FollowUpRequest.Source.INACTIVITY,
        status__in=[FollowUpRequest.Status.OPEN, FollowUpRequest.Status.IN_PROGRESS],
    ).exists()
    if duplicate:
        return None
    return FollowUpRequest.objects.create(
        church=profile.church,
        participant=profile.user,
        assigned_to=profile.assigned_mentor,
        source=FollowUpRequest.Source.INACTIVITY,
        reason=f"Sin actividad registrada en Emmaus durante al menos {days} días.",
    )


def generate_ai_draft(question: ParticipantQuestion, generated_by: User) -> tuple[AIDraft | None, str | None]:
    if not settings.LLM_ENABLED:
        # Church leaders read this: no setting names (docs/deployment.md has them for the server's administrator).
        return None, "Los borradores con IA no están activados. Pide a quien administra el servidor que los active."
    if not settings.LLM_BASE_URL or not settings.LLM_API_KEY or not settings.LLM_MODEL:
        return None, "Los borradores con IA no están listos todavía. Pide a quien administra el servidor que termine de configurarlos."

    endpoint = settings.LLM_BASE_URL
    if not endpoint.endswith("/chat/completions"):
        endpoint = f"{endpoint}/chat/completions"
    payload = {
        "model": settings.LLM_MODEL,
        "messages": [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Redacta una respuesta amable que un líder de la iglesia pueda revisar antes de enviarla.\n\n"
                    f"Pregunta del participante:\n{question.question_text}\n\n"
                    f"Nivel de cuidado: {question.get_sensitivity_level_display()}"
                ),
            },
        ],
        "temperature": 0.4,
        "max_tokens": 500,
    }
    req = request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.LLM_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"].strip()
    except (error.URLError, KeyError, IndexError, json.JSONDecodeError) as exc:
        logger.exception("LLM draft generation failed")  # the detail goes to the log, never to the screen
        return None, "No se pudo generar el borrador. Inténtalo otra vez."

    draft = AIDraft.objects.create(
        church=question.church,
        related_question=question,
        generated_by=generated_by,
        generated_text=content,
    )
    return draft, None
