from django.urls import path

from .views import kiosk

app_name = "kiosk"

urlpatterns = [
    path("", kiosk.kiosk_home, name="home"),
    path("paso/<int:number>/", kiosk.kiosk_step, name="step"),
    path("revisar/", kiosk.kiosk_review, name="review"),
    path("terminar/", kiosk.kiosk_finish, name="finish"),
    path("desbloquear/", kiosk.kiosk_unlock, name="unlock"),
    path("latido/", kiosk.kiosk_heartbeat, name="heartbeat"),
    path("tiempo/", kiosk.kiosk_timeout, name="timeout"),
]
