from django import forms

from core.forms import TuErrorMessagesMixin
from events.models import Sede

from people.models import Person

from .models import WEEKDAY, WEEKDAYS, Group


class GroupForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = Group
        fields = ["name", "kind", "description", "audience", "sede", "meeting_frequency", "meeting_day", "meeting_week", "meeting_anchor", "meeting_time", "meeting_place", "accepts_requests"]
        labels = {"meeting_week": "Si es una vez al mes, cuál"}
        help_texts = {
            "meeting_frequency": "Con esto Emmaus sabe qué días toca reunión y no pide la lista de una semana en que no se reunieron.",
            "meeting_day": "Si no tienen día fijo, déjalo en «Todavía no se sabe».",
            "meeting_week": "Solo si se reúnen una vez al mes. Con jueves, «El segundo del mes» es el segundo jueves de cada mes.",
            "meeting_anchor": "Solo si se reúnen cada dos semanas: así Emmaus sabe qué semanas les toca. Puede ser la reunión pasada o la que viene.",
            "description": "Dos o tres frases. Es lo que lee alguien que busca un grupo.",
            "audience": "Por ejemplo: jóvenes de 15 a 18 años.",
            "meeting_time": "En formato de 24 horas, por ejemplo 19:30.",
            "meeting_place": "Solo lo ven quienes ya son parte del grupo.",
            "accepts_requests": "Si lo apagas, el grupo sigue en la lista pero nadie puede pedir unirse.",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3, "maxlength": 600}),
            "meeting_anchor": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "meeting_time": forms.TimeInput(format="%H:%M", attrs={"inputmode": "numeric", "placeholder": "19:30"}),
        }

    def __init__(self, *args, church, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].error_messages["required"] = "Escribe el nombre del grupo."
        self.fields["description"].max_length = 600
        self.fields["meeting_day"].choices = [("", "Todavía no se sabe")] + list(Group.Weekday.choices)
        self.fields["meeting_week"].choices = [("", "No aplica")] + list(Group.Week.choices)
        self.fields["meeting_anchor"].error_messages["invalid"] = "Escribe la fecha con el calendario o así: 2026-11-12."
        self.fields["meeting_time"].error_messages["invalid"] = "Escribe la hora así: 19:30."
        sedes = Sede.objects.filter(church=church, active=True)
        if sedes.exists():
            self.fields["sede"].queryset = sedes
            self.fields["sede"].empty_label = "Toda la iglesia"
        else:
            del self.fields["sede"]

    def clean(self):
        """Each rhythm asks only for what it needs to place its meetings, and keeps nothing of another rhythm:
        every two weeks needs a date they do meet (which also gives the day when it is not chosen), once a month
        needs which of the month's weekdays, and "sin día fijo" has no day."""
        data = super().clean()
        frequency, day, anchor = data.get("meeting_frequency"), data.get("meeting_day"), data.get("meeting_anchor")
        if "meeting_anchor" in self.errors or "meeting_day" in self.errors:
            return data
        if frequency == Group.Frequency.BIWEEKLY:
            if anchor is None and day is not None:
                self.add_error("meeting_anchor", "Elige una fecha en que sí se reúnen, para saber qué semanas les toca.")
            elif anchor is not None and day is None:
                data["meeting_day"] = anchor.weekday()
            elif anchor is not None and anchor.weekday() != day:
                self.add_error(
                    "meeting_anchor",
                    f"Esa fecha cae en {WEEKDAY[anchor.weekday()]} y el grupo se reúne los {WEEKDAYS[day]}. Elige una fecha que caiga en ese día.",
                )
        else:
            data["meeting_anchor"] = None
        if frequency == Group.Frequency.MONTHLY:
            if day is not None and not data.get("meeting_week") and "meeting_week" not in self.errors:
                self.add_error("meeting_week", "Elige cuál del mes: por ejemplo, el segundo.")
        else:
            data["meeting_week"] = None
        if frequency == Group.Frequency.VARIABLE:
            data["meeting_day"] = None
        return data


class MyDataForm(TuErrorMessagesMixin, forms.ModelForm):
    """"Mis datos": what a person may correct about themselves, how the church reaches them. The
    name on record and the date of birth stay with the administration (the date decides whether a
    guardian has to confirm, decision 0008)."""

    class Meta:
        model = Person
        fields = ["phone", "email", "preferred_contact_method"]
        labels = {"preferred_contact_method": "¿Cómo prefieres que te contacten?"}
        help_texts = {
            "phone": "Lo ve quien guía tu grupo y la administración de la iglesia.",
            "email": "Aquí te escribe la iglesia. No cambia el correo con el que entras a Emmaus.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].widget.attrs.update({"inputmode": "tel", "autocomplete": "tel"})
        self.fields["email"].widget.attrs["autocomplete"] = "email"

    def clean_email(self):
        # No "somebody else has this e-mail" here: this form is open to every person of the church, and that
        # answer would tell them who is on the church's list. The administration's form keeps the check.
        return self.cleaned_data["email"].strip().lower()
