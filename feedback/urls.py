from django.urls import path

from . import views

app_name = "feedback"

urlpatterns = [
    path("que-me-busquen/", views.contact, name="contact"),
    path("<slug:slug>/", views.give, name="give"),
    path("<slug:slug>/resultados/", views.results, name="results"),
]
