from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.templatetags.static import static
from django.urls import include, path
from django.views.generic import RedirectView

from core.forms import EmailAuthenticationForm, TuSetPasswordForm
from core.views import AccountPasswordChangeView, ThrottledLoginView, ThrottledPasswordResetView, account, switch_church, welcome_dismiss, welcome_restore
from events.views import join as join_views



class StaticIcon(RedirectView):
    """Browsers and phones ask for these at the root whatever the page links (issue #53)."""

    icon = ""

    def get_redirect_url(self, *args, **kwargs):
        return static(self.icon)


urlpatterns = [
    path("favicon.ico", StaticIcon.as_view(icon="img/favicon.ico")),
    path("apple-touch-icon.png", StaticIcon.as_view(icon="img/apple-touch-icon.png")),
    path("apple-touch-icon-precomposed.png", StaticIcon.as_view(icon="img/apple-touch-icon.png")),
    path("admin/", admin.site.urls),
    # "Mi cuenta": the name, the e-mail, where the person takes part, the password, and the welcomes they put away
    path("cuenta/", account, name="account"),
    path("cuenta/iglesia/", switch_church, name="switch_church"),
    path("cuenta/contrasena/cambiar/", AccountPasswordChangeView.as_view(), name="password_change"),
    path("cuenta/bienvenida/ocultar/", welcome_dismiss, name="welcome_dismiss"),
    path("cuenta/bienvenida/mostrar/", welcome_restore, name="welcome_restore"),
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
        auth_views.PasswordResetConfirmView.as_view(
            template_name="registration/password_set.html", success_url="/cuenta/contrasena/lista/", form_class=TuSetPasswordForm
        ),
        name="password_reset_confirm",
    ),
    path(
        "cuenta/contrasena/lista/",
        auth_views.PasswordResetCompleteView.as_view(template_name="registration/password_set_done.html"),
        name="password_reset_complete",
    ),
    path("accounts/login/", RedirectView.as_view(pattern_name="login", query_string=True)),
    # The community modules (decision 0004): each view answers 404 while its church has the module off.
    path("mi-iglesia/personas/", include("people.urls")),
    path("mi-iglesia/servir/", include("serving.urls")),
    path("mi-iglesia/acompanar/", include("care.urls")),
    path("mi-iglesia/recursos/", include("resources.urls")),
    path("mi-iglesia/avisos/", include("notices.urls")),
    path("mi-iglesia/eventos/", include("agenda.urls")),
    path("mi-iglesia/opiniones/", include("feedback.urls")),
    path("mi-iglesia/panorama/", include("overview.urls")),
    path("mi-iglesia/politicas/", include("policies.urls")),
    path("mi-iglesia/pagina-publica/", include("sitio.urls")),
    # Rooms and equipment (issue #94): signed-in people of the church only, never a public page.
    path("mi-iglesia/espacios/", include("espacios.urls")),
    # General forms and «Pendientes» (issue #92): signed-in people of the church.
    path("mi-iglesia/formularios/", include("formularios.urls")),
    # Private prayer requests (issue #97): off until a pastor of the church switches them on; only its pastors read.
    path("mi-iglesia/oracion/", include("oracion.urls")),
    path("mi-iglesia/", include("groups.urls")),
    # The public page of one event of the church (decision 0010, phase A): no account, nothing about people.
    path("evento/", include("agenda.public_urls")),
    # The church's public home (issue #87, a prototype): only what the administration published on purpose.
    # core.urls keeps /iglesia/<slug>/logotipo/<variant>/, which this include does not match.
    # The church's public calendar file (issue #93): /iglesia/<slug>/calendario.ics, only the public events.
    path("iglesia/", include("agenda.calendar_urls")),
    path("iglesia/", include("sitio.public_urls")),
    # A public form of the church (issue #92): no account, the address is a random token.
    path("formulario/", include("formularios.public_urls")),
    path("eventos/", include("events.urls")),
    path("estacion/", include("events.kiosk_urls")),
    # The printed sign: "Entra a /unirme/ y escribe el código". Public, outside /eventos/.
    path("unirme/", join_views.join, name="join"),
    path("e/<str:code>/", join_views.event_entry, name="event_entry"),
    path("", include("core.urls")),
]

handler400 = "core.views.bad_request"
handler403 = "core.views.permission_denied"
handler404 = "core.views.page_not_found"
handler500 = "core.views.server_error"
