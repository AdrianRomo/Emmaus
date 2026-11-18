from django.urls import path

from . import views

# One address and no namespace: the form, its review page and the save are steps of the same page.
urlpatterns = [
    path("", views.church_policies, name="church_policies"),
]
