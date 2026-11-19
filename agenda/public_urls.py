"""``/evento/<slug>/``: the one address of the agenda that needs no account (``agenda.public``)."""

from django.urls import path

from . import calendar, public

app_name = "evento"

urlpatterns = [
    path("<slug:slug>/", public.event, name="detail"),
    path("<slug:slug>/calendario.ics", calendar.event, name="calendar"),
]
