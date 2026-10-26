# Every screen per role, with real ids from a synthetic database. Prints JSON for e2e/review-all.mjs.
#   DEBUG=true python manage.py seed_camp_demo --reset --password DemoCamp-2026
#   DEBUG=true python manage.py seed_dev_data --password DemoCamp-2026
#   DEBUG=true python manage.py shell < e2e/all_urls.py | tail -1 > /tmp/urls.json
# Side effect: when the questionnaire has no draft version, one is created ("Crear borrador"), so the question
# editor can be opened (a published version is never editable: 403).
import json
from django.contrib.auth import get_user_model
from django.urls import reverse
from events.models import Event, EventParticipant, Interview, FollowUp, EventMembership, MembershipRole, ReportSnapshot, QuestionnaireVersion, Sede
from events.services.questionnaire import duplicate_version
from core.models import DiscipleshipPath

ev = Event.objects.get(slug="jovenes-2026")
u = lambda n, *a: reverse(f"events:{n}", args=[ev.slug, *a])  # noqa: E731
ana = EventParticipant.objects.filter(event=ev, coordinator__user__email="ana.lopez@demo.invalid")
done = Interview.objects.filter(participant__in=ana, status="completada").first()
# The pastor opens it: confidential referrals belong to the care team (events.access), so pick one that is not
fu = FollowUp.objects.filter(participant__event=ev, kind=FollowUp.Kind.CARE, confidential=False).first()
coord = EventMembership.objects.filter(event=ev, role=MembershipRole.COORDINATOR, user__email="ana.lopez@demo.invalid").first()
snap = ReportSnapshot.objects.filter(event=ev, status="lista").order_by("-cutoff_at").first() or ReportSnapshot.objects.filter(event=ev).order_by("-cutoff_at").first()
version = ev.questionnaire_version  # published: its page can be read, its questions cannot be edited
draft = (QuestionnaireVersion.objects.filter(questionnaire=version.questionnaire, status=QuestionnaireVersion.Status.DRAFT).order_by("-number").first()
         or duplicate_version(version, get_user_model().objects.get(email="laura.mendez@demo.invalid")))
question = draft.sections.first().questions.first()
member = EventMembership.objects.filter(event=ev, role=MembershipRole.PASTOR).first()
path = DiscipleshipPath.objects.first()
sede = Sede.objects.filter(church=ev.church).first()  # seed_camp_demo creates five
pages = {
  "ana.lopez@demo.invalid": [["c-computadora", u("choose_station")], ["c-inicio", u("coord_home")], ["c-busqueda", u("interview_search") + "?q=mat"],
     ["c-camperos", u("my_participants")], ["c-pendientes", u("coord_pending")], ["c-avance", u("coord_progress")], ["c-registrar", u("participant_create")],
     ["c-ficha", u("participant_detail", ana.first().public_id)], ["c-entrevista", u("interview_detail", done.public_id)], ["c-seguimientos", u("follow_ups")],
     ["c-respaldo", u("backup_list")], ["c-eventos", reverse("events:list")], ["c-ayuda", u("ayuda")]],
  "marta.rios@demo.invalid": [["t-equipo", u("tutor_home")], ["t-coordinadores", u("coordinators")], ["t-coordinador", u("coordinator_detail", coord.id)],
     ["t-entrevistas", u("interviews")], ["t-pendientes", u("pending")], ["t-reportes", u("reports")]],
  "ernesto.salas@demo.invalid": [["p-resumen", u("pastor_home")], ["p-avance", u("progress")], ["p-seguimientos", u("follow_ups")],
     ["p-seguimiento", u("follow_up_detail", fu.id)], ["p-equipo", u("team")], ["p-reportes", u("reports")], ["p-corte", u("report_detail", snap.public_id)]],
  "laura.mendez@demo.invalid": [["a-inicio", u("admin_home")], ["a-participantes", u("participants")], ["a-importar", u("participant_import")],
     ["a-equipo", u("team")], ["a-agregar", u("member_add")], ["a-persona", u("member_edit", member.id)], ["a-asignaciones", u("assignments")],
     ["a-cuestionarios", u("questionnaires")], ["a-version", u("version_detail", version.id)], ["a-pregunta", u("question_edit", draft.id, question.id)],
     ["a-configuracion", u("settings")], ["a-actividad", u("audit")], ["a-sesiones", u("sessions")], ["a-eliminar", u("participant_delete", ana.first().public_id)],
     ["a-nuevo-evento", reverse("events:create")], ["a-codigo", u("join_code_sign")], ["a-sedes", u("sedes")], ["a-sede-nueva", u("sede_add")], ["a-sede", u("sede_edit", sede.id)]],
  "admin@example.org": [["d-panorama", reverse("church_dashboard")], ["d-participantes", reverse("participants_list")], ["d-invitaciones", reverse("invitations_list")],
     ["d-rutas", reverse("path_list")], ["d-ruta", reverse("path_detail", args=[path.id])], ["d-preguntas", reverse("questions_inbox")],
     ["d-seguimientos", reverse("church_follow_up_list")], ["d-configuracion", reverse("church_settings")], ["d-personas", reverse("user_list")], ["d-403", u("admin_home")]],
  "mentor@example.org": [["m-inicio", reverse("mentor_dashboard")], ["m-seguimientos", reverse("follow_up_list")]],
  "participant@example.org": [["e-camino", reverse("participant_dashboard")], ["e-preguntas", reverse("participant_questions")], ["e-preguntar", reverse("ask_question")],
     ["e-conversacion", reverse("request_follow_up")]],
  "": [["x-entrar", reverse("login")], ["x-inicio", reverse("landing")], ["x-olvide", reverse("password_reset")], ["x-404", "/no-existe/"],
     ["x-unirme", reverse("join")], ["x-evento", reverse("event_entry", args=[ev.join_code])]],
}
print(json.dumps(pages))
