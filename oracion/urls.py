from django.urls import path

from . import views

app_name = "oracion"

urlpatterns = [
    # Who writes.
    path("", views.mine, name="mine"),
    path("nueva/", views.new, name="new"),
    path("<int:prayer_id>/borrar/", views.delete, name="delete"),
    # Who reads: the church's pastors, nobody else.
    path("recibidas/", views.inbox, name="inbox"),
    path("recibidas/avisos/", views.reader_email, name="reader_email"),
    path("recibidas/<int:prayer_id>/", views.read, name="read"),
    path("recibidas/<int:prayer_id>/leida/", views.mark_read, name="mark_read"),
    path("recibidas/<int:prayer_id>/responder/", views.reply, name="reply"),
    path("recibidas/<int:prayer_id>/hablamos/", views.mark_talked, name="mark_talked"),
    path("politica/", views.terms, name="terms"),
]
