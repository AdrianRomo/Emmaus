from django import forms

from core.forms import TuErrorMessagesMixin

from .models import EventFeedback


class FeedbackForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = EventFeedback
        fields = ["rating", "recommend", "comment"]
        widgets = {
            "rating": forms.RadioSelect,
            "recommend": forms.RadioSelect,
            "comment": forms.Textarea(attrs={"rows": 3, "maxlength": 500}),
        }
        help_texts = {"comment": "Opcional. No escribas tu nombre ni datos de otras personas."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rating"].choices = EventFeedback.Rating.choices
        self.fields["recommend"].choices = EventFeedback.Recommend.choices
        self.fields["rating"].error_messages["required"] = "Elige una opción."
        self.fields["recommend"].error_messages["required"] = "Elige una opción."
