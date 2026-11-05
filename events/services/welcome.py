"""The welcome of each event role, built from what that role already sees (``core.onboarding`` has the frame).

Each builder returns ``None`` once the person put the welcome away ("Entendido"), else a ``Welcome`` whose
"Primeros pasos" come from the live data through the caller's ``EventAccess``: a coordinador counts only their own
camperos, a tutor only their team, and nothing here names a screen the role's bar does not have (the coordinador's
page never says "Reportes" or "Configuración": events/tests/test_access.py keeps those words off their screens).
"""

from __future__ import annotations

from django.urls import reverse

from core.onboarding import (
    Can,
    Step,
    Welcome,
    event_role_key,
    is_dismissed,
    welcome_title,
    with_name_step,
)

from ..models import FollowUp, Interview, ReportSnapshot
from .participants import minors_without_authorization


def _url(name: str, event, query: str = "") -> str:
    return reverse(f"events:{name}", args=[event.slug]) + query


def for_coordinator(event, access, *, station) -> Welcome | None:
    key = event_role_key("coordinador")
    if is_dismissed(access.user, key):
        return None
    participants = access.participants()
    has_people = participants.exists()
    completed = access.interviews().filter(status=Interview.Status.COMPLETED).exists()
    steps = []
    if event.stations.filter(is_active=True).exists():
        # Pending, it links nowhere: "Elegir computadora" is the line right above this block (coordinator/home.html)
        steps.append(Step(
            "Elige tu computadora", station is not None, "", "",
            "El número está pegado en la computadora; el enlace para elegirla está arriba de este recuadro. Así tu tutor sabe dónde estás.",
        ))
    steps.append(Step(
        "Ten camperos asignados", has_people, _url("participant_create", event), "Registrar campero",
        "" if has_people else "Regístralos tú o pide a la administración del evento que te los asigne.",
    ))
    if has_people and event.requires_guardian_authorization:
        minors = minors_without_authorization(participants, event).count()
        steps.append(Step(
            "Registra la autorización de tus camperos menores de edad", minors == 0,
            _url("my_participants", event, "?autorizacion=falta"), "Ver quiénes la necesitan",
            f"{minors} sin autorización registrada. Sin ella no se puede entrevistar a un menor." if minors else "",
        ))
    steps.append(Step(
        "Completa tu primera entrevista", completed, _url("interview_search", event), "Buscar a un campero",
        "Explícale el aviso de privacidad. Puedes hacerle las preguntas tú o entregarle la computadora para que responda a solas.",
    ))
    return Welcome(
        key=key, title=welcome_title(access.user),
        role_line=f"En {event.name} eres coordinador.",
        intro="Tu trabajo es platicar con cada uno de tus camperos. Emmaus guarda cada respuesta al avanzar, te dice a quién "
        "te falta y anota a quién hay que buscar después. Ves solo a los camperos asignados a ti.",
        can=[
            Can("Mi inicio", _url("coord_home", event), "la entrevista que sigue y lo que espera tu decisión."),
            Can("Mis camperos", _url("my_participants", event), "las personas asignadas a ti, con su estado."),
            Can("Entrevistas pendientes", _url("coord_pending", event), "las que faltan y las interrumpidas."),
            Can("Mi avance", _url("coord_progress", event), "cuántas llevas y las completadas hace poco."),
        ],
        steps=with_name_step(access.user, steps),
        ask="Si algo no cuadra, pregunta a tu tutor o a la administración del evento.",
        help_url=_url("ayuda", event),
    )


def for_tutor(event, access) -> Welcome | None:
    key = event_role_key("tutor")
    if is_dismissed(access.user, key):
        return None
    coordinators = access.coordinators().count()
    has_people = access.participants().exists()
    steps = [
        Step(
            "Ten coordinadores asignados", coordinators > 0, _url("coordinators", event), "Ver coordinadores",
            f"Supervisas a {coordinators}." if coordinators else "La administración del evento te los asigna en “Equipo”.",
        ),
        Step(
            "Tu equipo tiene camperos", has_people, _url("coordinators", event), "Ver el equipo",
            "" if has_people else "Cada coordinador registra a los suyos, o la administración importa la lista.",
        ),
    ]
    return Welcome(
        key=key, title=welcome_title(access.user),
        role_line=f"En {event.name} eres tutor{f' de {access.membership.team_name}' if access.membership and access.membership.team_name else ''}.",
        intro="Cuidas a un equipo de coordinadores: ves cómo va cada uno, quién tiene una entrevista interrumpida y qué "
        "seguimientos esperan. No lees las respuestas de los camperos; ves que existen y quién las atiende.",
        can=[
            Can("Mi equipo", _url("tutor_home", event), "una fila por coordinador y, arriba, quién necesita ayuda ahora."),
            Can("Coordinadores", _url("coordinators", event), "cada uno con sus camperos y dónde está."),
            Can("Entrevistas", _url("interviews", event), "todas las de tu equipo, con filtros."),
            Can("Pendientes", _url("pending", event), "las interrumpidas, las que esperan una decisión y los seguimientos."),
            Can("Reportes", _url("reports", event), "los cortes de tu equipo y la presentación."),
        ],
        steps=with_name_step(access.user, steps),
        ask="Si un coordinador no puede retomar una entrevista, la administración del evento la libera.",
        help_url=_url("ayuda", event),
    )


def for_pastor(event, access) -> Welcome | None:
    key = event_role_key("pastor")
    if is_dismissed(access.user, key):
        return None
    has_people = access.participants().exists()
    has_cutoff = access.snapshots().filter(status=ReportSnapshot.Status.READY).exists()
    unassigned = access.follow_ups().filter(kind=FollowUp.Kind.CARE, status__in=FollowUp.OPEN_STATUSES, assigned_to__isnull=True).count()
    steps = [
        Step(
            "Hay camperos registrados", has_people, _url("progress", event), "Ver el avance",
            "" if has_people else "Todavía no hay. En cuanto la administración registre o importe la lista, el avance aparece aquí.",
        ),
        Step(
            "Hay un corte guardado", has_cutoff, _url("reports", event), "Ver reportes",
            "Un corte es la foto del avance a cierta hora: sirve para la reunión y para el PDF.",
        ),
        Step(
            "Cada seguimiento tiene responsable", unassigned == 0,
            _url("follow_ups", event, "?responsable=ninguno"), "Asignar responsables",
            f"{unassigned} sin responsable." if unassigned else "",
        ),
    ]
    return Welcome(
        key=key, title=welcome_title(access.user),
        role_line=f"En {event.name} eres pastor.",
        intro="Lees cómo va el evento sin leer las respuestas de nadie: cuánto falta, qué equipos necesitan apoyo y qué "
        "seguimientos esperan. Las respuestas confidenciales las lee solo quien tiene ese acceso explícito.",
        can=[
            Can("Resumen general", _url("pastor_home", event), "cinco preguntas con su respuesta, que se actualizan solas."),
            Can("Seguimiento", _url("follow_ups", event), "a quién hay que buscar y quién se encarga."),
            Can("Reportes", _url("reports", event), "los cortes, el PDF y la presentación para la reunión."),
            Can("Equipo", _url("team", event), "quién es quién en el evento, solo lectura."),
        ],
        steps=with_name_step(access.user, steps),
        ask="Si necesitas leer información confidencial, la administración del evento te lo otorga en “Equipo”.",
        help_url=_url("ayuda", event),
    )


def for_admin(event, access, *, steps_done: int, steps_total: int) -> Welcome | None:
    """The administración's checklist already lives on this screen ("Preparación"): the welcome frames it, never repeats it."""
    key = event_role_key("administrador")
    if is_dismissed(access.user, key):
        return None
    if steps_done == steps_total:
        preparation = f"“Preparación”, aquí abajo, dice que los {steps_total} pasos están listos."
    else:
        preparation = f"“Preparación”, aquí abajo, dice qué falta: {steps_done} de {steps_total} pasos listos."
    return Welcome(
        key=key, title=welcome_title(access.user),
        role_line=f"En {event.name} eres administrador.",
        intro=f"Tú dejas listo el evento para que el equipo pueda entrevistar: los procedimientos de la iglesia, el "
        f"cuestionario, los camperos, el equipo y las computadoras. {preparation}",
        can=[
            Can("Inicio", _url("admin_home", event), "qué falta, cómo va y la actividad reciente."),
            Can("Camperos", _url("participants", event), "registrar, importar la lista, asignar coordinador."),
            Can("Equipo", _url("team", event), "agregar personas con su rol, asignar tutores, el código para unirse."),
            Can("Cuestionarios", _url("questionnaires", event), "las preguntas que responden los camperos, en versiones."),
            Can("Configuración", _url("settings", event), "procedimientos, fechas, cortes, privacidad y computadoras."),
        ],
        steps=with_name_step(access.user, []),
        ask="Si algo del servidor o del correo no funciona, pregunta a quien instaló Emmaus.",
        help_url=_url("ayuda", event),
    )
