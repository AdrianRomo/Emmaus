from django.urls import path

from . import views

app_name = "sitio"

urlpatterns = [
    path("", views.edit, name="edit"),
    path("vista-previa/", views.preview, name="preview"),
    path("publicar/", views.publish, name="publish"),
    path("dejar-de-publicar/", views.unpublish, name="unpublish"),
    path("horarios/nuevo/", views.time_create, name="time_create"),
    path("horarios/<int:time_id>/editar/", views.time_edit, name="time_edit"),
    path("horarios/<int:time_id>/quitar/", views.time_delete, name="time_delete"),
    path("recursos/<int:item_id>/mostrar/", views.media_show, name="media_show"),
    path("recursos/<int:item_id>/quitar/", views.media_hide, name="media_hide"),
]
