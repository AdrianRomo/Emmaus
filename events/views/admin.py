from __future__ import annotations

import csv
import json

from django import forms
from django.contrib import messages
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.models import UserChurchRole
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from core.services import send_email_safely

from ..access import resolve_access
from ..forms import (
    EventForm,
    EventSettingsForm,
    ImportForm,
    MemberForm,
    MembershipEditForm,
    OptionForm,
    ProceduresForm,
    QuestionForm,
    SectionForm,
)
from ..models import (
    AccessRequest,
    AuditEvent,
    Event,
    EventMembership,
    EventParticipant,
    FollowUp,
    Interview,
    InterviewStation,
    InterviewStatus,
    MembershipRole,
    ParticipantSession,
    Question,
    QuestionOption,
    QuestionnaireVersion,
    Section,
    TutorAssignment,
    normalize_name,
)
from ..services import audit
from ..services import interviews as interview_svc
from ..services import participants as participant_svc
from ..services import team as team_svc
from ..services.audit import ACTION_LABELS
from ..services.questionnaire import all_questions, build_steps, describe_answer, duplicate_version, load_answers, publish_version
from ..services.metrics import local_tz
from ..templatetags.emmaus import MESES
from .common import ADMIN, event_view

IMPORT_SESSION_KEY = "import_preview"


# ---- participants ---------------------------------------------------------------------------


@event_view(ADMIN, MembershipRole.PASTOR, MembershipRole.TUTOR)
def participants(request, event, access):
    qs = access.participants(include_inactive=True).select_related("coordinator__user", "sede")
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("estado", "")
    coordinator = request.GET.get("coordinador", "")
    active = request.GET.get("participa", "si")
    sede = request.GET.get("sede", "")
    if query:
        filters = Q(normalized_name__contains=normalize_name(query)) | Q(room__iexact=query) | Q(church_group__icontains=query)
        number = query.lstrip("#")
        if number.isascii() and number.isdigit():  # "²".isdigit() is True and int("²") raises
            filters |= Q(number=int(number))
        qs = qs.filter(filters)
    if status in InterviewStatus.values:
        qs = qs.filter(interview_status=status)
    if coordinator == "ninguno":
        qs = qs.filter(coordinator__isnull=True)
    elif coordinator.isascii() and coordinator.isdigit():
        qs = qs.filter(coordinator_id=int(coordinator))
    if active == "si":
        qs = qs.filter(is_active=True)
    elif active == "no":
        qs = qs.filter(is_active=False)
    sede_options = _sede_options(event, access, qs)  # counted under every filter but its own
    if sede == "ninguna":
        qs = qs.filter(sede__isnull=True)
    elif sede.isascii() and sede.isdigit():
        qs = qs.filter(sede_id=int(sede))
    page = Paginator(qs.order_by("full_name"), 50).get_page(request.GET.get("pagina"))
    return render(
        request,
        "events/admin/participants.html",
        {
            "page": page,
            "query": query,
            "selected": {"estado": status, "coordinador": coordinator, "participa": active, "sede": sede},
            "sede_options": sede_options,
            "status_choices": InterviewStatus.choices,
            "coordinators": access.coordinators(),
            "can_manage": access.is_admin,
            "querystring": request.GET.urlencode(),
            "total": access.participants().count(),
        },
    )


@event_view(ADMIN)
def participant_import(request, event, access):
    preview = request.session.get(IMPORT_SESSION_KEY)
    if preview and preview.get("event") != event.id:
        preview = None
    if request.method == "POST" and request.POST.get("action") == "confirm" and preview:
        include = {int(line) for line in request.POST.getlist("include") if line.isascii() and line.isdigit()}
        result = participant_svc.apply_import(event, preview["rows"], actor=request.user, include_lines=include)
        request.session.pop(IMPORT_SESSION_KEY, None)
        messages.success(request, result.message)
        return redirect("events:participants", slug=event.slug)
    if request.method == "POST" and request.POST.get("action") == "cancel":
        request.session.pop(IMPORT_SESSION_KEY, None)
        return redirect("events:participant_import", slug=event.slug)
    form = ImportForm(request.POST or None, request.FILES or None)
    problems = []
    if request.method == "POST" and request.POST.get("action") != "confirm" and form.is_valid():
        rows, problems = participant_svc.parse_import(event, form.cleaned_data["file"].read())
        if not problems:
            preview = {"event": event.id, "rows": [r.as_session() for r in rows], "name": form.cleaned_data["file"].name}
            request.session[IMPORT_SESSION_KEY] = preview
            return redirect("events:participant_import", slug=event.slug)
    summary = None
    if preview:
        rows = preview["rows"]
        summary = {
            "total": len(rows),
            "valid": sum(1 for r in rows if not r["errors"]),
            "errors": sum(1 for r in rows if r["errors"]),
            "duplicates": sum(1 for r in rows if r["duplicate_of"] or r["duplicate_in_file"]),
        }
    return render(
        request,
        "events/admin/import.html",
        {"form": form, "problems": problems, "preview": preview, "summary": summary},
    )


def _safe_cell(value):
    """Spreadsheets run cells that start with = + - @ as formulas; neutralize them."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@event_view(ADMIN)
def participant_export(request, event, access):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    response["Content-Disposition"] = f'attachment; filename="camperos-{event.slug}-{stamp}.csv"'
    response.write("﻿")  # Excel opens UTF-8 correctly with a BOM
    writer = csv.writer(response)
    writer.writerow(["Número", "Nombre", "Edad", "Cuarto", "Iglesia o grupo", "Sede", "Coordinador", "Entrevista", "Participa", "Registro"])
    for p in access.participants(include_inactive=True).select_related("coordinator__user", "sede").order_by("number"):
        writer.writerow([_safe_cell(value) for value in [
            p.code, p.full_name, p.age, p.room, p.church_group, p.sede.name if p.sede else "",
            p.coordinator.display_name if p.coordinator else "",
            p.get_interview_status_display(), "Sí" if p.is_active else "No",
            timezone.localtime(p.created_at).strftime("%d/%m/%Y %H:%M"),
        ]])
    audit.record(request.user, event, "exportacion", "Descargó la lista de camperos (sin respuestas)", None, kind="lista")
    return response


# The place a family recognises in "hora de ...", for the zones an event can choose (forms.MEXICO_TIME_ZONES).
TIME_ZONE_PLACES = {
    "America/Mexico_City": "Ciudad de México",
    "America/Cancun": "Cancún",
    "America/Chihuahua": "Chihuahua",
    "America/Ciudad_Juarez": "Ciudad Juárez",
    "America/Mazatlan": "Mazatlán",
    "America/Hermosillo": "Hermosillo",
    "America/Tijuana": "Tijuana",
}


def _family_date(value, event) -> str:
    """'26 de octubre de 2026, 11:00:30 (hora de Ciudad de México)', in the event's time zone.

    The export is read by a family, not parsed by a program; the seconds and the zone keep it exact.
    A zone outside the list (set through the Django admin) is named by its own key, never left out.
    """
    if value is None:
        return ""
    zone = local_tz(event)
    value = timezone.localtime(value, zone)
    place = TIME_ZONE_PLACES.get(zone.key) or zone.key.rsplit("/", 1)[-1].replace("_", " ")
    return f"{value.day} de {MESES[value.month - 1]} de {value.year}, {value:%H:%M:%S} (hora de {place})"


@event_view(ADMIN)
def participant_data_export(request, event, access, public_id):
    """Authorized export of one person's record (e.g. a request from the family).

    Keys are Spanish words a family can read ("iglesia o grupo", not "iglesia_o_grupo"), and dates are
    written out. The content is the record itself: registry data, the interview and every answer, and the
    authorization records with their notes.
    """
    participant = get_object_or_404(access.participants(include_inactive=True), public_id=public_id)
    if not access.can_view_sensitive:
        raise PermissionDenied
    interview = participant.interviews.exclude(status=Interview.Status.DISCARDED).first()
    answers = []
    if interview:
        values = load_answers(interview)
        answered_at = dict(interview.answers.values_list("question_id", "updated_at"))
        for question in all_questions(interview.version):
            if question.id in values:
                answers.append({
                    "pregunta": question.text,
                    "respuesta": describe_answer(question, values[question.id]),
                    "fecha": _family_date(answered_at.get(question.id), event),
                })
    data = {
        "evento": event.name,
        "número de campero": participant.code,
        "nombre": participant.full_name,
        "edad": participant.age,
        "cuarto": participant.room,
        "iglesia o grupo": participant.church_group,
        "fecha de registro": _family_date(participant.created_at, event),
        "entrevista": {
            "estado": interview.get_status_display() if interview else "Sin entrevista",
            "versión del cuestionario": interview.version.number if interview else None,
            "fecha en que se completó": _family_date(interview.completed_at, event) if interview else "",
            "respuestas": answers,
        },
        # The notes are the campero's own data: for a withdrawn authorization, the reason it was withdrawn.
        "autorizaciones": [
            {"tipo": c.get_kind_display(), "estado": c.get_status_display(), "fecha": _family_date(c.recorded_at, event), "notas": c.notes}
            for c in participant.consents.all()
        ],
    }
    audit.record(request.user, event, "exportacion", f"Descargó los datos completos del campero #{participant.code}", participant, kind="individual")
    response = HttpResponse(json.dumps(data, ensure_ascii=False, indent=2), content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="campero-{participant.code}.json"'
    return response


class ParticipantDeleteForm(forms.Form):
    """The full name, typed; and, when a safety report about the person is open, a box saying so."""

    confirm_name = forms.CharField(
        label="Para confirmar, escribe su nombre completo",
        error_messages={"required": "Escribe su nombre completo exactamente como aparece."},
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )

    def __init__(self, *args, participant, open_safety: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.participant = participant
        self.fields["confirm_name"].help_text = participant.full_name
        if open_safety:
            one = open_safety == 1
            label = "Entiendo que se borrará un reporte de riesgo abierto" if one else f"Entiendo que se borrarán {open_safety} reportes de riesgo abiertos"
            error = f"Marca esta casilla para borrar sus datos junto con {'el reporte abierto' if one else 'los reportes abiertos'}."
            self.fields["understand_safety"] = forms.BooleanField(label=label, error_messages={"required": error})

    def clean_confirm_name(self):
        typed = self.cleaned_data["confirm_name"].strip()
        if typed.lower() != self.participant.full_name.lower():
            raise forms.ValidationError("Escribe su nombre completo exactamente como aparece.")
        return typed


def _erased_counts(participant, access) -> dict:
    """What deleting the person takes with it, as counts only (never what any of it says).

    Only what this viewer may see in "Seguimiento" (``access.follow_ups()``): confidential referrals are
    counted only with confidential access, so the page of an administrator without it reads the same
    whether or not one exists. The template says, always, that they would be deleted too.
    """
    open_follow_ups = access.follow_ups().filter(participant=participant, status__in=FollowUp.OPEN_STATUSES)
    return {
        "open_care": open_follow_ups.filter(kind=FollowUp.Kind.CARE).count(),
        "open_safety": open_follow_ups.filter(kind=FollowUp.Kind.SAFETY).count(),
        "interviews": participant.interviews.exclude(status=Interview.Status.DISCARDED).count(),
    }


@event_view(ADMIN)
def participant_delete(request, event, access, public_id):
    participant = get_object_or_404(access.participants(include_inactive=True), public_id=public_id)
    # Counted again on every request: a report filed after the page was opened still needs its own box.
    counts = _erased_counts(participant, access)
    form = ParticipantDeleteForm(request.POST or None, participant=participant, open_safety=counts["open_safety"])
    if request.method == "POST" and form.is_valid():
        participant_svc.delete_participant_data(participant, actor=request.user)
        messages.success(request, "Se eliminaron los datos del campero.")
        return redirect("events:participants", slug=event.slug)
    return render(request, "events/admin/participant_delete.html", {"participant": participant, "form": form, "counts": counts})


# ---- team -----------------------------------------------------------------------------------


def _managed_elsewhere(event, user) -> bool:
    """A superuser, or an account with a role or a team in another church: this administration does not own it."""
    if user.is_superuser:
        return True
    if UserChurchRole.objects.filter(user=user).exclude(church_id=event.church_id).exists():
        return True
    return EventMembership.objects.filter(user=user).exclude(event__church_id=event.church_id).exists()


def _can_issue_activation_link(event, user) -> bool:
    """Only for accounts created here that never set a password.

    An account that already has a password, a staff/superuser account, or one that also
    belongs to another church must use "¿Olvidaste tu contraseña?" (e-mail to its owner).
    """
    if user.has_usable_password() or user.is_staff:
        return False
    return not _managed_elsewhere(event, user)


def _activation_link(request, user) -> str:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return request.build_absolute_uri(reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token}))


def _member_added(request, event, added):
    """After "Agregar persona" or an approved request: a new account gets its link (by e-mail and on screen).

    An existing account that never set a password gets the link too, when ``_can_issue_activation_link``
    allows it; otherwise it could never sign in. The message says an e-mail went out only when one could.
    """
    membership, user = added.membership, added.user
    if added.is_new or _can_issue_activation_link(event, user):
        created_link = _activation_link(request, user)
        configured = team_svc.email_is_configured()
        sent = configured and send_email_safely(
            f"Te invitaron a {event.name} en Emmaus",
            f"Hola {user.first_name}:\n\nTe agregaron al equipo de {event.name}. Crea tu contraseña aquí:\n{created_link}\n\nSi no esperabas este correo, ignóralo.",
            [user.email],
        )
        if sent:
            delivery = " Le enviamos un correo para crear su contraseña."
        elif not configured:
            delivery = " No hay correo configurado: comparte este enlace con la persona."
        else:
            delivery = " Copia el enlace para que cree su contraseña."
        messages.success(request, f"{membership.display_name} ya forma parte del equipo.{delivery}")
        # The page also writes the message to send by hand (UX-24); it says an e-mail went out only when one did.
        context = {
            "membership": membership,
            "link": created_link,
            "first_name": user.first_name or membership.display_name,
            "role_label": membership.get_role_display(),
            "email_sent": bool(sent),
        }
        return render(request, "events/admin/member_created.html", context)
    if _managed_elsewhere(event, user) or (user.is_staff and not user.has_usable_password()):
        # Its link may only go to its owner's e-mail, and this administration may not learn whether it has a
        # password: one message, with or without one.
        messages.success(
            request,
            f"{membership.display_name} ya forma parte del equipo. Esta cuenta ya existe y la administra otra iglesia "
            "o el sistema; pide a esa administración que le dé acceso.",
        )
        return redirect("events:team", slug=event.slug)
    messages.success(request, f"{membership.display_name} ya forma parte del equipo. Puede entrar con su contraseña de siempre.")
    return redirect("events:team", slug=event.slug)


@event_view(ADMIN)
def member_add(request, event, access):
    form = MemberForm(request.POST or None, event=event)
    if request.method == "POST" and form.is_valid() and form.cleaned_data["can_view_sensitive"] and form.cleaned_data["email"] == (request.user.email or "").lower():
        form.add_error("can_view_sensitive", "Otra persona de la administración debe otorgarte este acceso.")
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            added = team_svc.add_member(
                event, request.user, full_name=data["full_name"], email=data["email"], role=data["role"],
                team_name=data["team_name"], can_view_sensitive=data["can_view_sensitive"], tutor=data.get("tutor"),
            )
        except team_svc.AlreadyMember:  # added by someone else since the form was checked
            form.add_error("email", "Esta persona ya forma parte del equipo de este evento.")
        else:
            return _member_added(request, event, added)
    return render(request, "events/admin/member_form.html", {"form": form})


def _requests_url(event) -> str:
    return reverse("events:team", args=[event.slug]) + "#solicitudes"


@require_POST
@event_view(ADMIN)
def access_request_approve(request, event, access, pk):
    """Add the person who asked, with the role the administration chose (the same path as "Agregar persona")."""
    role = request.POST.get("role", "")
    if role not in MembershipRole.values:
        messages.error(request, "Elige el rol que tendrá esta persona.")
        return redirect(_requests_url(event))
    with transaction.atomic():
        access_request = get_object_or_404(AccessRequest.objects.select_for_update(), pk=pk, event=event)
        if access_request.status != AccessRequest.Status.PENDING:
            messages.info(request, "Esta solicitud ya se atendió.")
            return redirect(_requests_url(event))
        # Anybody can type anybody's e-mail. An account that already has access here (church admin or pastor,
        # superuser, active member) is never changed by a request: that is "Editar", on purpose.
        account = team_svc.account_for_email(access_request.email)
        current = resolve_access(account, event) if account is not None else None
        if current is not None:
            return _refuse_existing_access(request, event, access_request, account.get_full_name() or account.email, current.role_label.lower())
        try:
            added = team_svc.add_member(event, request.user, full_name=access_request.full_name, email=access_request.email, role=role)
        except team_svc.AlreadyMember as already:  # became a member while this request waited (a race)
            membership = already.membership
            return _refuse_existing_access(request, event, access_request, membership.display_name, membership.get_role_display().lower())
        access_request.status = AccessRequest.Status.APPROVED
        access_request.decided_at = timezone.now()
        access_request.decided_by = request.user
        access_request.membership = added.membership
        access_request.save(update_fields=["status", "decided_at", "decided_by", "membership"])
        audit.record(
            request.user, event, "solicitud_aprobada",
            f"Aprobó la solicitud de acceso de {access_request.full_name} como {added.membership.get_role_display().lower()}", access_request,
        )
    return _member_added(request, event, added)


def _refuse_existing_access(request, event, access_request, name, role_label):
    """Close the request without touching the account. Called inside the approval's transaction."""
    access_request.status = AccessRequest.Status.REJECTED
    access_request.decided_at = timezone.now()
    access_request.decided_by = request.user
    access_request.decision_note = f"Ya tenía acceso como {role_label}."
    access_request.save(update_fields=["status", "decided_at", "decided_by", "decision_note"])
    audit.record(request.user, event, "solicitud_rechazada",
                 f"Cerró la solicitud de acceso de {access_request.full_name}: la cuenta ya tenía acceso como {role_label}", access_request)
    messages.warning(request, f"{name} ya tiene acceso a este evento como {role_label}. Si quieres cambiar su rol, hazlo en Equipo, Editar.")
    return redirect(_requests_url(event))


@require_POST
@event_view(ADMIN)
def access_request_reject(request, event, access, pk):
    with transaction.atomic():
        access_request = get_object_or_404(AccessRequest.objects.select_for_update(), pk=pk, event=event)
        if access_request.status != AccessRequest.Status.PENDING:
            messages.info(request, "Esta solicitud ya se atendió.")
            return redirect(_requests_url(event))
        access_request.status = AccessRequest.Status.REJECTED
        access_request.decided_at = timezone.now()
        access_request.decided_by = request.user
        access_request.save(update_fields=["status", "decided_at", "decided_by"])
        audit.record(request.user, event, "solicitud_rechazada", f"Rechazó la solicitud de acceso de {access_request.full_name}", access_request)
    messages.success(request, f"Se rechazó la solicitud de {access_request.full_name}.")
    return redirect(_requests_url(event))


@require_POST
@event_view(ADMIN)
def join_code_regenerate(request, event, access):
    code = event.regenerate_join_code()
    audit.record(request.user, event, "evento_configurado", "Generó un código nuevo para unirse")
    messages.success(request, f"El código anterior y el nombre del evento dejan de funcionar. Ahora solo entra el código nuevo: {code}.")
    return redirect(reverse("events:team", args=[event.slug]) + "#codigo")


@require_POST
@event_view(ADMIN)
def join_by_name_enable(request, event, access):
    """After a new code, the name stays off until the administration turns it back on."""
    event.join_by_name = True
    event.save(update_fields=["join_by_name", "updated_at"])
    audit.record(request.user, event, "evento_configurado", "Volvió a permitir unirse con el nombre del evento")
    messages.success(request, f"Se puede entrar otra vez con el nombre del evento, {event.slug}.")
    return redirect(reverse("events:team", args=[event.slug]) + "#codigo")


@event_view(ADMIN)
def join_code_sign(request, event, access):
    """The page to print and hang where volunteers arrive."""
    event.ensure_join_code()
    return render(request, "events/admin/join_code_sign.html", {
        "join_url": request.build_absolute_uri(reverse("join")),
        "entry_url": request.build_absolute_uri(reverse("event_entry", args=[event.join_code])),
    })


@event_view(ADMIN)
def member_edit(request, event, access, membership_id):
    membership = get_object_or_404(EventMembership.objects.select_related("user"), pk=membership_id, event=event)
    before = {"role": membership.role, "sensitive": membership.can_view_sensitive, "active": membership.is_active}
    form = MembershipEditForm(request.POST or None, instance=membership)
    link = None
    can_link = _can_issue_activation_link(event, membership.user)
    if request.method == "POST" and request.POST.get("action") == "link":
        if not can_link:
            raise PermissionDenied
        link = _activation_link(request, membership.user)
        audit.record(request.user, event, "integrante_modificado", f"Generó un enlace para crear contraseña de {membership.display_name}", membership)
    elif request.method == "POST" and form.is_valid():
        if membership.user_id == request.user.id and not form.cleaned_data["is_active"]:
            form.add_error("is_active", "No puedes desactivar tu propio acceso.")
        elif membership.user_id == request.user.id and form.cleaned_data["can_view_sensitive"] and not before["sensitive"]:
            form.add_error("can_view_sensitive", "Otra persona de la administración debe otorgarte este acceso.")
        else:
            with transaction.atomic():
                saved = form.save()
                tutor = form.cleaned_data.get("tutor")
                TutorAssignment.objects.filter(coordinator=saved).delete()
                if saved.role == MembershipRole.COORDINATOR and tutor:
                    TutorAssignment.objects.create(event=event, tutor=tutor, coordinator=saved, created_by=request.user)
                if saved.role != MembershipRole.TUTOR:
                    TutorAssignment.objects.filter(tutor=saved).delete()
                if saved.role != MembershipRole.COORDINATOR:
                    EventParticipant.objects.filter(coordinator=saved).update(coordinator=None)
                audit.record(request.user, event, "integrante_modificado", f"Modificó el acceso de {saved.display_name}", saved,
                             role=[before["role"], saved.role], active=[before["active"], saved.is_active])
                if before["sensitive"] != saved.can_view_sensitive:
                    audit.record(
                        request.user, event, "acceso_confidencial",
                        f"{'Otorgó' if saved.can_view_sensitive else 'Retiró'} acceso confidencial a {saved.display_name}", saved,
                    )
            messages.success(request, "Cambios guardados.")
            return redirect("events:team", slug=event.slug)
    return render(request, "events/admin/member_form.html", {"form": form, "membership": membership, "link": link, "can_link": can_link})


@event_view(ADMIN)
def assignments(request, event, access):
    coordinators = EventMembership.objects.filter(event=event, role=MembershipRole.COORDINATOR).select_related("user", "tutor_assignment")
    tutors = EventMembership.objects.filter(event=event, role=MembershipRole.TUTOR, is_active=True).select_related("user")
    if request.method == "POST":
        tutor_by_id = {t.id: t for t in EventMembership.objects.filter(event=event, role=MembershipRole.TUTOR)}
        changed = 0
        with transaction.atomic():
            for coordinator in coordinators:
                field = f"tutor_{coordinator.id}"
                if field not in request.POST:
                    continue  # a stale or partial form never clears assignments it did not show
                raw = request.POST.get(field, "")
                current = getattr(coordinator, "tutor_assignment", None)
                target = tutor_by_id.get(int(raw)) if raw.isascii() and raw.isdigit() else None
                if (current.tutor_id if current else None) == (target.id if target else None):
                    continue
                TutorAssignment.objects.filter(coordinator=coordinator).delete()
                if target:
                    TutorAssignment.objects.create(event=event, tutor=target, coordinator=coordinator, created_by=request.user)
                changed += 1
            if changed:
                audit.record(request.user, event, "asignacion_tutor", f"Cambió {changed} {'asignación' if changed == 1 else 'asignaciones'} de tutor", None, count=changed)
        messages.success(request, "Asignaciones guardadas." if changed else "No hubo cambios.")
        return redirect("events:assignments", slug=event.slug)
    counts = dict(event.participants.filter(is_active=True).values_list("coordinator").annotate(n=Count("id")))
    rows = []
    for c in coordinators:
        current = getattr(c, "tutor_assignment", None)
        options = list(tutors)
        if current and current.tutor not in options:
            options.append(current.tutor)  # keep an inactive tutor selectable so saving does not unassign
        rows.append({"coordinator": c, "current": current, "count": counts.get(c.id, 0), "options": options})
    return render(request, "events/admin/assignments.html", {"rows": rows, "tutors": tutors})


# ---- questionnaires -------------------------------------------------------------------------


@event_view(ADMIN)
def questionnaires(request, event, access):
    versions = QuestionnaireVersion.objects.filter(questionnaire__church=event.church).select_related("questionnaire").annotate(
        uses=Count("interviews")
    )
    return render(request, "events/admin/questionnaires.html", {"versions": versions})


def _version_for(event, version_id) -> QuestionnaireVersion:
    return get_object_or_404(QuestionnaireVersion, pk=version_id, questionnaire__church=event.church)


@event_view(ADMIN)
def version_detail(request, event, access, version_id):
    version = _version_for(event, version_id)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "duplicate":
            draft = duplicate_version(version, request.user)
            audit.record(request.user, event, "cuestionario", f"Creó el borrador versión {draft.number}", draft)
            messages.success(request, f"Se creó la versión {draft.number} como borrador. Puedes editarla.")
            return redirect("events:version_detail", slug=event.slug, version_id=draft.id)
        if action == "publish" and version.is_editable:
            try:
                publish_version(version, request.user)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                audit.record(request.user, event, "cuestionario", f"Publicó la versión {version.number}", version)
                messages.success(request, f"Versión {version.number} publicada. Ya no se puede modificar.")
            return redirect("events:version_detail", slug=event.slug, version_id=version.id)
        if action == "use" and version.status == QuestionnaireVersion.Status.PUBLISHED:
            event.questionnaire_version = version
            event.save(update_fields=["questionnaire_version", "updated_at"])
            audit.record(request.user, event, "cuestionario", f"Usará la versión {version.number} en entrevistas nuevas", version)
            messages.success(request, "Las entrevistas nuevas usarán esta versión. Las anteriores conservan la suya.")
            return redirect("events:version_detail", slug=event.slug, version_id=version.id)
        if action == "delete_question" and version.is_editable:
            question = Question.objects.filter(pk=request.POST.get("question") or 0, section__version=version).first()
            if question is None:
                raise Http404
            question.delete()
            return redirect("events:version_detail", slug=event.slug, version_id=version.id)
        if action == "delete_option" and version.is_editable:
            option = QuestionOption.objects.filter(pk=request.POST.get("option") or 0, question__section__version=version).first()
            if option:
                option.delete()
            return redirect("events:version_detail", slug=event.slug, version_id=version.id)
    sections = version.sections.prefetch_related("questions__options", "questions__condition_question")
    return render(
        request,
        "events/admin/version_detail.html",
        {"version": version, "sections": sections, "steps": build_steps(version), "in_use": event.questionnaire_version_id == version.id},
    )


@event_view(ADMIN)
def section_add(request, event, access, version_id):
    version = _version_for(event, version_id)
    if not version.is_editable:
        raise PermissionDenied
    form = SectionForm(request.POST or None, initial={"order": version.sections.count() + 1})
    if request.method == "POST" and form.is_valid():
        section = form.save(commit=False)
        section.version = version
        section.save()
        return redirect("events:version_detail", slug=event.slug, version_id=version.id)
    return render(request, "events/admin/simple_form.html", {"form": form, "title": "Nueva sección", "submit_label": "Guardar sección", "back": reverse("events:version_detail", args=[event.slug, version.id])})


@event_view(ADMIN)
def question_edit(request, event, access, version_id, question_id=None, section_id=None):
    version = _version_for(event, version_id)
    if not version.is_editable:
        raise PermissionDenied
    if question_id:
        question = get_object_or_404(Question, pk=question_id, section__version=version)
        section = question.section
    else:
        section = get_object_or_404(Section, pk=section_id, version=version)
        question = Question(section=section, order=section.questions.count() + 1)
    form = QuestionForm(request.POST or None, instance=question, version=version)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.section = section
        if not item.code:
            base = slugify(item.text)[:50] or "pregunta"
            code, n = base, 2
            while Question.objects.filter(section__version=version, code=code).exclude(pk=item.pk).exists():
                code, n = f"{base}-{n}", n + 1
            item.code = code
        item.condition_option_codes = form.cleaned_data["condition_codes_list"]
        item.save()
        messages.success(request, "Pregunta guardada.")
        return redirect("events:version_detail", slug=event.slug, version_id=version.id)
    return render(request, "events/admin/question_form.html", {"form": form, "version": version, "question": question})


@event_view(ADMIN)
def option_add(request, event, access, version_id, question_id):
    version = _version_for(event, version_id)
    question = get_object_or_404(Question, pk=question_id, section__version=version)
    if not version.is_editable:
        raise PermissionDenied
    form = OptionForm(request.POST or None, initial={"order": question.options.count() + 1})
    if request.method == "POST" and form.is_valid():
        option = form.save(commit=False)
        option.question = question
        base = slugify(option.label)[:50] or "opcion"
        code, n = base, 2
        while question.options.filter(code=code).exists():
            code, n = f"{base}-{n}", n + 1
        option.code = code
        option.save()
        return redirect("events:version_detail", slug=event.slug, version_id=version.id)
    return render(request, "events/admin/simple_form.html", {"form": form, "title": f"Nueva opción para “{question.text}”", "submit_label": "Guardar opción", "back": reverse("events:version_detail", args=[event.slug, version.id])})


# ---- settings, stations, audit, sessions ------------------------------------------------------


@event_view(ADMIN)
def settings_view(request, event, access):
    # Its own copy of the event: a bound form writes its clean values into its instance even when it is invalid, and
    # the page (links, the computers' forms, the bar) is drawn from ``event``: an unsaved slug made them point nowhere.
    basics = EventForm(request.POST if request.POST.get("form") == "basics" else None, instance=Event.objects.get(pk=event.pk), prefix="basics")
    settings_form = EventSettingsForm(request.POST if request.POST.get("form") == "settings" else None, instance=event, prefix="cfg")
    procedures = ProceduresForm(request.POST if request.POST.get("form") == "procedures" else None)
    if request.method == "POST":
        which = request.POST.get("form")
        target = {"basics": basics, "settings": settings_form, "procedures": procedures}.get(which)
        if target is not None and target.is_valid():
            slug = event.slug
            if which == "procedures":
                event.procedures_approved_at = timezone.now()
                event.procedures_approved_by = request.user
                event.save(update_fields=["procedures_approved_at", "procedures_approved_by", "updated_at"])
                audit.record(request.user, event, "procedimientos_aprobados", "Confirmó que la iglesia aprobó sus procedimientos", event)
            else:
                saved = target.save()
                audit.record(request.user, event, "evento_configurado", "Cambió la configuración del evento", event, form=which)
                slug = saved.slug  # a new slug is a new address
            # Back to the section the form is in: the page is long (audit UX-14)
            section = {"basics": "datos", "settings": "cortes", "procedures": "procedimientos"}[which]
            messages.success(request, "Datos del evento guardados." if which == "basics" else "Configuración guardada.")
            return redirect(reverse("events:settings", args=[slug]) + f"#{section}")
    return render(
        request,
        "events/admin/settings.html",
        {"basics": basics, "settings_form": settings_form, "procedures": procedures, "stations": event.stations.all()},
    )


@require_POST
@event_view(ADMIN)
def stations(request, event, access):
    action = request.POST.get("action")
    if action == "add":
        number = (event.stations.order_by("-number").values_list("number", flat=True).first() or 0) + 1
        InterviewStation.objects.create(event=event, number=number, name=f"Computadora {number}")
    elif action == "toggle":
        station = get_object_or_404(InterviewStation, pk=request.POST.get("station"), event=event)
        station.is_active = not station.is_active
        station.save(update_fields=["is_active"])
    return redirect(reverse("events:settings", args=[event.slug]) + "#computadoras")


@event_view(ADMIN)
def audit_log(request, event, access):
    qs = AuditEvent.objects.filter(event=event).select_related("actor")
    action = request.GET.get("accion", "")
    if action in ACTION_LABELS:
        qs = qs.filter(action=action)
    page = Paginator(qs, 50).get_page(request.GET.get("pagina"))
    # Without confidential access, rows about confidential follow-ups and safety reports say only
    # "Movimiento confidencial": their summaries name the campero.
    rows = audit.with_shown_summaries(page.object_list, access)
    return render(
        request,
        "events/admin/audit.html",
        {"page": page, "rows": rows, "actions": sorted(ACTION_LABELS.items(), key=lambda kv: kv[1]), "selected": action},
    )


@event_view(ADMIN)
def sessions(request, event, access):
    if request.method == "POST":
        import uuid

        def valid_uuid(raw):
            try:
                return uuid.UUID(raw)
            except (TypeError, ValueError):
                raise Http404

        if request.POST.get("participant_session"):
            ps = get_object_or_404(ParticipantSession, public_id=valid_uuid(request.POST["participant_session"]), interview__event=event)
            interview_svc.revoke_participant_session(ps, actor=request.user)
            messages.success(request, "Se cerró la pantalla del campero. La entrevista sigue en curso para retomarla.")
        elif request.POST.get("interview"):
            interview = get_object_or_404(Interview, public_id=valid_uuid(request.POST["interview"]), event=event)
            interview_svc.revoke_claim(interview, actor=request.user)
            messages.success(request, "La entrevista quedó libre para abrirse en otra computadora.")
        else:
            raise Http404
        return redirect("events:sessions", slug=event.slug)
    now = timezone.now()
    captures = ParticipantSession.objects.filter(interview__event=event).exclude(state=ParticipantSession.State.CLOSED).select_related(
        "interview__participant", "station", "started_by"
    )
    claims = Interview.objects.filter(event=event, status=Interview.Status.IN_PROGRESS).select_related("participant", "lock_holder", "station").order_by("-last_activity_at")
    return render(request, "events/admin/sessions.html", {"captures": captures, "claims": claims, "now": now})


# ---- sedes ----------------------------------------------------------------------------------
# Imported next to their only users so that this whole area stays at the end of the file.
from django.db.models import ProtectedError  # noqa: E402

from ..access import administered_churches  # noqa: E402
from ..forms import EventSedesForm, SedeForm  # noqa: E402
from ..models import Sede  # noqa: E402


def _sede_options(event, access, filtered) -> list[dict]:
    """The "Sede" filter of Participantes: "Mérida (80)" for each sede, and "Sin sede".

    ``filtered`` is the list under every other filter (participation, interview, coordinator, search), so each
    count is the rows that choosing it shows. Empty when the church has no sedes. A sede that is no longer
    offered stays listed while camperos this person can see have it, whatever the other filters leave.
    """
    counts = dict(filtered.order_by().values_list("sede_id").annotate(n=Count("id")))
    offered = set(event.allowed_sedes().values_list("pk", flat=True))
    in_use = set(access.participants(include_inactive=True).exclude(sede=None).order_by().values_list("sede_id", flat=True))
    sedes = [s for s in Sede.objects.filter(church_id=event.church_id) if s.pk in offered or s.pk in in_use]
    if not sedes:
        return []
    options = [{"value": str(s.pk), "label": f"{s.name} ({counts.get(s.pk, 0)})"} for s in sedes]
    options.append({"value": "ninguna", "label": f"Sin sede ({counts.get(None, 0)})"})
    return options


@event_view(ADMIN)
def sample_csv(request, event, access):
    """The example file of "Importar camperos": the headers the import reads and two rows it accepts."""
    sedes = [s.name for s in event.allowed_sedes()[:2]]
    coordinator = access.coordinators().filter(is_active=True, user__email__gt="").select_related("user").first()
    rows = [
        ["Mariana López Ruiz", 15, "4", "Iglesia Centro", coordinator.user.email if coordinator else "", "sí", sedes[0] if sedes else ""],
        ["Diego Hernández Cruz", 17, "Cabaña 2", "Iglesia Centro", "", "no", sedes[-1] if sedes else ""],
    ]
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="camperos-ejemplo.csv"'
    response.write("\ufeff")  # Excel opens UTF-8 correctly with a BOM
    writer = csv.writer(response)
    writer.writerow(participant_svc.IMPORT_COLUMNS)
    for row in rows:
        writer.writerow([_safe_cell(value) for value in row])
    return response


def _manages_sedes(user, event) -> bool:
    """Sedes belong to the church: a church administrator (or a superuser) adds, edits and deletes them. An
    Administrador who is only a member of one event chooses which of them take part in it, nothing more."""
    return administered_churches(user).filter(pk=event.church_id).exists()


@event_view(ADMIN)
def sedes(request, event, access):
    """Sedes de la iglesia: the church's campuses and which of them take part in this event."""
    # Every box unchecked sends no "sedes" at all: still a submission (the whole church), not an empty form.
    form = EventSedesForm(request.POST if request.method == "POST" else None, event=event)
    if request.method == "POST" and form.is_valid():
        chosen = form.cleaned_data["sedes"]
        event.sedes.set(chosen)
        names = ", ".join(s.name for s in chosen) or "todas"
        audit.record(request.user, event, "evento_configurado", f"Cambió las sedes que participan: {names}", event, form="sedes")
        messages.success(request, "Sedes del evento guardadas.")
        return redirect("events:sedes", slug=event.slug)
    rows = Sede.objects.filter(church_id=event.church_id).annotate(
        n=Count("participants", filter=Q(participants__event=event, participants__is_active=True))
    )
    chosen = list(event.sedes.all())
    context = {
        "sedes": rows,
        "form": form,
        "chosen_active": [s for s in chosen if s.active],
        "chosen_inactive": [s for s in chosen if not s.active],
        "manages_sedes": _manages_sedes(request.user, event),
    }
    return render(request, "events/admin/sedes.html", context)


@event_view(ADMIN)
def sede_edit(request, event, access, sede_id=None):
    if not _manages_sedes(request.user, event):
        raise PermissionDenied
    sede = get_object_or_404(Sede, pk=sede_id, church_id=event.church_id) if sede_id else None
    form = SedeForm(request.POST or None, instance=sede, church=event.church)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        verb = "Agregó" if sede is None else "Modificó"
        audit.record(request.user, event, "evento_configurado", f"{verb} la sede «{saved.name}»", saved, form="sede")
        messages.success(request, f"Sede «{saved.name}» guardada.")
        return redirect("events:sedes", slug=event.slug)
    in_use = sede is not None and sede.participants.exists()
    return render(request, "events/admin/sede_form.html", {"form": form, "sede": sede, "in_use": in_use})


@require_POST
@event_view(ADMIN)
def sede_delete(request, event, access, sede_id):
    """Only a sede no campero has (in any event of the church). One with camperos is deactivated instead."""
    if not _manages_sedes(request.user, event):
        raise PermissionDenied
    sede = get_object_or_404(Sede, pk=sede_id, church_id=event.church_id)
    name = sede.name
    try:
        with transaction.atomic():
            sede.delete()
    except ProtectedError:
        messages.error(request, f"«{name}» tiene camperos registrados y no se puede borrar. Desactívala para que ya no aparezca.")
        return redirect("events:sede_edit", slug=event.slug, sede_id=sede_id)
    audit.record(request.user, event, "evento_configurado", f"Borró la sede «{name}»", None, form="sede")
    messages.success(request, f"Se borró la sede «{name}».")
    return redirect("events:sedes", slug=event.slug)
