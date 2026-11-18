from django.urls import path

from . import views

app_name = "serving"

urlpatterns = [
    path("", views.home, name="home"),
    path("no-puedo/", views.unavailable_add, name="unavailable_add"),
    path("no-puedo/<int:day_id>/quitar/", views.unavailable_remove, name="unavailable_remove"),
    path("equipos/", views.team_admin, name="team_admin"),
    path("equipos/nuevo/", views.team_create, name="team_create"),
    path("equipos/<int:team_id>/", views.team, name="team"),
    path("equipos/<int:team_id>/editar/", views.team_edit, name="team_edit"),
    path("equipos/<int:team_id>/archivar/", views.team_archive, name="team_archive"),
    path("equipos/<int:team_id>/personas/agregar/", views.member_add, name="member_add"),
    path("equipos/<int:team_id>/personas/<int:membership_id>/quitar/", views.member_remove, name="member_remove"),
    path("equipos/<int:team_id>/fechas/nueva/", views.date_create, name="date_create"),
    path("fechas/<int:date_id>/", views.date_detail, name="date_detail"),
    path("fechas/<int:date_id>/borrar/", views.date_delete, name="date_delete"),
    path("fechas/<int:assignment_id>/responder/", views.respond, name="respond"),
]
