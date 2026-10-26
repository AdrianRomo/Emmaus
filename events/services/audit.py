from __future__ import annotations

from ..models import AuditEvent


def record(actor, event, action: str, summary: str, obj=None, **metadata) -> AuditEvent:
    """Store an audit row. ``summary`` and ``metadata`` must never contain answer contents."""
    label = ""
    if actor is not None and getattr(actor, "is_authenticated", False):
        label = actor.get_full_name() or actor.email or actor.username
    return AuditEvent.objects.create(
        actor=actor if label else None,
        actor_label=label or "Sistema",
        event=event,
        action=action,
        summary=summary[:300],
        object_type=obj.__class__.__name__ if obj is not None else "",
        object_id=str(getattr(obj, "public_id", None) or getattr(obj, "pk", "") or "") if obj is not None else "",
        metadata=metadata,
    )


ACTION_LABELS = {
    "evento_creado": "Creó el evento",
    "evento_configurado": "Cambió la configuración",
    "procedimientos_aprobados": "Confirmó los procedimientos de la iglesia",
    "integrante_agregado": "Agregó a una persona al equipo",
    "integrante_modificado": "Modificó a una persona del equipo",
    "solicitud_acceso": "Pidió acceso al equipo",
    "solicitud_aprobada": "Aprobó una solicitud de acceso",
    "solicitud_rechazada": "Rechazó una solicitud de acceso",
    "acceso_confidencial": "Cambió un acceso a información confidencial",
    "asignacion_tutor": "Cambió una asignación de tutor",
    "campero_creado": "Registró un campero",
    "campero_modificado": "Modificó datos de un campero",
    "campero_reasignado": "Reasignó un campero",
    "campero_baja": "Dio de baja a un campero",
    "campero_eliminado": "Eliminó los datos de un campero",
    "importacion": "Importó camperos",
    "autorizacion": "Registró una autorización",
    "entrevista_iniciada": "Inició una entrevista",
    "entrevista_retomada": "Retomó una entrevista",
    "entrevista_control": "Tomó el control de una entrevista",
    "entrevista_completada": "Completó una entrevista",
    "entrevista_corregida": "Corrigió una entrevista",
    "entrevista_descartada": "Descartó una entrevista",
    "captura_iniciada": "Entregó la computadora a un campero",
    "captura_desbloqueada": "Recuperó la computadora",
    "sesion_revocada": "Cerró una sesión de captura",
    "consulta_confidencial": "Consultó información confidencial",
    "seguimiento": "Actualizó un seguimiento",
    "reporte_riesgo": "Reportó una situación de riesgo",
    "corte_generado": "Generó un corte",
    "exportacion": "Descargó datos",
    "cuestionario": "Cambió un cuestionario",
}
