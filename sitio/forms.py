from django import forms

from . import services
from .models import PublicHome, ServiceTime


class PublicHomeForm(forms.Form):
    """What the church says about itself to anybody. Every field is optional: an empty one is not on the page."""

    welcome = forms.CharField(
        label="Bienvenida", required=False, max_length=PublicHome.WELCOME_MAX,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=f"Una o dos frases para quien todavía no conoce tu iglesia. Hasta {PublicHome.WELCOME_MAX} caracteres.",
        error_messages={"max_length": f"La bienvenida puede tener hasta {PublicHome.WELCOME_MAX} caracteres. Acórtala."},
    )
    address = forms.CharField(
        label="Dirección", required=False, max_length=PublicHome.ADDRESS_MAX,
        widget=forms.Textarea(attrs={"rows": 3, "autocomplete": "street-address"}),
        help_text="La dirección del lugar donde se reúnen, como la escribirías en una invitación.",
        error_messages={"max_length": f"La dirección puede tener hasta {PublicHome.ADDRESS_MAX} caracteres. Acórtala."},
    )
    map_url = forms.CharField(
        label="Enlace al mapa", required=False, max_length=500,
        widget=forms.URLInput(attrs={"inputmode": "url", "autocapitalize": "off", "spellcheck": "false"}),
        help_text="En tu aplicación de mapas, busca el lugar, toca «Compartir» y pega aquí el enlace. Debe empezar con https://. "
                  "Se abre en el sitio del mapa: Emmaus no muestra mapas.",
        error_messages={"max_length": "Ese enlace es demasiado largo. Usa el enlace corto que da el mapa en «Compartir»."},
    )
    contact_phone = forms.CharField(
        label="Teléfono de contacto", required=False, max_length=30,
        widget=forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "tel"}),
        help_text="El teléfono de la iglesia o de su oficina, nunca el personal de alguien: cualquiera podrá verlo y llamar.",
        error_messages={"max_length": services.BAD_PHONE},
    )

    def clean_map_url(self):
        try:
            return services.clean_map_url(self.cleaned_data["map_url"])
        except services.SiteError as error:
            raise forms.ValidationError(error.message) from None

    def clean_contact_phone(self):
        try:
            return services.clean_phone(self.cleaned_data["contact_phone"])
        except services.SiteError as error:
            raise forms.ValidationError(error.message) from None


class ServiceTimeForm(forms.Form):
    weekday = forms.TypedChoiceField(
        label="Día", choices=[("", "Elige un día"), *ServiceTime.Weekday.choices], coerce=int,
        error_messages={"required": "Elige el día de la reunión.", "invalid_choice": "Elige el día de la reunión."},
    )
    time = forms.TimeField(
        label="Hora", widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        error_messages={"required": "Escribe la hora de la reunión.", "invalid": "Escribe la hora con este formato: 11:00."},
    )
    name = forms.CharField(
        label="Qué reunión es", max_length=80, help_text="Por ejemplo: Reunión general, Jóvenes, Oración.",
        error_messages={"required": "Escribe qué reunión es, por ejemplo «Reunión general».",
                        "max_length": "El nombre puede tener hasta 80 caracteres. Acórtalo."},
    )
    place = forms.CharField(
        label="Lugar", required=False, max_length=120,
        help_text="Solo si no es en la dirección de la iglesia, o para decir en qué salón.",
        error_messages={"max_length": "El lugar puede tener hasta 120 caracteres. Acórtalo."},
    )
    position = forms.IntegerField(
        label="Orden en la lista", required=False, min_value=0, max_value=99,
        widget=forms.NumberInput(attrs={"inputmode": "numeric", "min": 0, "max": 99}),
        help_text="Déjalo vacío para ordenar por día, empezando en domingo. Un número menor va primero.",
        error_messages={"invalid": "Escribe un número entero.", "min_value": "Escribe 0 o más.", "max_value": "Escribe 99 o menos."},
    )
