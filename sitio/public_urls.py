"""``/iglesia/<slug>/``: the church's public home, the one address of ``sitio`` that needs no account."""

from django.urls import path

from . import public

app_name = "iglesia"

urlpatterns = [
    path("<slug:slug>/", public.home, name="home"),
]
