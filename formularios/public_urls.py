"""``/formulario/<token>/``: the one address of the forms that needs no account (``formularios.public``)."""

from django.urls import path

from . import public

app_name = "formulario"

urlpatterns = [
    path("<str:token>/", public.fill, name="fill"),
    path("<str:token>/gracias/", public.thanks, name="thanks"),
]
