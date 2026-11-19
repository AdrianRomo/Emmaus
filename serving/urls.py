from django.urls import path

from . import rota_views, views

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
    # Wave 10: roles, the rotation, swaps, reminders and the order of a date (serving.rota_views).
    path("equipos/<int:team_id>/organizar/", rota_views.organize, name="organize"),
    path("equipos/<int:team_id>/roles/guardar/", rota_views.role_save, name="role_save"),
    path("equipos/<int:team_id>/roles/<int:role_id>/retirar/", rota_views.role_retire, name="role_retire"),
    path("equipos/<int:team_id>/rotacion/guardar/", rota_views.rotation_save, name="rotation_save"),
    path("equipos/<int:team_id>/ajustes/", rota_views.settings_save, name="settings_save"),
    path("equipos/<int:team_id>/rotacion/", rota_views.rotation, name="rotation"),
    path("mis-fechas/<int:assignment_id>/cambio/", rota_views.swap_create, name="swap_create"),
    path("cambios/<int:swap_id>/responder/", rota_views.swap_answer, name="swap_answer"),
    path("cambios/<int:swap_id>/retirar/", rota_views.swap_cancel, name="swap_cancel"),
    path("cambios/<int:swap_id>/decidir/", rota_views.swap_decide, name="swap_decide"),
    path("recordatorios/", rota_views.reminders_choice, name="reminders_choice"),
    path("fechas/<int:date_id>/orden/", rota_views.plan, name="plan"),
    path("fechas/<int:date_id>/orden/copiar/", rota_views.plan_copy, name="plan_copy"),
    path("fechas/<int:date_id>/orden/<int:item_id>/", rota_views.plan_item, name="plan_item"),
]
