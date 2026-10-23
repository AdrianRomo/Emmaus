from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import RedirectView

from core.forms import EmailAuthenticationForm
from core.views import ThrottledLoginView, ThrottledPasswordResetView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("cuenta/entrar/", ThrottledLoginView.as_view(authentication_form=EmailAuthenticationForm), name="login"),
    path("cuenta/olvide-mi-contrasena/", ThrottledPasswordResetView.as_view(), name="password_reset"),
    path(
        "cuenta/olvide-mi-contrasena/enviado/",
        auth_views.PasswordResetDoneView.as_view(template_name="registration/password_reset_done.html"),
        name="password_reset_done",
    ),
    path("cuenta/salir/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "cuenta/contrasena/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(template_name="registration/password_set.html", success_url="/cuenta/contrasena/lista/"),
        name="password_reset_confirm",
    ),
    path(
        "cuenta/contrasena/lista/",
        auth_views.PasswordResetCompleteView.as_view(template_name="registration/password_set_done.html"),
        name="password_reset_complete",
    ),
    path("accounts/login/", RedirectView.as_view(pattern_name="login", query_string=True)),
    path("eventos/", include("events.urls")),
    path("estacion/", include("events.kiosk_urls")),
    path("", include("core.urls")),
]

handler403 = "core.views.permission_denied"
handler404 = "core.views.page_not_found"
