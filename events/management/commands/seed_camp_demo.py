"""Synthetic camp for demos, screenshots and load tests. Never for production.

Every name is generated from common first names and surnames; no real person is used.
"""

import os
import random
import secrets
from datetime import time, timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import Church, Role
from core.services import assign_role
from events.models import (
    Answer,
    ConsentRecord,
    Event,
    EventMembership,
    EventParticipant,
    FollowUp,
    Interview,
    InterviewStation,
    InterviewStatus,
    MembershipRole,
    Sede,
    TutorAssignment,
)
from events.services.event_questionnaires import use_version
from events.services.metrics import local_tz
from events.services.questionnaire import (
    DEFAULT_CAMP_QUESTIONNAIRE,
    all_questions,
    create_questionnaire_from_definition,
    options_by_code,
)
from events.services.reports import generate_snapshot, scopes_for_event, slots_for_day

FIRST = ["Mariana", "Sofía", "Valeria", "Camila", "Regina", "Ximena", "Daniela", "Fernanda", "Renata", "Paula", "Andrea",
         "Itzel", "Abril", "Natalia", "Jimena", "Lucía", "Diego", "Santiago", "Mateo", "Emiliano", "Leonardo", "Sebastián",
         "Iker", "Joaquín", "Ángel", "Gael", "Rodrigo", "Tadeo", "Emilio", "Alejandro", "Bruno", "Adrián", "Josué", "Aarón"]
LAST = ["Hernández", "García", "Martínez", "López", "González", "Pérez", "Rodríguez", "Sánchez", "Ramírez", "Cruz",
        "Flores", "Gómez", "Morales", "Vázquez", "Reyes", "Jiménez", "Torres", "Díaz", "Gutiérrez", "Ruiz", "Mendoza",
        "Aguilar", "Ortiz", "Moreno", "Castillo", "Romero", "Álvarez", "Méndez", "Chávez", "Rivera", "Juárez", "Domínguez"]
GROUPS = ["Iglesia Centro", "Iglesia Lomas", "Iglesia del Valle", "Misión Tepeyac", "Iglesia Esperanza",
          "Grupo Universitario", "Iglesia Nueva Vida", "Misión Santa Fe"]
TEAMS = ["Equipo Betel", "Equipo Jordán", "Equipo Monte Sion", "Equipo Getsemaní"]
SEDES = [("Satélite", "satelite", "Naucalpan"), ("Zona Esmeralda", "zona-esmeralda", "Atizapán"),
         ("Lomas Verdes", "lomas-verdes", "Naucalpan"), ("CDMX", "cdmx", "Ciudad de México"), ("Mérida", "merida", "Mérida")]
STAFF = [
    ("Laura", "Méndez Ortiz", "administrador"), ("Ernesto", "Salas Vega", "pastor"), ("Rebeca", "Torres Lima", "cuidado"),
    ("Marta", "Ríos Paredes", "tutor"), ("Hugo", "Cárdenas Beltrán", "tutor"), ("Patricia", "Núñez Soto", "tutor"),
    ("Samuel", "Ibarra Rosas", "tutor"),
    ("Ana", "López Ferrer", "coordinador"), ("Carlos", "Vega Durán", "coordinador"), ("Elena", "Fuentes Mora", "coordinador"),
    ("Jorge", "Pineda Cruz", "coordinador"), ("Lucía", "Serrano Gil", "coordinador"), ("Raúl", "Ochoa León", "coordinador"),
    ("Diana", "Campos Rey", "coordinador"), ("Tomás", "Herrera Paz", "coordinador"), ("Sara", "Lozano Mejía", "coordinador"),
    ("Iván", "Cortés Nava", "coordinador"), ("Noemí", "Valdez Ruiz", "coordinador"), ("Pablo", "Rangel Sosa", "coordinador"),
]
DOUBTS = ["Me cuesta entender cómo orar.", "A veces dudo si Dios me escucha.", "Tengo preguntas sobre la Biblia.",
          "No sé cómo explicar mi fe a mis amigos."]
SUPPORT = ["Me gustaría platicar con alguien sobre esto.", "Prefiero contarlo en persona.", "Es algo que pasa en casa."]
OBSTACLES = ["Me cuesta ser constante.", "Tengo miedo de lo que piensen otros.", "No tengo con quién estudiar la Biblia."]


def weighted(rng, pairs):
    total = sum(w for _, w in pairs)
    pick = rng.uniform(0, total)
    upto = 0
    for value, weight in pairs:
        upto += weight
        if pick <= upto:
            return value
    return pairs[-1][0]


class Command(BaseCommand):
    help = "Crea un campamento de demostración con datos sintéticos (solo con DEBUG=true)."

    def add_arguments(self, parser):
        parser.add_argument("--participants", type=int, default=400)
        parser.add_argument("--completed", type=float, default=0.62, help="Fracción de entrevistas completadas.")
        parser.add_argument("--password", default=os.environ.get("EMMAUS_DEMO_PASSWORD", ""))
        parser.add_argument("--seed", type=int, default=2026)
        parser.add_argument("--slug", default="jovenes-2026")
        parser.add_argument("--hours", type=int, default=9, help="Horas simuladas de entrevistas hasta ahora.")
        parser.add_argument("--reset", action="store_true", help="Borra el evento de demostración si ya existe.")

    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError("Este comando solo funciona con DEBUG=true. Nunca uses datos de demostración en producción.")
        rng = random.Random(opts["seed"])
        password = opts["password"] or secrets.token_urlsafe(9)
        existing = Event.objects.filter(slug=opts["slug"]).first()
        if existing and not opts["reset"]:
            raise CommandError(f"Ya existe el evento {opts['slug']}. Usa --reset para recrearlo.")
        with transaction.atomic():
            if existing:
                for snapshot in existing.snapshots.all():
                    snapshot.delete(purge=True)
                existing.delete()
            event, users = self._build(rng, password, opts)
        self._simulate(event, users, rng, opts)
        self.stdout.write(self.style.SUCCESS(f"Listo: {event.name} ({event.participants.count()} camperos sintéticos)."))
        self.stdout.write("Cuentas de demostración (todas con la misma contraseña):")
        for label, user in users["by_label"].items():
            self.stdout.write(f"  {label:<22} {user.email}")
        self.stdout.write(f"Contraseña: {password}")

    # ---------------------------------------------------------------------------------
    def _user(self, first, last, password):
        email = f"{first.lower()}.{last.split()[0].lower()}@demo.invalid".translate(str.maketrans("áéíóúñ", "aeioun"))
        user, _ = User.objects.get_or_create(username=email, defaults={"email": email})
        user.first_name, user.last_name, user.email, user.is_active = first, last, email, True
        user.set_password(password)
        user.save()
        return user

    def _build(self, rng, password, opts):
        church, _ = Church.objects.get_or_create(slug="iglesia-demo", defaults={"name": "Iglesia de demostración"})
        sedes = [
            Sede.objects.update_or_create(church=church, slug=slug, defaults={"name": name, "city": city, "order": order, "active": True})[0]
            for order, (name, slug, city) in enumerate(SEDES)
        ]
        # Its own random stream: the sedes leave every other seeded value (names, ages, interviews) as it was.
        sede_rng = random.Random(opts["seed"] + 1)
        version = create_questionnaire_from_definition(church, DEFAULT_CAMP_QUESTIONNAIRE)
        today = timezone.localdate()
        event = Event.objects.create(
            church=church, name="Campamento de Jóvenes 2026", slug=opts["slug"], location="Rancho El Encino, Valle de Bravo",
            starts_on=today - timedelta(days=1), ends_on=today + timedelta(days=1),
            report_window_start=time(0, 0), report_window_end=time(23, 0), procedures_approved_at=timezone.now(),
        )
        use_version(event, version)
        for n in range(1, 11):
            InterviewStation.objects.create(event=event, number=n, name=f"Computadora {n}")
        users = {"by_label": {}, "tutors": [], "coordinators": []}
        for first, last, kind in STAFF:
            user = self._user(first, last, password)
            role = {"cuidado": MembershipRole.PASTOR}.get(kind, kind)
            membership = EventMembership.objects.create(event=event, user=user, role=role, can_view_sensitive=kind == "cuidado")
            if kind == "administrador":
                assign_role(user, church, Role.CHURCH_ADMIN)
                user.is_staff = True
                user.save(update_fields=["is_staff"])
            if kind == "tutor":
                membership.team_name = TEAMS[len(users["tutors"])]
                membership.save(update_fields=["team_name"])
                users["tutors"].append(membership)
            if kind == "coordinador":
                users["coordinators"].append(membership)
            key = {"administrador": "Administración", "pastor": "Pastor", "cuidado": "Equipo de cuidado"}.get(kind, kind.capitalize())
            users["by_label"].setdefault(key, user)
        for index, coordinator in enumerate(users["coordinators"]):
            TutorAssignment.objects.create(event=event, tutor=users["tutors"][index % len(users["tutors"])], coordinator=coordinator)
        names = set()
        for number in range(1, opts["participants"] + 1):
            while True:
                name = f"{rng.choice(FIRST)} {rng.choice(LAST)} {rng.choice(LAST)}"
                if name not in names:
                    names.add(name)
                    break
            age = weighted(rng, [(rng.randint(12, 14), 30), (rng.randint(15, 17), 45), (rng.randint(18, 24), 25)])
            coordinator = users["coordinators"][(number - 1) % len(users["coordinators"])] if number % 57 else None
            participant = EventParticipant.objects.create(
                event=event, number=number, full_name=name, age=age, room=f"Cabaña {rng.randint(1, 20)}",
                church_group=rng.choice(GROUPS), coordinator=coordinator, sede=sede_rng.choice(sedes),
            )
            if age < 18 and rng.random() < 0.94:
                ConsentRecord.objects.create(participant=participant, kind=ConsentRecord.Kind.GUARDIAN, method=ConsentRecord.Method.PAPER)
        return event, users

    def _answer_set(self, rng):
        answers = {"salvo": [weighted(rng, [("si", 62), ("no", 8), ("no_seguro", 22), ("prefiero_no", 8)])]}
        answers["dudas"] = [weighted(rng, [("si", 35), ("no", 55), ("prefiero_no", 10)])]
        if answers["dudas"] == ["si"] and rng.random() < 0.5:
            answers["dudas_detalle"] = rng.choice(DOUBTS)
        support = weighted(rng, [("ninguna", 40), ("prefiero_no", 10), ("cats", 50)])
        if support == "cats":
            cats = ["familia", "bienestar_emocional", "vida_personal", "relaciones", "sexualidad", "otra"]
            weights = [30, 28, 20, 18, 6, 8]
            chosen = {weighted(rng, list(zip(cats, weights))) for _ in range(rng.choice([1, 1, 2]))}
            answers["apoyo"] = sorted(chosen)
            if rng.random() < 0.4:
                answers["apoyo_detalle"] = rng.choice(SUPPORT)
        else:
            answers["apoyo"] = [support]
        decision = weighted(rng, [("recibir_cristo", 12), ("consagrarse", 25), ("seguir_aprendiendo", 38), ("ninguna", 18), ("prefiero_no", 7)])
        answers["decision"] = [decision] + (["hablar_lider"] if decision not in ("ninguna", "prefiero_no") and rng.random() < 0.3 else [])
        if "recibir_cristo" in answers["decision"]:
            answers["orientacion"] = [weighted(rng, [("si", 50), ("no", 20), ("pendiente", 30)])]
        if rng.random() < 0.15:
            answers["impedimento"] = rng.choice(OBSTACLES)
        return answers

    def _write_interview(self, event, participant, rng, *, completed_at, users, status):
        version = event.questionnaire_versions()[0]
        coordinator_user = participant.coordinator.user if participant.coordinator else users["by_label"]["Administración"]
        started = completed_at - timedelta(minutes=rng.randint(7, 16))
        interview = Interview.objects.create(
            event=event, participant=participant, version=version, conducted_by=coordinator_user,
            mode=Interview.Mode.SUPERVISED if rng.random() < 0.3 else Interview.Mode.ASSISTED,
            station=event.stations.get(number=rng.randint(1, 10)), started_at=started,
            last_activity_at=completed_at, status=status,
            completed_at=completed_at if status == Interview.Status.COMPLETED else None,
        )
        questions = {q.code: q for q in all_questions(version)}
        values = self._answer_set(rng)
        if status == Interview.Status.IN_PROGRESS:
            keep = list(values)[: rng.randint(1, 3)]
            values = {k: values[k] for k in keep}
        for code, value in values.items():
            question = questions[code]
            answer = Answer.objects.create(interview=interview, question=question, text_value=value if isinstance(value, str) else "",
                                           answered_by=None if interview.mode == Interview.Mode.SUPERVISED else coordinator_user,
                                           answered_by_participant=interview.mode == Interview.Mode.SUPERVISED)
            if not isinstance(value, str):
                opts = options_by_code(question)
                answer.options.set([opts[c] for c in value])
        if status == Interview.Status.COMPLETED:
            wants = "hablar_lider" in values.get("decision", []) or values.get("apoyo", ["ninguna"])[0] not in ("ninguna", "prefiero_no")
            decision = weighted(rng, [("requerido", 70), ("por_evaluar", 15), ("no_requerido", 15)]) if wants else weighted(rng, [("no_requerido", 88), ("por_evaluar", 12)])
            interview.follow_up_decision = decision
            interview.save(update_fields=["follow_up_decision"])
            created = []
            if decision == "requerido":
                reason = FollowUp.Reason.TALK_TO_LEADER if "hablar_lider" in values.get("decision", []) else FollowUp.Reason.COORDINATOR
                created.append(FollowUp.objects.create(
                    event=event, participant=participant, interview=interview, reason=reason, created_by=coordinator_user,
                    status=weighted(rng, [("pendiente", 35), ("en_proceso", 30), ("atendido", 35)]),
                    assigned_to=rng.choice(users["tutors"]).user if rng.random() < 0.7 else None,
                ))
            if values.get("apoyo", ["ninguna"])[0] not in ("ninguna", "prefiero_no"):
                # Mirrors finalize(): support requested in a confidential question goes to the care team only.
                created.append(FollowUp.objects.create(
                    event=event, participant=participant, interview=interview, reason=FollowUp.Reason.SUPPORT_REQUESTED,
                    confidential=True, status=weighted(rng, [("pendiente", 50), ("en_proceso", 30), ("atendido", 20)]),
                ))
            FollowUp.objects.filter(pk__in=[f.pk for f in created]).update(created_at=completed_at + timedelta(minutes=1))
        participant.interview_status = InterviewStatus.COMPLETED if status == Interview.Status.COMPLETED else InterviewStatus.IN_PROGRESS
        participant.save(update_fields=["interview_status"])
        return interview

    def _simulate(self, event, users, rng, opts):
        now = timezone.now()
        start = now - timedelta(hours=opts["hours"])
        eligible = [p for p in event.participants.filter(coordinator__isnull=False).order_by("?")]
        # Teams progress unevenly so the dashboards have something to say.
        rates = {t.id: r for t, r in zip(users["tutors"], [1.0, 0.85, 0.7, 0.45])}
        planned = []
        for p in eligible:
            rate = rates.get(p.coordinator.tutor_assignment.tutor_id, 0.75) / 0.75 * opts["completed"]
            if rng.random() < rate:
                planned.append((start + timedelta(seconds=rng.uniform(0, (now - start).total_seconds() - 900)), p))
        planned.sort(key=lambda pair: pair[0])
        tz = local_tz(event)
        slots = []
        for day in {start.astimezone(tz).date(), now.astimezone(tz).date()}:
            slots += [s for s in slots_for_day(event, day) if start < s <= now]
        slots.sort()
        cursor = 0
        for slot in slots + [None]:
            limit = slot or now
            while cursor < len(planned) and planned[cursor][0] <= limit:
                self._write_interview(event, planned[cursor][1], rng, completed_at=planned[cursor][0], users=users, status=Interview.Status.COMPLETED)
                cursor += 1
            if slot is not None:
                for scope in scopes_for_event(event):
                    generate_snapshot(event, scope, trigger="automatico", cutoff_at=slot, now=slot)
        pending = list(event.participants.filter(interview_status=InterviewStatus.PENDING, coordinator__isnull=False)[:8])
        for index, participant in enumerate(pending):
            minutes = 35 if index < 2 else rng.randint(1, 9)
            interview = self._write_interview(event, participant, rng, completed_at=now - timedelta(minutes=minutes), users=users, status=Interview.Status.IN_PROGRESS)
            Interview.objects.filter(pk=interview.pk).update(last_activity_at=now - timedelta(minutes=minutes))
        done = event.interviews.filter(status=Interview.Status.COMPLETED).first()
        if done:
            FollowUp.objects.create(event=event, participant=done.participant, interview=done, kind=FollowUp.Kind.SAFETY,
                                    reason=FollowUp.Reason.SAFETY, details="Reporte sintético de prueba.", created_by=done.conducted_by)
