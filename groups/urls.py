from django.urls import path

from . import views

app_name = "community"

urlpatterns = [
    path("", views.home, name="home"),
    path("grupos/", views.groups, name="groups"),
    path("grupos/administrar/", views.group_admin_list, name="group_admin_list"),
    path("grupos/nuevo/", views.group_create, name="group_create"),
    path("grupos/<int:group_id>/", views.group_detail, name="group_detail"),
    path("grupos/<int:group_id>/pedir-unirme/", views.group_request, name="group_request"),
    path("grupos/<int:group_id>/retirar-solicitud/", views.group_withdraw, name="group_withdraw"),
    path("grupos/<int:group_id>/salir/", views.group_leave, name="group_leave"),
    path("grupos/<int:group_id>/personas/", views.group_people, name="group_people"),
    path("grupos/<int:group_id>/personas/agregar/", views.member_add, name="member_add"),
    path("grupos/<int:group_id>/personas/<int:membership_id>/quitar/", views.member_remove, name="member_remove"),
    path("grupos/<int:group_id>/solicitudes/<int:request_id>/", views.request_decide, name="request_decide"),
    path("grupos/<int:group_id>/editar/", views.group_edit, name="group_edit"),
    path("grupos/<int:group_id>/archivar/", views.group_archive, name="group_archive"),
]
