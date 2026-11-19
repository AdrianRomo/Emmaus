from django.urls import path

from . import views

app_name = "espacios"

urlpatterns = [
    path("", views.space_list, name="list"),
    path("<int:space_id>/", views.week, name="week"),
    path("<int:space_id>/pedir/", views.request_booking, name="request"),
    path("reservas/<int:booking_id>/cancelar/", views.cancel, name="cancel"),
    path("solicitudes/", views.queue, name="queue"),
    path("reservas/<int:booking_id>/aprobar/", views.approve, name="approve"),
    path("reservas/<int:booking_id>/rechazar/", views.reject, name="reject"),
    path("administrar/", views.manage, name="manage"),
    path("administrar/nuevo/", views.space_create, name="space_create"),
    path("administrar/<int:space_id>/editar/", views.space_edit, name="space_edit"),
    path("uso/", views.usage, name="usage"),
]
