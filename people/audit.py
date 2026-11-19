"""The church-level activity log (issue #35). One write path: ``record_church``."""

from .models import ChurchAuditEvent

ACTION_LABELS = {
    "modulo_cambiado": "Encendió o apagó un módulo",
    "persona_creada": "Registró a una persona",
    "persona_modificada": "Modificó los datos de una persona",
    "persona_edad_confirmada": "Confirmó la edad de una persona",
    "persona_vinculada": "Vinculó a una persona con un campero",
    "persona_desvinculada": "Quitó el vínculo de una persona con un campero",
    "cuenta_creada": "Dio acceso a una persona",
    "cuenta_vinculada": "Vinculó a una persona con una cuenta que ya existía",
    "persona_duplicado_descartado": "Dijo que dos registros no son la misma persona",
    "persona_duplicado_reabierto": "Volvió a poner en revisión dos registros que había descartado",
    "persona_union": "Unió dos registros de la misma persona",
    "persona_union_deshecha": "Deshizo la unión de dos registros de persona",
    "grupo_creado": "Creó un grupo",
    "grupo_modificado": "Modificó un grupo",
    "grupo_archivado": "Archivó un grupo",
    "grupo_lider": "Cambió quién guía un grupo",
    "grupo_solicitud": "Pidió unirse a un grupo",
    "grupo_solicitud_retirada": "Retiró su solicitud a un grupo",
    "grupo_solicitud_aceptada": "Aceptó una solicitud a un grupo",
    "grupo_solicitud_declinada": "Declinó una solicitud a un grupo",
    "grupo_solicitud_cerrada_union": "Se cerró una solicitud a un grupo al unir dos registros de la misma persona",
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
    "plan_publicado": "Publicó un plan de lectura",
    "plan_modificado": "Escribió o modificó un plan de lectura o uno de sus días",
    "plan_retirado": "Retiró un plan de lectura",
    "evento_sencillo_creado": "Creó un evento de la iglesia",
    "evento_sencillo_datos": "Modificó los datos de un evento",
    "serie_creada": "Creó una serie de eventos",
    "serie_modificada": "Modificó, terminó o reanudó una serie de eventos",
    "serie_fechas": "Se escribieron las fechas que siguen de una serie de eventos",
    "serie_fecha": "Dijo que un día de una serie no hay, o que sí hay",
    "evento_publicado": "Publicó un evento para la iglesia",
    "evento_pagina": "Modificó la página de un evento",
    "evento_inscripcion": "Se inscribió a un evento",
    "evento_inscripcion_cancelada": "Canceló su inscripción a un evento",
    "evento_asistencia": "Registró la asistencia de un evento",
    "evento_preguntas": "Agregó, modificó o borró una pregunta frecuente de un evento",
    "evento_aviso_publico_publicado": "Publicó un aviso público de un evento",
    "evento_aviso_publico_modificado": "Modificó un aviso público de un evento",
    "evento_aviso_publico_retirado": "Retiró un aviso público de un evento",
    "evento_aviso_publico_borrado": "Borró para siempre un aviso público retirado de un evento",
    "evento_programa": "Agregó, modificó o quitó un momento del programa de un evento",
    "evento_registro_puerta": "Registró a alguien que llegó a un evento",
    "evento_autorizacion": "Registró la autorización de un tutor para un evento",
    "evento_autorizacion_retirada": "Retiró la autorización de un tutor para un evento",
    "evento_registro_anulado": "Anuló el registro de alguien que llegó a un evento",
    "evento_inscripcion_revisada": "Una inscripción a un evento cambió al revisarse la edad o la autorización",
    "evento_visita_edad": "Dijo si una visita de un evento es mayor o menor de edad",
    "evento_visita_persona": "Agregó a una visita de un evento a las personas de la iglesia",
    "evento_lista_espera": "Se anotó en la lista de espera de un evento",
    "evento_lista_oferta": "Se ofreció un lugar a quien seguía en la lista de espera de un evento",
    "evento_lista_aceptada": "Aceptó el lugar que se le ofreció en un evento",
    "evento_lista_salida": "Salió de la lista de espera de un evento o no aceptó el lugar",
    "evento_lista_vencida": "Venció el lugar ofrecido a alguien de la lista de espera de un evento",
    "evento_lista_retirada": "Quitó a alguien de la lista de espera de un evento",
    "equipo_creado": "Creó un equipo",
    "equipo_modificado": "Modificó un equipo",
    "equipo_archivado": "Archivó un equipo",
    "equipo_integrante": "Agregó o quitó a una persona de un equipo, o cambió quién lo coordina",
    "equipo_fecha": "Agregó, modificó o borró una fecha de un equipo",
    "equipo_asignacion": "Pidió a una persona servir en una fecha, o la quitó de ella",
    "equipo_respuesta": "Respondió a una fecha para servir o marcó un día que no puede",
    "equipo_rol": "Agregó, modificó o retiró un rol de un equipo",
    "equipo_ajustes": "Cambió los ajustes de cambios y recordatorios de un equipo",
    "equipo_rotacion": "Guardó la rotación de un equipo o confirmó lo que propuso",
    "equipo_cambio": "Pidió, respondió o retiró un cambio de fecha entre dos personas de un equipo",
    "equipo_orden": "Modificó el orden de una fecha de un equipo",
    "acompanar_creado": "Creó un seguimiento de persona",
    "acompanar_asignado": "Asignó un seguimiento de persona o cambió su fecha",
    "acompanar_paso": "Registró un paso de un seguimiento de persona",
    "acompanar_estado": "Cambió el estado de un seguimiento de persona",
    "politica_cambiada": "Cambió una política de la iglesia",
    "sitio_modificado": "Modificó la página pública de la iglesia",
    "sitio_publicado": "Publicó la página pública de la iglesia",
    "sitio_despublicado": "Dejó de publicar la página pública de la iglesia",
    "recurso_publico": "Mostró un recurso en la página pública",
    "recurso_privado": "Quitó un recurso de la página pública",
    "iglesia_elegida": "Eligió trabajar en esta iglesia (una cuenta que es parte de más de una)",
    "espacio_creado": "Agregó un espacio o un equipo",
    "espacio_modificado": "Modificó un espacio o un equipo",
    "espacio_reserva_solicitada": "Pidió la reserva de un espacio",
    "espacio_reserva_aprobada": "Aprobó la reserva de un espacio",
    "espacio_reserva_sobre_choque": "Aprobó la reserva de un espacio aunque se empalma con otra",
    "espacio_reserva_rechazada": "Rechazó la reserva de un espacio",
    "espacio_reserva_cancelada": "Canceló la reserva de un espacio",
    "contenido_aprobacion_pedida": "Pidió la aprobación pastoral de un contenido",
    "contenido_aprobacion_retirada": "Retiró la solicitud de aprobación pastoral de un contenido",
    "contenido_aprobado": "Aprobó un contenido para publicarse",
    "contenido_devuelto": "Devolvió un contenido sin aprobarlo",
    "contenido_fuera_de_publicacion": "Un contenido publicado cambió y salió de publicación",
    "persona_etapa": "Cambió la etapa de una persona",
    "etiqueta_creada": "Creó una etiqueta para las personas",
    "etiqueta_borrada": "Borró una etiqueta de las personas",
    "persona_etiquetas": "Cambió las etiquetas de una persona",
    "personas_exportadas": "Descargó la lista de personas de la iglesia",
    "personas_importadas": "Importó personas desde un archivo",
    "personas_importacion_deshecha": "Deshizo una importación de personas",
    "formulario_creado": "Creó un formulario",
    "formulario_modificado": "Modificó un formulario o sus preguntas",
    "formulario_publicado": "Publicó un formulario o una versión nueva de sus preguntas",
    "formulario_cerrado": "Cerró un formulario",
    "formulario_lectores": "Agregó o quitó un lector de un formulario",
    "formulario_respuesta": "Llegó una respuesta a un formulario",
    "formulario_tarea": "Se creó o cambió un pendiente de un formulario",
    "formulario_persona_agregada": "Agregó a personas de la iglesia a quien respondió un formulario",
    "formulario_exportado": "Descargó las respuestas de un formulario",
    "formulario_respuestas_borradas": "Se borraron las respuestas de un formulario al terminar el tiempo que se guardan",
    "formulario_respuestas_borradas_a_mano": "Borró respuestas de un formulario",
    # Prayer requests (oracion.services). Only the pastors' own actions: writing or deleting one leaves no row.
    "oracion_politica": "Encendió, apagó o ajustó las peticiones de oración",
    "oracion_abierta": "Abrió una petición de oración",
    "oracion_atendida": "Marcó o respondió una petición de oración",
    # What repeats (wave 12): bookings that repeat (espacios.repeticion), and the dates and order templates of a
    # team that follows a series of events (serving.follow). Ids, counts and fixed words: never a title or a name.
    "espacio_serie_solicitada": "Pidió una reserva que se repite",
    "espacio_serie_aprobada": "Aprobó una reserva que se repite",
    "espacio_serie_rechazada": "Rechazó una reserva que se repite",
    "espacio_serie_terminada": "Terminó una reserva que se repite, o retiró su solicitud",
    "espacio_serie_fechas": "Se apartaron fechas de una reserva que se repite",
    "equipo_serie": "Dijo que un equipo sirve en cada fecha de una serie de eventos, o que ya no",
    "equipo_serie_fechas": "Se escribieron fechas de un equipo que sigue una serie de eventos",
    "equipo_plantilla": "Guardó, usó o quitó una plantilla del orden de un equipo",
}

# Keys a caller may pass as metadata: identifiers and closed vocabulary only, never contact data or free text.
ALLOWED_METADATA = frozenset({
    "module", "value", "group_id", "person_id", "event_id", "role", "source", "minor_confirmed", "registration_id", "method",
    # Fixed words only: which rule of the event a walk-up went over and why; why a row was annulled; what the
    # team said at the door about a visitor's age; where a registration stands after a review.
    "override", "reason", "minor", "standing", "age_checked",
    # A church policy (policies.services.save): which one, by its column, and the number or yes/no before ("value" is after).
    "policy", "previous",
    # A room or a piece of equipment (espacios.services), by its id: never its name or what a booking is for.
    "space_id",
    # Duplicate review and merges (people.merge): the other record of the pair and the merge, by id.
    "other_person_id", "merge_id",
    # A pastoral approval of content (resources.approvals): which of the three fixed permitted uses.
    "permitted_use",
    # People data quality (people.tags, people.exchange): a tag by its id, never its name; how many rows.
    "tag_id", "count",
    # An import of people (people.imports), by its id; and how many of its people an undo left in place.
    "import_id", "kept",
    # A general form (formularios.services), by its id: never its title, a question, an answer or a contact.
    "form_id",
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
