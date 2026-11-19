"""The application shell of the redesigned experience ("Santuario", docs/v2/DESIGN_SYSTEM.md).

One navigation for every signed-in page of a church that uses the community module: a side rail on
wide screens, a top bar with bottom tabs on phones, and the "Más" panel that holds everything.
It is built from data here, never in a template, so the three places always agree.

Sections, in order: what is everybody's (Mi iglesia, Avisos, Eventos, Grupos), what the person is
responsible for ("A tu cargo": "Panorama" for the pastor and the administration, the groups they
guide and the events they work in), the
discipleship module's own links, and the administration. A link is
listed only when the view behind it would answer: this module decides what is *shown*, the views
decide what is *allowed* (decision 0004, rule 4).
"""

from django.urls import reverse
from django.utils import timezone

from .authz import Action, has_scope

GROUP_VIEWS = {
    "community:groups", "community:group_detail", "community:group_leave",
}
GROUP_WORK_VIEWS = {
    "community:group_people", "community:member_remove", "community:group_meetings", "community:meeting_record",
}
GROUP_ADMIN_VIEWS = {
    "community:group_admin_list", "community:group_create", "community:group_edit", "community:group_archive",
}
AGENDA_VIEWS = {
    "agenda:list", "agenda:detail", "agenda:cancel", "agenda:register", "agenda:accept_offer", "agenda:leave_waiting",
    "feedback:give", "agenda:series",
}
AGENDA_ADMIN_VIEWS = {
    "agenda:admin_list", "agenda:page_edit", "agenda:registrations", "agenda:create", "agenda:event_edit", "agenda:duplicate",
    "agenda:walkup", "agenda:confirm_age", "agenda:guest_age", "agenda:annul", "agenda:consent", "agenda:consent_revoke", "agenda:guest_promote", "agenda:faq", "agenda:faq_edit",
    "agenda:faq_delete", "agenda:schedule", "agenda:schedule_edit", "agenda:schedule_delete", "feedback:results",
    "agenda:waiting_remove", "agenda:visit_follow_up",
    "agenda:series_create", "agenda:series_admin", "agenda:series_edit",
}
DEVOTIONAL_VIEWS = {"resources:devotional_today", "resources:devotional_list", "resources:devotional_detail"}
PLAN_VIEWS = {
    "resources:plan_list", "resources:plan_detail", "resources:plan_start", "resources:plan_leave", "resources:plan_day",
    "resources:plan_day_read",
}
RESOURCE_ADMIN_VIEWS = {
    "resources:devotional_admin", "resources:devotional_create", "resources:devotional_edit",
    "resources:media_admin", "resources:media_create", "resources:media_edit", "resources:media_retire",
    "resources:plan_admin", "resources:plan_create", "resources:plan_edit", "resources:plan_days", "resources:plan_day_create",
    "resources:plan_day_edit", "resources:plan_day_delete", "resources:plan_publish", "resources:plan_retire",
}
# The pastoral approval of content: the pastor's own place; for the administration it belongs to "Devocionales y recursos".
APPROVAL_VIEWS = {"resources:approval_queue", "resources:approval_review", "resources:approval_approve", "resources:approval_return"}
SERVING_VIEWS = {"serving:home", "serving:swap_create", "serving:plan"}
SERVING_ADMIN_VIEWS = {
    "serving:team_admin", "serving:team_create", "serving:team", "serving:team_edit", "serving:team_archive",
    "serving:member_remove", "serving:date_create", "serving:date_detail", "serving:date_delete",
    "serving:organize", "serving:rotation",
}
OVERVIEW_VIEWS = {"overview:panorama"}
CARE_VIEWS = {"care:mine", "care:detail"}
CARE_ADMIN_VIEWS = {"care:admin_list", "care:create", "care:edit", "care:cancel"}
# Rooms and equipment: one destination, "Espacios". The administration's pages mark the same link.
# General forms (issue #92): answering one, the staff's screens, and «Pendientes».
FORM_VIEWS = {"formularios:list", "formularios:fill", "formularios:thanks"}
FORM_ADMIN_VIEWS = {
    "formularios:manage", "formularios:create", "formularios:detail", "formularios:edit", "formularios:question_create",
    "formularios:question_edit", "formularios:publish", "formularios:close", "formularios:readers", "formularios:submissions",
    "formularios:submission", "formularios:task_assign", "formularios:add_person", "formularios:loose_tasks",
}
SPACE_VIEWS = {
    "espacios:list", "espacios:week", "espacios:request", "espacios:cancel", "espacios:queue", "espacios:approve",
    "espacios:reject", "espacios:manage", "espacios:space_create", "espacios:space_edit", "espacios:usage",
}
PEOPLE_VIEWS = {
    "people:list", "people:create", "people:detail", "people:edit", "people:from_event_list", "people:from_event",
    "people:grant_access", "people:access_link",
    "people:duplicates", "people:merge_preview", "people:merge_detail",
    "people:stage", "people:tags", "people:tag_delete", "people:person_tags",
    "people:export", "people:import", "people:import_detail", "people:import_undo",
}
# Prayer requests: the writer's pages, and the pastors' (the church's switch included).
PRAYER_VIEWS = {"oracion:mine", "oracion:new", "oracion:delete"}
# «Quién puede qué» and the administration's task for a request to remove data (cuenta_datos, issue #98)
POWER_VIEWS = {"cuenta_datos:powers"}
DATA_REQUEST_VIEWS = {"cuenta_datos:requests", "cuenta_datos:request"}
PRAYER_READER_VIEWS = {
    "oracion:inbox", "oracion:read", "oracion:mark_read", "oracion:reply", "oracion:mark_talked", "oracion:reader_email",
    "oracion:terms",
}
# The directory between members: the list, and the church's switch (a pastor's). A person's own choice is part of "Mis datos".
DIRECTORY_VIEWS = {"directorio:list", "directorio:policy"}
# Icons for the discipleship module's links, by url name (core.context_processors builds those links): one each, so
# two links of the same rail never share a drawing.
MODULE_ICONS = {
    "church_settings": "ajustes", "participants_list": "personas", "participant_dashboard": "camino",
    "mentor_dashboard": "guia", "church_dashboard": "reporte", "path_list": "lectura",
    "follow_up_list": "acompanar", "church_follow_up_list": "acompanar",
}


# The drawing of a folded section's line (a long rail folds the sections that do not hold the current page)
SECTION_ICONS = {"Para crecer": "devocional", "A tu cargo": "guia", "Discipulado": "camino", "Administración": "ajustes"}


def _item(label, url, icon, active=False, badge=0, badge_label=""):
    return {"label": label, "url": url, "icon": icon, "active": active, "badge": badge, "badge_label": badge_label}


def build_shell(request, church, module_items, show_events: bool) -> dict:
    """The rail's sections and the phone's tabs for ``request``. ``module_items`` are the
    discipleship links the bar would have shown (label, url, names)."""
    from groups.access import managed_group_ids
    from groups.models import Group, GroupJoinRequest
    from care import services as care_services
    from espacios import access as spaces_access
    from espacios import services as spaces_services
    from espacios.models import Space
    from notices import services as notices_services
    from overview.access import can_see_overview
    from serving import services as serving_services
    from people.flags import enabled
    from people.models import Person

    match = getattr(request, "resolver_match", None)
    view = getattr(match, "view_name", "") or ""
    url_name = getattr(match, "url_name", "") or ""
    kwargs = getattr(match, "kwargs", {}) or {}
    is_admin = has_scope(request.user, church, "church", church.pk, Action.MANAGE)

    managed = list(
        Group.objects.filter(pk__in=managed_group_ids(request.user, church), church=church, status=Group.Status.ACTIVE)
        .order_by("name", "id")
    )
    pending = {}
    if managed:
        rows = GroupJoinRequest.objects.filter(group__in=managed, status=GroupJoinRequest.Status.PENDING)
        for group_id in rows.values_list("group_id", flat=True):
            pending[group_id] = pending.get(group_id, 0) + 1
    working_on = kwargs.get("group_id") if view in GROUP_WORK_VIEWS else None
    in_managed = working_on in {group.pk for group in managed}

    person = Person.objects.filter(church=church, user=request.user, merged_into__isnull=True).first()
    unread = notices_services.unread_count(person, is_admin=is_admin) if person else 0
    main = [
        _item("Mi iglesia", reverse("community:home"), "inicio", view in {"community:home", "community:profile", "directorio:mine"}),
        _item("Avisos", reverse("notices:list"), "avisos", view.startswith("notices:"), unread,
              "aviso nuevo" if unread == 1 else "avisos nuevos"),
        _item("Eventos", reverse("agenda:list"), "eventos", view in AGENDA_VIEWS),
        _item("Grupos", reverse("community:groups"), "grupos",
              view in GROUP_VIEWS or (view in GROUP_WORK_VIEWS and not in_managed and not is_admin)),
    ]
    reading = [
        _item("Devocional", reverse("resources:devotional_today"), "devocional", view in DEVOTIONAL_VIEWS),
        _item("Planes de lectura", reverse("resources:plan_list"), "plan", view in PLAN_VIEWS),
        _item("Recursos", reverse("resources:media_list"), "recursos", view == "resources:media_list"),
    ]
    # A quiet door, never a number: only for who may write one today, or still has one to read or delete.
    from oracion import shell as prayer_shell

    if person and prayer_shell.shows_writer_link(church, person):
        reading.append(_item("Pedir oración", reverse("oracion:mine"), "servir", view in PRAYER_VIEWS))
    sections = [{"label": "", "items": main}, {"label": "Para crecer", "items": reading}]

    # What the person is responsible for: the groups they guide, and the events they work in (the camp's team).
    serving = _item("Eventos donde sirves", reverse("events:list") + "?todos=1", "equipo", view in {"events:list", "events:create"})
    charge = [
        _item(group.name, reverse("community:group_people", args=[group.pk]), "guia", working_on == group.pk,
              pending.get(group.pk, 0),
              "solicitud por decidir" if pending.get(group.pk) == 1 else "solicitudes por decidir")
        for group in managed
    ]
    # The pastor (who may not be an administrator) and the administration look after the whole church: its totals.
    if can_see_overview(request.user, church):
        charge.insert(0, _item("Panorama", reverse("overview:panorama"), "camino", view in OVERVIEW_VIEWS))
        if not is_admin:
            # Of the two roles that see Panorama, the one that is not the administration is the pastor, who approves
            # what the administration prepared before it is published (resources.approvals). A number of pieces of
            # content, never of people.
            from resources import approvals

            to_approve = approvals.pending_count(church)
            charge.insert(1, _item("Aprobación pastoral", reverse("resources:approval_queue"), "devocional", view in APPROVAL_VIEWS,
                                   to_approve, "contenido por aprobar" if to_approve == 1 else "contenidos por aprobar"))
        # The same two roles read who holds a power in the church (cuenta_datos.views.powers asks again).
        charge.insert(1 if is_admin else 2, _item("Quién puede qué", reverse("cuenta_datos:powers"), "candado", view in POWER_VIEWS))
    # Only a pastor of this church (asked again here, not inferred): the requests not yet marked as read.
    unread_prayers = prayer_shell.reader_badge(request.user, church)
    if unread_prayers is not None:
        charge.append(_item("Peticiones de oración", reverse("oracion:inbox"), "servir", view in PRAYER_READER_VIEWS,
                            unread_prayers, "petición sin leer" if unread_prayers == 1 else "peticiones sin leer"))
    if show_events:
        charge.append(serving)
    if person and serving_services.has_teams(person):
        waiting = serving_services.pending_count(person)
        charge.append(_item("Servir", reverse("serving:home"), "servir", view in SERVING_VIEWS, waiting,
                            "fecha por confirmar" if waiting == 1 else "fechas por confirmar"))
    accompanying = care_services.open_count(person) if person else 0
    if accompanying:
        charge.append(_item("Acompañar", reverse("care:mine"), "acompanar", view in CARE_VIEWS, accompanying,
                            "persona que acompañas" if accompanying == 1 else "personas que acompañas"))
    # Who is responsible for something may ask for a room, once the church has listed one. (The administration
    # has its own link below; everybody else may read the weeks but is not sent there.)
    if not is_admin and Space.objects.filter(church=church, active=True).exists() and spaces_access.can_request(
        request.user, church, guides_group=bool(managed)
    ):
        charge.append(_item("Espacios", reverse("espacios:list"), "equipo", view in SPACE_VIEWS))
    # General forms: who reads the answers of one through a group or a team gets the door to them; and «Pendientes»,
    # everything that waits for this person in one list, when something does (always for the administration).
    from formularios import access as forms_access
    from formularios import queue as pending_queue
    from formularios import views as forms_views

    if not is_admin and forms_access.readable_form_ids(request.user, church):
        charge.append(_item("Respuestas de formularios", reverse("formularios:manage"), "recursos", view in FORM_ADMIN_VIEWS))
    waiting_all = pending_queue.count(request.user, church, person, is_church_admin=is_admin, care_mine=accompanying,
                                      group_requests=sum(pending.values()))
    if waiting_all or is_admin:
        charge.insert(0, _item("Pendientes", reverse("formularios:queue"), "plan", view == "formularios:queue" or view in DATA_REQUEST_VIEWS, waiting_all,
                               "pendiente" if waiting_all == 1 else "pendientes"))
    # A person whose date of birth says they are under 18 gets a 404 from the forms (formularios.views): no link to it.
    if forms_views.open_member_form_count(church) and not _minor_by_birth_date(person):
        main.append(_item("Formularios", reverse("formularios:list"), "recursos", view in FORM_VIEWS))
    # The directory between members: listed only while the church's switch is on AND this person may read it (who
    # is in it, the administration, a pastor). While it is off, a pastor alone gets the door to the switch.
    from people import directory as member_directory

    if member_directory.shows_link(request.user, church, person):
        main.append(_item("Directorio", reverse("directorio:list"), "lista", view in DIRECTORY_VIEWS))
    elif not member_directory.is_on(church) and member_directory.is_pastor(request.user, church):
        charge.append(_item("Directorio entre miembros", reverse("directorio:policy"), "lista", view in DIRECTORY_VIEWS))
    if charge:
        sections.append({"label": "A tu cargo", "items": charge})

    admin_urls = set()
    admin = []
    if is_admin:
        if enabled(church, "people"):
            admin.append(_item("Personas", reverse("people:list"), "personas", view in PEOPLE_VIEWS))
        admin.append(_item(
            "Grupos de la iglesia", reverse("community:group_admin_list"), "grupos",
            view in GROUP_ADMIN_VIEWS or (view in GROUP_WORK_VIEWS and not in_managed),
        ))
        admin.append(_item("Eventos de la iglesia", reverse("agenda:admin_list"), "eventos", view in AGENDA_ADMIN_VIEWS))
        admin.append(_item("Equipos de la iglesia", reverse("serving:team_admin"), "servir", view in SERVING_ADMIN_VIEWS))
        asked = spaces_services.pending_count(church)
        admin.append(_item("Espacios y equipo", reverse("espacios:list"), "equipo", view in SPACE_VIEWS, asked,
                           "solicitud de reserva por decidir" if asked == 1 else "solicitudes de reserva por decidir"))
        loose = care_services.unassigned_count(church, person)
        admin.append(_item("Seguimiento de personas", reverse("care:admin_list"), "acompanar",
                           view in CARE_ADMIN_VIEWS or (view in CARE_VIEWS and not accompanying), loose,
                           "seguimiento sin responsable" if loose == 1 else "seguimientos sin responsable"))
        admin.append(_item("Formularios de la iglesia", reverse("formularios:manage"), "recursos", view in FORM_ADMIN_VIEWS))
        admin.append(_item("Devocionales y recursos", reverse("resources:devotional_admin"), "devocional",
                           view in RESOURCE_ADMIN_VIEWS or view in APPROVAL_VIEWS))
        # "Configuración" acts in the church of the request, like every link here (core.views.role_required).
        admin.append(_item("Configuración", reverse("church_settings"), "ajustes", url_name in {"church_settings", "user_list", "church_policies"} or view.startswith("sitio:")))
        admin_urls = {item["url"] for item in admin}

    module = [
        _item(item["label"], item["url"], MODULE_ICONS.get(item["names"][0], "camino"), url_name in item["names"])
        for item in module_items if item["url"] not in admin_urls
    ]
    if module:
        sections.append({"label": "Discipulado", "items": module})
    if admin:
        sections.append({"label": "Administración", "items": admin})

    # A long rail (more than 8 places: a guide's 9, an administration's 16) does not fit a laptop's height (768px): each labelled section that does not
    # hold the current page folds into one line (its label, with a dot when something inside waits); the section of
    # the current page stays open. The event's own rail folds the church apart (shell.html, event_nav).
    if sum(len(section["items"]) for section in sections) > 8:
        for section in sections:
            if section["label"] and not any(item["active"] for item in section["items"]):
                section["fold"] = True
                section["icon"] = SECTION_ICONS.get(section["label"], "mas")

    # Phones: Mi iglesia, then the two places this person uses most, and "Más" (the panel).
    home, _notices, events, groups = main[:4]
    if managed:
        # The tab opens where its count is: the first group with requests waiting, else the first group
        first = next((group for group in managed if pending.get(group.pk)), managed[0])
        total = sum(pending.values())
        tabs = [home, groups, _item(
            "A tu cargo", reverse("community:group_people", args=[first.pk]), "guia", in_managed, total,
            "solicitud por decidir" if total == 1 else "solicitudes por decidir",
        )]
    elif show_events:
        tabs = [home, events, {**serving, "label": "Sirves"}]
    else:
        tabs = [home, events, groups]

    name = request.user.get_full_name() or request.user.email
    return {"sections": sections, "tabs": tabs, "name": name, "church": church}


def build_event_shell(request, church) -> dict:
    """The rail and the tabs of an account that only works in events (no church role and no person of
    ``church``): the list of its events, and, inside one, the event's own pages (``shell.html`` draws
    them from ``event_nav``). Nothing of "Mi iglesia" is listed: those views would not answer."""
    view = getattr(getattr(request, "resolver_match", None), "view_name", "") or ""
    events = _item("Eventos donde sirves", reverse("events:list") + "?todos=1", "equipo", view in {"events:list", "events:create"})
    name = request.user.get_full_name() or request.user.email
    return {"sections": [{"label": "", "items": [events]}], "tabs": [{**events, "label": "Eventos"}], "name": name, "church": church}


def _minor_by_birth_date(person) -> bool:
    """Whether the person's own date of birth says they are under 18 today. No query: the fuller rule
    (oracion.access.known_minor) decides at the door of the form; this only keeps a dead link out of the menu."""
    born = getattr(person, "birth_date", None)
    if not born:
        return False
    today = timezone.localdate()
    return (today.year - born.year - ((today.month, today.day) < (born.month, born.day))) < 18
