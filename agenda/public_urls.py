"""``/evento/<slug>/``: the one address of the agenda that needs no account (``agenda.public``)."""

from django.urls import path

from . import calendar, public, subscription_views, visitor_views

app_name = "evento"

urlpatterns = [
    # The links of the subscription e-mails carry a signed token and no slug: they must keep working (to
    # unsubscribe) whatever becomes of the event's address. Three segments: no event's own address is shadowed.
    path("avisos-por-correo/confirmar/<str:token>/", subscription_views.confirm, name="subscription_confirm"),
    path("avisos-por-correo/baja/<str:token>/", subscription_views.unsubscribe, name="subscription_unsubscribe"),
    path("<slug:slug>/avisos-por-correo/", subscription_views.subscribe, name="subscribe"),
    # Signing up without an account (agenda.visitors, issue #93): the same shape. "inscripcion" (three segments)
    # shadows no event's own address; the two links carry a signed token and no slug.
    path("inscripcion/confirmar/<str:token>/", visitor_views.confirm, name="visitor_confirm"),
    path("inscripcion/mia/<str:token>/", visitor_views.manage, name="visitor_manage"),
    path("<slug:slug>/inscribirme/", visitor_views.sign_up, name="visitor_signup"),
    path("<slug:slug>/", public.event, name="detail"),
    path("<slug:slug>/calendario.ics", calendar.event, name="calendar"),
]
