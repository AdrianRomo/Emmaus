from django.urls import path

from . import views

app_name = "people"

urlpatterns = [
    path("", views.person_list, name="list"),
    path("nueva/", views.person_create, name="create"),
    path("desde-un-evento/", views.from_event_list, name="from_event_list"),
    path("desde-un-evento/<slug:slug>/", views.from_event, name="from_event"),
    path("desde-un-evento/<slug:slug>/<uuid:public_id>/", views.from_event_link, name="from_event_link"),
    path("<int:person_id>/", views.person_detail, name="detail"),
    path("<int:person_id>/editar/", views.person_edit, name="edit"),
    path("<int:person_id>/acceso/", views.person_grant_access, name="grant_access"),
    path("<int:person_id>/acceso/enlace/", views.person_access_link, name="access_link"),
    path("<int:person_id>/vinculos/<int:link_id>/quitar/", views.person_unlink, name="unlink"),
]
