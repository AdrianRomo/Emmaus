from django.urls import path

from . import views

app_name = "agenda"

urlpatterns = [
    path("", views.event_list, name="list"),
    path("administrar/", views.admin_list, name="admin_list"),
    path("<slug:slug>/", views.detail, name="detail"),
    path("<slug:slug>/inscribirme/", views.register, name="register"),
    path("<slug:slug>/cancelar/", views.cancel, name="cancel"),
    path("<slug:slug>/pagina/", views.page_edit, name="page_edit"),
    path("<slug:slug>/inscritos/", views.registrations, name="registrations"),
]
