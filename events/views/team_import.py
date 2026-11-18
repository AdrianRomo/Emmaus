"""«Importar equipo» (issue #50): a CSV of the event team, reviewed before anything is written.

Administrators of this event only (``event_view(ADMIN)``, which resolves access inside the event's church). The
rules of what a row may do live in ``services/team_import``; the invitations go out through ``admin._invite``,
the same path as "Agregar persona".
"""

from __future__ import annotations

import csv

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render

from ..forms import TeamImportForm
from ..services import team_import as import_svc
from ..services.exports import safe_cell
from .admin import _can_issue_activation_link, _invite, _managed_elsewhere
from .common import ADMIN, event_view

SESSION_KEY = "team_import_preview"


@event_view(ADMIN)
def team_import(request, event, access):
    preview = request.session.get(SESSION_KEY)
    if preview and preview.get("event") != event.id:
        preview = None
    action = request.POST.get("action") if request.method == "POST" else None
    if action == "cancel":
        request.session.pop(SESSION_KEY, None)
        return redirect("events:team_import", slug=event.slug)
    if action == "confirm" and preview:
        include = {int(line) for line in request.POST.getlist("include") if line.isascii() and line.isdigit()}
        result = import_svc.apply(event, preview["rows"], actor=request.user, include_lines=include)
        request.session.pop(SESSION_KEY, None)
        if not result.added:
            messages.info(request, "No se agregó a nadie: todas las personas del archivo ya estaban en el equipo o no marcaste ninguna fila.")
            return redirect("events:team", slug=event.slug)
        return _imported(request, event, result)
    form = TeamImportForm(request.POST or None, request.FILES or None)
    problems = []
    if request.method == "POST" and action != "confirm" and form.is_valid():
        upload = form.cleaned_data["file"]
        rows, problems = import_svc.read_file(upload.read())
        if not problems:
            request.session[SESSION_KEY] = {"event": event.id, "rows": rows, "name": upload.name[:120]}
            return redirect("events:team_import", slug=event.slug)
    context = {"form": form, "problems": problems, "preview": preview, "columns": import_svc.TEAM_COLUMNS,
               "max_rows": import_svc.MAX_TEAM_ROWS}
    if preview:
        rows = import_svc.plan(event, preview["rows"])  # read again on every visit: it shows the team as it is now
        context["rows"] = rows
        context["summary"] = {
            "total": len(rows),
            "new": sum(1 for r in rows if r.is_new),
            "unchanged": sum(1 for r in rows if r.is_unchanged),
            "errors": sum(1 for r in rows if r.errors),
        }
        context["email_configured"] = import_svc.team_svc.email_is_configured()
    return render(request, "events/admin/team_import.html", context)


def _imported(request, event, result):
    """Invite each person added and say, one by one, what happened with their e-mail. A link that could not be
    sent is shown here once (never stored): the administrator shares it by hand, as on "Persona agregada"."""
    people = []
    for added in result.added:
        membership, user = added.membership, added.user
        person = {"membership": membership, "link": "", "delivery": ""}
        if added.is_new or _can_issue_activation_link(event, user):
            link, sent, configured = _invite(request, event, user)
            if sent:
                person["delivery"] = "sent"
            else:
                person["delivery"] = "failed" if configured else "not_configured"
                person["link"] = link
        elif _managed_elsewhere(event, user) or (user.is_staff and not user.has_usable_password()):
            person["delivery"] = "elsewhere"
        else:
            person["delivery"] = "has_password"
        people.append(person)
    counts = {key: sum(1 for p in people if p["delivery"] == key) for key in ("sent", "failed", "not_configured")}
    return render(request, "events/admin/team_import_done.html", {
        "people": people, "counts": counts, "links": counts["failed"] + counts["not_configured"],
        "unchanged": result.unchanged, "without_tutor": result.without_tutor,
    })


@event_view(ADMIN)
def team_import_sample(request, event, access):
    """The example file of "Importar equipo": the headers the import reads and three rows it accepts. The
    addresses end in .invalid, which no mail server delivers: the file must be edited before it is useful."""
    rows = [
        ["Teresa Molina Vega", "teresa.molina@ejemplo.invalid", "tutor", "Equipo Monte Sion", ""],
        ["Carlos Ruiz Peña", "carlos.ruiz@ejemplo.invalid", "coordinador", "", "teresa.molina@ejemplo.invalid"],
        ["Paola Núñez Lara", "paola.nunez@ejemplo.invalid", "coordinador", "Equipo Monte Sion", ""],
    ]
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="equipo-ejemplo.csv"'
    response.write("﻿")  # Excel opens UTF-8 correctly with a BOM
    writer = csv.writer(response)
    writer.writerow(import_svc.TEAM_COLUMNS)
    for row in rows:
        writer.writerow([safe_cell(value) for value in row])
    return response
