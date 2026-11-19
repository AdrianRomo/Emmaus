"""``/iglesia/<slug>/calendario.ics``: the church's public calendar (``agenda.calendar``), beside its public home."""

from django.urls import path

from . import calendar

app_name = "calendario"

urlpatterns = [
    path("<slug:slug>/calendario.ics", calendar.feed, name="feed"),
]
