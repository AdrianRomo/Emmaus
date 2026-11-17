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
    "aviso_enterado_pedido": "Pidió «Enterado» otra vez en un aviso urgente",
    "devocional_publicado": "Publicó un devocional",
    "devocional_modificado": "Modificó un devocional",
    "recurso_publicado": "Publicó un recurso",
    "recurso_modificado": "Modificó un recurso",
    "recurso_retirado": "Retiró un recurso",
    "evento_sencillo_creado": "Creó un evento de la iglesia",
    "evento_sencillo_datos": "Modificó los datos de un evento",
    "evento_publicado": "Publicó un evento para la iglesia",
    "evento_pagina": "Modificó la página de un evento",
    "evento_inscripcion": "Se inscribió a un evento",
    "evento_inscripcion_cancelada": "Canceló su inscripción a un evento",
    "evento_asistencia": "Registró la asistencia de un evento",
    "evento_preguntas": "Agregó, modificó o borró una pregunta frecuente de un evento",
    "evento_programa": "Agregó, modificó o quitó un momento del programa de un evento",
    "evento_registro_puerta": "Registró a alguien que llegó a un evento",
    "evento_autorizacion": "Registró la autorización de un tutor para un evento",
    "evento_autorizacion_retirada": "Retiró la autorización de un tutor para un evento",
    "evento_visita_persona": "Agregó a una visita de un evento a las personas de la iglesia",
    "equipo_creado": "Creó un equipo",
    "equipo_modificado": "Modificó un equipo",
    "equipo_archivado": "Archivó un equipo",
    "equipo_integrante": "Agregó o quitó a una persona de un equipo, o cambió quién lo coordina",
    "equipo_fecha": "Agregó, modificó o borró una fecha de un equipo",
    "equipo_asignacion": "Pidió a una persona servir en una fecha, o la quitó de ella",
    "equipo_respuesta": "Respondió a una fecha para servir o marcó un día que no puede",
    "acompanar_creado": "Creó un seguimiento de persona",
    "acompanar_asignado": "Asignó un seguimiento de persona o cambió su fecha",
    "acompanar_paso": "Registró un paso de un seguimiento de persona",
    "acompanar_estado": "Cambió el estado de un seguimiento de persona",
}

# Keys a caller may pass as metadata: identifiers and closed vocabulary only, never contact data or free text.
ALLOWED_METADATA = frozenset({
    "module", "value", "group_id", "person_id", "event_id", "role", "source", "minor_confirmed", "registration_id", "method",
})


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
