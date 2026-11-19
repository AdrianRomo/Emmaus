"""The ONE list of what Emmaus holds about a person in one church (issue #98, part A).

Every foreign key of the repository that points at ``people.Person`` or at the account model is in
exactly one of two places here:

* a ``Category`` (``relations``): rows that are the person's own, shown as a count on «Mis datos en
  Emmaus» and written into «Descargar mis datos»; or
* ``NOT_THEIRS``, with the reason in words: records the church keeps (who of the staff did something,
  what was written ABOUT a person, the camp's confidential record, the tools that join duplicates).

``cuenta_datos/tests/test_registry.py`` fails when a new foreign key is in neither, so a new table
cannot be forgotten: somebody has to decide.

A collector reads only the church of the request, only rows of this one person or account, and never
copies another person's name, a decision's author, a reason, a tag, a stage or a note. Text the person
wrote (a prayer request, the answers of a form) is left out unless the download asked for that kind.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from django.apps import apps
from django.contrib.auth import get_user_model
from django.urls import NoReverseMatch, reverse
from django.utils import formats, timezone

from formularios.services import church_tz
from people.models import Person

# The kinds of text the person wrote that the download includes only when they tick its box.
TEXT_PRAYER, TEXT_FORMS = "oracion", "formularios"
TEXT_KINDS = {
    TEXT_PRAYER: "El texto de mis peticiones de oración",
    TEXT_FORMS: "Lo que respondí en los formularios de la iglesia y al inscribirme a un evento",
}


@dataclass(frozen=True)
class Ctx:
    church: object
    person: Person | None
    user: object
    texts: frozenset = field(default_factory=frozenset)


def yes_no(value) -> str:
    return "Sí" if value else "No"


def day(value) -> str:
    return formats.date_format(value, r"j \d\e F \d\e Y").lower() if value else ""


def moment(ctx: Ctx, value) -> str:
    """An instant, read where the church is: «14 de marzo de 2026, 18:30»."""
    if not value:
        return ""
    return formats.date_format(timezone.localtime(value, church_tz(ctx.church)), r"j \d\e F \d\e Y, H:i").lower()


def _url(name: str, *args) -> str:
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return ""


# ---- collectors: label -> rows (each row a dict of plain words) -----------------------------------
def _profile(ctx: Ctx) -> dict:
    found: dict[str, list] = {}
    person = ctx.person
    if person is not None:
        found["Tu registro en esta iglesia"] = [{
            "nombre": person.full_name,
            "fecha de nacimiento": day(person.birth_date),
            "correo": person.email,
            "teléfono": person.phone,
            "cómo prefieres que te contacten": person.get_preferred_contact_method_display() if person.preferred_contact_method else "",
            "cuándo te bautizaste": day(person.baptized_on),
            "cuándo llegaste a esta iglesia": day(person.arrived_on),
            "registro creado": moment(ctx, person.created_at),
        }]
        sedes = apps.get_model("people.CampusAffiliation").objects.filter(person=person, sede__church=ctx.church).select_related("sede")
        found["Sedes a las que vas"] = [
            {"sede": row.sede.name, "principal": yes_no(row.is_primary), "desde": day(row.start_date), "hasta": day(row.end_date)}
            for row in sedes.order_by("id")
        ]
    profile = apps.get_model("core.ParticipantProfile").objects.filter(user=ctx.user, church=ctx.church).first()
    if profile is not None:
        found["Tu perfil del camino de discipulado"] = [{
            "teléfono": profile.phone_number,
            "cómo prefieres que te contacten": profile.get_preferred_contact_method_display() if profile.preferred_contact_method else "",
            "desde": moment(ctx, profile.date_joined),
        }]
    return found


def _groups(ctx: Ctx) -> dict:
    if ctx.person is None:
        return {}
    memberships = apps.get_model("groups.GroupMembership").objects.filter(person=ctx.person, group__church=ctx.church)
    requests = apps.get_model("groups.GroupJoinRequest").objects.filter(person=ctx.person, group__church=ctx.church)
    return {
        "Grupos en los que estás o estuviste": [
            {"grupo": row.group.name, "cómo participas": row.get_role_display(), "estado": row.get_status_display(),
             "desde": moment(ctx, row.start_at), "hasta": moment(ctx, row.end_at)}
            for row in memberships.select_related("group").order_by("start_at", "id")
        ],
        "Solicitudes para unirte a un grupo": [
            {"grupo": row.group.name, "estado": row.get_status_display(), "pedida": moment(ctx, row.created_at),
             "respondida": moment(ctx, row.decided_at)}
            for row in requests.select_related("group").order_by("created_at", "id")
        ],
    }


def _attendance(ctx: Ctx) -> dict:
    """What the group's page already shows this person (decision 0009): the meetings of the groups they are in
    now, since they joined, while the list of names is kept. ``groups.services.own_attendance`` is that rule."""
    if ctx.person is None:
        return {}
    from groups import services as groups_services

    memberships = apps.get_model("groups.GroupMembership").objects.filter(
        person=ctx.person, group__church=ctx.church, status="activa"
    ).select_related("group")
    rows = []
    for membership in memberships.order_by("group__name", "id"):
        for meeting in reversed(groups_services.own_attendance(membership, limit=None)):
            if meeting.came:
                rows.append({"grupo": membership.group.name, "reunión": day(meeting.date), "fuiste": "Sí"})
    return {"Reuniones de tus grupos a las que fuiste": rows}


def _events(ctx: Ctx) -> dict:
    if ctx.person is None:
        return {}
    rows = apps.get_model("agenda.EventRegistration").objects.filter(person=ctx.person, event__church=ctx.church)
    found = []
    for row in rows.select_related("event").prefetch_related("answers__question").order_by("created_at", "id"):
        one = {"evento": row.event.name, "día del evento": day(row.event.starts_on), "estado": row.get_status_display(),
               "te inscribiste": moment(ctx, row.created_at), "cancelada": moment(ctx, row.cancelled_at)}
        # What the person answered when signing up (wave 12) hangs from their own registration: theirs. How many,
        # always; the words only with the box of the answers, as a form's.
        answers = sorted(row.answers.all(), key=lambda answer: (answer.question.position, answer.pk))
        if answers:
            one["preguntas que respondiste al inscribirte"] = len(answers)
            if TEXT_FORMS in ctx.texts:
                one["lo que respondiste al inscribirte"] = [{"pregunta": answer.question.text, "respuesta": answer.value} for answer in answers]
        found.append(one)
    return {"Inscripciones a eventos": found}


def _serving(ctx: Ctx) -> dict:
    if ctx.person is None:
        return {}
    person, church = ctx.person, ctx.church
    get = apps.get_model
    memberships = get("serving.TeamMembership").objects.filter(person=person, team__church=church).select_related("team")
    dates = get("serving.Assignment").objects.filter(person=person, service_date__team__church=church)
    swaps_from = get("serving.SwapRequest").objects.filter(from_person=person, assignment__service_date__team__church=church)
    swaps_to = get("serving.SwapRequest").objects.filter(to_person=person, assignment__service_date__team__church=church)
    rotation = get("serving.RotationEntry").objects.filter(person=person, role__team__church=church).select_related("role__team")
    moments = get("serving.PlanItem").objects.filter(responsible=person, service_date__team__church=church)
    preference = get("serving.ServingPreference").objects.filter(person=person).first()

    def swap(row) -> dict:
        return {"equipo": row.assignment.service_date.team.name, "fecha": day(row.assignment.service_date.date),
                "estado": row.get_status_display(), "pedido": moment(ctx, row.created_at)}

    related = "assignment__service_date__team"
    return {
        "Equipos en los que sirves o serviste": [
            {"equipo": row.team.name, "cómo participas": row.get_role_display(), "estado": row.get_status_display(),
             "desde": moment(ctx, row.created_at)}
            for row in memberships.order_by("created_at", "id")
        ],
        "Fechas en las que te pidieron servir": [
            {"equipo": row.service_date.team.name, "fecha": day(row.service_date.date), "rol": row.role.name if row.role_id else "",
             "tu respuesta": row.get_status_display(), "respondiste": moment(ctx, row.responded_at)}
            for row in dates.select_related("service_date__team", "role").order_by("service_date__date", "id")
        ],
        "Días que marcaste como «no puedo»": [
            {"día": day(row.date)} for row in get("serving.Unavailable").objects.filter(person=person).order_by("date", "id")
        ],
        "Cambios de fecha que pediste": [swap(row) for row in swaps_from.select_related(related).order_by("created_at", "id")],
        "Cambios de fecha que te pidieron": [swap(row) for row in swaps_to.select_related(related).order_by("created_at", "id")],
        "Rotaciones en las que estás": [
            {"equipo": row.role.team.name, "rol": row.role.name} for row in rotation.order_by("role__team__name", "id")
        ],
        "Momentos a tu cargo en el orden de una fecha": [
            {"equipo": row.service_date.team.name, "fecha": day(row.service_date.date)}
            for row in moments.select_related("service_date__team").order_by("service_date__date", "id")
        ],
        "Recordatorios para servir": (
            [{"recordatorios por correo": yes_no(preference.reminders), "elegido": moment(ctx, preference.updated_at)}] if preference else []
        ),
    }


def _notices(ctx: Ctx) -> dict:
    if ctx.person is None:
        return {}
    get = apps.get_model
    reads = get("notices.AnnouncementRead").objects.filter(person=ctx.person, announcement__church=ctx.church)
    mails = get("notices.NoticeEmail").objects.filter(person=ctx.person, announcement__church=ctx.church)
    choice = get("notices.EmailPreference").objects.filter(person=ctx.person).first()
    return {
        "Avisos que leíste": [
            {"aviso": row.announcement.title, "leído": moment(ctx, row.read_at), "«Enterado»": moment(ctx, row.acknowledged_at)}
            for row in reads.select_related("announcement").order_by("read_at", "id")
        ],
        "Correos de avisos que Emmaus te mandó o intentó mandar": [
            {"aviso": row.announcement.title, "resultado": row.get_outcome_display(), "registrado": moment(ctx, row.created_at)}
            for row in mails.select_related("announcement").order_by("created_at", "id")
        ],
        "Qué avisos quieres por correo": (
            [{"elección": choice.get_choice_display(), "elegido": moment(ctx, choice.updated_at)}] if choice else []
        ),
    }


def _reading(ctx: Ctx) -> dict:
    if ctx.person is None:
        return {}
    get = apps.get_model
    plans = get("resources.ReadingPlanProgress").objects.filter(person=ctx.person, plan__church=ctx.church).select_related("plan")
    day_reads = get("resources.ReadingPlanDayRead").objects.filter(progress__in=plans).select_related("day")
    by_plan: dict[int, list] = {}
    for read in day_reads.order_by("day__position", "id"):
        by_plan.setdefault(read.progress_id, []).append(read)
    devotionals = get("resources.DevotionalRead").objects.filter(person=ctx.person, devotional__church=ctx.church)
    return {
        "Planes de lectura que empezaste": [
            {"plan": row.plan.title, "empezaste": moment(ctx, row.started_at),
             "días que marcaste como leídos": [
                 {"día": read.day.position, "leído": moment(ctx, read.read_at)} for read in by_plan.get(row.pk, [])
             ]}
            for row in plans.order_by("started_at", "id")
        ],
        "Devocionales que marcaste como leídos": [
            {"devocional": row.devotional.title, "del día": day(row.devotional.date), "leído": moment(ctx, row.read_at)}
            for row in devotionals.select_related("devotional").order_by("read_at", "id")
        ],
    }


def _prayer(ctx: Ctx) -> dict:
    """By ACCOUNT, as every prayer screen (``oracion.access.own``): only the requests still alive."""
    from oracion.access import own

    rows = []
    for prayer in own(ctx.user, ctx.church).order_by("created_at", "id"):
        row = {"escrita": moment(ctx, prayer.created_at), "se borra": moment(ctx, prayer.expires_at),
               "leída por un pastor": yes_no(prayer.read_at), "con respuesta": yes_no(prayer.replied_at)}
        if TEXT_PRAYER in ctx.texts:
            row["lo que escribiste"] = prayer.body
        rows.append(row)
    found = {"Peticiones de oración que escribiste": rows}
    choice = apps.get_model("oracion.ReaderPreference").objects.filter(user=ctx.user, church=ctx.church).first()
    if choice is not None:
        found["Aviso por correo cuando llega una petición (si lees las de tu iglesia)"] = [
            {"avisarme por correo": yes_no(choice.email_new), "elegido": moment(ctx, choice.updated_at)}
        ]
    return found


def _forms(ctx: Ctx) -> dict:
    """Only what the person answered signed in (a form «para las personas de la iglesia»). An answer to a public
    form that the administration later tied to a record was typed by somebody Emmaus cannot name: not here."""
    if ctx.person is None:
        return {}
    from formularios.services import described, kept_until

    rows = []
    submissions = apps.get_model("formularios.Submission").objects.filter(
        person=ctx.person, form__church=ctx.church, form__audience="iglesia"
    ).select_related("form", "version")
    for submission in submissions.order_by("created_at", "id"):
        row = {"formulario": submission.form.title, "respondiste": moment(ctx, submission.created_at),
               "se borra": day(kept_until(submission))}
        if TEXT_FORMS in ctx.texts:
            row["lo que respondiste"] = [{"pregunta": label, "respuesta": value} for _key, label, value in described(submission)]
        rows.append(row)
    return {"Formularios de la iglesia que respondiste": rows}


def _spaces(ctx: Ctx) -> dict:
    if ctx.person is None:
        return {}
    rows = apps.get_model("espacios.Booking").objects.filter(requested_by=ctx.person, space__church=ctx.church)
    series = apps.get_model("espacios.BookingSeries").objects.filter(requested_by=ctx.person, space__church=ctx.church)
    return {
        "Reservas de espacios que pediste": [
            {"espacio": row.space.name, "para qué (lo escribiste tú)": row.title, "empieza": moment(ctx, row.starts_at),
             "termina": moment(ctx, row.ends_at), "estado": row.get_status_display(), "pedida": moment(ctx, row.created_at)}
            for row in rows.select_related("space").order_by("created_at", "id")
        ],
        # A booking that repeats (wave 12) is one request: its rule, as the person wrote it. Each date it wrote is a
        # booking of the list above. Never the reason the administration gave, nor who decided or ended it.
        "Reservas que se repiten que pediste": [
            {"espacio": row.space.name, "para qué (lo escribiste tú)": row.title, "cada cuántas semanas": row.interval_weeks,
             "desde": day(row.starts_on), "hasta": day(row.ends_on), "de": f"{row.starts_at:%H:%M}", "a": f"{row.ends_at:%H:%M}",
             "estado": row.get_status_display(), "pedida": moment(ctx, row.created_at)}
            for row in series.select_related("space").order_by("created_at", "id")
        ],
    }


DIRECTORY_SHOWN = (("show_phone", "tu teléfono"), ("show_email", "tu correo"), ("show_groups", "tus grupos"), ("show_teams", "tus equipos"))


def _directory_entry(ctx: Ctx):
    """The person's own row of the directory between members (``people.DirectoryEntry``), or None. A row that
    says «no» is still a row Emmaus holds: somebody who left keeps it, with every choice cleared."""
    if ctx.person is None or ctx.person.church_id != ctx.church.pk:
        return None
    return apps.get_model("people.DirectoryEntry").objects.filter(person=ctx.person).first()


def _directory(ctx: Ctx) -> dict:
    """What the person chose for the directory, from the row their own screen writes («Aparecer en el directorio»):
    whether they said yes, each of the four choices and since when. Never a value (the phone is in the profile) and
    never whether anybody is shown today: that also depends on the church's switch, which is not theirs."""
    entry = _directory_entry(ctx)
    if entry is None:
        return {}
    row = {"elegiste aparecer con tu nombre": yes_no(entry.listed)}
    row.update({f"elegiste mostrar {label}": yes_no(getattr(entry, name)) for name, label in DIRECTORY_SHOWN})
    row["lo aceptaste"] = moment(ctx, entry.accepted_at) if entry.listed else ""
    row["último cambio"] = moment(ctx, entry.updated_at)
    return {"Lo que elegiste para el directorio": [row]}


def _directory_words(ctx: Ctx) -> list[str]:
    """The same choice in one sentence, for the page (a count of one row would say nothing)."""
    entry = _directory_entry(ctx)
    if entry is None:
        return []
    if not entry.listed:
        return ["Elegiste no aparecer. No se muestra nada tuyo."]
    shown = ["tu nombre"] + [label for name, label in DIRECTORY_SHOWN if getattr(entry, name)]
    listed = shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " y " + shown[-1]
    since = f", desde el {day(timezone.localtime(entry.accepted_at, church_tz(ctx.church)))}" if entry.accepted_at else ""
    return [f"Elegiste aparecer con {listed}{since}."]


def _camps(ctx: Ctx) -> dict:
    """That the person took part, and nothing else: the camp's record has its own rules and screens."""
    if ctx.person is None:
        return {}
    links = apps.get_model("people.EventParticipantLink").objects.filter(person=ctx.person, event__church=ctx.church)
    return {
        "Eventos en los que participaste": [
            {"evento": link.event.name, "empezó": day(link.event.starts_on), "terminó": day(link.event.ends_on)}
            for link in links.select_related("event").order_by("event__starts_on", "id")
        ]
    }


def _discipleship(ctx: Ctx) -> dict:
    get = apps.get_model
    paths = get("core.Enrollment").objects.filter(participant=ctx.user, path__church=ctx.church).select_related("path")
    questions = get("core.ParticipantQuestion").objects.filter(participant=ctx.user, church=ctx.church)
    return {
        "Caminos de discipulado": [
            {"camino": row.path.title, "empezaste": moment(ctx, row.started_at), "terminaste": moment(ctx, row.completed_at)}
            for row in paths.order_by("started_at", "id")
        ],
        "Preguntas que le hiciste a tu mentor": [
            {"hecha": moment(ctx, row.created_at), "estado": row.get_status_display()} for row in questions.order_by("created_at", "id")
        ],
    }


def _roles(ctx: Ctx) -> dict:
    from events.access import ROLE_LABELS

    get = apps.get_model
    church_role = get("core.UserChurchRole").objects.filter(user=ctx.user, church=ctx.church).first()
    memberships = get("events.EventMembership").objects.filter(user=ctx.user, event__church=ctx.church).select_related("event")
    return {
        "Tu rol en la iglesia": (
            [{"rol": church_role.get_role_display(), "desde": moment(ctx, church_role.created_at)}] if church_role else []
        ),
        "Equipos de eventos en los que estás o estuviste": [
            {"evento": row.event.name, "rol": ROLE_LABELS.get(row.role, row.role), "vigente": yes_no(row.is_active),
             "con acceso a respuestas confidenciales": yes_no(row.can_view_sensitive), "desde": moment(ctx, row.created_at)}
            for row in memberships.order_by("created_at", "id")
        ],
    }


def _account(ctx: Ctx) -> dict:
    from core import onboarding

    user = ctx.user
    welcomes = apps.get_model("core.OnboardingState").objects.filter(user=user, dismissed_at__isnull=False)
    asked = apps.get_model("cuenta_datos.RemovalRequest").objects.filter(user=user, church=ctx.church)
    return {
        "Tu cuenta para entrar": [{
            "nombre": user.get_full_name(),
            "correo con el que entras": user.email,
            "cuenta creada": moment(ctx, user.date_joined),
            "última vez que entraste": moment(ctx, user.last_login),
            "tiene contraseña": yes_no(user.has_usable_password()),
        }],
        "Bienvenidas que guardaste": [
            {"bienvenida": onboarding.KEY_LABELS.get(row.key, row.key), "guardada": moment(ctx, row.dismissed_at)}
            for row in welcomes.order_by("dismissed_at", "id")
        ],
        "Veces que pediste que borren tus datos": [
            {"qué pediste": row.get_ask_display(), "estado": row.get_status_display(), "pedido": moment(ctx, row.created_at),
             "cerrado": moment(ctx, row.closed_at)}
            for row in asked.order_by("created_at", "id")
        ],
    }


# ---- where each category is seen or changed --------------------------------------------------------
def _where(label: str, name: str) -> Callable[[Ctx], tuple[str, str] | None]:
    def where(ctx: Ctx):
        url = _url(name)
        return (label, url) if url else None

    return where


def _where_prayer(ctx: Ctx):
    from oracion import shell as prayer_shell

    if ctx.person is not None and prayer_shell.shows_writer_link(ctx.church, ctx.person):
        return ("Ver mis peticiones", reverse("oracion:mine"))
    return None


def _where_forms(ctx: Ctx):
    from people.minors import known_minor

    if ctx.person is None or known_minor(ctx.person):
        return None  # the forms answer 404 to a known minor
    return ("Ver los formularios", reverse("formularios:list"))


def _where_discipleship(ctx: Ctx):
    if getattr(ctx.church, "discipleship_enabled", False) and apps.get_model("core.ParticipantProfile").objects.filter(
        user=ctx.user, church=ctx.church
    ).exists():
        return ("Ver mi camino", reverse("participant_dashboard"))
    return None


def _where_directory(ctx: Ctx):
    """«Aparecer en el directorio», while that page opens for this person (``people.directory.shows_choice``: it is
    offered to them, or they have a choice of their own to take back)."""
    from people import directory

    if directory.shows_choice(ctx.church, ctx.person):
        return ("Aparecer en el directorio", reverse("directorio:mine"))
    return None


@dataclass(frozen=True)
class Category:
    """``relations``: the foreign keys this category answers for. ``staff_count``: whether the administration,
    on the task a removal request leaves, may read how many rows there are (never for what is private to the
    person: what they read, what they prayed, when they came)."""

    key: str
    title: str
    why: str
    icon: str
    relations: tuple[tuple[str, str], ...]
    collect: Callable[[Ctx], dict]
    where: Callable[[Ctx], tuple[str, str] | None] = lambda ctx: None
    staff_count: bool = False
    text_kind: str = ""
    # Sentences for the page where a count would say nothing (the one row of the directory): never a contact value.
    words: Callable[[Ctx], list[str]] = lambda ctx: []

    @property
    def staff_title(self) -> str:
        """The category named to the administration (the person's page says «tu»)."""
        return STAFF_TITLES.get(self.key, self.title)


STAFF_TITLES = {
    "perfil": "Perfil y datos de contacto",
    "asistencia": "Asistencia a su grupo",
    "avisos": "Avisos leídos y elección de correos",
    "lectura": "Lo que ha leído",
    "formularios": "Formularios que respondió con su cuenta",
    "directorio": "Lo que muestra en el directorio",
    "campamentos": "Eventos en los que participó",
    "discipulado": "Camino de discipulado",
    "roles": "Roles en la iglesia y en sus eventos",
    "cuenta": "Cuenta para entrar",
}


CATEGORIES: tuple[Category, ...] = (
    Category(
        "perfil", "Tu perfil y cómo contactarte",
        "Para saber quién eres en tu iglesia y cómo buscarte.", "cuenta",
        (("people.Person", "user"), ("people.CampusAffiliation", "person"), ("core.ParticipantProfile", "user")),
        _profile, _where("Cambiar mis datos", "community:profile"), staff_count=True,
    ),
    Category(
        "grupos", "Grupos y solicitudes",
        "Para que tu grupo sepa que eres parte y para responder lo que pediste.", "grupos",
        (("groups.GroupMembership", "person"), ("groups.GroupJoinRequest", "person")),
        _groups, _where("Ver mis grupos", "community:groups"), staff_count=True,
    ),
    Category(
        "asistencia", "Tu asistencia a tu grupo",
        "Quien guía tu grupo pasa lista. Tú ves la tuya en la página de tu grupo; los nombres se borran a los 12 meses.", "lista",
        (("groups.GroupAttendance", "person"),),
        _attendance, _where("Ver mis grupos", "community:groups"),
    ),
    Category(
        "eventos", "Inscripciones a eventos",
        "Para guardar tu lugar y saber cuántas personas van.", "eventos",
        (("agenda.EventRegistration", "person"),),
        _events, _where("Ver los eventos", "agenda:list"), staff_count=True,
    ),
    Category(
        "servir", "Equipos, fechas y cambios para servir",
        "Para organizar quién sirve cada fecha y avisarte.", "servir",
        (("serving.TeamMembership", "person"), ("serving.Assignment", "person"), ("serving.Unavailable", "person"),
         ("serving.RotationEntry", "person"), ("serving.SwapRequest", "from_person"), ("serving.SwapRequest", "to_person"),
         ("serving.PlanItem", "responsible"), ("serving.ServingPreference", "person")),
        _serving, _where("Ver Servir", "serving:home"), staff_count=True,
    ),
    Category(
        "avisos", "Avisos leídos y tu elección de correos",
        "Para no volver a mostrarte como nuevo lo que ya leíste y mandarte solo los correos que elegiste.", "avisos",
        (("notices.AnnouncementRead", "person"), ("notices.EmailPreference", "person"), ("notices.NoticeEmail", "person")),
        _notices, _where("Ver los avisos", "notices:list"),
    ),
    Category(
        "lectura", "Lo que has leído",
        "Para que sigas tu plan donde lo dejaste. Solo tú lo ves: nadie de tu iglesia puede consultarlo.", "lectura",
        (("resources.ReadingPlanProgress", "person"), ("resources.DevotionalRead", "person")),
        _reading, _where("Ver los planes de lectura", "resources:plan_list"),
    ),
    Category(
        "oracion", "Peticiones de oración",
        "Para que un pastor de tu iglesia pueda leerlas. Aquí solo está cuántas escribiste: el texto está en su propia página.",
        "servir",
        (("oracion.PrayerRequest", "author"), ("oracion.PrayerRequest", "person"), ("oracion.ReaderPreference", "user")),
        _prayer, _where_prayer, text_kind=TEXT_PRAYER,
    ),
    Category(
        "formularios", "Formularios que respondiste con tu cuenta",
        "Para lo que cada formulario dijo al pedírtelo. Cada respuesta se borra en la fecha que el formulario te avisó.", "recursos",
        (("formularios.Submission", "person"),),
        _forms, _where_forms, staff_count=True, text_kind=TEXT_FORMS,
    ),
    Category(
        "espacios", "Reservas de espacios",
        "Para apartar el lugar que pediste y avisarte qué se decidió.", "equipo",
        (("espacios.Booking", "requested_by"), ("espacios.BookingSeries", "requested_by")),
        _spaces, _where("Ver los espacios", "espacios:list"), staff_count=True,
    ),
    Category(
        "directorio", "Lo que muestras en el directorio",
        "Para mostrar a otras personas de tu iglesia solo lo que tú elegiste, y solo mientras tu iglesia ofrece el directorio.", "personas",
        (("people.DirectoryEntry", "person"),), _directory, _where_directory, words=_directory_words,
    ),
    Category(
        "campamentos", "Eventos en los que participaste",
        "Para que tu iglesia sepa que fuiste. Tus respuestas y entrevistas de un campamento no están aquí: son un expediente "
        "confidencial con sus propias reglas.", "calendario",
        (("people.EventParticipantLink", "person"),),
        _camps, staff_count=True,
    ),
    Category(
        "discipulado", "Tu camino de discipulado",
        "Para seguir tus lecciones y que tu mentor te responda.", "camino",
        (("core.Enrollment", "participant"), ("core.ParticipantQuestion", "participant")),
        _discipleship, _where_discipleship,
    ),
    Category(
        "roles", "Lo que puedes hacer en tu iglesia",
        "Para saber qué pantallas te tocan: tu rol en la iglesia y en los equipos de sus eventos.", "guia",
        (("core.UserChurchRole", "user"), ("events.EventMembership", "user"), ("access.RoleAssignment", "subject_user")),
        _roles, _where("Ver Mi cuenta", "account"), staff_count=True,
    ),
    Category(
        "cuenta", "Tu cuenta para entrar",
        "Para que entres con tu correo y tu contraseña. Emmaus guarda cuándo entraste por última vez; la contraseña no se puede leer.",
        "candado",
        (("core.OnboardingState", "user"), ("cuenta_datos.RemovalRequest", "user")),
        _account, _where("Ver Mi cuenta", "account"), staff_count=True,
    ),
)
BY_KEY = {category.key: category for category in CATEGORIES}

# ---- what is not the person's to download, and why -------------------------------------------------
STAFF_ACT = (
    "Dice qué cuenta del equipo creó, registró, publicó, aprobó o decidió algo. Es el registro de trabajo de la iglesia, "
    "no un dato sobre la persona."
)
AUDIT = "Registro de actividad: la iglesia lo conserva para saber quién hizo qué. No se edita ni se entrega."
ABOUT = (
    "Lo lleva el equipo pastoral o la administración SOBRE la persona (un seguimiento, una etiqueta, una nota). "
    "Es un registro de la iglesia: se pregunta a la administración."
)
CAMP = (
    "Expediente del campamento (entrevistas, respuestas, autorizaciones, seguimientos, cortes): confidencial, con sus propias "
    "reglas y su propia exportación para la familia (docs/privacy-and-safeguarding.md)."
)
MERGE = "Herramienta de la administración para unir registros repetidos de una misma persona: nombra dos registros, no datos nuevos."
OTHER_PERSON = "Nombra a otra persona (un tutor, un mentor, un pastor): no se copia a la descarga de nadie más."
ANONYMOUS = "Columna que ya no se usa: una opinión de un evento no guarda quién la dio."
TECHNICAL = "Oficina técnica de la instalación (/admin/): solo la usa quien opera el servidor."

MIRROR = (
    "Tabla técnica de Django: repite el rol de iglesia de la cuenta (que sí está en «Lo que puedes hacer en tu iglesia») "
    "o guarda permisos de la oficina técnica."
)

NOT_THEIRS: dict[tuple[str, str], str] = {
    ("admin.LogEntry", "user"): TECHNICAL,
    ("auth.User_groups", "user"): MIRROR,
    ("auth.User_user_permissions", "user"): MIRROR,
    ("core.Invitation", "invited_by"): STAFF_ACT,
    ("core.Invitation", "assigned_mentor"): OTHER_PERSON,
    ("core.ParticipantProfile", "assigned_mentor"): OTHER_PERSON,
    ("core.DiscipleshipPath", "created_by"): STAFF_ACT,
    ("core.QuestionResponse", "responder"): STAFF_ACT,
    ("core.FollowUpRequest", "participant"): ABOUT,
    ("core.FollowUpRequest", "created_by"): STAFF_ACT,
    ("core.FollowUpRequest", "assigned_to"): STAFF_ACT,
    ("core.PastoralNote", "participant"): ABOUT,
    ("core.PastoralNote", "author"): STAFF_ACT,
    ("core.AIDraft", "generated_by"): STAFF_ACT,
    ("events.Event", "procedures_approved_by"): STAFF_ACT,
    ("events.Event", "created_by"): STAFF_ACT,
    ("events.EventMembership", "created_by"): STAFF_ACT,
    ("events.MembershipAlias", "created_by"): STAFF_ACT,
    ("events.TutorAssignment", "created_by"): STAFF_ACT,
    ("events.AccessRequest", "decided_by"): STAFF_ACT,
    ("events.EventParticipant", "created_by"): CAMP,
    ("events.EventParticipant", "updated_by"): CAMP,
    ("events.ConsentRecord", "recorded_by"): CAMP,
    ("events.QuestionnaireVersion", "published_by"): STAFF_ACT,
    ("events.QuestionnaireVersion", "created_by"): STAFF_ACT,
    ("events.Interview", "conducted_by"): CAMP,
    ("events.Interview", "lock_holder"): CAMP,
    ("events.Interview", "discarded_by"): CAMP,
    ("events.Answer", "answered_by"): CAMP,
    ("events.AnswerRevision", "changed_by"): CAMP,
    ("events.ParticipantSession", "started_by"): CAMP,
    ("events.FollowUp", "assigned_to"): CAMP,
    ("events.FollowUp", "created_by"): CAMP,
    ("events.FollowUpDecisionChange", "changed_by"): CAMP,
    ("events.FollowUpDecisionChange", "continued_by"): CAMP,
    ("events.ReportSnapshot", "created_by"): CAMP,
    ("events.AuditEvent", "actor"): AUDIT,
    ("people.Person", "birth_date_verified_by"): STAFF_ACT,
    ("people.Person", "merged_into"): MERGE,
    ("people.ChurchAuditEvent", "actor"): AUDIT,
    ("people.EventParticipantLink", "linked_by"): STAFF_ACT,
    ("people.PersonMerge", "survivor"): MERGE,
    ("people.PersonMerge", "absorbed"): MERGE,
    ("people.PersonMerge", "merged_by"): MERGE,
    ("people.PersonMerge", "undone_by"): MERGE,
    ("people.DuplicateDismissal", "person_a"): MERGE,
    ("people.DuplicateDismissal", "person_b"): MERGE,
    ("people.DuplicateDismissal", "dismissed_by"): MERGE,
    ("people.Tag", "created_by"): STAFF_ACT,
    ("people.PersonTag", "person"): ABOUT,
    ("people.PersonTag", "added_by"): STAFF_ACT,
    ("people.PeopleImport", "imported_by"): STAFF_ACT,
    ("people.PeopleImport", "undone_by"): STAFF_ACT,
    ("groups.Group", "created_by"): STAFF_ACT,
    ("groups.GroupMembership", "added_by"): STAFF_ACT,
    ("groups.GroupJoinRequest", "decided_by"): STAFF_ACT,
    ("groups.GroupMeeting", "recorded_by"): STAFF_ACT,
    ("notices.Announcement", "created_by"): STAFF_ACT,
    ("agenda.EventPage", "created_by"): STAFF_ACT,
    ("agenda.EventRegistration", "registered_by"): STAFF_ACT,
    ("agenda.EventRegistration", "checked_in_by"): STAFF_ACT,
    ("agenda.GuardianConsent", "recorded_by"): STAFF_ACT,
    ("agenda.GuardianConsent", "revoked_by"): STAFF_ACT,
    ("agenda.EventNotice", "created_by"): STAFF_ACT,
    ("agenda.EventSeries", "created_by"): STAFF_ACT,
    ("agenda.SeriesOccurrence", "cancelled_by"): STAFF_ACT,
    ("resources.Devotional", "created_by"): STAFF_ACT,
    ("resources.MediaItem", "created_by"): STAFF_ACT,
    ("resources.ReadingPlan", "rights_confirmed_by"): STAFF_ACT,
    ("resources.ReadingPlan", "created_by"): STAFF_ACT,
    ("resources.ContentApproval", "requested_by"): STAFF_ACT,
    ("resources.ContentApproval", "approved_by"): STAFF_ACT,
    ("resources.ContentApproval", "returned_by"): STAFF_ACT,
    ("serving.Team", "created_by"): STAFF_ACT,
    ("serving.ServiceDate", "created_by"): STAFF_ACT,
    ("serving.SwapRequest", "decided_by"): STAFF_ACT,
    ("care.FollowUp", "person"): ABOUT,
    ("care.FollowUp", "owner"): ABOUT,
    ("care.FollowUp", "created_by"): STAFF_ACT,
    ("care.FollowUpStep", "by"): ABOUT,
    ("feedback.FeedbackGiven", "person"): ANONYMOUS,
    ("sitio.PublicHome", "published_by"): STAFF_ACT,
    ("sitio.PublicMedia", "published_by"): STAFF_ACT,
    ("espacios.Space", "created_by"): STAFF_ACT,
    ("espacios.Booking", "decided_by"): STAFF_ACT,
    ("formularios.Form", "long_text_ack_by"): STAFF_ACT,
    ("formularios.Form", "task_owner"): STAFF_ACT,
    ("formularios.Form", "created_by"): STAFF_ACT,
    ("formularios.FormVersion", "published_by"): STAFF_ACT,
    ("formularios.FormReader", "added_by"): STAFF_ACT,
    ("formularios.FormTask", "owner"): STAFF_ACT,
    ("formularios.FormTask", "closed_by"): STAFF_ACT,
    ("oracion.PrayerPolicy", "accepted_by"): STAFF_ACT,
    ("oracion.PrayerRequest", "only_reader"): OTHER_PERSON,
    ("cuenta_datos.RemovalRequest", "closed_by"): STAFF_ACT,
    # Wave 12. The pastor who switched the directory on for the church: the church's decision, not a choice of a person.
    ("people.DirectoryPolicy", "accepted_by"): STAFF_ACT,
    # A booking that repeats: who approved or rejected it, and who ended it (the administration, or who asked: the
    # series' state is in the download of who asked; which account pressed the button is the church's record).
    ("espacios.BookingSeries", "decided_by"): STAFF_ACT,
    ("espacios.BookingSeries", "ended_by"): STAFF_ACT,
    # A template of a team's order holds titles and minutes, no people: it is the team's, whoever saved it.
    ("serving.PlanTemplate", "created_by"): STAFF_ACT,
    # How many came to a gathering, typed by the administration: a number of the church, about no person.
    ("agenda.EventHeadcount", "recorded_by"): STAFF_ACT,
}


# ---- the inventory -------------------------------------------------------------------------------
def _points_at_a_person(relation) -> bool:
    target = getattr(relation, "related_model", None)
    return bool(
        relation.is_relation and (target is Person or target is get_user_model())
        and (relation.concrete or relation.many_to_many) and not relation.auto_created
    )


def relations_to_people() -> list[tuple[str, str]]:
    """Every relation of every installed model whose target is ``Person`` or the account model."""
    found = []
    for model in apps.get_models(include_auto_created=True):
        for relation in model._meta.get_fields():
            if _points_at_a_person(relation):
                found.append((model._meta.label, relation.name))
    return sorted(found)


def registered() -> dict[tuple[str, str], str]:
    """(model, field) -> the key of the category that answers for it."""
    return {relation: category.key for category in CATEGORIES for relation in category.relations}


def undecided_relations() -> list[tuple[str, str]]:
    known = registered()
    return [relation for relation in relations_to_people() if relation not in known and relation not in NOT_THEIRS]


# ---- reading -------------------------------------------------------------------------------------
def _count(found: dict) -> int:
    return sum(len(rows) for rows in found.values())


@dataclass
class Summary:
    category: Category
    count: int
    parts: list[tuple[str, int]]
    where: tuple[str, str] | None
    words: list[str] = field(default_factory=list)


def summaries(ctx: Ctx) -> list[Summary]:
    """One line per category for the page: how many rows, by kind, and where they are seen or changed."""
    found = []
    for category in CATEGORIES:
        data = category.collect(ctx)
        parts = [(label, len(rows)) for label, rows in data.items() if rows]
        found.append(Summary(category, _count(data), parts, category.where(ctx), category.words(ctx)))
    return found


def staff_counts(ctx: Ctx) -> list[tuple[Category, int | None]]:
    """For the administration's task: the count of each category it may already see; ``None`` for what is
    private to the person (named, never counted)."""
    found = []
    for category in CATEGORIES:
        found.append((category, _count(category.collect(ctx)) if category.staff_count else None))
    return found


NOT_INCLUDED = (
    "Lo que el equipo pastoral o la administración escribió o lleva sobre ti (seguimientos, etiquetas, etapa, notas): son registros "
    "de tu iglesia. Pregunta a la administración de tu iglesia.",
    "El registro de actividad de la iglesia (quién hizo qué).",
    "Tus entrevistas, respuestas, autorizaciones y seguimientos de un campamento: son un expediente confidencial con sus propias "
    "reglas. Pídelo a la administración del evento.",
    "Nombres, correos o teléfonos de otras personas: ni quién guía tu grupo, ni quién coordina tu equipo, ni quién decidió algo.",
    "Respuestas a formularios públicos (los que se llenan sin cuenta), aunque después se hayan ligado a tu registro.",
    "Las respuestas que un pastor escribió a tus peticiones de oración y lo que escribiste en el camino de discipulado: "
    "están en sus propias páginas.",
    "Lo que tengas en otra iglesia que use Emmaus: descárgalo estando en esa iglesia.",
)


def export(ctx: Ctx, *, now=None) -> dict:
    """The download: plain words for keys, the church of the request only."""
    categories = []
    for category in CATEGORIES:
        data = {label: rows for label, rows in category.collect(ctx).items() if rows}
        categories.append({"categoría": category.title, "para qué lo tiene Emmaus": category.why, "datos": data})
    included = [TEXT_KINDS[kind] for kind in TEXT_KINDS if kind in ctx.texts]
    return {
        "qué es este archivo": (
            "Lo que Emmaus guarda sobre ti en esta iglesia, por categoría. Lo generaste tú; Emmaus no guardó una copia."
        ),
        "iglesia": ctx.church.name,
        "generado": moment(ctx, now or timezone.now()) + f" (hora de {church_tz(ctx.church).key})",
        "textos que pediste incluir": included or ["Ninguno"],
        "lo que este archivo no trae": list(NOT_INCLUDED),
        "categorías": categories,
    }
