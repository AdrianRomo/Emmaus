from django.urls import reverse
from django.utils.functional import SimpleLazyObject

from .access import get_role_context
from .models import Church, Role
from .services import progress_for_participant


def first_active_church():
    """The church the public pages speak for (the landing page names the same one)."""
    return Church.objects.filter(active=True).order_by("created_at").first()


def current_role(request):
    # brand_church: whose logo the bar of base.html shows (core.templatetags.brand). Event shells use event.church.
    if not request.user.is_authenticated:
        church = first_active_church()
        return {"theme_church": church, "brand_church": church}
    context = get_role_context(request.user)
    role = context.get("current_role")
    nav_items = []

    if role == Role.PARTICIPANT:
        nav_items = [
            {"label": "Mi camino", "url": reverse("participant_dashboard"), "names": ["participant_dashboard"]},
            {"label": "Preguntas", "url": reverse("participant_questions"), "names": ["participant_questions", "participant_question_detail", "ask_question"]},
            {"label": "Pedir una conversación", "url": reverse("request_follow_up"), "names": ["request_follow_up"]},
            {"label": "Mi cuenta", "url": reverse("participant_dashboard"), "names": []},
        ]
        summary = progress_for_participant(request.user)
        if summary.current_progress:
            nav_items.insert(
                1,
                {
                    "label": "Lección actual",
                    "url": reverse("participant_lesson", kwargs={"lesson_id": summary.current_progress.lesson_id}),
                    "names": ["participant_lesson"],
                },
            )
    elif role == Role.MENTOR:
        nav_items = [
            {"label": "Inicio", "url": reverse("mentor_dashboard"), "names": ["mentor_dashboard"]},
            {"label": "Mis participantes", "url": f"{reverse('mentor_dashboard')}#assigned-participants", "names": ["participant_detail"]},
            {"label": "Preguntas", "url": f"{reverse('mentor_dashboard')}#open-questions", "names": ["question_detail"]},
            {"label": "Seguimientos", "url": reverse("follow_up_list"), "names": ["follow_up_list"]},
            {"label": "Notas pastorales", "url": f"{reverse('mentor_dashboard')}#assigned-participants", "names": []},
        ]
    elif role == Role.PASTOR:
        nav_items = [
            {"label": "Inicio", "url": reverse("church_dashboard"), "names": ["church_dashboard"]},
            {"label": "Participantes", "url": reverse("participants_list"), "names": ["participants_list", "participant_detail", "assign_mentor"]},
            {"label": "Preguntas", "url": reverse("questions_inbox"), "names": ["questions_inbox", "church_question_detail"]},
            {"label": "Preguntas escaladas", "url": reverse("escalations"), "names": ["escalations"]},
            {"label": "Seguimientos", "url": reverse("church_follow_up_list"), "names": ["church_follow_up_list"]},
            {"label": "Notas pastorales", "url": reverse("notes_list"), "names": ["notes_list"]},
        ]
    elif role == Role.CHURCH_ADMIN:
        nav_items = [
            {"label": "Inicio", "url": reverse("church_dashboard"), "names": ["church_dashboard"]},
            {"label": "Participantes", "url": reverse("participants_list"), "names": ["participants_list", "participant_detail", "assign_mentor"]},
            {"label": "Invitaciones", "url": reverse("invitations_list"), "names": ["invitations_list", "create_invitation"]},
            {"label": "Rutas", "url": reverse("path_list"), "names": ["path_list", "path_detail", "path_create", "path_edit"]},
            {"label": "Lecciones", "url": reverse("path_list"), "names": ["lesson_create", "lesson_edit", "reflection_question_create"]},
            {"label": "Preguntas", "url": reverse("questions_inbox"), "names": ["questions_inbox", "church_question_detail"]},
            {"label": "Seguimientos", "url": reverse("church_follow_up_list"), "names": ["church_follow_up_list"]},
            {"label": "Configuración", "url": reverse("church_settings"), "names": ["church_settings", "user_list"]},
        ]

    from events.access import accessible_events

    context["show_events_link"] = role in (Role.CHURCH_ADMIN, Role.PASTOR) or accessible_events(request.user).exists()
    context["app_nav_items"] = nav_items
    context["active_url_name"] = getattr(getattr(request, "resolver_match", None), "url_name", "")
    context["theme_church"] = context.get("current_church")
    # No church role (someone who only works in an event): the public pages' church, looked up only if drawn.
    context["brand_church"] = context["theme_church"] or SimpleLazyObject(first_active_church)
    return context
