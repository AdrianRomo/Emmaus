from django import forms

from core.forms import TuErrorMessagesMixin
from people.models import Person

from .models import FollowUp


def possible_owners(church, about: Person):
    """Who can be responsible: people of the church who can sign in, never the person it is about."""
    return (
        Person.objects.filter(church=church, merged_into__isnull=True, user__isnull=False, user__is_active=True)
        .exclude(pk=about.pk)
        .order_by("first_name", "last_name", "id")
    )


class FollowUpForm(TuErrorMessagesMixin, forms.Form):
    """Who will reach the person and by when; with ``with_reason``, also why. No field takes free text."""

    reason = forms.ChoiceField(label="¿Por qué hay que buscarla?", choices=FollowUp.Reason.choices, widget=forms.RadioSelect)
    owner = forms.ModelChoiceField(
        label="Responsable", queryset=Person.objects.none(), required=False, empty_label="Sin responsable por ahora",
        help_text="Solo quienes tienen acceso a Emmaus. Nadie más verá este seguimiento.",
    )
    due_on = forms.DateField(
        label="Para cuándo", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )

    def __init__(self, *args, church, about: Person, with_reason: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = possible_owners(church, about)
        self.fields["owner"].error_messages["invalid_choice"] = "Elige a una persona de la lista."
        if with_reason:
            self.fields["reason"].error_messages.update(
                required="Elige por qué hay que buscarla.", invalid_choice="Elige por qué hay que buscarla."
            )
        else:
            del self.fields["reason"]
