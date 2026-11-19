from django.urls import path

from . import views

app_name = "cuenta_datos"

urlpatterns = [
    path("mis-datos/", views.home, name="home"),
    path("mis-datos/descargar/", views.download, name="download"),
    path("mis-datos/pedir-borrado/", views.ask_removal, name="ask_removal"),
    path("mis-datos/pedir-borrado/<int:request_id>/retirar/", views.withdraw, name="withdraw"),
    path("solicitudes/", views.request_list, name="requests"),
    path("solicitudes/<int:request_id>/", views.request_detail, name="request"),
    path("solicitudes/<int:request_id>/atendida/", views.request_done, name="request_done"),
    path("quien-puede-que/", views.powers, name="powers"),
]
