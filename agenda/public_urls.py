"""``/evento/<slug>/``: the one address of the agenda that needs no account (``agenda.public``)."""

from django.urls import path

from . import public

app_name = "evento"

urlpatterns = [
    path("<slug:slug>/", public.event, name="detail"),
]
