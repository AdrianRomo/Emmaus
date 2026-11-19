"""The church's people as a spreadsheet, for its administration ("Descargar la lista").

What goes out is what the administration already reads on the people list and on each person's
page, plus the stage, the key dates and the tags: nothing else. Never a camp answer, a note, a
reason of a follow-up, attendance, reading progress or what an authorization says: this module
imports none of those models. Records that were merged away are not people of the list. A minor is
a row like any other, with these same columns. Every cell goes through the camp export's
``safe_cell`` (a spreadsheet must not run what a person typed).
"""

from __future__ import annotations

from events.services.exports import safe_cell

from .models import Person, PersonTag

HEADER = [
    "Nombre", "Apellidos", "Correo", "Teléfono", "Medio de contacto preferido", "Fecha de nacimiento",
    "Edad confirmada por el equipo", "Acceso a Emmaus", "Se registró desde", "Etapa", "Etapa anotada el", "Se bautizó el",
    "Llegó a la iglesia el", "Es miembro desde", "Etiquetas",
]
DATE_FORMAT = "%d/%m/%Y"
TAG_SEPARATOR = "; "


def _day(value) -> str:
    return value.strftime(DATE_FORMAT) if value else ""


def rows(church) -> list[list[str]]:
    """One row per active person of ``church``, by name; every cell already safe. Two queries."""
    tags: dict[int, list[str]] = {}
    links = PersonTag.objects.filter(person__church=church, tag__church=church).order_by("tag__normalized_name", "tag_id")
    for person_id, name in links.values_list("person_id", "tag__name"):
        tags.setdefault(person_id, []).append(name)
    people = Person.objects.filter(church=church, merged_into__isnull=True).order_by("first_name", "last_name", "id")
    table = []
    for person in people:
        verified = ""
        if person.birth_date is not None:
            verified = "Sí" if person.birth_date_verified else "No"
        cells = [
            person.first_name, person.last_name, person.email, person.phone, person.get_preferred_contact_method_display(),
            _day(person.birth_date), verified, "Sí" if person.user_id else "No", person.get_created_from_display(),
            person.get_stage_display() if person.stage else "", _day(person.stage_changed_on), _day(person.baptized_on),
            _day(person.arrived_on), _day(person.member_since), TAG_SEPARATOR.join(tags.get(person.pk, [])),
        ]
        table.append([safe_cell(cell) for cell in cells])
    return table
