"""Mi iglesia: the home of a person of the church, the groups, and the tools of who guides one.

Every view: the ``community`` module on for the current church (404 otherwise), then the person
the account is in that church (``request.person``; 404 without one). Groups are always fetched
inside ``request.church``, so an id of another church is a 404. What needs more than being of
the church asks ``core.authz.has_scope``: group ``manage`` for its guides, church ``manage`` for
the administration. No view here reads an interview, an answer or a camp follow-up.
"""

from datetime import date
from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import formats
from django.utils import timezone
from django.views.decorators.http import require_POST

from core import notify, onboarding
from core.paging import first_rows, paginate
from core.authz import Action, has_scope
from events.models import normalize_name
from feedback import services as feedback_services
from events.services import throttle
from people import flags
from people.decorators import module_required
from people.models import Person
from people.services import ensure_own_person, save_person

from agenda import services as agenda_services
from care import services as care_services
from notices import services as notices_services
from resources import plans as reading_plans
from resources import services as resources_services
from serving import services as serving_services

from . import services
from .access import church_groups, managed_group_ids
from .forms import GroupForm, MyDataForm
from .models import Group, GroupJoinRequest, GroupMeeting, GroupMembership

ACTIVE = GroupMembership.Status.ACTIVE
SEARCH_LIMIT = 10  # names under a search box (core/paging.py::first_rows)
PENDING = GroupJoinRequest.Status.PENDING
# The words of the filter are plurals; the model's are the singular a group page shows.
GROUP_KIND_FILTERS = [("pequeno", "Grupos pequeños"), ("estudio", "Estudios bíblicos"), ("otro", "Otros grupos")]


def community_view(view_func):
    @module_required("community")
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        request.person = ensure_own_person(request.user, request.church)
        if request.person is None:
            raise Http404
        request.is_church_admin = has_scope(request.user, request.church, "church", request.church.pk, Action.MANAGE)
        return view_func(request, *args, **kwargs)

    return wrapped


def church_admin_view(view_func):
    @community_view
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.is_church_admin:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped


def _leaders(group_ids) -> dict[int, list[str]]:
    """Who guides each group, by name: what somebody looking for a group reads."""
    names: dict[int, list[str]] = {}
    rows = GroupMembership.objects.filter(group_id__in=group_ids, status=ACTIVE, role=GroupMembership.Role.LEADER)
    for membership in rows.select_related("person").order_by("person__first_name", "id"):
        names.setdefault(membership.group_id, []).append(membership.person.full_name)
    return names


def _with_leaders(groups) -> list[Group]:
    groups = list(groups)
    leaders = _leaders([group.pk for group in groups])
    for group in groups:
        group.leader_names = leaders.get(group.pk, [])
    return groups


# ---- home ---------------------------------------------------------------------------------------
@community_view
def home(request):
    church, person, today = request.church, request.person, timezone.localdate()
    memberships = list(
        GroupMembership.objects.filter(person=person, status=ACTIVE, group__status=Group.Status.ACTIVE).select_related("group")
    )
    for membership in memberships:
        membership.next_meeting = membership.group.next_meeting(today)
    my_requests = list(GroupJoinRequest.objects.filter(person=person, status=PENDING).select_related("group"))
    month_ago = timezone.now() - timezone.timedelta(days=30)
    declined = list(
        GroupJoinRequest.objects.filter(person=person, status=GroupJoinRequest.Status.DECLINED, decided_at__gte=month_ago)
        .select_related("group")
    )
    managed = managed_group_ids(request.user, church)
    to_decide = (
        GroupJoinRequest.objects.filter(group_id__in=managed, status=PENDING, group__status=Group.Status.ACTIVE)
        .values("group_id", "group__name").annotate(total=Count("id")).order_by("group__name")
    )
    to_decide = list(to_decide)
    # Only what the church published for its people (agenda.EventPage), with this person's sign-up.
    # The home is a glance, so its lists are short on purpose and each one leads to the whole list: five events and
    # six dates in all under "Lo que viene" ("Ver todos los eventos"), three notices ("Ver todos los avisos"), three
    # dates to serve ("Ver todo en Servir"), three resources ("Ver todos los recursos").
    upcoming = agenda_services.upcoming_for(person, church, today)[:5]
    urgent_notices = notices_services.urgent_pending(person, is_admin=request.is_church_admin)
    notices = notices_services.for_home(person, is_admin=request.is_church_admin, skip=urgent_notices)
    devotional = resources_services.today_devotional(church, person, today)
    reading = reading_plans.current_for(church, person)  # (plan, first unread day) of this person only, or None
    serving_dates = serving_services.my_dates(person, today, limit=3)
    accompanying = care_services.open_count(person)
    opinions = feedback_services.pending_for(person, church, today)
    media = resources_services.latest_media(church)
    history = person.event_links.select_related("event").order_by("-event__starts_on")
    open_groups = church_groups(church).filter(accepts_requests=True).exists()
    any_group = Group.objects.filter(church=church).exists()

    # What is coming, in one list by day: the meetings of the person's groups and the church's events.
    agenda = [
        {
            "date": membership.next_meeting, "today": membership.next_meeting == today, "title": membership.group.name,
            "kind": "group", "time": membership.group.meeting_time, "place": membership.group.meeting_place,
            "url": reverse("community:group_detail", args=[membership.group.pk]),
        }
        for membership in memberships if membership.next_meeting
    ]
    agenda += [
        {"date": max(event.starts_on or today, today), "today": (event.starts_on or today) <= today, "title": event.name, "kind": "event", "event": event}
        for event in upcoming
    ]
    agenda.sort(key=lambda row: (row["date"], row["kind"], row["title"]))

    # The way so far, newest first: the groups the person is in and the events they were part of.
    journey = [
        {"date": timezone.localtime(membership.start_at).date(), "now": True, "membership": membership}
        for membership in memberships
    ]
    journey += [{"date": link.event.starts_on, "now": False, "event": link.event} for link in history if link.event.starts_on]
    journey.sort(key=lambda row: row["date"], reverse=True)

    missing = [label for label, value in (("tu teléfono", person.phone), ("tu correo", person.email)) if not value]

    sign = None
    if to_decide:
        total = sum(row["total"] for row in to_decide)
        first = to_decide[0]
        # One request: who asks and for which group, so the guide knows it before opening it.
        one = (
            GroupJoinRequest.objects.filter(group_id__in=managed, status=PENDING, group__status=Group.Status.ACTIVE)
            .select_related("person", "group").first() if total == 1 else None
        )
        sign = {
            "kind": "Espera tu respuesta",
            "who": one.person.full_name if one else "",
            "title": f"{one.person.full_name} quiere unirse a {one.group.name}" if one else f"{total} personas quieren unirse a tus grupos",
            "text": "",
            "url": reverse("community:group_people", args=[first["group_id"]]),
            "action": "Ver solicitud" if one else "Ver solicitudes",
        }
    elif request.is_church_admin and not any_group:
        sign = {
            "kind": "Para empezar",
            "title": "Crea el primer grupo de la iglesia",
            "text": "",
            "url": reverse("community:group_create"),
            "action": "Crear un grupo",
        }
    elif due := next(
        ((group, day) for group in (m.group for m in memberships if m.group.pk in managed) if (day := services.meeting_due(group, today))),
        None,
    ):
        group, day = due
        sign = {
            "kind": "Falta la lista",
            "title": f"¿Quién vino a {group.name}?",
            "text": f"Reunión del {_weekday_date(day)}",
            "url": reverse("community:meeting_record", args=[group.pk, day.isoformat()]),
            "action": "Pasar lista",
        }
    elif not memberships and not my_requests and open_groups:
        sign = {
            "kind": "",
            "title": "Encuentra tu grupo",
            "text": "Hay grupos que reciben a personas nuevas",
            "url": reverse("community:groups"),
            "action": "Ver los grupos",
        }
    # The first time, what this place is and three things to do first (core/onboarding.py). "A tu cargo" is named
    # only to who has it in the shell: the first active group they guide, as core/shell.py orders them.
    guided = Group.objects.filter(pk__in=managed, church=church, status=Group.Status.ACTIVE).order_by("name", "id").first()
    welcome = onboarding.for_member(
        request.user, church, person.first_name, has_group=bool(memberships), open_groups=open_groups,
        guided=(guided.name, reverse("community:group_people", args=[guided.pk])) if guided else None,
    )
    # "Hoy" and the days after it, as one list: what the church says today (notices not read yet, the devotional),
    # then every meeting, event and date to serve under its day. One place to look, in the order it happens.
    rows = [{"date": today, "kind": "notice", "notice": notice, "order": 0} for notice in notices]
    if devotional:
        rows.append({"date": today, "kind": "devotional", "devotional": devotional, "order": 1})
    if reading:
        rows.append({"date": today, "kind": "reading", "plan": reading[0], "day": reading[1], "order": 1})
    rows += [{**item, "order": 2, "time": item.get("time") or (item["event"].page.starts_at if item["kind"] == "event" else None)} for item in agenda[:6]]
    rows += [
        {"date": a.service_date.date, "kind": "serving", "assignment": a, "order": 2, "time": a.service_date.starts_at}
        for a in serving_dates
    ]
    rows.sort(key=lambda row: (row["date"], row["order"], str(row.get("time") or "")))
    days = []
    for row in rows:
        if not days or days[-1]["date"] != row["date"]:
            days.append({"date": row["date"], "today": row["date"] == today, "rows": []})
        days[-1]["rows"].append(row)
    return render(
        request,
        "groups/home.html",
        {
            "days": days,
            "church": church,
            "person": person,
            "welcome": welcome,
            "sign": sign,
            "memberships": memberships,
            "my_requests": my_requests,
            "declined": declined,
            "upcoming": upcoming,
            "history": history,
            "open_groups": open_groups,
            "people_enabled": request.is_church_admin and flags.enabled(church, "people"),
            "agenda": agenda[:6],
            "notices": notices,
            "urgent_notices": urgent_notices,
            "devotional": devotional,
            "reading": reading,
            "serving_dates": serving_dates,
            "accompanying": accompanying,
            "opinions": opinions,
            "media": media,
            "journey": journey,
            "missing": missing,
            "today": today,
        },
    )


@community_view
def profile(request):
    """"Mis datos": the person corrects how the church reaches them. Only their own row, and only
    the contact fields: the name on record and the date of birth are the administration's."""
    form = MyDataForm(request.POST or None, instance=request.person)
    if request.method == "POST" and form.is_valid():
        save_person(form.save(commit=False), request.user, created=False)
        messages.success(request, "Guardamos tus datos.")
        return redirect("community:home")
    return render(request, "groups/profile.html", {"form": form, "person": request.person})


# ---- discovery ----------------------------------------------------------------------------------
@community_view
def groups(request):
    """The groups of the church. First the person's own, then where they asked, then the ones that
    receive people, last the ones that do not. ``?tipo=`` keeps one kind, when the church has several."""
    person = request.person
    mine = set(GroupMembership.objects.filter(person=person, status=ACTIVE).values_list("group_id", flat=True))
    asked = set(GroupJoinRequest.objects.filter(person=person, status=PENDING).values_list("group_id", flat=True))
    rows = _with_leaders(church_groups(request.church).select_related("sede").order_by("name", "id"))
    for group in rows:
        group.state = "mine" if group.pk in mine else "asked" if group.pk in asked else "open" if group.is_open else "closed"
    rows.sort(key=lambda group: ("mine", "asked", "open", "closed").index(group.state))
    kinds = [
        {"value": value, "label": label, "count": sum(1 for group in rows if group.kind == value)}
        for value, label in GROUP_KIND_FILTERS
    ]
    kinds = [kind for kind in kinds if kind["count"]]
    chosen = request.GET.get("tipo", "")
    if chosen not in {kind["value"] for kind in kinds}:
        chosen = ""
    shown = [group for group in rows if not chosen or group.kind == chosen]
    return render(
        request,
        "groups/groups.html",
        {"groups": shown, "total": len(rows), "kinds": kinds if len(kinds) > 1 else [], "chosen": chosen},
    )


def _group(request, group_id: int, *, include_archived: bool = False) -> Group:
    queryset = Group.objects.filter(church=request.church).select_related("sede")
    if not include_archived:
        queryset = queryset.filter(status=Group.Status.ACTIVE)
    return get_object_or_404(queryset, pk=group_id)


def _can_manage(request, group: Group) -> bool:
    """Who guides the group, or the church's administration (which creates groups and names their guides)."""
    return request.is_church_admin or has_scope(request.user, request.church, "group", group.pk, Action.MANAGE)


@community_view
def group_detail(request, group_id: int):
    group = _group(request, group_id, include_archived=request.is_church_admin)
    person = request.person
    membership = GroupMembership.objects.filter(group=group, person=person, status=ACTIVE).first()
    pending = GroupJoinRequest.objects.filter(group=group, person=person, status=PENDING).first()
    return render(
        request,
        "groups/group_detail.html",
        {
            "group": _with_leaders([group])[0],
            "membership": membership,
            "pending": pending,
            "next_meeting": group.next_meeting() if membership else None,
            "can_manage": _can_manage(request, group),
            "members": GroupMembership.objects.filter(group=group, status=ACTIVE).count(),
            # Only the reader's own rows, and only to them: nobody else's attendance is on this page.
            "attendance": services.own_attendance(membership) if membership and group.is_active else [],
            "attendance_total": services.own_attendance_total(membership) if membership and group.is_active else 0,
        },
    )


def _absolute(request, name: str, *args) -> str:
    return request.build_absolute_uri(reverse(name, args=args))


@community_view
@require_POST
def group_request(request, group_id: int):
    group = _group(request, group_id)
    wait = throttle.consume(f"group-join:{request.user.pk}", limit=10, window_seconds=3600, block_seconds=600)
    if wait:
        messages.error(request, throttle.wait_message(wait))
        return redirect("community:group_detail", group.pk)
    try:
        join_request, created = services.request_to_join(group, request.person, request.user)
    except services.GroupError as error:
        messages.error(request, error.message)
        return redirect("community:group_detail", group.pk)
    if created:
        emails = list(
            GroupMembership.objects.filter(group=group, status=ACTIVE, role=GroupMembership.Role.LEADER, person__user__isnull=False)
            .exclude(person__user__email="").values_list("person__user__email", flat=True)
        )
        outcome = notify.NOT_CONFIGURED
        if emails:
            outcome = notify.notify(
                "solicitud_grupo_recibida", emails,
                {"church_name": group.church.name, "link": _absolute(request, "community:group_people", group.pk)},
            )
        # The message never claims an e-mail that did not go out (core.notify).
        if outcome == notify.SENT:
            messages.success(request, f"Listo. Pediste unirte a {group.name} y le avisamos por correo a quien lo guía.")
        else:
            messages.success(request, f"Listo. Pediste unirte a {group.name}. Quien lo guía verá tu solicitud cuando entre a Emmaus.")
    return redirect("community:group_detail", group.pk)


@community_view
@require_POST
def group_withdraw(request, group_id: int):
    group = _group(request, group_id, include_archived=True)
    pending = GroupJoinRequest.objects.filter(group=group, person=request.person, status=PENDING).first()
    if pending:
        services.withdraw_request(pending, request.user)
        messages.success(request, f"Retiraste tu solicitud a {group.name}.")
    return redirect("community:group_detail", group.pk) if group.is_active else redirect("community:home")


@community_view
def group_leave(request, group_id: int):
    group = _group(request, group_id)
    membership = get_object_or_404(GroupMembership, group=group, person=request.person, status=ACTIVE)
    if request.method == "POST":
        services.end_membership(membership, request.user)
        messages.success(request, f"Saliste de {group.name}.")
        return redirect("community:home")
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Quieres salir de {group.name}?",
            "text": "Dejarás de ver dónde se reúne. Si cambias de opinión, puedes pedir unirte otra vez.",
            "action": "Sí, salir del grupo",
            "back_url": reverse("community:group_detail", args=[group.pk]),
        },
    )


# ---- who guides a group -------------------------------------------------------------------------
def _managed_group(request, group_id: int) -> Group:
    group = _group(request, group_id, include_archived=request.is_church_admin)
    if not _can_manage(request, group):
        raise PermissionDenied
    return group


@community_view
def group_people(request, group_id: int):
    group = _managed_group(request, group_id)
    requests = GroupJoinRequest.objects.filter(group=group, status=PENDING).select_related("person")
    members = GroupMembership.objects.filter(group=group, status=ACTIVE).select_related("person", "person__user")
    query, found, found_total = request.GET.get("q", "").strip(), [], 0
    if request.is_church_admin and query:
        # Only the administration searches the church's people; a guide sees only who asked and who is in.
        found = Person.objects.filter(church=request.church, merged_into__isnull=True)
        for token in normalize_name(query).split():
            found = found.filter(normalized_name__contains=token)
        # Ten names under a search box on purpose: with more, the screen says how many and asks for more of the name.
        found, found_total = first_rows(found.exclude(group_memberships__in=members), SEARCH_LIMIT)
    return render(
        request,
        "groups/group_people.html",
        {"group": group, "requests": requests, "members": members, "member_count": len(members), "query": query,
         "found": found, "found_total": found_total, "me": request.person},
    )


@community_view
@require_POST
def request_decide(request, group_id: int, request_id: int):
    group = _managed_group(request, group_id)
    join_request = get_object_or_404(GroupJoinRequest.objects.select_related("person", "person__user"), pk=request_id, group=group)
    accept = request.POST.get("decision") == "aceptar"
    try:
        services.decide_request(join_request, request.user, accept=accept)
    except services.GroupError as error:
        messages.error(request, error.message)
        return redirect("community:group_people", group.pk)
    person = join_request.person
    outcome = notify.NOT_CONFIGURED
    if person.user_id and person.user.email:
        outcome = notify.notify(
            "solicitud_grupo_decidida", person.user.email,
            {"church_name": group.church.name, "link": _absolute(request, "community:home")},
        )
    told = " Le avisamos por correo." if outcome == notify.SENT else " Lo verá cuando entre a Emmaus."
    if accept:
        messages.success(request, f"{person.full_name} ya es parte de {group.name}.{told}")
    else:
        messages.success(request, f"No aceptaste la solicitud de {person.full_name}.{told}")
    return redirect("community:group_people", group.pk)


@community_view
def member_remove(request, group_id: int, membership_id: int):
    group = _managed_group(request, group_id)
    membership = get_object_or_404(GroupMembership.objects.select_related("person"), pk=membership_id, group=group, status=ACTIVE)
    if membership.is_leader and not request.is_church_admin:
        raise PermissionDenied  # who guides a group is the administration's decision
    if request.method == "POST":
        services.end_membership(membership, request.user)
        messages.success(request, f"{membership.person.full_name} ya no es parte de {group.name}.")
        return redirect("community:group_people", group.pk)
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Quitar a {membership.person.full_name} de {group.name}?",
            "text": "Dejará de ver dónde se reúne el grupo. Podrá pedir unirse otra vez.",
            "action": "Sí, quitar del grupo",
            "back_url": reverse("community:group_people", args=[group.pk]),
        },
    )


# ---- meetings -----------------------------------------------------------------------------------
def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Http404 from None


def _weekday_date(day: date) -> str:
    return formats.date_format(day, r"l j \d\e F").lower()


@community_view
def group_meetings(request, group_id: int):
    """The meetings of a group, for who guides it: the one that waits for its list, then the
    last ones with how many came. ``?fecha=`` opens the list of any past day."""
    group = _managed_group(request, group_id)
    chosen = request.GET.get("fecha", "").strip()
    if chosen:
        try:
            day = date.fromisoformat(chosen)
        except ValueError:
            messages.error(request, "Escribe la fecha con el calendario o así: 2026-11-12.")
            return redirect("community:group_meetings", group.pk)
        return redirect("community:meeting_record", group.pk, day.isoformat())
    meetings = paginate(request, services.with_present(group.meetings.all()))
    return render(
        request,
        "groups/group_meetings.html",
        {
            "group": group, "meetings": meetings, "due": services.meeting_due(group),
            "member_count": GroupMembership.objects.filter(group=group, status=ACTIVE).count(),
            "today": timezone.localdate(),
        },
    )


@community_view
def meeting_record(request, group_id: int, day: str):
    """"Pasar lista": who came on one day. Opening a day that already has a list shows it, ready to correct."""
    group = _managed_group(request, group_id)
    day = _day(day)
    if day > timezone.localdate():
        raise Http404
    meeting = GroupMeeting.objects.filter(group=group, date=day).first()
    if meeting and meeting.names_deleted:
        # Who came was deleted for its age (prune_attendance): the totals are read, the list is not written again.
        if request.method == "POST":
            messages.error(request, services.NAMES_DELETED)
            return redirect("community:group_meetings", group.pk)
        return render(request, "groups/meeting_record.html", {"group": group, "day": day, "day_words": _weekday_date(day), "meeting": meeting, "members": []})
    members = list(GroupMembership.objects.filter(group=group, status=ACTIVE).select_related("person"))
    if request.method == "POST":
        try:
            saved = services.record_meeting(group, day, request.POST.getlist("vino"), request.user)
        except services.GroupError as error:
            messages.error(request, error.message)
            return redirect("community:group_meetings", group.pk)
        came = "vino 1 persona" if saved.present == 1 else f"vinieron {saved.present} personas"
        messages.success(request, f"Guardamos la lista del {_weekday_date(day)}: {came} de {saved.expected}.")
        return redirect("community:group_meetings", group.pk)
    present = set(meeting.attendances.values_list("person_id", flat=True)) if meeting else set()
    for membership in members:
        membership.came = membership.person_id in present
    return render(
        request,
        "groups/meeting_record.html",
        {"group": group, "day": day, "day_words": _weekday_date(day), "meeting": meeting, "members": members},
    )


# ---- the administration -------------------------------------------------------------------------
@church_admin_view
def group_admin_list(request):
    groups = Group.objects.filter(church=request.church).annotate(
        member_count=Count("memberships", filter=Q(memberships__status=ACTIVE), distinct=True),
        pending_count=Count("join_requests", filter=Q(join_requests__status=PENDING), distinct=True),
    ).order_by("status", "name", "id")
    return render(request, "groups/admin_list.html", {"groups": _with_leaders(groups)})


@church_admin_view
def group_create(request):
    form = GroupForm(request.POST or None, church=request.church)
    if request.method == "POST" and form.is_valid():
        group = form.save(commit=False)
        group.church = request.church
        services.save_group(group, request.user, created=True)
        messages.success(request, f"Creaste el grupo {group.name}. Ahora elige quién lo guía.")
        return redirect("community:group_people", group.pk)
    return render(request, "groups/group_form.html", {"form": form, "group": None})


@church_admin_view
def group_edit(request, group_id: int):
    group = _group(request, group_id)
    form = GroupForm(request.POST or None, instance=group, church=request.church)
    if request.method == "POST" and form.is_valid():
        services.save_group(form.save(commit=False), request.user, created=False)
        messages.success(request, "Guardamos los cambios.")
        return redirect("community:group_detail", group.pk)
    return render(request, "groups/group_form.html", {"form": form, "group": group})


@church_admin_view
def group_archive(request, group_id: int):
    group = _group(request, group_id)
    if request.method == "POST":
        services.archive_group(group, request.user)
        messages.success(request, f"Archivaste el grupo {group.name}.")
        return redirect("community:group_admin_list")
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Archivar el grupo {group.name}?",
            "text": "Saldrá de la lista de grupos, sus solicitudes se cerrarán y quienes lo guían dejarán de administrarlo. No se borra nada.",
            "action": "Sí, archivar el grupo",
            "back_url": reverse("community:group_edit", args=[group.pk]),
        },
    )


@church_admin_view
@require_POST
def member_add(request, group_id: int):
    """The administration puts a person of the church in a group, as member or as its guide."""
    group = _group(request, group_id)
    person = get_object_or_404(Person, pk=request.POST.get("persona") or 0, church=request.church, merged_into__isnull=True)
    role = GroupMembership.Role.LEADER if request.POST.get("rol") == "lider" else GroupMembership.Role.MEMBER
    try:
        services.set_membership(group, person, request.user, role=role)
    except services.GroupError as error:
        messages.error(request, error.message)
        return redirect("community:group_people", group.pk)
    if role == GroupMembership.Role.LEADER:
        note = "" if person.user_id else " Todavía no tiene acceso a Emmaus: dáselo desde Personas para que pueda responder solicitudes."
        messages.success(request, f"{person.full_name} ahora guía {group.name}.{note}")
    else:
        messages.success(request, f"{person.full_name} ya es parte de {group.name}.")
    return redirect("community:group_people", group.pk)
