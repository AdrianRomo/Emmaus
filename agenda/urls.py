from django.urls import path

from . import duplicate, series_views, simple, views

app_name = "agenda"

urlpatterns = [
    path("", views.event_list, name="list"),
    path("administrar/", views.admin_list, name="admin_list"),
    path("nuevo/", simple.create, name="create"),
    # Series (issue #93). "administrar" is a reserved address; "serie/<n>/" matches no route of an event.
    path("administrar/series/nueva/", series_views.create, name="series_create"),
    path("administrar/series/<int:series_id>/", series_views.admin_detail, name="series_admin"),
    path("administrar/series/<int:series_id>/editar/", series_views.edit, name="series_edit"),
    path("administrar/series/<int:series_id>/terminar/", series_views.end, name="series_end"),
    path("administrar/series/<int:series_id>/reanudar/", series_views.resume, name="series_resume"),
    path("administrar/series/<int:series_id>/fechas/<int:occurrence_id>/no-hay/", series_views.call_off, name="series_call_off"),
    path("administrar/series/<int:series_id>/fechas/<int:occurrence_id>/si-hay/", series_views.restore, name="series_restore"),
    path("serie/<int:series_id>/", series_views.member_detail, name="series"),
    path("<slug:slug>/datos/", simple.edit, name="event_edit"),
    path("<slug:slug>/duplicar/", duplicate.duplicate, name="duplicate"),
    path("<slug:slug>/", views.detail, name="detail"),
    path("<slug:slug>/inscribirme/", views.register, name="register"),
    path("<slug:slug>/cancelar/", views.cancel, name="cancel"),
    path("<slug:slug>/aceptar-lugar/", views.accept_offer, name="accept_offer"),
    path("<slug:slug>/salir-de-la-lista/", views.leave_waiting, name="leave_waiting"),
    path("<slug:slug>/pagina/", views.page_edit, name="page_edit"),
    path("<slug:slug>/inscritos/", views.registrations, name="registrations"),
    path("<slug:slug>/inscritos/llego/", views.walkup, name="walkup"),
    path("<slug:slug>/inscritos/<int:registration_id>/autorizacion/", views.consent, name="consent"),
    path("<slug:slug>/inscritos/<int:registration_id>/confirmar-edad/", views.confirm_age, name="confirm_age"),
    path("<slug:slug>/inscritos/<int:registration_id>/edad-de-la-visita/", views.guest_age, name="guest_age"),
    path("<slug:slug>/inscritos/<int:registration_id>/anular/", views.annul, name="annul"),
    path("<slug:slug>/inscritos/<int:registration_id>/quitar-de-la-lista/", views.waiting_remove, name="waiting_remove"),
    path("<slug:slug>/inscritos/<int:registration_id>/retirar-autorizacion/", views.consent_revoke, name="consent_revoke"),
    path("<slug:slug>/inscritos/<int:registration_id>/agregar-a-personas/", views.guest_promote, name="guest_promote"),
    path("<slug:slug>/inscritos/<int:registration_id>/acompanar/", views.visit_follow_up, name="visit_follow_up"),
    path("<slug:slug>/preguntas/", views.faq, name="faq"),
    path("<slug:slug>/preguntas/<int:faq_id>/", views.faq_edit, name="faq_edit"),
    path("<slug:slug>/preguntas/<int:faq_id>/borrar/", views.faq_delete, name="faq_delete"),
    path("<slug:slug>/avisos-publicos/", views.public_notices, name="public_notices"),
    path("<slug:slug>/avisos-publicos/<int:notice_id>/", views.public_notice_edit, name="public_notice_edit"),
    path("<slug:slug>/avisos-publicos/<int:notice_id>/retirar/", views.public_notice_retire, name="public_notice_retire"),
    path("<slug:slug>/avisos-publicos/<int:notice_id>/borrar/", views.public_notice_delete, name="public_notice_delete"),
    path("<slug:slug>/programa/", views.schedule, name="schedule"),
    path("<slug:slug>/programa/<int:item_id>/", views.schedule_edit, name="schedule_edit"),
    path("<slug:slug>/programa/<int:item_id>/quitar/", views.schedule_delete, name="schedule_delete"),
]
