from django.urls import path

from . import views

app_name = "care"

urlpatterns = [
    path("", views.mine, name="mine"),
    path("todos/", views.admin_list, name="admin_list"),
    path("nuevo/", views.create, name="create"),
    path("<int:follow_up_id>/", views.detail, name="detail"),
    path("<int:follow_up_id>/editar/", views.edit, name="edit"),
    path("<int:follow_up_id>/paso/", views.step, name="step"),
    path("<int:follow_up_id>/estado/", views.status, name="status"),
    path("<int:follow_up_id>/cancelar/", views.cancel, name="cancel"),
]
