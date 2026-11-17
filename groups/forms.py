from django import forms

from core.forms import TuErrorMessagesMixin
from events.models import Sede

from .models import Group


class GroupForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = Group
        fields = ["name", "kind", "description", "audience", "sede", "meeting_day", "meeting_time", "meeting_place", "accepts_requests"]
        help_texts = {
            "description": "Dos o tres frases. Es lo que lee alguien que busca un grupo.",
            "audience": "Por ejemplo: jóvenes de 15 a 18 años.",
            "meeting_time": "En formato de 24 horas, por ejemplo 19:30.",
            "meeting_place": "Solo lo ven quienes ya son parte del grupo.",
            "accepts_requests": "Si lo apagas, el grupo sigue en la lista pero nadie puede pedir unirse.",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3, "maxlength": 600}),
            "meeting_time": forms.TimeInput(format="%H:%M", attrs={"inputmode": "numeric", "placeholder": "19:30"}),
        }

    def __init__(self, *args, church, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].error_messages["required"] = "Escribe el nombre del grupo."
        self.fields["description"].max_length = 600
        self.fields["meeting_day"].choices = [("", "Todavía no se sabe")] + list(Group.Weekday.choices)
        self.fields["meeting_time"].error_messages["invalid"] = "Escribe la hora así: 19:30."
        sedes = Sede.objects.filter(church=church, active=True)
        if sedes.exists():
            self.fields["sede"].queryset = sedes
            self.fields["sede"].empty_label = "Toda la iglesia"
        else:
            del self.fields["sede"]
