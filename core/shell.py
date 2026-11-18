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
    "feedback:give",
}
AGENDA_ADMIN_VIEWS = {
    "agenda:admin_list", "agenda:page_edit", "agenda:registrations", "agenda:create", "agenda:event_edit", "agenda:duplicate",
    "agenda:walkup", "agenda:confirm_age", "agenda:guest_age", "agenda:annul", "agenda:consent", "agenda:consent_revoke", "agenda:guest_promote", "agenda:faq", "agenda:faq_edit",
    "agenda:faq_delete", "agenda:schedule", "agenda:schedule_edit", "agenda:schedule_delete", "feedback:results",
    "agenda:waiting_remove", "agenda:visit_follow_up",
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
SERVING_VIEWS = {"serving:home"}
SERVING_ADMIN_VIEWS = {
    "serving:team_admin", "serving:team_create", "serving:team", "serving:team_edit", "serving:team_archive",
    "serving:member_remove", "serving:date_create", "serving:date_detail", "serving:date_delete",
}
OVERVIEW_VIEWS = {"overview:panorama"}
CARE_VIEWS = {"care:mine", "care:detail"}
CARE_ADMIN_VIEWS = {"care:admin_list", "care:create", "care:edit", "care:cancel"}
PEOPLE_VIEWS = {
    "people:list", "people:create", "people:detail", "people:edit", "people:from_event_list", "people:from_event",
    "people:grant_access", "people:access_link",
}
# Icons for the discipleship module's links, by url name (core.context_processors builds those links).
MODULE_ICONS = {
    "church_settings": "ajustes", "participants_list": "personas", "participant_dashboard": "camino",
    "mentor_dashboard": "camino", "church_dashboard": "camino", "path_list": "camino",
}


def _item(label, url, icon, active=False, badge=0, badge_label=""):
    return {"label": label, "url": url, "icon": icon, "active": active, "badge": badge, "badge_label": badge_label}


def build_shell(request, church, module_items, show_events: bool) -> dict:
    """The rail's sections and the phone's tabs for ``request``. ``module_items`` are the
    discipleship links the bar would have shown (label, url, names)."""
    from groups.access import managed_group_ids
    from groups.models import Group, GroupJoinRequest
    from care import services as care_services
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
        _item("Mi iglesia", reverse("community:home"), "inicio", view in {"community:home", "community:profile"}),
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
        loose = care_services.unassigned_count(church, person)
        admin.append(_item("Seguimiento de personas", reverse("care:admin_list"), "acompanar",
                           view in CARE_ADMIN_VIEWS or (view in CARE_VIEWS and not accompanying), loose,
                           "seguimiento sin responsable" if loose == 1 else "seguimientos sin responsable"))
        admin.append(_item("Devocionales y recursos", reverse("resources:devotional_admin"), "devocional", view in RESOURCE_ADMIN_VIEWS))
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

    # Phones: Mi iglesia, then the two places this person uses most, and "Más" (the panel).
    home, _notices, events, groups = main
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
