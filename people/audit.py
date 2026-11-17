"""The church-level activity log (issue #35). One write path: ``record_church``."""

from .models import ChurchAuditEvent

ACTION_LABELS = {
    "modulo_cambiado": "Encendió o apagó un módulo",
    "persona_creada": "Registró a una persona",
    "persona_modificada": "Modificó los datos de una persona",
    "persona_vinculada": "Vinculó a una persona con un campero",
    "persona_desvinculada": "Quitó el vínculo de una persona con un campero",
    "cuenta_creada": "Dio acceso a una persona",
    "cuenta_vinculada": "Vinculó a una persona con una cuenta que ya existía",
    "grupo_creado": "Creó un grupo",
    "grupo_modificado": "Modificó un grupo",
    "grupo_archivado": "Archivó un grupo",
    "grupo_lider": "Cambió quién guía un grupo",
    "grupo_solicitud": "Pidió unirse a un grupo",
    "grupo_solicitud_retirada": "Retiró su solicitud a un grupo",
    "grupo_solicitud_aceptada": "Aceptó una solicitud a un grupo",
    "grupo_solicitud_declinada": "Declinó una solicitud a un grupo",
    "grupo_integrante_agregado": "Agregó a una persona a un grupo",
    "grupo_integrante_salio": "Sacó a una persona de un grupo o la persona salió",
    "grupo_reunion": "Pasó lista de una reunión de un grupo",
    "aviso_publicado": "Publicó un aviso",
    "aviso_modificado": "Modificó un aviso",
    "aviso_retirado": "Retiró un aviso",
}

# Keys a caller may pass as metadata: identifiers and closed vocabulary only, never contact data or free text.
ALLOWED_METADATA = frozenset({"module", "value", "group_id", "person_id", "event_id", "role", "source", "minor_confirmed"})


def record_church(church, actor, action: str, summary: str, obj=None, **metadata) -> ChurchAuditEvent:
    """Store one row. ``summary`` names people by name only; ``metadata`` holds ids and fixed words."""
    if action not in ACTION_LABELS:
        raise ValueError(f"Unknown church audit action {action!r}.")
    refused = sorted(set(metadata) - ALLOWED_METADATA)
    if refused:
        raise ValueError(f"Church audit metadata may not carry {', '.join(refused)}.")
    label = ""
    if actor is not None and getattr(actor, "is_authenticated", False):
        label = actor.get_full_name() or actor.email or actor.username
    return ChurchAuditEvent.objects.create(
        church=church,
        actor=actor if label else None,
        actor_label=(label or "Sistema")[:160],
        action=action,
        summary=summary[:300],
        object_type=obj.__class__.__name__ if obj is not None else "",
        object_id=str(getattr(obj, "pk", "") or "") if obj is not None else "",
        metadata=metadata,
    )
