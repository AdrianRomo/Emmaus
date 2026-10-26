"""Borra los datos de demostración para que un evento real empiece con la base vacía.

Los marcadores son los mismos que usa el resto del código:

* iglesias con identificador ``iglesia-demo`` (``seed_camp_demo``, con cualquier ``--slug`` de
  evento, p. ej. ``jovenes-2026`` o ``carga-2026``) o ``emmaus-demo`` (``seed_dev_data`` y su ruta
  de discipulado);
* usuarios cuyo correo termina en ``@demo.invalid`` o ``@example.org`` (``events/checks.py`` y
  ``deactivate_demo_accounts``). Un superusuario nunca cuenta como demostración, aunque su correo
  lo parezca (``createsuperuser`` con una dirección de relleno): se omite y se avisa.

El comando se niega, y no borra nada, cuando la demostración está enredada con datos reales:

* datos de otra iglesia usan un cuestionario o una pregunta de una iglesia de demostración;
* cualquier fila que no cae con las iglesias de demostración apunta a un usuario de demostración,
  por cualquier llave foránea de cualquier aplicación (CASCADE o SET_NULL): borrarlo borraría o
  dejaría sin autor esos datos reales. La excepción es ``admin.LogEntry``: el historial del admin de
  Django de una cuenta de demostración es historia de la demostración y se borra con ella (el de un
  superusuario no se toca, porque el superusuario no se borra).

Si al final no queda ninguna iglesia, también borra todas las sesiones y los límites de intentos:
la base queda vacía para el primer evento real.

Sin ``--yes`` hace el mismo borrado dentro de una transacción que siempre se revierte, así que
muestra exactamente lo que se borraría sin cambiar nada. No depende de DEBUG: la demostración vive
en producción y ahí es donde hay que quitarla.
"""

from collections import Counter, defaultdict

from django.apps import apps
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, ProtectedError, Q

from core.models import (
    AIDraft,
    Church,
    DiscipleshipPath,
    Enrollment,
    FollowUpRequest,
    Lesson,
    LessonProgress,
    ParticipantProfile,
    ParticipantQuestion,
    PastoralNote,
    QuestionResponse,
    ReflectionAnswer,
    ReflectionQuestion,
)
from events.models import Answer, AuthThrottle, Event, EventParticipant, FollowUp, Interview, ReportSnapshot, Sede

DEMO_CHURCH_SLUGS = ("iglesia-demo", "emmaus-demo")
DEMO_EMAIL_DOMAINS = ("@demo.invalid", "@example.org")

DISCIPLESHIP_MODELS = (
    DiscipleshipPath, Lesson, ReflectionQuestion, Enrollment, LessonProgress, ReflectionAnswer, ParticipantProfile,
    ParticipantQuestion, QuestionResponse, FollowUpRequest, PastoralNote, AIDraft,
)
SUMMARY = (
    ("Iglesias", (Church,)),
    ("Eventos", (Event,)),
    ("Camperos", (EventParticipant,)),
    ("Sedes", (Sede,)),
    ("Entrevistas", (Interview,)),
    ("Respuestas", (Answer,)),
    ("Cortes de seguimiento", (ReportSnapshot,)),
    ("Seguimientos", (FollowUp,)),
    ("Usuarios", (User,)),
    # Paths, lessons, enrollments, participant questions, pastoral notes, AI drafts… (seed_dev_data).
    ("Discipulado", DISCIPLESHIP_MODELS),
    ("Historial del admin de Django", (LogEntry,)),
    ("Sesiones abiertas", (Session,)),
    ("Límites de intentos", (AuthThrottle,)),
)


def demo_marked_users():
    query = Q()
    for domain in DEMO_EMAIL_DOMAINS:
        query |= Q(email__iendswith=domain)
    return User.objects.filter(query)


def relations_to_user():
    """Every foreign key and one-to-one of every installed app that points at User, hidden ones included.

    ``User._meta.related_objects`` leaves out the ``related_name="+"`` fields (Event.created_by,
    Interview.lock_holder, …), so ask for the fields with ``include_hidden``. Left out on purpose,
    because they belong to the demo user and go with it (CASCADE): its own many-to-many tables
    (groups, permissions) and its Django admin history (admin.LogEntry), which records what the
    demo account did, not data anyone else relies on.
    """
    return [
        field for field in User._meta.get_fields(include_hidden=True)
        if field.auto_created and not field.concrete and (field.one_to_many or field.one_to_one)
        and field.related_model._meta.auto_created is not User
        and field.related_model is not LogEntry
    ]


def owning_event(obj):
    if isinstance(obj, Event):
        return obj
    if hasattr(obj, "event_id"):
        return obj.event
    if hasattr(obj, "interview_id"):
        return obj.interview.event
    return None


def describe_protected(objects):
    """'events.Event (1): retiro-real' for each kind of row that keeps a demo questionnaire alive."""
    by_label = defaultdict(list)
    for obj in objects:
        by_label[obj._meta.label].append(obj)
    parts = []
    for label, objs in sorted(by_label.items()):
        events = {owning_event(obj) for obj in objs}
        where = sorted(event.slug if event else "?" for event in events)
        parts.append(f"{label} ({len(objs)}): {', '.join(where)}")
    return "; ".join(parts)


class _DryRun(Exception):
    """Raised inside the transaction to undo a simulated purge."""


class Command(BaseCommand):
    help = (
        "Borra las iglesias de demostración (iglesia-demo, emmaus-demo) con todo lo que cuelga de ellas y los "
        "usuarios @demo.invalid / @example.org que ningún dato real usa (nunca superusuarios). "
        "Sin --yes solo muestra lo que se borraría."
    )

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="Borrar de verdad. Sin esta opción no se borra nada.")

    def handle(self, *args, **options):
        apply = options["yes"]
        churches = list(Church.objects.filter(slug__in=DEMO_CHURCH_SLUGS).order_by("slug"))
        superusers = list(demo_marked_users().filter(is_superuser=True).order_by("email"))
        users = list(demo_marked_users().filter(is_superuser=False).order_by("email"))

        if superusers:
            self.stdout.write(
                self.style.WARNING(f"Se omiten {len(superusers)} superusuario(s) con correo de demostración (nunca se borran):")
            )
            for user in superusers:
                self.stdout.write(f"  {user.email}")
        if not churches and not users:
            self.stdout.write(
                "No hay datos de demostración: ninguna iglesia " + " / ".join(DEMO_CHURCH_SLUGS)
                + " ni usuarios " + " / ".join(DEMO_EMAIL_DOMAINS) + " que no sean superusuarios. No se borró nada."
            )
            return

        self.stdout.write(f"Iglesias de demostración ({len(churches)}), por nombre; revisa que ninguna sea real:")
        for church in churches:
            self.stdout.write(f"  «{church.name}»  (identificador {church.slug})")
        self.stdout.write(f"Usuarios de demostración ({len(users)}):")
        for user in users:
            self.stdout.write(f"  {user.email}")

        try:
            with transaction.atomic():
                deleted, emptied = self._purge([c.pk for c in churches], users)
                if not apply:
                    raise _DryRun
        except _DryRun:
            pass

        self._report(deleted, "Se borró:" if apply else "Se borraría:")
        if emptied:
            verb = "se borraron" if apply else "se borrarían"
            self.stdout.write(
                f"No queda ninguna iglesia, así que también {verb} todas las sesiones abiertas "
                f"({deleted.get(Session._meta.label, 0)}) y los límites de intentos "
                f"({deleted.get(AuthThrottle._meta.label, 0)}): la base queda vacía para el evento real."
            )
        if not apply:
            self.stdout.write(
                self.style.WARNING("Simulación: no se borró nada. Para borrar de verdad: python manage.py purge_demo_data --yes")
            )
            return
        remaining = Church.objects.count()
        self.stdout.write(self.style.SUCCESS(f"Datos de demostración borrados. Quedan {remaining} iglesia(s)."))
        if not remaining:
            self.stdout.write("La base no tiene iglesias: abre / y Emmaus te llevará a /setup/ para crear la iglesia real.")

    def _purge(self, church_ids, users):
        """Delete in an order the PROTECT foreign keys accept; return (rows deleted per model, emptied).

        A plain ``Church.delete()`` fails on a seeded demo: Django's PROTECT raises as soon as a
        protected row exists, even when the same cascade would delete it. Interview.version,
        Event.questionnaire_version, Answer.question and AnswerRevision.question all point at the
        questionnaire the church cascade reaches, so the interviews and events go first.
        """
        deleted = Counter()

        def delete(queryset):
            _, per_model = queryset.delete()
            deleted.update({label: n for label, n in per_model.items() if n})

        try:
            delete(Interview.objects.filter(event__church_id__in=church_ids))
            delete(Event.objects.filter(church_id__in=church_ids))
            delete(Church.objects.filter(pk__in=church_ids))
        except ProtectedError as error:
            raise CommandError(
                "Datos de otras iglesias usan un cuestionario o una pregunta de una iglesia de demostración: "
                f"{describe_protected(error.protected_objects)}. No se borró nada. "
                "Cámbiales el cuestionario (o bórralos) y vuelve a intentarlo."
            ) from error

        # The demo churches are gone inside this transaction: whatever still points at a demo user is
        # not demo data. Deleting the user would delete it (CASCADE) or erase its author (SET_NULL).
        blocking = self._references([u.pk for u in users])
        if blocking:
            emails = {u.pk: u.email for u in users}
            lines = [
                f"  {emails[user_id]}: " + ", ".join(f"{ref}: {n}" for ref, n in sorted(refs.items()))
                for user_id, refs in sorted(blocking.items(), key=lambda item: emails[item[0]])
            ]
            raise CommandError(
                "Datos que no son de demostración apuntan a usuarios de demostración; borrarlos los borraría o los "
                "dejaría sin autor:\n" + "\n".join(lines) + "\nNo se borró nada. Quita esas referencias, o solo "
                "desactiva las cuentas con deactivate_demo_accounts."
            )
        delete(User.objects.filter(pk__in=[u.pk for u in users]))

        emptied = not Church.objects.exists()
        if emptied:
            # Nobody real is signed in on a demo database; the first real camp starts without leftovers.
            delete(Session.objects.all())
            delete(AuthThrottle.objects.all())
        return deleted, emptied

    def _references(self, user_ids):
        """{user id: {"app.Model.field": rows}} for every row that still points at one of these users."""
        found = defaultdict(dict)
        if not user_ids:
            return found
        for relation in relations_to_user():
            model, field = relation.related_model, relation.field
            rows = (
                model._base_manager.filter(**{f"{field.name}__in": user_ids})
                .values(field.attname).annotate(n=Count("pk")).order_by()
            )
            for row in rows:
                found[row[field.attname]][f"{model._meta.label}.{field.name}"] = row["n"]
        return found

    def _report(self, deleted, heading):
        self.stdout.write(heading)
        for label, models in SUMMARY:
            count = sum(deleted.get(model._meta.label, 0) for model in models)
            self.stdout.write(f"  {label:<30} {count:>7}")
        self.stdout.write("Detalle por tabla (todo lo que alcanza la cascada):")
        for label in sorted(deleted):
            app_label, _, model_name = label.partition(".")
            try:
                meta = apps.get_model(app_label, model_name)._meta
            except LookupError:
                meta = None
            # Many-to-many link tables (auto_created) have no Spanish name worth printing.
            name = str(meta.verbose_name_plural) if meta and not meta.auto_created else ""
            self.stdout.write(f"  {label:<40} {deleted[label]:>7}  {name}".rstrip())
        self.stdout.write(f"  {'Total':<40} {sum(deleted.values()):>7}")
