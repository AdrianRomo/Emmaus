from django.urls import path

from . import views

app_name = "notices"

urlpatterns = [
    path("", views.notice_list, name="list"),
    path("nuevo/", views.notice_create, name="create"),
    path("correo/", views.email_preferences, name="email_preferences"),
    path("<int:notice_id>/", views.notice_detail, name="detail"),
    path("<int:notice_id>/editar/", views.notice_edit, name="edit"),
    path("<int:notice_id>/retirar/", views.notice_retire, name="retire"),
    path("<int:notice_id>/enterado/", views.notice_acknowledge, name="acknowledge"),
]
