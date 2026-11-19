"""The staff's forms: what a form is, one question of its draft, who does a task. The rules live in
``formularios.services``; these classes collect the values and carry the words."""

from django import forms

from core.forms import TuErrorMessagesMixin

from . import services
from .models import MAX_OPTIONS, OPTION_MAX, Form, FormTask, Question

Kind = Question.Kind
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y"]


def _date(label, help_text=""):
    return forms.DateField(
        label=label, help_text=help_text, input_formats=DATE_FORMATS,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        error_messages={"required": "Elige el día.", "invalid": "Escribe la fecha así: día/mes/año, por ejemplo 14/03/2026."},
    )


class FormSettingsForm(TuErrorMessagesMixin, forms.Form):
    title = forms.CharField(
        label="Título", max_length=Form.TITLE_MAX, help_text=f"Lo lee quien responde. Hasta {Form.TITLE_MAX} caracteres, sin enlaces.",
        error_messages={"required": "Escribe el título."},
    )
    purpose = forms.CharField(
        label="Para qué lo pedimos", max_length=Form.PURPOSE_MAX,
        help_text="Una frase honesta que la persona lee antes de responder: para qué usará la iglesia lo que escriba. "
                  f"Se guarda con cada respuesta tal como se mostró. Hasta {Form.PURPOSE_MAX} caracteres.",
        error_messages={"required": "Escribe para qué lo pides."},
    )
    audience = forms.ChoiceField(
        label="Quién responde", choices=Form.Audience.choices, widget=forms.RadioSelect, initial=Form.Audience.MEMBERS,
        help_text="Con «cualquiera que tenga el enlace» no hace falta cuenta: quien lo reciba puede responder y reenviarlo.",
        error_messages={"required": "Elige quién responde."},
    )
    opens_on = _date("Abre el")
    closes_on = _date("Cierra el", "El último día en que se puede responder. Hasta un año después de abrir.")
    retention_days = forms.TypedChoiceField(
        label="Cuánto se guardan las respuestas después de que cierre", choices=Form.RETENTION_CHOICES, coerce=int, initial=90,
        widget=forms.RadioSelect,
        help_text="Pasado ese tiempo Emmaus borra las respuestas de este formulario, y sus pendientes con ellas.",
        error_messages={"required": "Elige cuánto se guardan las respuestas."},
    )
    creates_task = forms.BooleanField(
        label="Cada respuesta deja un pendiente «Dar seguimiento»", required=False,
        help_text="Para que alguien del equipo haga algo con cada respuesta. Emmaus no le envía nada a nadie: el pendiente aparece en «Pendientes».",
    )
    task_due_days = forms.IntegerField(
        label="Días para dar seguimiento", min_value=1, max_value=services.MAX_TASK_DAYS, initial=7,
        help_text="Cuántos días después de la respuesta vence su pendiente.",
        error_messages={"required": "Escribe cuántos días."},
    )

    def __init__(self, *args, form: Form | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.form = form
        if form is not None:
            # Who a task goes to is chosen among who reads this form's answers, so it needs the form to exist.
            people = services.owner_choices(form)
            self.owners = {str(person.user_id): person.user for person in people}
            self.fields["task_owner"] = forms.ChoiceField(
                label="Quién da seguimiento", required=False,
                choices=[("", "Nadie todavía: la administración lo asigna"), *((str(p.user_id), p.full_name) for p in people)],
                help_text="Solo aparece quien lee las respuestas de este formulario y tiene cuenta.",
            )
            self.order_fields(["title", "purpose", "audience", "opens_on", "closes_on", "retention_days", "creates_task", "task_owner", "task_due_days"])

    def values(self) -> dict:
        data = dict(self.cleaned_data)
        owner = data.pop("task_owner", "") if self.form is not None else ""
        data["task_owner"] = self.owners.get(owner) if owner else None
        return data


class QuestionForm(TuErrorMessagesMixin, forms.Form):
    label = forms.CharField(
        label="Pregunta", max_length=Question.LABEL_MAX, help_text=f"Hasta {Question.LABEL_MAX} caracteres, sin enlaces.",
        error_messages={"required": "Escribe la pregunta."},
    )
    required = forms.BooleanField(label="Es obligatoria", required=False)

    def __init__(self, *args, form: Form, kind: str, question: Question | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind = kind
        if kind in (Kind.CHOICE, Kind.MULTI):
            self.fields["options"] = forms.CharField(
                label="Opciones", widget=forms.Textarea(attrs={"rows": 5}),
                help_text=f"Una por renglón, entre 2 y {MAX_OPTIONS}. Cada una hasta {OPTION_MAX} caracteres.",
                error_messages={"required": "Escribe las opciones, una por renglón."},
            )
        if kind == Kind.CONTACT:
            self.fields["label"].label = "Título del bloque"
            self.fields["label"].help_text = "Por ejemplo: «Tus datos para contactarte». Siempre se pide el nombre."
            self.fields["required"].label = "Es obligatorio dejar los datos"
            self.fields["ask_email"] = forms.BooleanField(label="Pedir el correo", required=False)
            self.fields["ask_phone"] = forms.BooleanField(label="Pedir el teléfono", required=False)
            self.fields["consent"] = forms.CharField(
                label="Frase que la persona acepta", max_length=Question.CONSENT_MAX,
                help_text="La persona marca una casilla con esta frase para enviar sus datos. Di solo lo que la iglesia hará de verdad con ellos.",
                error_messages={"required": "Escribe la frase que la persona acepta."},
            )
        else:
            parents = services.condition_parents(form, question)
            choices = [("", "Siempre se muestra")]
            for parent in parents:
                choices += [(f"{parent.pk}|{value}", f"Solo si en «{parent.label}» eligen «{value}»") for value in services.values_of(parent)]
            if len(choices) > 1:
                self.fields["show_if"] = forms.ChoiceField(
                    label="Cuándo se muestra", required=False, choices=choices,
                    help_text="Puede depender de una pregunta anterior de una opción o de sí o no. "
                              "En la pantalla la pregunta se ve siempre, con esa indicación; Emmaus solo guarda su respuesta si se cumple.",
                )
        if kind == Kind.LONG and form.is_public and form.long_text_ack_at is None:
            self.fields["acknowledge_long_text"] = forms.BooleanField(
                required=False,
                label="Sé que en una respuesta larga de un formulario público la gente puede escribir cosas delicadas, y que este formulario no debe pedirlas",
            )

    def values(self) -> dict:
        data = self.cleaned_data
        parent, _, value = (data.get("show_if") or "").partition("|")
        return {
            "label": data["label"], "required": data["required"], "options": (data.get("options") or "").splitlines(),
            "show_if_question_id": int(parent) if parent else None, "show_if_value": value,
            "ask_email": data.get("ask_email", False), "ask_phone": data.get("ask_phone", False), "consent": data.get("consent", ""),
            "acknowledge_long_text": data.get("acknowledge_long_text", False),
        }


class TaskAssignForm(TuErrorMessagesMixin, forms.Form):
    owner = forms.ChoiceField(label="Quién da seguimiento", required=False)
    due_on = forms.DateField(
        label="Para cuándo", required=False, input_formats=DATE_FORMATS, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        error_messages={"invalid": "Escribe la fecha así: día/mes/año, por ejemplo 14/03/2026."},
    )

    def __init__(self, *args, form: Form, **kwargs):
        super().__init__(*args, **kwargs)
        people = services.owner_choices(form)
        self.owners = {str(person.user_id): person.user for person in people}
        self.fields["owner"].choices = [("", "Sin responsable"), *((str(p.user_id), p.full_name) for p in people)]
        self.fields["owner"].help_text = "Solo aparece quien lee las respuestas de este formulario y tiene cuenta. Emmaus no le avisa: avísale tú."

    def owner_user(self):
        return self.owners.get(self.cleaned_data["owner"])


class TaskNoteForm(forms.Form):
    note = forms.CharField(
        label="Nota del equipo", required=False, max_length=FormTask.NOTE_MAX,
        help_text=f"Una línea para el equipo, por ejemplo «Le llamé el martes». No copies aquí lo que la persona respondió. Hasta {FormTask.NOTE_MAX} caracteres.",
    )
