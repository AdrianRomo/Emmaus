from django import forms

from core.forms import TuErrorMessagesMixin

from . import registry
from .models import RemovalRequest


class DownloadForm(TuErrorMessagesMixin, forms.Form):
    """The password again, and one box per kind of text the person wrote (off unless they tick it)."""

    password = forms.CharField(
        label="Tu contraseña", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    oracion = forms.BooleanField(
        label=registry.TEXT_KINDS[registry.TEXT_PRAYER], required=False,
        help_text="Sin marcar, el archivo dice cuántas escribiste y cuándo, sin el texto.",
    )
    formularios = forms.BooleanField(
        label=registry.TEXT_KINDS[registry.TEXT_FORMS], required=False,
        help_text="Sin marcar, el archivo dice qué formularios respondiste y cuándo, sin tus respuestas.",
    )

    def texts(self) -> set[str]:
        return {kind for kind in registry.TEXT_KINDS if self.cleaned_data.get(kind)}


class RemovalForm(TuErrorMessagesMixin, forms.Form):
    ask = forms.ChoiceField(label="¿Qué quieres pedir?", choices=RemovalRequest.Ask.choices, widget=forms.RadioSelect)
