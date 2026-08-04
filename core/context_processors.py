from django.urls import reverse

from .access import get_role_context
from .models import Church, Role
from .services import progress_for_participant


def current_role(request):
    if not request.user.is_authenticated:
        return {"theme_church": Church.objects.filter(active=True).order_by("created_at").first()}
    context = get_role_context(request.user)
    role = context.get("current_role")
    nav_items = []

    if role == Role.PARTICIPANT:
        nav_items = [
            {"label": "My Journey", "url": reverse("participant_dashboard"), "names": ["participant_dashboard"]},
            {"label": "Questions", "url": reverse("participant_questions"), "names": ["participant_questions", "participant_question_detail", "ask_question"]},
            {"label": "Request Conversation", "url": reverse("request_follow_up"), "names": ["request_follow_up"]},
            {"label": "Account", "url": reverse("participant_dashboard"), "names": []},
        ]
        summary = progress_for_participant(request.user)
        if summary.current_progress:
            nav_items.insert(
                1,
                {
                    "label": "Current Lesson",
                    "url": reverse("participant_lesson", kwargs={"lesson_id": summary.current_progress.lesson_id}),
                    "names": ["participant_lesson"],
                },
            )
    elif role == Role.MENTOR:
        nav_items = [
            {"label": "Dashboard", "url": reverse("mentor_dashboard"), "names": ["mentor_dashboard"]},
            {"label": "My Participants", "url": f"{reverse('mentor_dashboard')}#assigned-participants", "names": ["participant_detail"]},
            {"label": "Questions", "url": f"{reverse('mentor_dashboard')}#open-questions", "names": ["question_detail"]},
            {"label": "Follow-ups", "url": reverse("follow_up_list"), "names": ["follow_up_list"]},
            {"label": "Notes", "url": f"{reverse('mentor_dashboard')}#assigned-participants", "names": []},
        ]
    elif role == Role.PASTOR:
        nav_items = [
            {"label": "Dashboard", "url": reverse("church_dashboard"), "names": ["church_dashboard"]},
            {"label": "Participants", "url": reverse("participants_list"), "names": ["participants_list", "participant_detail", "assign_mentor"]},
            {"label": "Questions", "url": reverse("questions_inbox"), "names": ["questions_inbox", "church_question_detail"]},
            {"label": "Escalations", "url": reverse("escalations"), "names": ["escalations"]},
            {"label": "Follow-ups", "url": reverse("church_follow_up_list"), "names": ["church_follow_up_list"]},
            {"label": "Notes", "url": reverse("notes_list"), "names": ["notes_list"]},
        ]
    elif role == Role.CHURCH_ADMIN:
        nav_items = [
            {"label": "Dashboard", "url": reverse("church_dashboard"), "names": ["church_dashboard"]},
            {"label": "Participants", "url": reverse("participants_list"), "names": ["participants_list", "participant_detail", "assign_mentor"]},
            {"label": "Invitations", "url": reverse("invitations_list"), "names": ["invitations_list", "create_invitation"]},
            {"label": "Paths", "url": reverse("path_list"), "names": ["path_list", "path_detail", "path_create", "path_edit"]},
            {"label": "Lessons", "url": reverse("path_list"), "names": ["lesson_create", "lesson_edit", "reflection_question_create"]},
            {"label": "Questions", "url": reverse("questions_inbox"), "names": ["questions_inbox", "church_question_detail"]},
            {"label": "Follow-ups", "url": reverse("church_follow_up_list"), "names": ["church_follow_up_list"]},
            {"label": "Settings", "url": reverse("church_settings"), "names": ["church_settings", "user_list"]},
        ]

    context["app_nav_items"] = nav_items
    context["active_url_name"] = getattr(getattr(request, "resolver_match", None), "url_name", "")
    context["theme_church"] = context.get("current_church")
    return context
