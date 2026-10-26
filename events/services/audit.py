from __future__ import annotations

from collections.abc import Iterable

from ..models import AuditEvent, FollowUp


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
    "autorizacion": "Registró una autorización",  # rows written before the confirmation page existed
    "autorizacion_registrada": "Confirmó la autorización firmada de un padre, madre o tutor legal",
    "autorizacion_retirada": "Quitó la autorización de un padre, madre o tutor legal",
    "entrevista_iniciada": "Inició una entrevista",
    "entrevista_retomada": "Retomó una entrevista",
    "entrevista_control": "Tomó el control de una entrevista",
    "entrevista_completada": "Completó una entrevista",
    "entrevista_corregida": "Corrigió una entrevista",
    "entrevista_descartada": "Descartó una entrevista",
    "captura_iniciada": "Entregó la computadora a un campero",
    "captura_desbloqueada": "Recuperó la computadora",
    "sesion_revocada": "Cerró la pantalla de un campero o liberó una entrevista",
    "consulta_confidencial": "Consultó información confidencial",
    "seguimiento": "Actualizó un seguimiento",
    "reporte_riesgo": "Reportó una situación de riesgo",
    "corte_generado": "Generó un corte",
    "exportacion": "Descargó datos",
    "cuestionario": "Cambió un cuestionario",
}


# Rows whose summary ties a campero to something only the authorized care team may know: that they have
# confidential answers or a confidential follow-up, or that somebody reported a risk about them. The
# activity log is read by administrators who may not hold confidential access.
CONFIDENTIAL_ACTIONS = frozenset({"consulta_confidencial", "reporte_riesgo"})
CONFIDENTIAL_SUMMARY = "Movimiento confidencial"


def with_shown_summaries(rows: Iterable[AuditEvent], access) -> list[AuditEvent]:
    """Give each row a ``shown_summary``: its summary, or "Movimiento confidencial" for a viewer without
    confidential access when the row concerns confidential follow-ups or safety reports.

    "seguimiento" rows are also written for ordinary follow-ups, which administrators see anyway; they are
    neutralised only when the follow-up is confidential or a safety report, or no longer exists (then nothing
    says which it was). The stored rows are never changed.
    """
    rows = list(rows)
    sensitive = getattr(access, "can_view_sensitive", False)
    ordinary = set()
    if not sensitive:
        ids = [r.object_id for r in rows if r.action == "seguimiento" and r.object_type == "FollowUp" and r.object_id.isdigit()]
        ordinary = {
            str(pk)
            for pk in FollowUp.objects.filter(pk__in=ids, kind=FollowUp.Kind.CARE, confidential=False).values_list("pk", flat=True)
        }
    for row in rows:
        hide = not sensitive and (
            row.action in CONFIDENTIAL_ACTIONS
            or (row.action == "seguimiento" and row.object_type == "FollowUp" and row.object_id not in ordinary)
        )
        row.shown_summary = CONFIDENTIAL_SUMMARY if hide else row.summary
    return rows
