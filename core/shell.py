"""The application shell of the redesigned experience ("Santuario", docs/v2/DESIGN_SYSTEM.md).

One navigation for every signed-in page of a church that uses the community module: a side rail on
wide screens, a top bar with bottom tabs on phones, and the "Más" panel that holds everything.
It is built from data here, never in a template, so the three places always agree.

Sections, in order: what is everybody's (Mi iglesia, Grupos, Eventos), the groups the person
guides ("A tu cargo"), the discipleship module's own links, and the administration. A link is
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
    from people.flags import enabled

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

    main = [
        _item("Mi iglesia", reverse("community:home"), "inicio", view in {"community:home", "community:profile"}),
        _item("Grupos", reverse("community:groups"), "grupos",
              view in GROUP_VIEWS or (view in GROUP_WORK_VIEWS and not in_managed and not is_admin)),
    ]
    if show_events:
        main.append(_item("Eventos", reverse("events:list") + "?todos=1", "eventos", view in {"events:list", "events:create"}))
    sections = [{"label": "", "items": main}]

    if managed:
        sections.append({
            "label": "A tu cargo",
            "items": [
                _item(group.name, reverse("community:group_people", args=[group.pk]), "guia", working_on == group.pk,
                      pending.get(group.pk, 0),
                      "solicitud por decidir" if pending.get(group.pk) == 1 else "solicitudes por decidir")
                for group in managed
            ],
        })

    admin_urls = set()
    admin = []
    if is_admin:
        if enabled(church, "people"):
            admin.append(_item("Personas", reverse("people:list"), "personas", view in PEOPLE_VIEWS))
        admin.append(_item(
            "Grupos de la iglesia", reverse("community:group_admin_list"), "grupos",
            view in GROUP_ADMIN_VIEWS or (view in GROUP_WORK_VIEWS and not in_managed),
        ))
        admin.append(_item("Configuración", reverse("church_settings"), "ajustes", url_name in {"church_settings", "user_list"}))
        admin_urls = {item["url"] for item in admin}

    module = [
        _item(item["label"], item["url"], MODULE_ICONS.get(item["names"][0], "camino"), url_name in item["names"])
        for item in module_items if item["url"] not in admin_urls
    ]
    if module:
        sections.append({"label": "Discipulado", "items": module})
    if admin:
        sections.append({"label": "Administración", "items": admin})

    # Phones: the two places everybody has, a third that depends on the person, and "Más" (the panel).
    tabs = main[:2]
    if managed:
        first = managed[0]
        total = sum(pending.values())
        tabs.append(_item(
            "A tu cargo", reverse("community:group_people", args=[first.pk]), "guia", in_managed, total,
            "solicitud por decidir" if total == 1 else "solicitudes por decidir",
        ))
    elif show_events:
        tabs.append(main[2])
    else:
        tabs.append(_item("Mi cuenta", reverse("account"), "cuenta", url_name in {"account", "password_change"}))

    name = request.user.get_full_name() or request.user.email
    return {"sections": sections, "tabs": tabs, "name": name, "church": church}
