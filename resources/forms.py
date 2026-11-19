from django import forms

from core.forms import TuErrorMessagesMixin

from . import approvals, services
from .models import ContentApproval, Devotional, MediaItem, ReadingPlan, ReadingPlanDay

DATE_INPUT = {"type": "date"}


class DevotionalForm(TuErrorMessagesMixin, forms.ModelForm):
    """The devotional of one day. ``church`` is needed to say that a day already has one."""

    publish = forms.BooleanField(
        label="Pedir la aprobación pastoral para publicarlo",
        required=False,
        help_text="Un pastor de tu iglesia lo revisa; si lo aprueba, las personas lo leen desde su día. "
        "Sin marcar se queda como borrador y solo lo ve la administración.",
    )

    class Meta:
        model = Devotional
        fields = ["date", "title", "passage", "body", "author_name"]
        help_texts = {
            "date": "Un devocional por día. Si eliges un día que todavía no llega, nadie lo leerá hasta entonces.",
            "passage": "Solo la cita, por ejemplo Juan 15:1-8. Emmaus no trae el texto de la Biblia: cada quien lo lee en la suya.",
            "body": f"Solo texto, hasta {Devotional.BODY_MAX} caracteres.",
            "author_name": "El nombre que aparece junto al devocional.",
        }
        widgets = {
            "date": forms.DateInput(attrs=DATE_INPUT, format="%Y-%m-%d"),
            "body": forms.Textarea(attrs={"rows": 14}),
        }

    def __init__(self, *args, church, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        if self.instance.pk:
            approval = getattr(self.instance, "approval", None)
            self.fields["publish"].initial = self.instance.is_published or bool(approval and approval.is_pending)
            if self.instance.is_published:
                self.fields["publish"].label = "Mantenerlo publicado"
                self.fields["publish"].help_text = "Si lo desmarcas, vuelve a ser un borrador y deja de verse."
        self.fields["date"].error_messages["required"] = "Elige el día del devocional."
        self.fields["title"].error_messages["required"] = "Escribe un título para el devocional."
        self.fields["body"].error_messages["required"] = "Escribe el devocional."

    def clean_date(self):
        day = self.cleaned_data["date"]
        if Devotional.objects.filter(church=self.church, date=day).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(services.DAY_TAKEN)
        return day

    def clean_body(self):
        body = self.cleaned_data["body"].replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(body) > Devotional.BODY_MAX:
            raise forms.ValidationError(
                f"El devocional puede tener hasta {Devotional.BODY_MAX} caracteres y este tiene {len(body)}. Acórtalo."
            )
        return body


class MediaItemForm(TuErrorMessagesMixin, forms.ModelForm):
    """A resource: what it is and where it lives. The address is the only thing Emmaus keeps of it."""

    url = forms.URLField(
        label="Enlace",
        max_length=500,
        assume_scheme="https",
        help_text="Pega la dirección de YouTube, Spotify o el sitio donde está. Debe empezar con https://. "
        "Emmaus solo guarda el enlace: el recurso se abre en ese sitio.",
        error_messages={"required": "Pega el enlace del recurso.", "invalid": services.BAD_URL},
    )

    class Meta:
        model = MediaItem
        fields = ["kind", "title", "url", "date", "speaker", "series", "description"]
        help_texts = {
            "date": "El día en que se predicó, se enseñó o se publicó.",
            "speaker": "Quien predicó, enseñó o canta.",
            "series": "Si es parte de una serie, escribe su nombre igual en cada recurso.",
            "description": f"Una o dos frases, hasta {MediaItem.DESCRIPTION_MAX} caracteres.",
        }
        widgets = {
            "date": forms.DateInput(attrs=DATE_INPUT, format="%Y-%m-%d"),
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["title"].error_messages["required"] = "Escribe el título del recurso."
        self.fields["date"].error_messages["required"] = "Elige la fecha del recurso."
        self.fields["kind"].error_messages.update(required="Elige qué tipo de recurso es.", invalid_choice="Elige qué tipo de recurso es.")

    def clean_url(self):
        try:
            return services.clean_url(self.cleaned_data["url"])
        except services.ResourceError as error:
            raise forms.ValidationError(error.message) from None

    def clean_description(self):
        text = self.cleaned_data["description"].replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(text) > MediaItem.DESCRIPTION_MAX:
            raise forms.ValidationError(
                f"La descripción puede tener hasta {MediaItem.DESCRIPTION_MAX} caracteres y esta tiene {len(text)}. Acórtala."
            )
        return text


def _plain(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


class ReadingPlanForm(TuErrorMessagesMixin, forms.ModelForm):
    """What a plan is called, what it is about and who prepared it. Its days are written one by one."""

    class Meta:
        model = ReadingPlan
        fields = ["title", "description", "author_line"]
        help_texts = {
            "description": f"Una o dos frases para quien decide si lo empieza, hasta {ReadingPlan.DESCRIPTION_MAX} caracteres.",
            "author_line": "Aparece junto al plan. Por ejemplo: Preparado por el equipo pastoral.",
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["title"].error_messages["required"] = "Escribe el título del plan."

    def clean_description(self):
        text = _plain(self.cleaned_data["description"])
        if len(text) > ReadingPlan.DESCRIPTION_MAX:
            raise forms.ValidationError(
                f"La descripción puede tener hasta {ReadingPlan.DESCRIPTION_MAX} caracteres y esta tiene {len(text)}. Acórtala."
            )
        return text


class ReadingPlanDayForm(TuErrorMessagesMixin, forms.ModelForm):
    """One day of a plan: the reference and the church's own words. Never the text of the Bible."""

    class Meta:
        model = ReadingPlanDay
        fields = ["reference", "title", "reflection", "question"]
        help_texts = {
            "reference": "Escribe la cita; el texto bíblico lo lee cada quien en su Biblia. "
            "No pegues texto de una traducción con derechos reservados.",
            "reflection": f"Lo que escribió tu iglesia, con sus propias palabras. Solo texto, hasta {ReadingPlanDay.REFLECTION_MAX} caracteres.",
            "question": "Una pregunta para que cada quien la piense. Nadie la responde en Emmaus.",
        }
        widgets = {"reflection": forms.Textarea(attrs={"rows": 10})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reference"].error_messages["required"] = "Escribe la cita del día, por ejemplo Juan 15:1-8."

    def clean_reflection(self):
        text = _plain(self.cleaned_data["reflection"])
        if len(text) > ReadingPlanDay.REFLECTION_MAX:
            raise forms.ValidationError(
                f"La reflexión puede tener hasta {ReadingPlanDay.REFLECTION_MAX} caracteres y esta tiene {len(text)}. Acórtala."
            )
        return text


class ApproveForm(forms.Form):
    """What a pastor states when approving: with which permission the church publishes this. ``revision``
    is the fingerprint of the preview the pastor read."""

    revision = forms.CharField(widget=forms.HiddenInput, required=False, max_length=64)
    permitted_use = forms.ChoiceField(
        label="¿Con qué permiso lo publica la iglesia?",
        choices=ContentApproval.Use.choices,
        widget=forms.RadioSelect,
        error_messages={"required": approvals.NO_USE, "invalid_choice": approvals.NO_USE},
    )
    source_note = forms.CharField(
        label="Nota sobre la fuente",
        required=False,
        max_length=ContentApproval.NOTE_MAX,
        help_text=f"Por ejemplo, quién dio el permiso o de dónde viene el texto. Solo la leen los pastores y la administración. "
        f"Hasta {ContentApproval.NOTE_MAX} caracteres.",
        error_messages={"max_length": f"La nota puede tener hasta {ContentApproval.NOTE_MAX} caracteres. Acórtala."},
    )


class ReturnForm(forms.Form):
    """Why a pastor does not approve what the administration asked to publish."""

    reason = forms.CharField(
        label="¿Qué hay que corregir?",
        max_length=ContentApproval.REASON_MAX,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=f"Lo leen los pastores y la administración de tu iglesia, aquí en Emmaus. No se envía por correo. "
        f"Hasta {ContentApproval.REASON_MAX} caracteres.",
        error_messages={
            "required": approvals.NO_REASON,
            "max_length": f"El motivo puede tener hasta {ContentApproval.REASON_MAX} caracteres. Acórtalo.",
        },
    )
