"""Tags: labels the church's administration defines for its own filtering of the people list.

Staff-only by construction: every reader is behind church administration (``people.data_views``,
the people list, the export). A tag is never a permission: nothing in ``core.authz`` or ``access``
imports this module. The callers have already asked for church administration; these functions
hold what is true whoever asks: one church, a short single-line name, at most ``MAX_TAGS`` per
church, and audit rows with ids and counts only (never the name, which a person typed).
"""

from __future__ import annotations

from django.db import DataError, IntegrityError, transaction

from events.models import normalize_name

from .audit import record_church
from .models import ChurchSettings, Person, PersonTag, Tag
from .services import PeopleError
from .text import has_control, normalized_fits

MAX_TAGS = 30
MAX_NAME = 40


class StaleTags(Exception):
    """The person's tags are not the ones the screen showed any more: nothing was written, show them again."""


def church_tags(church):
    return Tag.objects.filter(church=church)


SEPARATORS = ",;|"  # what separates tags in a cell of the import and of the export: never part of a name


def clean_name(raw: str) -> str:
    """One line, single-spaced, at most ``MAX_NAME`` characters, also once normalised (the form the unique
    column holds), with no control character and none of the separators of a file's tag cell: a name with one
    could be exported and never imported back. Raises ``PeopleError`` with a sentence for the screen."""
    raw = raw or ""
    if has_control(raw.strip(" ")):
        raise PeopleError("El nombre de la etiqueta va en una sola línea y sin caracteres que no se ven.", code="name")
    name = " ".join(raw.split())
    if not name or not normalize_name(name):
        raise PeopleError("Escribe el nombre de la etiqueta.", code="name")
    if any(ch in name for ch in SEPARATORS):
        raise PeopleError("El nombre de la etiqueta no puede llevar coma, punto y coma ni barra (|).", code="name")
    if len(name) > MAX_NAME:
        raise PeopleError(f"El nombre es muy largo: escribe {MAX_NAME} caracteres o menos.", code="name")
    if not normalized_fits(name, MAX_NAME):
        raise PeopleError("El nombre tiene letras especiales que ocupan más de lo que cabe: escríbelo con letras comunes.", code="name")
    return name


def _lock_church(church) -> None:
    """One writer of a church's tags at a time (the row of its module flags, which this app owns)."""
    list(ChurchSettings.objects.select_for_update().filter(church=church))


def create_tag(church, raw_name: str, actor) -> Tag:
    name = clean_name(raw_name)
    try:
        with transaction.atomic():
            _lock_church(church)
            if church_tags(church).filter(normalized_name=normalize_name(name)).exists():
                raise PeopleError("Ya hay una etiqueta con ese nombre.", code="taken")
            if church_tags(church).count() >= MAX_TAGS:
                raise PeopleError(f"Tu iglesia ya tiene {MAX_TAGS} etiquetas, que es el máximo. Borra una que ya no uses.", code="full")
            tag = Tag.objects.create(church=church, name=name, normalized_name=normalize_name(name), created_by=actor)
            record_church(church, actor, "etiqueta_creada", "Creó una etiqueta para las personas", tag, tag_id=tag.pk)
            return tag
    except IntegrityError as error:  # two people created the same name at once
        raise PeopleError("Ya hay una etiqueta con ese nombre.", code="taken") from error
    except DataError as error:  # the database cannot hold it: said in words, never an error page
        raise PeopleError("No pudimos guardar ese nombre. Escríbelo con letras comunes.", code="name") from error


def delete_tag(church, tag_id: int, actor, *, seen_count: int) -> int:
    """Delete a tag of ``church`` and take it off everybody. ``seen_count``: how many people the
    confirmation said carry it; if that changed, ``StaleTags`` and nothing is deleted. Returns that count."""
    with transaction.atomic():
        _lock_church(church)
        tag = church_tags(church).select_for_update().filter(pk=tag_id).first()
        if tag is None:
            raise PeopleError("Esa etiqueta ya no existe.", code="gone")
        count = carriers(tag)
        if count != seen_count:
            raise StaleTags
        record_church(church, actor, "etiqueta_borrada", "Borró una etiqueta de las personas", tag, tag_id=tag.pk, count=count)
        tag.delete()
        return count


def carriers(tag: Tag) -> int:
    """How many people of the list carry the tag. A record that was merged away is not a person of the list."""
    return PersonTag.objects.filter(tag=tag, person__merged_into__isnull=True).count()


def tags_of(person: Person) -> list[Tag]:
    return list(Tag.objects.filter(person_links__person=person, church_id=person.church_id))


def set_tags(church, person_id: int, tag_ids: set[int], actor, *, seen: set[int]) -> int:
    """Leave ``person`` with exactly ``tag_ids`` (tags of ``church`` only; any other id is refused).
    ``seen``: the tags the screen showed as set. Returns how many rows changed."""
    with transaction.atomic():
        person = Person.objects.select_for_update().filter(pk=person_id, church=church).first()
        if person is None or person.merged_into_id is not None:
            raise PeopleError("Esta persona ya no está en la lista de la iglesia.", code="merged")
        wanted = set(church_tags(church).filter(pk__in=tag_ids).values_list("pk", flat=True))
        if wanted != set(tag_ids):
            raise PeopleError("Alguna de esas etiquetas ya no existe. Revisa la lista.", code="gone")
        current = set(PersonTag.objects.filter(person=person).values_list("tag_id", flat=True))
        if current != set(seen):
            raise StaleTags
        added, removed = wanted - current, current - wanted
        if not added and not removed:
            return 0
        PersonTag.objects.filter(person=person, tag_id__in=removed).delete()
        PersonTag.objects.bulk_create([PersonTag(person=person, tag_id=pk, added_by=actor) for pk in sorted(added)])
        record_church(church, actor, "persona_etiquetas", "Cambió las etiquetas de una persona", person,
                      person_id=person.pk, count=len(wanted))
        return len(added) + len(removed)
