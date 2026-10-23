from __future__ import annotations

import csv
import json

from django.contrib import messages
from django.contrib.auth.models import User
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
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from core.services import full_name_to_parts, send_email_safely

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
    AuditEvent,
    EventMembership,
    EventParticipant,
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
from ..services.audit import ACTION_LABELS
from ..services.questionnaire import all_questions, build_steps, describe_answer, duplicate_version, load_answers, publish_version
from .common import ADMIN, event_view

IMPORT_SESSION_KEY = "import_preview"


# ---- participants ---------------------------------------------------------------------------


@event_view(ADMIN, MembershipRole.PASTOR, MembershipRole.TUTOR)
def participants(request, event, access):
    qs = access.participants(include_inactive=True).select_related("coordinator__user")
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("estado", "")
    coordinator = request.GET.get("coordinador", "")
    active = request.GET.get("participa", "si")
    if query:
        filters = Q(normalized_name__contains=normalize_name(query)) | Q(room__iexact=query) | Q(church_group__icontains=query)
        if query.lstrip("#").isdigit():
            filters |= Q(number=int(query.lstrip("#")))
        qs = qs.filter(filters)
    if status in InterviewStatus.values:
        qs = qs.filter(interview_status=status)
    if coordinator == "ninguno":
        qs = qs.filter(coordinator__isnull=True)
    elif coordinator.isdigit():
        qs = qs.filter(coordinator_id=int(coordinator))
    if active == "si":
        qs = qs.filter(is_active=True)
    elif active == "no":
        qs = qs.filter(is_active=False)
    page = Paginator(qs.order_by("full_name"), 50).get_page(request.GET.get("pagina"))
    return render(
        request,
        "events/admin/participants.html",
        {
            "page": page,
            "query": query,
            "selected": {"estado": status, "coordinador": coordinator, "participa": active},
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
        include = {int(line) for line in request.POST.getlist("include") if line.isdigit()}
        created = participant_svc.apply_import(event, preview["rows"], actor=request.user, include_lines=include)
        request.session.pop(IMPORT_SESSION_KEY, None)
        messages.success(request, f"Se importaron {created} camperos.")
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


@event_view(ADMIN)
def participant_export(request, event, access):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    response["Content-Disposition"] = f'attachment; filename="camperos-{event.slug}-{stamp}.csv"'
    response.write("﻿")  # Excel opens UTF-8 correctly with a BOM
    writer = csv.writer(response)
    writer.writerow(["Número", "Nombre", "Edad", "Cuarto", "Iglesia o grupo", "Coordinador", "Entrevista", "Participa", "Registro"])
    for p in access.participants(include_inactive=True).select_related("coordinator__user").order_by("number"):
        writer.writerow([
            p.code, p.full_name, p.age, p.room, p.church_group,
            p.coordinator.display_name if p.coordinator else "",
            p.get_interview_status_display(), "Sí" if p.is_active else "No",
            timezone.localtime(p.created_at).strftime("%d/%m/%Y %H:%M"),
        ])
    audit.record(request.user, event, "exportacion", "Descargó la lista de camperos (sin respuestas)", None, kind="lista")
    return response


@event_view(ADMIN)
def participant_data_export(request, event, access, public_id):
    """Authorized export of one person's record (e.g. a request from the family)."""
    participant = get_object_or_404(access.participants(include_inactive=True), public_id=public_id)
    if not access.can_view_sensitive:
        raise PermissionDenied
    interview = participant.interviews.exclude(status=Interview.Status.DISCARDED).first()
    answers = []
    if interview:
        values = load_answers(interview)
        for question in all_questions(interview.version):
            if question.id in values:
                answers.append({"pregunta": question.text, "respuesta": describe_answer(question, values[question.id])})
    data = {
        "evento": event.name,
        "numero": participant.code,
        "nombre": participant.full_name,
        "edad": participant.age,
        "cuarto": participant.room,
        "iglesia_o_grupo": participant.church_group,
        "registro": participant.created_at.isoformat(),
        "entrevista": {
            "estado": interview.get_status_display() if interview else "Sin entrevista",
            "version_cuestionario": interview.version.number if interview else None,
            "completada": interview.completed_at.isoformat() if interview and interview.completed_at else None,
            "respuestas": answers,
        },
        "autorizaciones": [
            {"tipo": c.get_kind_display(), "estado": c.get_status_display(), "fecha": c.recorded_at.isoformat()}
            for c in participant.consents.all()
        ],
    }
    audit.record(request.user, event, "exportacion", f"Descargó los datos completos del campero #{participant.code}", participant, kind="individual")
    response = HttpResponse(json.dumps(data, ensure_ascii=False, indent=2), content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="campero-{participant.code}.json"'
    return response


@event_view(ADMIN)
def participant_delete(request, event, access, public_id):
    participant = get_object_or_404(access.participants(include_inactive=True), public_id=public_id)
    if request.method == "POST":
        if request.POST.get("confirm_name", "").strip().lower() != participant.full_name.lower():
            messages.error(request, "Escribe el nombre completo exactamente como aparece para confirmar.")
        else:
            participant_svc.delete_participant_data(participant, actor=request.user)
            messages.success(request, "Se eliminaron los datos del campero.")
            return redirect("events:participants", slug=event.slug)
    return render(request, "events/admin/participant_delete.html", {"participant": participant})


# ---- team -----------------------------------------------------------------------------------


def _activation_link(request, user) -> str:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return request.build_absolute_uri(reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token}))


@event_view(ADMIN)
def member_add(request, event, access):
    form = MemberForm(request.POST or None, event=event)
    created_link = None
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            user = User.objects.filter(email__iexact=data["email"]).first()
            is_new = user is None
            if is_new:
                first, last = full_name_to_parts(data["full_name"])
                user = User.objects.create_user(username=data["email"], email=data["email"], first_name=first, last_name=last)
                user.set_unusable_password()
                user.save()
            membership = EventMembership.objects.create(
                event=event,
                user=user,
                role=data["role"],
                team_name=data["team_name"] if data["role"] == MembershipRole.TUTOR else "",
                can_view_sensitive=data["can_view_sensitive"],
                created_by=request.user,
            )
            if data["role"] == MembershipRole.COORDINATOR and data.get("tutor"):
                TutorAssignment.objects.create(event=event, tutor=data["tutor"], coordinator=membership, created_by=request.user)
            audit.record(request.user, event, "integrante_agregado", f"Agregó a {membership.display_name} como {membership.get_role_display().lower()}", membership)
            if data["can_view_sensitive"]:
                audit.record(request.user, event, "acceso_confidencial", f"Otorgó acceso confidencial a {membership.display_name}", membership)
        if is_new:
            created_link = _activation_link(request, user)
            sent = send_email_safely(
                f"Te invitaron a {event.name} en Emmaus",
                f"Hola {user.first_name}:\n\nTe agregaron al equipo de {event.name}. Crea tu contraseña aquí:\n{created_link}\n\nSi no esperabas este correo, ignóralo.",
                [user.email],
            )
            messages.success(
                request,
                f"{membership.display_name} ya forma parte del equipo."
                + (" Le enviamos un correo para crear su contraseña." if sent else " Copia el enlace para que cree su contraseña."),
            )
            return render(request, "events/admin/member_created.html", {"membership": membership, "link": created_link})
        messages.success(request, f"{membership.display_name} ya forma parte del equipo. Puede entrar con su contraseña de siempre.")
        return redirect("events:team", slug=event.slug)
    return render(request, "events/admin/member_form.html", {"form": form})


@event_view(ADMIN)
def member_edit(request, event, access, membership_id):
    membership = get_object_or_404(EventMembership.objects.select_related("user"), pk=membership_id, event=event)
    before = {"role": membership.role, "sensitive": membership.can_view_sensitive, "active": membership.is_active}
    form = MembershipEditForm(request.POST or None, instance=membership)
    link = None
    if request.method == "POST" and request.POST.get("action") == "link":
        link = _activation_link(request, membership.user)
    elif request.method == "POST" and form.is_valid():
        if membership.user_id == request.user.id and not form.cleaned_data["is_active"]:
            form.add_error("is_active", "No puedes desactivar tu propio acceso.")
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
    return render(request, "events/admin/member_form.html", {"form": form, "membership": membership, "link": link})


@event_view(ADMIN)
def assignments(request, event, access):
    coordinators = EventMembership.objects.filter(event=event, role=MembershipRole.COORDINATOR).select_related("user", "tutor_assignment")
    tutors = EventMembership.objects.filter(event=event, role=MembershipRole.TUTOR, is_active=True).select_related("user")
    if request.method == "POST":
        tutor_by_id = {t.id: t for t in tutors}
        changed = 0
        with transaction.atomic():
            for coordinator in coordinators:
                raw = request.POST.get(f"tutor_{coordinator.id}", "")
                current = getattr(coordinator, "tutor_assignment", None)
                target = tutor_by_id.get(int(raw)) if raw.isdigit() else None
                if (current.tutor_id if current else None) == (target.id if target else None):
                    continue
                TutorAssignment.objects.filter(coordinator=coordinator).delete()
                if target:
                    TutorAssignment.objects.create(event=event, tutor=target, coordinator=coordinator, created_by=request.user)
                changed += 1
            if changed:
                audit.record(request.user, event, "asignacion_tutor", f"Cambió {changed} asignación(es) de tutor", None, count=changed)
        messages.success(request, "Asignaciones guardadas." if changed else "No hubo cambios.")
        return redirect("events:assignments", slug=event.slug)
    counts = dict(event.participants.filter(is_active=True).values_list("coordinator").annotate(n=Count("id")))
    rows = [{"coordinator": c, "current": getattr(c, "tutor_assignment", None), "count": counts.get(c.id, 0)} for c in coordinators]
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
            Question.objects.filter(pk=request.POST.get("question"), section__version=version).first().delete()
            return redirect("events:version_detail", slug=event.slug, version_id=version.id)
        if action == "delete_option" and version.is_editable:
            option = QuestionOption.objects.filter(pk=request.POST.get("option"), question__section__version=version).first()
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
    return render(request, "events/admin/simple_form.html", {"form": form, "title": "Nueva sección", "back": reverse("events:version_detail", args=[event.slug, version.id])})


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
    return render(request, "events/admin/simple_form.html", {"form": form, "title": f"Nueva opción para “{question.text}”", "back": reverse("events:version_detail", args=[event.slug, version.id])})


# ---- settings, stations, audit, sessions ------------------------------------------------------


@event_view(ADMIN)
def settings_view(request, event, access):
    basics = EventForm(request.POST if request.POST.get("form") == "basics" else None, instance=event, prefix="basics")
    settings_form = EventSettingsForm(request.POST if request.POST.get("form") == "settings" else None, instance=event, prefix="cfg")
    procedures = ProceduresForm(request.POST if request.POST.get("form") == "procedures" else None)
    if request.method == "POST":
        which = request.POST.get("form")
        target = {"basics": basics, "settings": settings_form, "procedures": procedures}.get(which)
        if target is not None and target.is_valid():
            if which == "procedures":
                event.procedures_approved_at = timezone.now()
                event.procedures_approved_by = request.user
                event.save(update_fields=["procedures_approved_at", "procedures_approved_by", "updated_at"])
                audit.record(request.user, event, "procedimientos_aprobados", "Confirmó que la iglesia aprobó sus procedimientos", event)
            else:
                saved = target.save()
                audit.record(request.user, event, "evento_configurado", "Cambió la configuración del evento", event, form=which)
                if which == "basics" and saved.slug != event.slug:
                    return redirect("events:settings", slug=saved.slug)
            messages.success(request, "Configuración guardada.")
            return redirect("events:settings", slug=event.slug)
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
    return render(request, "events/admin/audit.html", {"page": page, "actions": sorted(ACTION_LABELS.items(), key=lambda kv: kv[1]), "selected": action})


@event_view(ADMIN)
def sessions(request, event, access):
    if request.method == "POST":
        if request.POST.get("participant_session"):
            ps = get_object_or_404(ParticipantSession, public_id=request.POST["participant_session"], interview__event=event)
            interview_svc.revoke_participant_session(ps, actor=request.user)
            messages.success(request, "Sesión de captura cerrada. La entrevista quedó en curso para retomarla.")
        elif request.POST.get("interview"):
            interview = get_object_or_404(Interview, public_id=request.POST["interview"], event=event)
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
