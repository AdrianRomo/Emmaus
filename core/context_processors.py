from django.urls import reverse
from django.utils.functional import SimpleLazyObject

from . import tenancy
from .access import LEADER_ROLES, PASTORAL_ROLES, get_role_context
from .models import Role, UserChurchRole
from .services import progress_for_participant


def _role_context_in(user, church) -> dict:
    """``core.access.get_role_context`` for the church of this request, when it is not the church of
    the account's first role: the role the account holds *there* (none for a person without one)."""
    record = UserChurchRole.objects.filter(user=user, church=church).select_related("church").first()
    role = record.role if record else None
    return {
        "current_role_record": record,
        "current_church": church,
        "current_role": role,
        "is_church_admin": role == Role.CHURCH_ADMIN,
        "is_pastor": role == Role.PASTOR,
        "is_mentor": role == Role.MENTOR,
        "is_participant": role == Role.PARTICIPANT,
        "is_leader": role in LEADER_ROLES if role else False,
        "is_pastoral_leader": role in PASTORAL_ROLES if role else False,
    }


def current_role(request):
    # brand_church: whose logo the bar of base.html shows (core.templatetags.brand). Event shells use event.church.
    if not request.user.is_authenticated:
        church = tenancy.current_church(request)
        # The public pages wear the redesigned look when the church they speak for uses the community module.
        from people.flags import enabled

        return {"theme_church": church, "brand_church": church, "ui_v2": enabled(church, "community")}
    context = get_role_context(request.user)
    # The church of this request (core.tenancy): the one the account chose among its own, else its first.
    where = tenancy.resolve(request, role_church=context["current_church"])
    if where.elsewhere:
        # Working in another church than the first role's: the bar, the logo and the colours are that church's, with
        # the role held there. The discipleship screens still follow the first role (core/access.py), so their
        # links are not offered here and their views answer 404 until the person goes back (core.views.role_required).
        context = _role_context_in(request.user, where.church)
        context["church_elsewhere"] = True  # "Volver a Configuración" would lead to the other church's settings
    if where.others:
        context["church_choices"] = where.others  # the switcher: only for an account of more than one church
    role = context.get("current_role")
    nav_items = []

    if where.elsewhere:
        pass
    elif role == Role.PARTICIPANT:
        nav_items = [
            {"label": "Mi camino", "url": reverse("participant_dashboard"), "names": ["participant_dashboard"]},
            {"label": "Preguntas", "url": reverse("participant_questions"), "names": ["participant_questions", "participant_question_detail", "ask_question"]},
            {"label": "Pedir una conversación", "url": reverse("request_follow_up"), "names": ["request_follow_up"]},
        ]
        # "Lección actual" only while there is one: once the path is done, current_progress is its last lesson, finished.
        current = progress_for_participant(request.user).current_progress
        if current and not current.completed_at:
            nav_items.insert(
                1,
                {
                    "label": "Lección actual",
                    "url": reverse("participant_lesson", kwargs={"lesson_id": current.lesson_id}),
                    "names": ["participant_lesson"],
                },
            )
    elif role == Role.MENTOR:
        # One item per page: the mentor's people and their questions are on the home and open from it.
        nav_items = [
            {"label": "Inicio", "url": reverse("mentor_dashboard"), "names": ["mentor_dashboard", "participant_detail", "question_detail"]},
            {"label": "Seguimientos", "url": reverse("follow_up_list"), "names": ["follow_up_list"]},
        ]
    elif role in (Role.PASTOR, Role.CHURCH_ADMIN) and not context["current_church"].discipleship_enabled:
        # The module is off (Church.discipleship_enabled): none of its pages in the bar. The admin keeps the church settings,
        # where the module is switched on.
        if role == Role.CHURCH_ADMIN:
            nav_items = [{"label": "Configuración", "url": reverse("church_settings"), "names": ["church_settings", "user_list"]}]
    elif role == Role.PASTOR:
        nav_items = [
            {"label": "Inicio", "url": reverse("church_dashboard"), "names": ["church_dashboard"]},
            {"label": "Participantes", "url": reverse("participants_list"), "names": ["participants_list", "participant_detail", "assign_mentor"]},
            {"label": "Preguntas", "url": reverse("questions_inbox"), "names": ["questions_inbox", "church_question_detail"]},
            {"label": "Para el pastor", "url": reverse("escalations"), "names": ["escalations"]},
            {"label": "Seguimientos", "url": reverse("church_follow_up_list"), "names": ["church_follow_up_list"]},
            {"label": "Notas pastorales", "url": reverse("notes_list"), "names": ["notes_list"]},
        ]
    elif role == Role.CHURCH_ADMIN:
        nav_items = [
            {"label": "Inicio", "url": reverse("church_dashboard"), "names": ["church_dashboard"]},
            {"label": "Participantes", "url": reverse("participants_list"), "names": ["participants_list", "participant_detail", "assign_mentor"]},
            {"label": "Invitaciones", "url": reverse("invitations_list"), "names": ["invitations_list", "create_invitation"]},
            {
                "label": "Rutas",
                "url": reverse("path_list"),
                "names": ["path_list", "path_detail", "path_create", "path_edit", "lesson_create", "lesson_edit", "reflection_question_create"],
            },
            {"label": "Preguntas", "url": reverse("questions_inbox"), "names": ["questions_inbox", "church_question_detail"]},
            {"label": "Seguimientos", "url": reverse("church_follow_up_list"), "names": ["church_follow_up_list"]},
            {"label": "Configuración", "url": reverse("church_settings"), "names": ["church_settings", "user_list"]},
        ]

    from events.access import accessible_events

    context["app_nav_label"] = "Discipulado" if nav_items else "Principal"
    module_items = nav_items
    nav_items = _community_nav(request, context, where.church) + nav_items
    context["show_events_link"] = role in (Role.CHURCH_ADMIN, Role.PASTOR) or accessible_events(request.user).exists()
    if context.get("community_on"):
        # The redesigned shell (core/shell.py) replaces the bar on every page of base.html; the event's own
        # shells keep their bar for now and wear the new tokens (docs/v2/SCREEN_MATRIX.md).
        from .shell import build_shell

        context["ui_v2"] = True
        context["shell"] = build_shell(
            request, context["shell_church"], module_items, context["show_events_link"], settings_link=not where.elsewhere
        )
    context["app_nav_items"] = nav_items
    context["active_url_name"] = getattr(getattr(request, "resolver_match", None), "url_name", "")
    context["theme_church"] = context.get("current_church")
    # No church role (someone who only works in an event): the public pages' church, looked up only if drawn.
    context["brand_church"] = context["theme_church"] or SimpleLazyObject(tenancy.first_active_church)
    return context


def _community_nav(request, context, church) -> list[dict]:
    """"Mi iglesia" and "Grupos" lead the bar while the account's church uses the community module
    (decision 0004); with the module off this returns nothing and the bar is what it always was. A
    person of the church with no church role gets the church's name in the bar from here."""
    from people.flags import enabled

    from .authz import Action, has_scope

    if not enabled(church, "community"):
        return []
    if context.get("current_church") is None:
        context["current_church"] = church
    context["app_nav_label"] = "Principal"
    context["community_on"] = True  # templates/core/ayuda.html adds the community tasks
    context["shell_church"] = church  # the church the community views act in (core.authz), not the first role's
    items = [
        {"label": "Mi iglesia", "url": reverse("community:home"), "views": ["community:home"]},
        {
            "label": "Grupos",
            "url": reverse("community:groups"),
            "views": [
                "community:groups", "community:group_detail", "community:group_people", "community:group_leave",
                "community:member_remove", "community:group_admin_list", "community:group_create",
                "community:group_edit", "community:group_archive",
            ],
        },
    ]
    if enabled(church, "people") and has_scope(request.user, church, "church", church.pk, Action.MANAGE):
        items.append({"label": "Personas", "url": reverse("people:list"), "views": [
            "people:list", "people:create", "people:detail", "people:edit", "people:from_event_list", "people:from_event",
            "people:grant_access", "people:access_link",
        ]})
    # The bar marks the current page by url name; these routes are namespaced, so they are marked here.
    view_name = getattr(getattr(request, "resolver_match", None), "view_name", "")
    for item in items:
        item["active"] = view_name in item.pop("views")
    return items
