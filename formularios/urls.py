from django.urls import path

from . import views

app_name = "formularios"

urlpatterns = [
    path("", views.member_list, name="list"),
    path("<int:form_id>/responder/", views.fill, name="fill"),
    path("<int:form_id>/gracias/", views.thanks, name="thanks"),
    path("pendientes/", views.pending, name="queue"),
    path("administrar/", views.manage, name="manage"),
    path("administrar/nuevo/", views.create, name="create"),
    path("administrar/pendientes-sin-responsable/", views.loose_tasks, name="loose_tasks"),
    path("administrar/<int:form_id>/", views.detail, name="detail"),
    path("administrar/<int:form_id>/editar/", views.edit, name="edit"),
    path("administrar/<int:form_id>/preguntas/nueva/", views.question, name="question_create"),
    path("administrar/<int:form_id>/preguntas/<int:question_id>/", views.question, name="question_edit"),
    path("administrar/<int:form_id>/preguntas/<int:question_id>/quitar/", views.question_delete, name="question_delete"),
    path("administrar/<int:form_id>/preguntas/<int:question_id>/mover/", views.question_move, name="question_move"),
    path("administrar/<int:form_id>/publicar/", views.publish, name="publish"),
    path("administrar/<int:form_id>/cerrar/", views.close, name="close"),
    path("administrar/<int:form_id>/lectores/", views.readers, name="readers"),
    path("administrar/<int:form_id>/respuestas/", views.submissions, name="submissions"),
    path("administrar/<int:form_id>/respuestas/descargar/", views.export, name="export"),
    path("administrar/<int:form_id>/respuestas/<int:submission_id>/", views.submission, name="submission"),
    path("administrar/<int:form_id>/respuestas/<int:submission_id>/pendiente/", views.task, name="task"),
    path("administrar/<int:form_id>/respuestas/<int:submission_id>/asignar/", views.task_assign, name="task_assign"),
    path("administrar/<int:form_id>/respuestas/<int:submission_id>/persona/", views.add_person, name="add_person"),
]
