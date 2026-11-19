"""``/evento/<slug>/``: the one address of the agenda that needs no account (``agenda.public``)."""

from django.urls import path

from . import calendar, public, subscription_views

app_name = "evento"

urlpatterns = [
    # The links of the subscription e-mails carry a signed token and no slug: they must keep working (to
    # unsubscribe) whatever becomes of the event's address. Three segments: no event's own address is shadowed.
    path("avisos-por-correo/confirmar/<str:token>/", subscription_views.confirm, name="subscription_confirm"),
    path("avisos-por-correo/baja/<str:token>/", subscription_views.unsubscribe, name="subscription_unsubscribe"),
    path("<slug:slug>/avisos-por-correo/", subscription_views.subscribe, name="subscribe"),
    path("<slug:slug>/", public.event, name="detail"),
    path("<slug:slug>/calendario.ics", calendar.event, name="calendar"),
]
