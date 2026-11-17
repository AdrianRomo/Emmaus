from django.urls import path

from . import views

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
    path("", views.media_list, name="media_list"),
    path("administrar/", views.media_admin, name="media_admin"),
    path("nuevo/", views.media_create, name="media_create"),
    path("<int:item_id>/editar/", views.media_edit, name="media_edit"),
    path("<int:item_id>/retirar/", views.media_retire, name="media_retire"),
]
