from django.urls import path

from .views import admin, coordinator, dashboards, home, interview, reports

app_name = "events"

E = "<slug:slug>/"
I = "<uuid:public_id>/"

urlpatterns = [
    path("", home.events_home, name="list"),
    path("nuevo/", home.event_create, name="create"),
    path(E, home.event_home, name="home"),
    path(E + "computadora/", home.choose_station, name="choose_station"),
    # Coordinador
    path(E + "mi-inicio/", coordinator.coord_home, name="coord_home"),
    path(E + "mis-camperos/", coordinator.my_participants, name="my_participants"),
    path(E + "camperos/nuevo/", coordinator.participant_create, name="participant_create"),
    path(E + "pendientes/mias/", coordinator.coord_pending, name="coord_pending"),
    path(E + "mi-avance/", coordinator.coord_progress, name="coord_progress"),
    path(E + "lista-de-respaldo/", coordinator.backup_list, name="backup_list"),
    path(E + "camperos/" + I, coordinator.participant_detail, name="participant_detail"),
    path(E + "camperos/" + I + "editar/", coordinator.participant_edit, name="participant_edit"),
    path(E + "camperos/" + I + "autorizacion/", coordinator.guardian_authorization, name="guardian_authorization"),
    path(E + "camperos/" + I + "entrevista/", interview.interview_start, name="interview_start"),
    path(E + "camperos/<uuid:participant_id>/riesgo/", interview.safety_report, name="safety_report"),
    # Entrevistas
    path(E + "entrevistas/iniciar/", interview.interview_search, name="interview_search"),
    path(E + "entrevistas/" + I, interview.interview_detail, name="interview_detail"),
    path(E + "entrevistas/" + I + "continuar/", interview.interview_resume, name="interview_resume"),
    path(E + "entrevistas/" + I + "otra-computadora/", interview.interview_conflict, name="interview_conflict"),
    path(E + "entrevistas/" + I + "paso/<int:number>/", interview.interview_step, name="interview_step"),
    path(E + "entrevistas/" + I + "revision/", interview.interview_review, name="interview_review"),
    path(E + "entrevistas/" + I + "completada/", interview.interview_done, name="interview_done"),
    path(E + "entrevistas/" + I + "entregar/", interview.interview_handoff, name="interview_handoff"),
    path(E + "entrevistas/" + I + "descartar/", interview.interview_discard, name="interview_discard"),
    path(E + "entrevistas/" + I + "corregir/<int:number>/", interview.interview_correct, name="interview_correct"),
    # Tutor / pastor
    path(E + "mi-equipo/", dashboards.tutor_home, name="tutor_home"),
    path(E + "coordinadores/", dashboards.coordinators, name="coordinators"),
    path(E + "coordinadores/<int:membership_id>/", dashboards.coordinator_detail, name="coordinator_detail"),
    path(E + "entrevistas/", dashboards.interviews, name="interviews"),
    path(E + "pendientes/", dashboards.pending, name="pending"),
    path(E + "resumen/", dashboards.pastor_home, name="pastor_home"),
    path(E + "avance/", dashboards.progress, name="progress"),
    path(E + "seguimiento/", dashboards.follow_ups, name="follow_ups"),
    path(E + "seguimiento/<int:follow_up_id>/", dashboards.follow_up_detail, name="follow_up_detail"),
    path(E + "equipo/", dashboards.team, name="team"),
    # Reportes
    path(E + "reportes/", reports.reports, name="reports"),
    path(E + "reportes/generar/", reports.report_generate, name="report_generate"),
    path(E + "reportes/" + I, reports.report_detail, name="report_detail"),
    path(E + "reportes/" + I + "pdf/", reports.report_pdf, name="report_pdf"),
    path(E + "reportes/" + I + "presentacion/", reports.report_presentation, name="report_presentation"),
    path(E + "presentacion/", reports.presentation_live, name="presentation_live"),
    # Administración
    path(E + "administracion/", dashboards.admin_home, name="admin_home"),
    path(E + "participantes/", admin.participants, name="participants"),
    path(E + "participantes/importar/", admin.participant_import, name="participant_import"),
    path(E + "participantes/exportar/", admin.participant_export, name="participant_export"),
    path(E + "camperos/" + I + "datos/", admin.participant_data_export, name="participant_data_export"),
    path(E + "camperos/" + I + "eliminar/", admin.participant_delete, name="participant_delete"),
    path(E + "equipo/agregar/", admin.member_add, name="member_add"),
    path(E + "equipo/<int:membership_id>/", admin.member_edit, name="member_edit"),
    path(E + "equipo/asignaciones/", admin.assignments, name="assignments"),
    path(E + "solicitudes/<int:pk>/aprobar/", admin.access_request_approve, name="access_request_approve"),
    path(E + "solicitudes/<int:pk>/rechazar/", admin.access_request_reject, name="access_request_reject"),
    path(E + "codigo/nuevo/", admin.join_code_regenerate, name="join_code_regenerate"),
    path(E + "codigo/nombre/", admin.join_by_name_enable, name="join_by_name_enable"),
    path(E + "administracion/codigo/", admin.join_code_sign, name="join_code_sign"),
    path(E + "cuestionarios/", admin.questionnaires, name="questionnaires"),
    path(E + "cuestionarios/<int:version_id>/", admin.version_detail, name="version_detail"),
    path(E + "cuestionarios/<int:version_id>/secciones/nueva/", admin.section_add, name="section_add"),
    path(E + "cuestionarios/<int:version_id>/secciones/<int:section_id>/pregunta/", admin.question_edit, name="question_add"),
    path(E + "cuestionarios/<int:version_id>/preguntas/<int:question_id>/", admin.question_edit, name="question_edit"),
    path(E + "cuestionarios/<int:version_id>/preguntas/<int:question_id>/opcion/", admin.option_add, name="option_add"),
    path(E + "configuracion/", admin.settings_view, name="settings"),
    path(E + "configuracion/computadoras/", admin.stations, name="stations"),
    path(E + "configuracion/actividad/", admin.audit_log, name="audit"),
    path(E + "configuracion/sesiones/", admin.sessions, name="sessions"),
]
