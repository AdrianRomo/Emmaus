""""Importar personas": a CSV reviewed before anything is written, and the undo of an import.

Every view: ``people.views.church_admin_required`` (the ``people`` module on for the current church,
404 otherwise; then church ``manage`` through ``core.authz.has_scope``, 403 otherwise). An import is
fetched inside ``request.church``: the id of another church's is a 404. What a file may do is in
``people.imports``; nothing here writes a person.
"""

import csv

from django import forms
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.forms import TuErrorMessagesMixin
from events.services.exports import safe_cell

from . import imports
from .models import PeopleImport, Person
from .services import PeopleError
from .views import church_admin_required

SHOWN_NEW = 100  # of the rows that would be created; the screen says how many more there are


class ImportForm(TuErrorMessagesMixin, forms.Form):
    file = forms.FileField(
        label="Archivo CSV",
        help_text="Columnas: Nombre. Opcionales: Apellidos, Correo, Teléfono, Fecha de nacimiento, Etapa y Etiquetas.",
        error_messages={"required": "Elige un archivo CSV."},
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > imports.MAX_BYTES:
            raise forms.ValidationError("El archivo es muy grande. El máximo es 1 MB.")
        if not upload.name.lower().endswith((".csv", ".txt")):
            raise forms.ValidationError("El archivo debe ser .csv. Guarda la hoja de cálculo como CSV (UTF-8) y súbela otra vez.")
        return upload


def _recent(church) -> list[PeopleImport]:
    rows = list(PeopleImport.objects.filter(church=church).select_related("imported_by")[:10])
    for record in rows:
        record.can_undo = imports.can_undo(record)
    return rows


def _preview(request, rows: list[dict], *, stale: bool = False):
    plan = imports.plan(request.church, rows)
    new = plan.new
    response = render(request, "people/import_preview.html", {
        "plan": plan, "new": new[:SHOWN_NEW], "new_count": len(new), "new_more": max(len(new) - SHOWN_NEW, 0),
        "exists": plan.of(imports.EXISTS), "repeated": plan.of(imports.REPEATED), "errors": plan.of(imports.ERROR),
        "minors": plan.minors, "total": len(plan.rows), "stale": stale, "undo_hours": imports.UNDO_HOURS,
        "packed": imports.pack(request.church, request.user, rows),
    })
    response["Cache-Control"] = "no-store"  # the page carries the file's cells back to the browser
    return response


@church_admin_required
def people_import(request):
    """The file (GET and a POST with it), its preview, and the confirmation (POST ``accion=confirmar``)."""
    form = ImportForm()
    problems: list[str] = []
    if request.method == "POST" and request.POST.get("accion") == "confirmar":
        try:
            rows = imports.unpack(request.church, request.user, request.POST.get("datos", ""))
        except imports.BadPack:
            problems = ["No pudimos confirmar esta importación: la revisión ya no es válida. Sube el archivo otra vez. No se registró a nadie."]
        else:
            try:
                record = imports.apply(request.church, rows, request.user, token=request.POST.get("revision", ""))
            except imports.StalePreview:
                return _preview(request, rows, stale=True)
            except PeopleError as error:
                messages.error(request, error.message)
                return redirect("people:import")
            count = record.created_count
            messages.success(request, f"Registraste a {count} persona{'' if count == 1 else 's'} desde el archivo.")
            return redirect("people:import_detail", record.pk)
    elif request.method == "POST":
        form = ImportForm(request.POST, request.FILES)
        if form.is_valid():
            rows, problems = imports.read_file(form.cleaned_data["file"].read())
            if not problems:
                return _preview(request, rows)
    return render(request, "people/import.html", {
        "form": form, "problems": problems, "columns": imports.COLUMNS, "max_rows": imports.MAX_ROWS,
        "recent": _recent(request.church), "undo_hours": imports.UNDO_HOURS, "stages": [label for _, label in Person.Stage.choices],
    }, status=400 if problems else 200)


@church_admin_required
def people_import_sample(request):
    """The example file: the headers the import reads and two rows it accepts. The addresses end in
    .invalid, which no mail server delivers: the file must be edited before it is useful."""
    rows = [
        ["Teresa", "Molina Vega", "teresa.molina@ejemplo.invalid", "55 5555 0101", "14/03/1988", "miembro", ""],
        ["Carlos", "Ruiz Peña", "", "55 5555 0102", "", "visita", ""],
    ]
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="personas-ejemplo.csv"'
    response.write("﻿")  # Excel opens UTF-8 correctly with a BOM
    writer = csv.writer(response)
    writer.writerow(imports.COLUMNS)
    for row in rows:
        writer.writerow([safe_cell(value) for value in row])
    return response


def _import(request, import_id: int) -> PeopleImport:
    return get_object_or_404(PeopleImport.objects.select_related("imported_by", "undone_by"), pk=import_id, church=request.church)


@church_admin_required
def people_import_detail(request, import_id: int):
    """One import: who, when, how many; and, while it can be undone, exactly what undoing it would delete."""
    record = _import(request, import_id)
    context = {"record": record, "can_undo": imports.can_undo(record), "until": imports.undoable_until(record),
               "undo_hours": imports.UNDO_HOURS}
    if context["can_undo"]:
        plan = imports.undo_plan(record)
        context["undo"] = plan
        context["kept"] = Person.objects.filter(pk__in=plan.keep, church=request.church, merged_into__isnull=True)[:50]
    return render(request, "people/import_detail.html", context)


@church_admin_required
@require_POST
def people_import_undo(request, import_id: int):
    record = _import(request, import_id)
    try:
        record = imports.undo(request.church, record.pk, request.user, token=request.POST.get("revision", ""))
    except imports.StalePreview:
        messages.error(request, "No deshicimos nada: algo cambió en estas personas mientras lo revisabas. Esto es lo que pasaría ahora.")
    except PeopleError as error:
        messages.error(request, error.message)
    else:
        deleted = record.undo_report.get("deleted", 0)
        messages.success(request, f"Deshiciste la importación: se borr{'ó' if deleted == 1 else 'aron'} {deleted} persona{'' if deleted == 1 else 's'}.")
    return redirect("people:import_detail", record.pk)
