from django import forms

from core.forms import TuErrorMessagesMixin

from .models import Person


class PersonForm(TuErrorMessagesMixin, forms.ModelForm):
    """A person of the church, as the administration registers them: the name, and how to reach them."""

    class Meta:
        model = Person
        fields = ["first_name", "last_name", "email", "phone", "preferred_contact_method", "birth_date"]
        labels = {"preferred_contact_method": "¿Cómo prefiere que la contacten?"}
        help_texts = {
            "email": "Con él entrará a Emmaus cuando le des acceso.",
            "birth_date": (
                "Por ejemplo: 14/03/2009. Sirve para saber si es menor de edad. Escribirla o cambiarla aquí no la confirma: "
                "la edad se confirma desde los inscritos de un evento, y si cambias la fecha hay que confirmarla otra vez."
            ),
        }
        widgets = {"birth_date": forms.DateInput(format="%d/%m/%Y", attrs={"inputmode": "numeric", "autocomplete": "off"})}

    def __init__(self, *args, church, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.fields["first_name"].error_messages["required"] = "Escribe el nombre."
        self.fields["birth_date"].input_formats = ["%d/%m/%Y", "%Y-%m-%d"]
        self.fields["birth_date"].error_messages["invalid"] = "Escribe la fecha así: día/mes/año, por ejemplo 14/03/2009."
        self.fields["phone"].widget.attrs.update({"inputmode": "tel", "autocomplete": "off"})
        self.fields["email"].widget.attrs["autocomplete"] = "off"

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if email:
            taken = Person.objects.filter(church=self.church, email__iexact=email, merged_into__isnull=True)
            if self.instance.pk:
                taken = taken.exclude(pk=self.instance.pk)
            if taken.exists():
                raise forms.ValidationError("Otra persona de la iglesia ya tiene este correo. Revisa si es la misma persona.")
        return email
