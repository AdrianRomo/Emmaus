from django import forms
from django.utils import timezone

from core.forms import TuErrorMessagesMixin

from .models import Person
from .text import has_control, normalized_fits


# The key dates of a person. Which of them a form carries is the form's own decision.
KEY_DATES = ("baptized_on", "arrived_on", "member_since")
SELF_KEY_DATES = ("baptized_on", "arrived_on")  # the ones that are the person's own to say ("Mis datos")
OLDEST_YEAR = 1900


def key_date_widgets(names) -> dict:
    return {name: forms.DateInput(format="%d/%m/%Y", attrs={"inputmode": "numeric", "autocomplete": "off"}) for name in names}


class KeyDatesMixin:
    """Day/month/year dates that already happened. Shared by the administration's form and "Mis datos"."""

    def setup_key_dates(self) -> None:
        for name in KEY_DATES:
            if name in self.fields:
                self.fields[name].input_formats = ["%d/%m/%Y", "%Y-%m-%d"]
                self.fields[name].error_messages["invalid"] = "Escribe la fecha así: día/mes/año, por ejemplo 14/03/2009."

    def clean(self):
        data = super().clean()
        today = timezone.localdate()
        for name in KEY_DATES:
            value = data.get(name)
            if value is not None and (value > today or value.year < OLDEST_YEAR):
                self.add_error(name, "Revisa la fecha: tiene que ser un día que ya pasó.")
        return data


class PersonForm(KeyDatesMixin, TuErrorMessagesMixin, forms.ModelForm):
    """A person of the church, as the administration registers them: the name, and how to reach them."""

    class Meta:
        model = Person
        fields = ["first_name", "last_name", "email", "phone", "preferred_contact_method", "birth_date", "stage", *KEY_DATES]
        labels = {"preferred_contact_method": "¿Cómo prefiere que se le contacte?"}
        help_texts = {
            "baptized_on": "Por ejemplo: 14/03/2009. Si solo sabes el año, déjala vacía.",
            "member_since": "Solo la administración la escribe: la persona no la ve ni la cambia.",
            "stage": "La anota la administración y solo la ve la administración. Emmaus nunca la cambia por su cuenta.",
            "email": "Con él entrará a Emmaus cuando le des acceso.",
            "birth_date": (
                "Por ejemplo: 14/03/2009. Sirve para saber si es menor de edad. Escribirla o cambiarla aquí no la confirma: "
                "la edad se confirma desde los inscritos de un evento, y si cambias la fecha hay que confirmarla otra vez."
            ),
        }
        widgets = key_date_widgets(("birth_date", *KEY_DATES))

    def __init__(self, *args, church, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        if self.instance.pk:
            # The stage of a person who already exists has its own form on their page (``services.set_stage``):
            # a copy of the person read when this form was opened must never write an old stage back.
            del self.fields["stage"]
        else:
            self.fields["stage"].choices = [("", "Sin anotar")] + list(Person.Stage.choices)
        self.setup_key_dates()
        self.fields["first_name"].error_messages["required"] = "Escribe el nombre."
        self.fields["birth_date"].input_formats = ["%d/%m/%Y", "%Y-%m-%d"]
        self.fields["birth_date"].error_messages["invalid"] = "Escribe la fecha así: día/mes/año, por ejemplo 14/03/2009."
        self.fields["phone"].widget.attrs.update({"inputmode": "tel", "autocomplete": "off"})
        self.fields["email"].widget.attrs["autocomplete"] = "off"

    def clean(self):
        data = super().clean()
        full = f"{data.get('first_name') or ''} {data.get('last_name') or ''}"
        if has_control(full):
            self.add_error("first_name", "El nombre no puede llevar caracteres que no se ven. Escríbelo de nuevo a mano.")
        elif not normalized_fits(full, Person._meta.get_field("normalized_name").max_length):
            # ``normalized_name`` is what the column holds, and compatibility letters grow when normalised.
            self.add_error("first_name", "El nombre tiene letras especiales que ocupan más de lo que cabe: escríbelo con letras comunes.")
        return data

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if email:
            taken = Person.objects.filter(church=self.church, email__iexact=email, merged_into__isnull=True)
            if self.instance.pk:
                taken = taken.exclude(pk=self.instance.pk)
            if taken.exists():
                raise forms.ValidationError("Otra persona de la iglesia ya tiene este correo. Revisa si es la misma persona.")
        return email
