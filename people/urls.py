from django.urls import path

from . import data_views, import_views, merge_views, views

app_name = "people"

urlpatterns = [
    path("", views.person_list, name="list"),
    path("nueva/", views.person_create, name="create"),
    path("desde-un-evento/", views.from_event_list, name="from_event_list"),
    path("desde-un-evento/<slug:slug>/", views.from_event, name="from_event"),
    path("desde-un-evento/<slug:slug>/<uuid:public_id>/", views.from_event_link, name="from_event_link"),
    path("duplicados/", merge_views.duplicate_list, name="duplicates"),
    path("duplicados/descartar/", merge_views.duplicate_dismiss, name="duplicate_dismiss"),
    path("duplicados/descartados/<int:dismissal_id>/revisar/", merge_views.duplicate_reopen, name="duplicate_reopen"),
    path("duplicados/<int:survivor_id>/<int:absorbed_id>/", merge_views.merge_preview, name="merge_preview"),
    path("uniones/<int:merge_id>/", merge_views.merge_detail, name="merge_detail"),
    path("uniones/<int:merge_id>/deshacer/", merge_views.merge_undo, name="merge_undo"),
    path("exportar/", data_views.people_export, name="export"),
    path("importar/", import_views.people_import, name="import"),
    path("importar/ejemplo.csv", import_views.people_import_sample, name="import_sample"),
    path("importaciones/<int:import_id>/", import_views.people_import_detail, name="import_detail"),
    path("importaciones/<int:import_id>/deshacer/", import_views.people_import_undo, name="import_undo"),
    path("etiquetas/", data_views.tag_list, name="tags"),
    path("etiquetas/<int:tag_id>/borrar/", data_views.tag_delete, name="tag_delete"),
    path("<int:person_id>/", views.person_detail, name="detail"),
    path("<int:person_id>/etiquetas/", data_views.person_tags, name="person_tags"),
    path("<int:person_id>/editar/", views.person_edit, name="edit"),
    path("<int:person_id>/etapa/", views.person_stage, name="stage"),
    path("<int:person_id>/acceso/", views.person_grant_access, name="grant_access"),
    path("<int:person_id>/acceso/enlace/", views.person_access_link, name="access_link"),
    path("<int:person_id>/vinculos/<int:link_id>/quitar/", views.person_unlink, name="unlink"),
]
