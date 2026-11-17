"""The welcome a person reads the first time they open a role, and whether they put it away.

A welcome answers four questions for someone new: where am I, what can I do here, what is missing, what do I do next.
It is built from the live data every time (never a stored checklist), it sits on the role's home above the next-action
panel, and one button ("Entendido") puts it away for that role, for good, in ``core.OnboardingState``; "Mi cuenta"
brings it back. The event roles are built in ``events/services/welcome.py``; the church roles and the person with no
role yet are built here. Nothing in a welcome reaches past what the role already sees on that screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from .models import Church, OnboardingState, Role, UserChurchRole

# One key per role a person can hold. The data step of core/0011 marks every key a person already held as put away,
# so nobody who was already working in Emmaus meets a welcome for the role they had.
EVENT_ROLE_KEYS = {
    "administrador": "rol:administrador",
    "pastor": "rol:pastor",
    "tutor": "rol:tutor",
    "coordinador": "rol:coordinador",
}
CHURCH_ROLE_KEYS = {
    Role.CHURCH_ADMIN: "iglesia:church_admin",
    Role.PASTOR: "iglesia:pastor",
    Role.MENTOR: "iglesia:mentor",
    Role.PARTICIPANT: "iglesia:participant",
}
NO_ROLE_KEY = "sin-rol"
KEYS = frozenset({*EVENT_ROLE_KEYS.values(), *CHURCH_ROLE_KEYS.values(), NO_ROLE_KEY})

# What each key is called when "Mi cuenta" offers to show it again
KEY_LABELS = {
    "rol:administrador": "de administración de un evento",
    "rol:pastor": "de pastor de un evento",
    "rol:tutor": "de tutor",
    "rol:coordinador": "de coordinador",
    "iglesia:church_admin": "de administración de la iglesia",
    "iglesia:pastor": "de pastor de la iglesia",
    "iglesia:mentor": "de mentor",
    "iglesia:participant": "de tu camino",
    NO_ROLE_KEY: "de cuando aún no tenías un rol",
}


def event_role_key(role: str) -> str:
    return EVENT_ROLE_KEYS[role]


def church_role_key(role: str) -> str:
    return CHURCH_ROLE_KEYS[role]


def is_dismissed(user: User, key: str) -> bool:
    if not getattr(user, "is_authenticated", False):
        return True
    return OnboardingState.objects.filter(user=user, key=key, dismissed_at__isnull=False).exists()


def dismiss(user: User, key: str) -> None:
    """"Entendido": the welcome for this key is not shown again. Unknown keys are refused by the view, not here."""
    OnboardingState.objects.update_or_create(user=user, key=key, defaults={"dismissed_at": timezone.now()})


def restore(user: User, key: str) -> None:
    """"Volver a ver la bienvenida": the next visit to that role's home shows it again."""
    OnboardingState.objects.filter(user=user, key=key).delete()


def dismissed_keys(user: User) -> list[str]:
    return list(OnboardingState.objects.filter(user=user, dismissed_at__isnull=False).order_by("key").values_list("key", flat=True))


# ---- The welcome itself ------------------------------------------------------------------------------------------


@dataclass
class Step:
    """One line of "Primeros pasos": what to do, whether it is done (from the live data), and where to do it."""

    title: str
    done: bool
    url: str = ""
    link: str = ""
    note: str = ""
    optional: bool = False


@dataclass
class Can:
    """One line of "Lo que puedes hacer aquí": a screen of the role and what it is for."""

    label: str
    url: str
    text: str


@dataclass
class Welcome:
    key: str
    title: str
    role_line: str
    intro: str
    can: list[Can] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    ask: str = ""
    help_url: str = ""

    @property
    def pending(self) -> list[Step]:
        return [step for step in self.steps if not step.done]

    @property
    def done_count(self) -> int:
        return sum(1 for step in self.steps if step.done)


def greeting_name(user: User) -> str:
    """The first name, else the full name, else nothing (the screen then says "Hola." and offers "Mi cuenta")."""
    return (user.first_name or user.get_full_name() or "").strip()


def welcome_title(user: User) -> str:
    name = greeting_name(user)
    return f"Te damos la bienvenida, {name}." if name else "Te damos la bienvenida."


def name_step(user: User) -> Step | None:
    """Pending only while the account has no name: the greetings and the team lists use it."""
    if greeting_name(user):
        return None
    return Step("Escribe tu nombre", False, reverse("account"), "Ir a Mi cuenta", "Así te saluda cada pantalla y te reconoce tu equipo.")


def with_name_step(user: User, steps: list[Step]) -> list[Step]:
    step = name_step(user)
    return [step, *steps] if step else steps


def for_church_role(user: User, role_record: UserChurchRole | None, *, can_create_event: bool) -> Welcome | None:
    """The welcome of a church role on "Campamentos y eventos" or the discipleship overview. None once put away."""
    from events.models import Event, EventMembership

    role = role_record.role if role_record else None
    if role is None:
        key = NO_ROLE_KEY
        if is_dismissed(user, key):
            return None
        return Welcome(
            key=key,
            title=welcome_title(user),
            role_line="Todavía no tienes un rol en ningún evento ni en una iglesia.",
            intro="Emmaus es la herramienta con la que una iglesia conversa con cada persona de sus eventos y recuerda a quién "
            "buscar después. Para trabajar en un evento, alguien de su administración te agrega al equipo.",
            can=[
                Can("Escribir el código", reverse("join"), "si te dieron el código de un evento, pide acceso con él."),
                Can("Mi cuenta", reverse("account"), "tu nombre, tu correo y tu contraseña."),
            ],
            steps=with_name_step(user, [
                Step("Pide acceso a un evento con su código", False, reverse("join"), "Escribir el código",
                     "Cuando la administración te dé acceso, el evento aparece en “Campamentos”."),
            ]),
            ask="Si no tienes un código, pide a la administración de tu iglesia que te agregue al equipo.",
            help_url=reverse("ayuda"),
        )

    key = church_role_key(role)
    if is_dismissed(user, key):
        return None
    church: Church = role_record.church
    events = Event.objects.filter(church=church).exclude(status=Event.Status.ARCHIVED)
    has_events = events.exists()
    has_team = EventMembership.objects.filter(event__church=church, is_active=True).exists()

    if role == Role.CHURCH_ADMIN:
        can = [
            Can("Campamentos", reverse("events:list") + "?todos=1", "cada evento de la iglesia; en cada uno eres administrador."),
            Can("Configuración de la iglesia", reverse("church_settings"), "el nombre, el logotipo, los colores y quién tiene un rol en la iglesia."),
        ]
        if church.discipleship_enabled:
            can.append(Can("Panorama del ministerio", reverse("church_dashboard"), "el discipulado: rutas, participantes, preguntas y seguimientos."))
        steps = [
            # Pending, it has no link of its own: "Crear campamento" is the one filled button of "Campamentos y eventos"
            Step("Crea el primer campamento", has_events, "", "",
                 "Emmaus le prepara un cuestionario y 10 computadoras de entrevista. Es el botón “Crear campamento” de esta página."
                 if can_create_event else "Quien administra la iglesia lo crea desde “Campamentos y eventos”."),
            Step("Agrega a tu equipo", has_team, reverse("events:list") + "?todos=1", "Abrir un evento",
                 "En el evento, en “Equipo”, agregas a cada persona con su rol, o imprimes el código para que pidan acceso."),
            Step("Sube el logotipo de la iglesia", bool(church.logo), reverse("church_settings"), "Ir a Configuración de la iglesia",
                 "Opcional. Ocupa el lugar de la palabra “Emmaus” en cada pantalla.", optional=True),
        ]
        return Welcome(
            key=key, title=welcome_title(user),
            role_line=f"En {church.name} administras Emmaus.",
            intro="Tú dejas listo cada evento: sus procedimientos, su equipo, sus camperos y sus cuestionarios. Dentro de cada evento, "
            "“Inicio” te dice qué falta.",
            can=can, steps=with_name_step(user, steps),
            ask="Si algo del servidor o del correo no funciona, pregunta a quien instaló Emmaus.",
            help_url=reverse("ayuda"),
        )

    if role == Role.PASTOR:
        can = [Can("Campamentos", reverse("events:list") + "?todos=1", "cada evento de la iglesia; en cada uno lees el “Resumen general”.")]
        if church.discipleship_enabled:
            can.append(Can("Panorama del ministerio", reverse("church_dashboard"), "el discipulado: preguntas para el pastor, seguimientos y quién se quedó callado."))
        steps = [
            Step("Abre un evento", has_events, reverse("events:list") + "?todos=1", "Ir a Campamentos",
                 "" if has_events else "Todavía no hay eventos: la administración de la iglesia crea el primero."),
        ]
        return Welcome(
            key=key, title=welcome_title(user),
            role_line=f"En {church.name} eres pastor.",
            intro="Ves cómo va cada evento sin leer las respuestas de nadie: cuánto falta, qué equipos necesitan apoyo y qué "
            "seguimientos esperan. Las respuestas confidenciales las lee solo quien tiene ese acceso explícito.",
            can=can, steps=with_name_step(user, steps),
            ask="Si necesitas leer información confidencial, la administración del evento te lo otorga en “Equipo”.",
            help_url=reverse("ayuda"),
        )

    if role == Role.MENTOR:
        from .access import visible_participant_profiles
        from .models import QuestionResponse

        has_people = visible_participant_profiles(user).exists()
        answered = QuestionResponse.objects.filter(responder=user).exists()
        steps = [
            Step("Ten participantes asignados", has_people, reverse("mentor_dashboard"), "Ver mis participantes",
                 "" if has_people else "La administración o un pastor te los asigna."),
            Step("Responde la primera pregunta", answered, reverse("mentor_dashboard"), "Ver las preguntas que esperan",
                 "Cada pregunta la lee primero su mentor; lo delicado se pasa al pastor."),
        ]
        return Welcome(
            key=key, title=welcome_title(user),
            role_line=f"En {church.name} eres mentor.",
            intro="Acompañas a las personas que te asignan: lees sus preguntas, les respondes y anotas los seguimientos. "
            "Nunca ves a quien no acompañas.",
            can=[
                Can("Inicio", reverse("mentor_dashboard"), "quién espera tu respuesta, tus seguimientos y tus participantes."),
                Can("Seguimientos", reverse("follow_up_list"), "las conversaciones pedidas y su estado."),
            ],
            steps=with_name_step(user, steps),
            ask="Si alguien está en peligro, llama al 911 y avisa en persona a tu pastor.",
            help_url=reverse("ayuda"),
        )

    # Participant: a person of the church walking a path
    from .models import LessonProgress, ParticipantProfile

    profile: ParticipantProfile | None = getattr(user, "participant_profile", None)
    contact_ok = bool(profile and (profile.phone_number or profile.preferred_contact_method not in ("", ParticipantProfile.ContactMethod.UNSPECIFIED)))
    finished = LessonProgress.objects.filter(enrollment__participant=user, completed_at__isnull=False).exists()
    steps = [
        Step("Di cómo prefieres que te contacten", contact_ok, reverse("account"), "Ir a Mi cuenta",
             "Así tu mentor sabe por dónde buscarte."),
        Step("Termina tu primera lección", finished, reverse("participant_dashboard"), "Ver mi camino",
             "Avanza a tu ritmo: puedes guardar tu reflexión y volver después."),
    ]
    return Welcome(
        key=key, title=welcome_title(user),
        role_line=f"En {church.name} recorres tu camino de discipulado.",
        intro="Aquí lees tus lecciones, escribes tus reflexiones y haces preguntas a líderes de confianza. Lo que escribes lo "
        "leen solo tu mentor y los pastores; nadie lo califica.",
        can=[
            Can("Mi camino", reverse("participant_dashboard"), "tu ruta y la lección que sigue."),
            Can("Preguntas", reverse("participant_questions"), "las que has hecho y sus respuestas."),
            Can("Pedir una conversación", reverse("request_follow_up"), "cuando quieras platicar con alguien en persona."),
        ],
        steps=with_name_step(user, steps),
        ask="Emmaus no es un servicio de emergencias. Si estás en peligro, llama al 911.",
        help_url=reverse("ayuda"),
    )
