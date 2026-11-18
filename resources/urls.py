from django.urls import path

from . import plan_views, views

app_name = "resources"

# The fixed words go before the numbers.
urlpatterns = [
    path("devocional/", views.devotional_today, name="devotional_today"),
    path("devocional/anteriores/", views.devotional_list, name="devotional_list"),
    path("devocional/administrar/", views.devotional_admin, name="devotional_admin"),
    path("devocional/nuevo/", views.devotional_create, name="devotional_create"),
    path("devocional/<int:devotional_id>/", views.devotional_detail, name="devotional_detail"),
    path("devocional/<int:devotional_id>/leido/", views.devotional_read, name="devotional_read"),
    path("devocional/<int:devotional_id>/editar/", views.devotional_edit, name="devotional_edit"),
    path("planes/", plan_views.plan_list, name="plan_list"),
    path("planes/administrar/", plan_views.plan_admin, name="plan_admin"),
    path("planes/nuevo/", plan_views.plan_create, name="plan_create"),
    path("planes/<int:plan_id>/", plan_views.plan_detail, name="plan_detail"),
    path("planes/<int:plan_id>/empezar/", plan_views.plan_start, name="plan_start"),
    path("planes/<int:plan_id>/dejar/", plan_views.plan_leave, name="plan_leave"),
    path("planes/<int:plan_id>/dia/<int:position>/", plan_views.plan_day, name="plan_day"),
    path("planes/<int:plan_id>/dia/<int:position>/leido/", plan_views.plan_day_read, name="plan_day_read"),
    path("planes/<int:plan_id>/editar/", plan_views.plan_edit, name="plan_edit"),
    path("planes/<int:plan_id>/dias/", plan_views.plan_days, name="plan_days"),
    path("planes/<int:plan_id>/dias/nuevo/", plan_views.plan_day_create, name="plan_day_create"),
    path("planes/<int:plan_id>/dias/<int:day_id>/editar/", plan_views.plan_day_edit, name="plan_day_edit"),
    path("planes/<int:plan_id>/dias/<int:day_id>/quitar/", plan_views.plan_day_delete, name="plan_day_delete"),
    path("planes/<int:plan_id>/publicar/", plan_views.plan_publish, name="plan_publish"),
    path("planes/<int:plan_id>/retirar/", plan_views.plan_retire, name="plan_retire"),
    path("", views.media_list, name="media_list"),
    path("administrar/", views.media_admin, name="media_admin"),
    path("nuevo/", views.media_create, name="media_create"),
    path("<int:item_id>/editar/", views.media_edit, name="media_edit"),
    path("<int:item_id>/retirar/", views.media_retire, name="media_retire"),
]
