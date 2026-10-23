from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView, PasswordResetView
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .access import (
    can_answer_question,
    get_user_role,
    has_leader_access,
    has_pastoral_access,
    visible_ai_drafts,
    visible_notes,
    visible_participant_profiles,
    visible_questions,
)
from .forms import (
    AIDraftReviewForm,
    AcceptInvitationForm,
    AssignMentorForm,
    ChurchSettingsForm,
    FollowUpRequestForm,
    InitialSetupForm,
    InvitationForm,
    LessonForm,
    ParticipantFollowUpForm,
    ParticipantQuestionForm,
    PastoralNoteForm,
    PathForm,
    QuestionResponseForm,
    ReflectionQuestionForm,
    TuPasswordResetForm,
)
from .models import (
    AIDraft,
    Church,
    DiscipleshipPath,
    FollowUpRequest,
    Invitation,
    Lesson,
    LessonProgress,
    ParticipantProfile,
    ParticipantQuestion,
    PastoralNote,
    QuestionResponse,
    ReflectionAnswer,
    Role,
)
from .services import (
    assign_role,
    create_initial_church,
    default_path_for_church,
    enroll_participant_in_path,
    full_name_to_parts,
    generate_ai_draft,
    invite_url,
    last_activity_at,
    notify_follow_up_created,
    notify_invitation,
    notify_question_created,
    notify_question_escalated,
    notify_question_response,
    progress_for_participant,
)


QUESTION_DISCLAIMER = (
    "Emmaus te ayuda a conectar con líderes de tu iglesia. No es un servicio de emergencias, terapia, consejo médico "
    "ni consejo legal, y no sustituye el cuidado pastoral. Si estás en peligro inmediato o piensas hacerte daño o "
    "hacerle daño a otra persona, llama de inmediato al 911 o a los servicios de emergencia de tu localidad."
)


def role_required(*roles: str):
    def decorator(view_func):
        @login_required
        def wrapped(request, *args, **kwargs):
            role_record = get_user_role(request.user)
            if not role_record:
                messages.error(request, "Tu cuenta todavía no está vinculada a ninguna iglesia.")
                return redirect("landing")
            if role_record.role not in roles:
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


def health(request):
    return HttpResponse("ok")


def landing(request):
    if not Church.objects.exists():
        return redirect("initial_setup")
    if request.user.is_authenticated:
        return redirect("dashboard")
    church = Church.objects.filter(active=True).order_by("created_at").first()
    return render(request, "core/landing.html", {"church": church})


def initial_setup(request):
    if Church.objects.exists():
        return redirect("dashboard" if request.user.is_authenticated else "landing")
    form = InitialSetupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        church, user = create_initial_church(
            church_name=form.cleaned_data["church_name"],
            church_slug=form.cleaned_data["church_slug"],
            admin_name=form.cleaned_data["admin_name"],
            admin_email=form.cleaned_data["admin_email"],
            admin_password=form.cleaned_data["admin_password"],
        )
        login(request, user)
        messages.success(request, f"{church.name} ya está lista en Emmaus.")
        return redirect("church_dashboard")
    return render(request, "core/initial_setup.html", {"form": form})


@login_required
def dashboard(request):
    """Send each person to the place where their work is.

    Event roles (campamentos) come first; the discipleship module keeps its own homes.
    """
    from events.access import accessible_events

    role_record = get_user_role(request.user)
    has_events = accessible_events(request.user).exists()
    if has_events or request.user.is_superuser or (role_record and role_record.role == Role.CHURCH_ADMIN):
        return redirect("events:list")
    if not role_record:
        if not Church.objects.exists():
            return redirect("initial_setup")
        raise PermissionDenied
    if role_record.role == Role.PARTICIPANT:
        return redirect("participant_dashboard")
    if role_record.role == Role.MENTOR:
        return redirect("mentor_dashboard")
    return redirect("church_dashboard")


def permission_denied(request, exception=None):
    return render(request, "errors/403.html", status=403)


def page_not_found(request, exception=None):
    return render(request, "errors/404.html", status=404)


def accept_invitation(request, token: str):
    invitation = get_object_or_404(Invitation.objects.select_related("church", "assigned_mentor", "path"), token=token)
    if not invitation.can_accept:
        return render(request, "core/invitation_unavailable.html", {"invitation": invitation}, status=410)
    form = AcceptInvitationForm(request.POST or None, invitation=invitation)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            first_name, last_name = full_name_to_parts(form.cleaned_data["full_name"])
            user = User.objects.create_user(
                username=invitation.email.lower(),
                email=invitation.email.lower(),
                password=form.cleaned_data["password1"],
                first_name=first_name,
                last_name=last_name,
                is_staff=invitation.role in [Role.CHURCH_ADMIN, Role.PASTOR],
            )
            assign_role(user, invitation.church, invitation.role)
            if invitation.role == Role.PARTICIPANT:
                ParticipantProfile.objects.create(
                    user=user,
                    church=invitation.church,
                    assigned_mentor=invitation.assigned_mentor,
                    preferred_contact_method=form.cleaned_data.get("preferred_contact_method")
                    or ParticipantProfile.ContactMethod.UNSPECIFIED,
                    phone_number=form.cleaned_data.get("phone_number", ""),
                    consent_accepted_at=timezone.now(),
                    emergency_disclaimer_accepted_at=timezone.now(),
                )
                enroll_participant_in_path(user, invitation.path or default_path_for_church(invitation.church))
            invitation.accepted_at = timezone.now()
            invitation.save(update_fields=["accepted_at"])
        login(request, user)
        messages.success(request, "Te damos la bienvenida a Emmaus.")
        if invitation.role == Role.PARTICIPANT:
            return redirect("participant_dashboard")
        return redirect("dashboard")
    return render(request, "core/accept_invitation.html", {"form": form, "invitation": invitation})


@role_required(Role.CHURCH_ADMIN, Role.PASTOR, Role.MENTOR)
def create_invitation(request):
    role_record = get_user_role(request.user)
    church = role_record.church
    if role_record.role != Role.CHURCH_ADMIN and not church.leaders_can_invite_participants:
        raise PermissionDenied
    form = InvitationForm(request.POST or None, church=church, inviter_role=role_record.role)
    invitation = None
    manual_link = None
    if request.method == "POST" and form.is_valid():
        invitation = form.save(commit=False)
        invitation.church = church
        invitation.invited_by = request.user
        invitation.save()
        manual_link = invite_url(request, invitation)
        email_sent = notify_invitation(request, invitation)
        if email_sent:
            messages.success(request, "La invitación se envió por correo.")
        else:
            messages.warning(request, "No se pudo enviar el correo. Copia el enlace de invitación que aparece abajo y compártelo directamente.")
    return render(
        request,
        "core/invitation_form.html",
        {"form": form, "invitation": invitation, "manual_link": manual_link},
    )


@role_required(Role.CHURCH_ADMIN, Role.PASTOR, Role.MENTOR)
def invitations_list(request):
    role_record = get_user_role(request.user)
    invitations = role_record.church.invitations.select_related("invited_by", "assigned_mentor", "path")
    if role_record.role != Role.CHURCH_ADMIN:
        invitations = invitations.filter(role=Role.PARTICIPANT)
    rows = [{"invitation": invitation, "link": invite_url(request, invitation)} for invitation in invitations[:100]]
    return render(request, "core/invitations_list.html", {"rows": rows})


@role_required(Role.CHURCH_ADMIN)
def church_settings(request):
    church = get_user_role(request.user).church
    form = ChurchSettingsForm(request.POST or None, instance=church)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Se guardó la configuración de la iglesia.")
        return redirect("church_settings")
    return render(request, "core/church_settings.html", {"form": form})


def ensure_participant_enrolled(user: User):
    summary = progress_for_participant(user)
    if summary.enrollment:
        return summary
    profile = getattr(user, "participant_profile", None)
    if not profile:
        return summary
    path = default_path_for_church(profile.church)
    if path:
        enroll_participant_in_path(user, path)
        return progress_for_participant(user)
    return summary


@role_required(Role.PARTICIPANT)
def participant_dashboard(request):
    profile = request.user.participant_profile
    summary = ensure_participant_enrolled(request.user)
    questions = request.user.participant_questions.prefetch_related("responses").order_by("-created_at")[:5]
    answers = ReflectionAnswer.objects.filter(enrollment__participant=request.user).select_related("lesson", "question")[:10]
    follow_ups = request.user.follow_ups.filter(status__in=[FollowUpRequest.Status.OPEN, FollowUpRequest.Status.IN_PROGRESS])
    return render(
        request,
        "core/participant_dashboard.html",
        {
            "profile": profile,
            "summary": summary,
            "questions": questions,
            "answers": answers,
            "follow_ups": follow_ups,
        },
    )


@role_required(Role.PARTICIPANT)
def participant_lesson(request, lesson_id: int):
    summary = ensure_participant_enrolled(request.user)
    if not summary.enrollment:
        raise Http404
    lesson = get_object_or_404(
        Lesson.objects.prefetch_related("reflection_questions"),
        pk=lesson_id,
        path=summary.enrollment.path,
        is_active=True,
    )
    progress, _ = LessonProgress.objects.get_or_create(enrollment=summary.enrollment, lesson=lesson)
    existing_answers = {
        answer.question_id: answer
        for answer in ReflectionAnswer.objects.filter(enrollment=summary.enrollment, lesson=lesson)
    }
    previous_lesson = (
        summary.enrollment.path.lessons.filter(is_active=True, order__lt=lesson.order).order_by("-order").first()
    )
    next_lesson = (
        summary.enrollment.path.lessons.filter(is_active=True, order__gt=lesson.order).order_by("order").first()
    )
    if request.method == "POST":
        for question in lesson.reflection_questions.all():
            answer_text = request.POST.get(f"question_{question.id}", "").strip()
            if answer_text:
                ReflectionAnswer.objects.update_or_create(
                    enrollment=summary.enrollment,
                    lesson=lesson,
                    question=question,
                    defaults={"answer_text": answer_text},
                )
        if request.POST.get("mark_complete"):
            progress.completed_at = timezone.now()
            progress.save(update_fields=["completed_at", "last_viewed_at"])
            if not summary.enrollment.lesson_progress.filter(completed_at__isnull=True).exists():
                summary.enrollment.completed_at = timezone.now()
                summary.enrollment.save(update_fields=["completed_at"])
            messages.success(request, "Marcaste la lección como terminada.")
            return redirect("participant_dashboard")
        messages.success(request, "Se guardó tu reflexión.")
        return redirect("participant_lesson", lesson_id=lesson.id)
    return render(
        request,
        "core/participant_lesson.html",
        {
            "lesson": lesson,
            "progress": progress,
            "existing_answers": existing_answers,
            "summary": summary,
            "previous_lesson": previous_lesson,
            "next_lesson": next_lesson,
        },
    )


@role_required(Role.PARTICIPANT)
def participant_questions(request):
    questions = request.user.participant_questions.prefetch_related("responses").order_by("-created_at")
    return render(request, "core/participant_questions.html", {"questions": questions})


@role_required(Role.PARTICIPANT)
def ask_question(request):
    profile = request.user.participant_profile
    form = ParticipantQuestionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        question = form.save(commit=False)
        question.church = profile.church
        question.participant = request.user
        question.related_enrollment = progress_for_participant(request.user).enrollment
        question.save()
        notify_question_created(question)
        messages.success(request, "Tu pregunta se envió a los líderes de tu iglesia.")
        return redirect("participant_questions")
    return render(
        request,
        "core/ask_question.html",
        {"form": form, "question_disclaimer": QUESTION_DISCLAIMER},
    )


@role_required(Role.PARTICIPANT)
def request_follow_up(request):
    profile = request.user.participant_profile
    form = ParticipantFollowUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        follow_up = FollowUpRequest.objects.create(
            church=profile.church,
            participant=request.user,
            created_by=request.user,
            assigned_to=profile.assigned_mentor,
            source=FollowUpRequest.Source.PARTICIPANT_REQUEST,
            reason=form.cleaned_data["reason"],
        )
        notify_follow_up_created(follow_up)
        messages.success(request, "Tu solicitud para platicar se envió a tu iglesia.")
        return redirect("participant_dashboard")
    return render(request, "core/request_follow_up.html", {"form": form})


@role_required(Role.MENTOR)
def mentor_dashboard(request):
    participants = list(visible_participant_profiles(request.user))
    inactive_cutoff = timezone.now() - timezone.timedelta(days=7)
    for profile in participants:
        profile.progress_summary = progress_for_participant(profile.user)
        profile.last_activity = last_activity_at(profile.user)
        profile.is_inactive = profile.last_activity is None or profile.last_activity < inactive_cutoff
        profile.open_question_count = profile.user.participant_questions.filter(
            status__in=[ParticipantQuestion.Status.OPEN, ParticipantQuestion.Status.ESCALATED]
        ).count()
        profile.open_follow_up_count = profile.user.follow_ups.filter(
            status__in=[FollowUpRequest.Status.OPEN, FollowUpRequest.Status.IN_PROGRESS]
        ).count()
        if profile.open_question_count:
            profile.suggested_action = "Responder pregunta"
        elif profile.open_follow_up_count:
            profile.suggested_action = "Continuar seguimiento"
        elif profile.is_inactive:
            profile.suggested_action = "Contactar esta semana"
        elif profile.progress_summary.enrollment:
            profile.suggested_action = "Revisar reflexiones"
        else:
            profile.suggested_action = "Sin pendientes"
    questions = visible_questions(request.user).filter(status__in=[ParticipantQuestion.Status.OPEN, ParticipantQuestion.Status.ESCALATED])
    follow_ups = FollowUpRequest.objects.filter(
        church=get_user_role(request.user).church,
        participant__participant_profile__assigned_mentor=request.user,
        status__in=[FollowUpRequest.Status.OPEN, FollowUpRequest.Status.IN_PROGRESS],
    ).select_related("participant", "assigned_to")
    priority_participants = [
        profile
        for profile in participants
        if profile.open_question_count or profile.open_follow_up_count or profile.is_inactive
    ]
    return render(
        request,
        "core/mentor_dashboard.html",
        {
            "participants": participants,
            "priority_participants": priority_participants,
            "questions": questions,
            "follow_ups": follow_ups,
        },
    )


@role_required(Role.CHURCH_ADMIN, Role.PASTOR, Role.MENTOR)
def participant_detail(request, profile_id: int):
    profile = get_object_or_404(visible_participant_profiles(request.user), pk=profile_id)
    role_record = get_user_role(request.user)
    note_form = PastoralNoteForm(prefix="note")
    follow_up_form = FollowUpRequestForm(
        prefix="follow",
        church=role_record.church,
        initial={"participant": profile.user, "assigned_to": profile.assigned_mentor},
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "note":
            note_form = PastoralNoteForm(request.POST, prefix="note")
            if note_form.is_valid():
                note = note_form.save(commit=False)
                note.church = profile.church
                note.participant = profile.user
                note.author = request.user
                note.save()
                messages.success(request, "Se guardó la nota pastoral.")
                return redirect("participant_detail", profile_id=profile.id)
        elif action == "follow":
            follow_up_form = FollowUpRequestForm(request.POST, prefix="follow", church=role_record.church)
            if follow_up_form.is_valid():
                follow_up = follow_up_form.save(commit=False)
                follow_up.church = profile.church
                follow_up.participant = profile.user
                follow_up.created_by = request.user
                follow_up.source = FollowUpRequest.Source.MANUAL
                follow_up.save()
                notify_follow_up_created(follow_up)
                messages.success(request, "Se creó el seguimiento.")
                return redirect("participant_detail", profile_id=profile.id)
    summary = progress_for_participant(profile.user)
    questions = profile.user.participant_questions.prefetch_related("responses").all()
    notes = visible_notes(request.user).filter(participant=profile.user)
    follow_ups = profile.user.follow_ups.select_related("assigned_to", "created_by")
    return render(
        request,
        "core/participant_detail.html",
        {
            "profile": profile,
            "summary": summary,
            "questions": questions,
            "notes": notes,
            "follow_ups": follow_ups,
            "note_form": note_form,
            "follow_up_form": follow_up_form,
        },
    )


@role_required(Role.CHURCH_ADMIN, Role.PASTOR, Role.MENTOR, Role.PARTICIPANT)
def question_detail(request, question_id: int):
    question = get_object_or_404(visible_questions(request.user).prefetch_related("responses"), pk=question_id)
    response_form = QuestionResponseForm(prefix="response")
    draft_form = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "respond":
            if not can_answer_question(request.user, question):
                raise PermissionDenied
            response_form = QuestionResponseForm(request.POST, prefix="response")
            if response_form.is_valid():
                response = response_form.save(commit=False)
                response.question = question
                response.responder = request.user
                response.save()
                question.status = ParticipantQuestion.Status.ANSWERED
                question.save(update_fields=["status", "updated_at"])
                notify_question_response(response)
                messages.success(request, "Tu respuesta se envió.")
                return redirect("church_question_detail" if has_pastoral_access(request.user) else "question_detail", question_id=question.id)
        elif action == "escalate":
            if not can_answer_question(request.user, question):
                raise PermissionDenied
            question.status = ParticipantQuestion.Status.ESCALATED
            question.save(update_fields=["status", "updated_at"])
            follow_up = FollowUpRequest.objects.create(
                church=question.church,
                participant=question.participant,
                created_by=request.user,
                assigned_to=question.participant.participant_profile.assigned_mentor,
                source=FollowUpRequest.Source.QUESTION_ESCALATION,
                reason=f"Pregunta escalada: {question.question_text[:300]}",
            )
            notify_question_escalated(question)
            notify_follow_up_created(follow_up)
            messages.success(request, "La pregunta se escaló a los pastores.")
            return redirect("church_question_detail" if has_pastoral_access(request.user) else "question_detail", question_id=question.id)
        elif action == "generate_ai":
            if not can_answer_question(request.user, question) or not has_leader_access(request.user):
                raise PermissionDenied
            draft, error = generate_ai_draft(question, request.user)
            if error:
                messages.warning(request, error)
            else:
                messages.success(request, "Se generó un borrador con IA. Revísalo antes de enviarlo.")
            return redirect("church_question_detail" if has_pastoral_access(request.user) else "question_detail", question_id=question.id)
        elif action == "review_ai":
            if not can_answer_question(request.user, question) or not has_leader_access(request.user):
                raise PermissionDenied
            draft = get_object_or_404(visible_ai_drafts(request.user), pk=request.POST.get("draft_id"), related_question=question)
            draft_form = AIDraftReviewForm(request.POST, instance=draft)
            if draft_form.is_valid():
                chosen_action = draft_form.cleaned_data["review_action"]
                draft = draft_form.save(commit=False)
                draft.reviewed_at = timezone.now()
                if chosen_action == "reject":
                    draft.status = AIDraft.Status.REJECTED
                    draft.save()
                    messages.success(request, "Se rechazó el borrador.")
                    return redirect("church_question_detail" if has_pastoral_access(request.user) else "question_detail", question_id=question.id)
                body = draft.edited_final_text.strip() or draft.generated_text
                draft.status = AIDraft.Status.EDITED if chosen_action == "edit" else AIDraft.Status.APPROVED
                draft.edited_final_text = body
                draft.save()
                response = QuestionResponse.objects.create(
                    question=question,
                    responder=request.user,
                    response_body=body,
                )
                question.status = ParticipantQuestion.Status.ANSWERED
                question.save(update_fields=["status", "updated_at"])
                notify_question_response(response)
                messages.success(request, "El borrador revisado se envió como tu respuesta.")
                return redirect("church_question_detail" if has_pastoral_access(request.user) else "question_detail", question_id=question.id)
    drafts = visible_ai_drafts(request.user).filter(related_question=question) if has_leader_access(request.user) else AIDraft.objects.none()
    draft_forms = [(draft, AIDraftReviewForm(instance=draft, initial={"edited_final_text": draft.generated_text})) for draft in drafts]
    return render(
        request,
        "core/question_detail.html",
        {
            "question": question,
            "response_form": response_form,
            "draft_forms": draft_forms,
            "can_answer": can_answer_question(request.user, question),
            "question_disclaimer": QUESTION_DISCLAIMER,
        },
    )


@role_required(Role.CHURCH_ADMIN, Role.PASTOR)
def church_dashboard(request):
    role_record = get_user_role(request.user)
    church = role_record.church
    participants = ParticipantProfile.objects.filter(church=church).select_related("user", "assigned_mentor")
    week_ago = timezone.now() - timezone.timedelta(days=7)
    inactive_profiles = []
    recent_active_profiles = []
    for profile in participants:
        activity = last_activity_at(profile.user)
        profile.last_activity = activity
        if activity is None or activity < week_ago:
            inactive_profiles.append(profile)
        else:
            recent_active_profiles.append(profile)
    context = {
        "church": church,
        "participant_count": participants.count(),
        "new_this_week": participants.filter(date_joined__gte=week_ago).count(),
        "waiting_for_mentor": participants.filter(assigned_mentor__isnull=True).count(),
        "open_questions": ParticipantQuestion.objects.filter(church=church, status=ParticipantQuestion.Status.OPEN).count(),
        "sensitive_questions": ParticipantQuestion.objects.filter(
            church=church,
            sensitivity_level__in=[ParticipantQuestion.Sensitivity.SENSITIVE, ParticipantQuestion.Sensitivity.PASTORAL_CARE_REQUIRED],
        ).count(),
        "escalated_questions": ParticipantQuestion.objects.filter(church=church, status=ParticipantQuestion.Status.ESCALATED).count(),
        "open_follow_ups": FollowUpRequest.objects.filter(
            church=church, status__in=[FollowUpRequest.Status.OPEN, FollowUpRequest.Status.IN_PROGRESS]
        ).count(),
        "inactive_profiles": inactive_profiles,
        "participants_without_mentor": participants.filter(assigned_mentor__isnull=True)[:6],
        "escalated_questions_list": ParticipantQuestion.objects.filter(
            church=church, status=ParticipantQuestion.Status.ESCALATED
        ).select_related("participant")[:6],
        "open_follow_ups_list": FollowUpRequest.objects.filter(
            church=church, status__in=[FollowUpRequest.Status.OPEN, FollowUpRequest.Status.IN_PROGRESS]
        ).select_related("participant", "assigned_to")[:6],
        "recent_active_profiles": recent_active_profiles[:6],
        "recent_notes": PastoralNote.objects.filter(church=church).select_related("participant", "author")[:5],
        "recent_questions": ParticipantQuestion.objects.filter(church=church).select_related("participant")[:5],
    }
    return render(request, "core/church_dashboard.html", context)


@role_required(Role.CHURCH_ADMIN, Role.PASTOR)
def participants_list(request):
    profiles = visible_participant_profiles(request.user)
    for profile in profiles:
        profile.progress_summary = progress_for_participant(profile.user)
        profile.last_activity = last_activity_at(profile.user)
    return render(request, "core/participants_list.html", {"profiles": profiles})


@role_required(Role.CHURCH_ADMIN, Role.PASTOR)
def user_list(request):
    church = get_user_role(request.user).church
    users = User.objects.filter(church_roles__church=church).select_related().order_by("church_roles__role", "email")
    return render(request, "core/user_list.html", {"users": users})


@role_required(Role.CHURCH_ADMIN, Role.PASTOR)
def assign_mentor(request, profile_id: int):
    profile = get_object_or_404(ParticipantProfile, pk=profile_id, church=get_user_role(request.user).church)
    form = AssignMentorForm(request.POST or None, church=profile.church, initial={"mentor": profile.assigned_mentor})
    if request.method == "POST" and form.is_valid():
        profile.assigned_mentor = form.cleaned_data["mentor"]
        profile.save(update_fields=["assigned_mentor"])
        messages.success(request, "Se actualizó el mentor asignado.")
        return redirect("participants_list")
    return render(request, "core/assign_mentor.html", {"profile": profile, "form": form})


@role_required(Role.CHURCH_ADMIN, Role.PASTOR)
def questions_inbox(request):
    questions = visible_questions(request.user).select_related("participant").prefetch_related("responses")
    return render(request, "core/questions_inbox.html", {"questions": questions})


@role_required(Role.CHURCH_ADMIN, Role.PASTOR)
def escalations(request):
    questions = visible_questions(request.user).filter(status=ParticipantQuestion.Status.ESCALATED).select_related("participant")
    return render(request, "core/escalations.html", {"questions": questions})


@role_required(Role.CHURCH_ADMIN, Role.PASTOR, Role.MENTOR)
def follow_up_list(request):
    role_record = get_user_role(request.user)
    follow_ups = FollowUpRequest.objects.filter(church=role_record.church).select_related(
        "participant", "assigned_to", "created_by"
    )
    if role_record.role == Role.MENTOR:
        follow_ups = follow_ups.filter(
            Q(assigned_to=request.user) | Q(participant__participant_profile__assigned_mentor=request.user)
        )
    form = FollowUpRequestForm(request.POST or None, church=role_record.church)
    if request.method == "POST" and form.is_valid():
        follow_up = form.save(commit=False)
        follow_up.church = role_record.church
        follow_up.created_by = request.user
        follow_up.source = FollowUpRequest.Source.MANUAL
        if role_record.role == Role.MENTOR and not visible_participant_profiles(request.user).filter(user=follow_up.participant).exists():
            raise PermissionDenied
        follow_up.save()
        notify_follow_up_created(follow_up)
        messages.success(request, "Se creó el seguimiento.")
        return redirect("follow_up_list" if role_record.role == Role.MENTOR else "church_follow_up_list")
    return render(request, "core/follow_up_list.html", {"follow_ups": follow_ups, "form": form})


@role_required(Role.CHURCH_ADMIN, Role.PASTOR, Role.MENTOR)
def complete_follow_up(request, follow_up_id: int):
    role_record = get_user_role(request.user)
    follow_up = get_object_or_404(FollowUpRequest, pk=follow_up_id, church=role_record.church)
    if role_record.role == Role.MENTOR and follow_up.assigned_to_id != request.user.id:
        if follow_up.participant.participant_profile.assigned_mentor_id != request.user.id:
            raise PermissionDenied
    if request.method == "POST":
        follow_up.status = FollowUpRequest.Status.COMPLETED
        follow_up.completed_at = timezone.now()
        follow_up.save(update_fields=["status", "completed_at"])
        messages.success(request, "El seguimiento quedó completado.")
    return redirect("follow_up_list" if role_record.role == Role.MENTOR else "church_follow_up_list")


@role_required(Role.CHURCH_ADMIN, Role.PASTOR)
def notes_list(request):
    notes = visible_notes(request.user).select_related("participant", "author")
    return render(request, "core/notes_list.html", {"notes": notes})


@role_required(Role.CHURCH_ADMIN, Role.PASTOR)
def path_list(request):
    paths = get_user_role(request.user).church.paths.annotate(lesson_count=Count("lessons")).order_by("-is_default", "title")
    return render(request, "core/path_list.html", {"paths": paths})


@role_required(Role.CHURCH_ADMIN, Role.PASTOR)
def path_create(request):
    church = get_user_role(request.user).church
    form = PathForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        path = form.save(commit=False)
        path.church = church
        path.created_by = request.user
        path.save()
        messages.success(request, "Se creó la ruta de discipulado.")
        return redirect("path_detail", path_id=path.id)
    return render(request, "core/path_form.html", {"form": form, "title": "Nueva ruta"})


@role_required(Role.CHURCH_ADMIN, Role.PASTOR)
def path_edit(request, path_id: int):
    path = get_object_or_404(DiscipleshipPath, pk=path_id, church=get_user_role(request.user).church)
    form = PathForm(request.POST or None, instance=path)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Se guardó la ruta de discipulado.")
        return redirect("path_detail", path_id=path.id)
    return render(request, "core/path_form.html", {"form": form, "title": "Editar ruta", "path": path})


@role_required(Role.CHURCH_ADMIN, Role.PASTOR)
def path_detail(request, path_id: int):
    path = get_object_or_404(
        DiscipleshipPath.objects.prefetch_related("lessons__reflection_questions"),
        pk=path_id,
        church=get_user_role(request.user).church,
    )
    return render(request, "core/path_detail.html", {"path": path})


@role_required(Role.CHURCH_ADMIN, Role.PASTOR)
def lesson_create(request, path_id: int):
    path = get_object_or_404(DiscipleshipPath, pk=path_id, church=get_user_role(request.user).church)
    next_order = (path.lessons.aggregate(max_order=Max("order"))["max_order"] or 0) + 1
    form = LessonForm(request.POST or None, initial={"order": next_order})
    if request.method == "POST" and form.is_valid():
        lesson = form.save(commit=False)
        lesson.path = path
        lesson.save()
        messages.success(request, "Se creó la lección.")
        return redirect("path_detail", path_id=path.id)
    return render(request, "core/lesson_form.html", {"form": form, "path": path, "title": "Nueva lección"})


@role_required(Role.CHURCH_ADMIN, Role.PASTOR)
def lesson_edit(request, lesson_id: int):
    lesson = get_object_or_404(Lesson, pk=lesson_id, path__church=get_user_role(request.user).church)
    form = LessonForm(request.POST or None, instance=lesson)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Se guardó la lección.")
        return redirect("path_detail", path_id=lesson.path_id)
    return render(request, "core/lesson_form.html", {"form": form, "path": lesson.path, "lesson": lesson, "title": "Editar lección"})


@role_required(Role.CHURCH_ADMIN, Role.PASTOR)
def reflection_question_create(request, lesson_id: int):
    lesson = get_object_or_404(Lesson, pk=lesson_id, path__church=get_user_role(request.user).church)
    next_order = lesson.reflection_questions.count() + 1
    form = ReflectionQuestionForm(request.POST or None, initial={"order": next_order})
    if request.method == "POST" and form.is_valid():
        question = form.save(commit=False)
        question.lesson = lesson
        question.save()
        messages.success(request, "Se agregó la pregunta de reflexión.")
        return redirect("path_detail", path_id=lesson.path_id)
    return render(request, "core/reflection_question_form.html", {"form": form, "lesson": lesson})


class ThrottledLoginView(LoginView):
    """Login with a per-account attempt limit shared by all workers (8 tries per 15 minutes)."""

    template_name = "registration/login.html"

    def post(self, request, *args, **kwargs):
        from events.services import throttle

        username = (request.POST.get("username") or "").strip().lower()
        key = f"login:{username}"
        wait = throttle.consume(key, limit=8, window_seconds=60 * 60, block_seconds=5 * 60, max_block_seconds=60 * 60) if username else 0
        if wait:
            form = self.get_form()
            form.is_valid()
            form.errors.clear()
            form.add_error(None, throttle.wait_message(wait))
            return self.form_invalid(form)
        response = super().post(request, *args, **kwargs)
        if request.user.is_authenticated:
            throttle.reset(key)
        return response


class ThrottledPasswordResetView(PasswordResetView):
    """At most 3 reset e-mails per address per hour (prevents mail flooding)."""

    template_name = "registration/password_reset_form.html"
    form_class = TuPasswordResetForm
    email_template_name = "registration/password_reset_email.txt"
    subject_template_name = "registration/password_reset_subject.txt"
    success_url = "/cuenta/olvide-mi-contrasena/enviado/"

    def form_valid(self, form):
        from events.services import throttle

        email = form.cleaned_data["email"].strip().lower()
        if throttle.consume(f"reset:{email}", limit=3, window_seconds=60 * 60, block_seconds=60 * 60):
            return redirect(self.get_success_url())  # same page as success: no hint either way
        return super().form_valid(form)
