"""Espacios: the rooms and the equipment of the church, their week, and who asked for what.

Every view goes through ``groups.views.community_view`` (signed in, the ``community`` module on for
the current church or 404, the person the account is in this church). Spaces and bookings are always
fetched inside ``request.church``: an id of another church is a 404, POSTs included. Nothing here is
reachable without an account and no public page links to it.

Who may do more than look is ``espacios.access``: asking for a booking needs to be responsible for
something in the church (403 otherwise); deciding, managing the spaces and the usage report are the
administration's (``church_admin_view``). The title of a booking, who asked and its event reach a
template only through ``_shown``, which blanks them for everybody else.
"""

from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import formats, timezone

from core.nav import safe_next
from core.paging import paginate
from groups.views import church_admin_view, community_view

from . import access, services
from .forms import BookingForm, RejectForm, SpaceForm, UsageForm
from .models import Booking, Space

Status = Booking.Status


def _weekday_date(day) -> str:
    return formats.date_format(day, r"l j \d\e F").lower()


def _when(booking) -> str:
    return f"{_weekday_date(booking.day)}, de {booking.start_time:%H:%M} a {booking.end_time:%H:%M}"


def _space(request, space_id: int) -> Space:
    """A space of this church. One that is switched off exists only for the administration."""
    space = get_object_or_404(Space, pk=space_id, church=request.church)
    if not space.active and not request.is_church_admin:
        raise Http404
    return space


def _booking(request, booking_id: int) -> Booking:
    return get_object_or_404(Booking.objects.select_related("space", "requested_by", "event"), pk=booking_id, space__church=request.church)


def _shown(request, bookings, tz):
    """Localize each booking and say what this reader may read of it. For anybody but the
    administration and who asked, the title, the person and the event are removed from the object
    itself, so a template cannot print them by mistake."""
    now = timezone.now()
    for booking in bookings:
        services.localize(booking, tz)
        booking.is_mine = booking.requested_by_id == request.person.pk
        booking.show_details = access.can_read_details(booking, request.person, request.is_church_admin)
        booking.can_cancel = booking.show_details and booking.holds and booking.ends_at > now
        if not booking.show_details:
            booking.title, booking.requested_by, booking.event, booking.decision_reason = "Ocupado", None, None, ""
    return bookings


def _monday(request, tz) -> date:
    today = timezone.now().astimezone(tz).date()
    try:
        chosen = date.fromisoformat(request.GET.get("semana", ""))
    except ValueError:
        chosen = today
    if abs((chosen - today).days) > 366 * 2:
        chosen = today
    return services.week_of(chosen)


# ---- everybody of the church --------------------------------------------------------------------
@community_view
def space_list(request):
    tz = services.church_tz(request.church)
    may_request = access.can_request(request.user, request.church, is_church_admin=request.is_church_admin)
    spaces = list(Space.objects.filter(church=request.church, active=True))
    mine = _shown(request, services.mine(request.person, tz), tz) if may_request else []
    return render(
        request,
        "espacios/list.html",
        {
            "spaces": spaces, "may_request": may_request, "mine": mine,
            "pending": services.pending_count(request.church) if request.is_church_admin else 0,
        },
    )


@community_view
def week(request, space_id: int):
    space = _space(request, space_id)
    tz = services.church_tz(request.church)
    monday = _monday(request, tz)
    days = services.week(space, monday, tz)
    for day in days:
        _shown(request, day["bookings"], tz)
    today = timezone.now().astimezone(tz).date()
    return render(
        request,
        "espacios/week.html",
        {
            "space": space, "days": days, "monday": monday, "sunday": monday + timedelta(days=6), "today": today,
            "previous": (monday - timedelta(days=7)).isoformat(), "next": (monday + timedelta(days=7)).isoformat(),
            "is_this_week": monday == services.week_of(today),
            "may_request": space.active and access.can_request(request.user, request.church, is_church_admin=request.is_church_admin),
            "tz_name": str(tz),
        },
    )


@community_view
def request_booking(request, space_id: int):
    space = _space(request, space_id)
    if not access.can_request(request.user, request.church, is_church_admin=request.is_church_admin):
        raise PermissionDenied
    if not space.active:
        raise Http404
    tz = services.church_tz(request.church)
    initial = {}
    try:
        initial["day"] = date.fromisoformat(request.GET.get("dia", ""))
    except ValueError:
        pass
    form = BookingForm(request.POST or None, church=request.church, tz=tz, with_event=request.is_church_admin, initial=initial)
    clashes = []
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        # Only the administration keeps a booking over another one, and only naming the ones it saw listed.
        seen = None
        if request.is_church_admin and request.POST.get("sobre") == "1":
            seen = [int(value) for value in request.POST.getlist("visto") if value.isdigit()]
        try:
            booking = services.request_booking(
                space, request.person, request.user, title=data["title"], starts_at=data["starts_at"], ends_at=data["ends_at"],
                setup_minutes=data["setup_minutes"], teardown_minutes=data["teardown_minutes"], event=data.get("event"),
                approve=request.is_church_admin, seen=seen,
            )
        except services.Clash as clash:
            clashes = _shown(request, clash.bookings, tz)
            form.add_error(None, "Ese horario se empalma con otra reserva de este espacio, contando el tiempo para preparar y recoger.")
        except services.SpaceError as error:
            form.add_error(None, error.message)
        else:
            services.localize(booking, tz)
            if booking.status == Status.APPROVED:
                messages.success(request, f"Reservaste {space.name} el {_when(booking)}.")
            else:
                messages.success(
                    request,
                    f"Pediste {space.name} el {_when(booking)}. El horario queda apartado mientras la administración decide. "
                    "Emmaus no te avisa cuando decida: revísalo aquí, en «Lo que pediste».",
                )
            return redirect("espacios:list")
    return render(request, "espacios/request_form.html", {"form": form, "space": space, "clashes": clashes, "tz_name": str(tz)})


@community_view
def cancel(request, booking_id: int):
    """Who asked for a booking, or the administration, frees its hours. Anybody else: it does not exist."""
    booking = _booking(request, booking_id)
    if not access.can_read_details(booking, request.person, request.is_church_admin):
        raise Http404
    tz = services.church_tz(request.church)
    services.localize(booking, tz)
    back = reverse("espacios:week", args=[booking.space_id]) + f"?semana={booking.day.isoformat()}"
    if not booking.space.active and not request.is_church_admin:
        back = reverse("espacios:list")
    if request.method == "POST":
        try:
            services.cancel(booking, request.user, by_admin=request.is_church_admin and booking.requested_by_id != request.person.pk)
        except services.SpaceError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, f"Cancelaste la reserva de {booking.space.name} del {_when(booking)}. El horario quedó libre.")
        return redirect(back)
    if not booking.holds or booking.ends_at <= timezone.now():
        raise Http404
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Cancelar la reserva de {booking.space.name}?",
            "text": f"{booking.title}: {_when(booking)}. El horario queda libre para alguien más."
                    + ("" if booking.requested_by_id == request.person.pk else " Emmaus no le avisa a quien la pidió: avísale tú."),
            "action": "Sí, cancelar la reserva",
            "back_url": back,
        },
    )


# ---- the administration -------------------------------------------------------------------------
@church_admin_view
def queue(request):
    tz = services.church_tz(request.church)
    now = timezone.now()
    rows = Booking.objects.filter(space__church=request.church).select_related("space", "requested_by", "event", "decided_by")
    waiting = Q(status=Status.REQUESTED, ends_at__gt=now)
    chosen = "decididas" if request.GET.get("estado") == "decididas" else ""
    if chosen:
        page = paginate(request, rows.exclude(waiting).order_by("-starts_at", "-id"))
    else:
        page = paginate(request, rows.filter(waiting).order_by("starts_at", "id"))
    page.object_list = list(page.object_list)
    _shown(request, page.object_list, tz)
    for booking in page.object_list:
        booking.expired = booking.status == Status.REQUESTED
        if not chosen:
            booking.clashes = _shown(
                request, list(services.overlapping(booking.space_id, booking.blocked_from, booking.blocked_until, exclude=booking.pk)
                              .select_related("requested_by", "event")), tz)
    return render(request, "espacios/queue.html", {"bookings": page, "chosen": chosen, "pending": rows.filter(waiting).count()})


@church_admin_view
def approve(request, booking_id: int):
    booking = _booking(request, booking_id)
    if request.method != "POST":
        # Approving is a POST. A GET is a reload or «atrás» on the clash page an action in place left at this
        # address: back to the queue, where the booking still waits.
        return redirect("espacios:queue")
    tz = services.church_tz(request.church)
    seen = None
    if request.POST.get("sobre") == "1":
        seen = [int(value) for value in request.POST.getlist("visto") if value.isdigit()]
    try:
        services.approve(booking, request.user, seen=seen)
    except services.Clash as clash:
        # The clash, listed: the only page from which the administration can approve over it.
        _shown(request, [booking], tz)
        return render(
            request, "espacios/clash.html",
            {"booking": booking, "clashes": _shown(request, clash.bookings, tz), "again": seen is not None},
        )
    except services.SpaceError as error:
        messages.error(request, error.message)
    else:
        services.localize(booking, tz)
        messages.success(request, f"Aprobaste la reserva de {booking.space.name} del {_when(booking)}. Emmaus no le avisa a quien la pidió: avísale tú.")
    # Back to the list it was pressed on (its page and filter), where emmaus.js reads the new state (data-inline)
    return redirect(safe_next(request, request.POST.get("siguiente"), reverse("espacios:queue")))


@church_admin_view
def reject(request, booking_id: int):
    booking = _booking(request, booking_id)
    if booking.status != Status.REQUESTED:
        if request.method == "POST":
            messages.error(request, services.DECIDED)
            return redirect("espacios:queue")
        raise Http404
    tz = services.church_tz(request.church)
    form = RejectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.reject(booking, request.user, form.cleaned_data["reason"])
        except services.SpaceError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, "Rechazaste la solicitud. El horario quedó libre. Emmaus no le avisa a quien la pidió: avísale tú.")
        return redirect("espacios:queue")
    _shown(request, [booking], tz)
    return render(request, "espacios/reject.html", {"form": form, "booking": booking})


@church_admin_view
def manage(request):
    now = timezone.now()
    spaces = Space.objects.filter(church=request.church).annotate(
        coming=Count("bookings", filter=Q(bookings__status__in=Booking.HOLDING, bookings__ends_at__gt=now)),
    ).order_by("-active", "kind", "name", "id")
    return render(request, "espacios/manage.html", {"spaces": spaces})


def _space_form(request, space: Space | None):
    form = SpaceForm(request.POST or None, instance=space)
    if request.method == "POST" and form.is_valid():
        try:
            saved = services.save_space(request.church, request.user, space=space, **form.cleaned_data)
        except services.SpaceError as error:
            form.add_error("name", error.message)
        else:
            messages.success(request, f"Agregaste {saved.name}." if space is None else "Guardamos los cambios.")
            return redirect("espacios:manage")
    return render(request, "espacios/space_form.html", {"form": form, "space": space})


@church_admin_view
def space_create(request):
    return _space_form(request, None)


@church_admin_view
def space_edit(request, space_id: int):
    return _space_form(request, _space(request, space_id))


@church_admin_view
def usage(request):
    """Hours booked per space between two days. Bookings and hours only: nobody is named or counted."""
    tz = services.church_tz(request.church)
    today = timezone.now().astimezone(tz).date()
    first = today.replace(day=1)
    initial = {"desde": first, "hasta": (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)}
    asked = "desde" in request.GET or "hasta" in request.GET
    form = UsageForm(request.GET if asked else None, initial=initial)
    rows, period = None, None
    if not asked:
        period = (initial["desde"], initial["hasta"])
    elif form.is_valid():
        period = (form.cleaned_data["desde"], form.cleaned_data["hasta"])
    if period:
        rows = services.usage(request.church, period[0], period[1], tz)
    return render(request, "espacios/usage.html", {"form": form, "rows": rows, "period": period})
