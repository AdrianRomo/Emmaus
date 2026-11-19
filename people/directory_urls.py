from django.urls import path

from . import directory_views as views

app_name = "directorio"

urlpatterns = [
    # Who reads: who is in it, the administration and the pastors, while the church's switch is on.
    path("", views.directory, name="list"),
    # The person of the request, about themselves. No address here names anybody.
    path("aparecer/", views.mine, name="mine"),
    # The church's switch: its pastors, nobody else.
    path("politica/", views.policy, name="policy"),
]
