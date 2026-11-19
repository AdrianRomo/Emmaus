"""The form a person fills, built from the questions of one published version.

``AnswerForm`` validates what was sent against ``FormVersion.schema`` and leaves two plain values
for ``services.submit``: ``answers`` ({key: text | list of texts}) and ``contact``. A question that
hangs from another one counts only when that one was answered with the stated value: otherwise
whatever was sent for it is dropped, and it is never demanded. Nothing is computed from an answer.
"""

from django import forms

from core.forms import use_tu_error_messages

from . import services
from .models import LONG_MAX, SHORT_MAX, Question

Kind = Question.Kind
REQUIRED = "Responde esta pregunta."
HONEYPOT = "sitio_web"  # a box people never see; whatever fills it is not a person (formularios.public)


def _choices(options):
    return [(option, option) for option in options]


class AnswerForm(forms.Form):
    def __init__(self, *args, schema, **kwargs):
        self.schema = schema
        super().__init__(*args, **kwargs)
        labels = {item["key"]: item["label"] for item in schema}
        self.items = []  # what the template walks: one per question, with its bound field(s)
        for item in schema:
            key, kind, condition = item["key"], item["kind"], item.get("show_if")
            # A question that depends on another is never required by the browser: the server decides.
            required = bool(item.get("required")) and not condition
            hint = ""
            if condition:
                hint = f"Responde solo si en «{labels.get(condition['key'], '')}» elegiste «{condition['value']}»."
            common = {"label": item["label"], "required": required, "help_text": hint, "error_messages": {"required": REQUIRED}}
            if kind == Kind.SHORT:
                field = forms.CharField(max_length=SHORT_MAX, **common)
            elif kind == Kind.LONG:
                field = forms.CharField(max_length=LONG_MAX, widget=forms.Textarea(attrs={"rows": 5}), **common)
                field.help_text = (hint + " " if hint else "") + f"Hasta {LONG_MAX} caracteres."
            elif kind == Kind.CHOICE:
                field = forms.ChoiceField(choices=_choices(item["options"]), widget=forms.RadioSelect, **common)
            elif kind == Kind.MULTI:
                field = forms.MultipleChoiceField(choices=_choices(item["options"]), widget=forms.CheckboxSelectMultiple, **common)
            elif kind == Kind.YES_NO:
                field = forms.ChoiceField(choices=_choices(services.YES_NO), widget=forms.RadioSelect, **common)
            elif kind == Kind.DATE:
                field = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"), input_formats=["%Y-%m-%d", "%d/%m/%Y"], **common)
                field.error_messages["invalid"] = "Escribe la fecha así: día/mes/año, por ejemplo 14/03/2026."
            elif kind == Kind.CONTACT:
                self._add_contact(item)
                continue
            else:  # a kind a later release added and this code does not know: never guess
                continue
            self.fields[key] = field
            self.items.append({"kind": kind, "key": key})
        for field in self.fields.values():
            invalid = field.error_messages.get("invalid") if isinstance(field, forms.DateField) else None
            use_tu_error_messages(field)
            if invalid:
                field.error_messages["invalid"] = invalid

    def _add_contact(self, item):
        key, required = item["key"], bool(item.get("required"))
        self.contact_item = item
        self.fields[f"{key}_nombre"] = forms.CharField(
            label="Nombre", max_length=120, required=required, widget=forms.TextInput(attrs={"autocomplete": "given-name"}),
            error_messages={"required": "Escribe tu nombre."},
        )
        self.fields[f"{key}_apellidos"] = forms.CharField(
            label="Apellidos", max_length=120, required=False, widget=forms.TextInput(attrs={"autocomplete": "family-name"}),
        )
        names = [f"{key}_nombre", f"{key}_apellidos"]
        if item.get("ask_email"):
            self.fields[f"{key}_correo"] = forms.EmailField(
                label="Correo electrónico", max_length=254, required=False, widget=forms.EmailInput(attrs={"autocomplete": "email"}),
            )
            names.append(f"{key}_correo")
        if item.get("ask_phone"):
            self.fields[f"{key}_telefono"] = forms.CharField(
                label="Teléfono", max_length=30, required=False, widget=forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "tel"}),
            )
            names.append(f"{key}_telefono")
        self.fields[f"{key}_acepto"] = forms.BooleanField(label=item.get("consent", ""), required=False)
        names.append(f"{key}_acepto")
        self.items.append({"kind": Kind.CONTACT, "key": key, "names": names, "label": item["label"], "required": required})

    def bound_items(self):
        """For the template: each question with its ``BoundField`` (or its list, for the contact block)."""
        for item in self.items:
            if item["kind"] == Kind.CONTACT:
                yield {**item, "fields": [self[name] for name in item["names"]]}
            else:
                yield {**item, "field": self[item["key"]]}

    def clean(self):
        data = super().clean()
        answers = {}
        for item in self.schema:
            key, kind, condition = item["key"], item["kind"], item.get("show_if")
            if kind == Kind.CONTACT or key not in self.fields:
                continue
            if condition and answers.get(condition["key"]) != condition["value"]:
                self._errors.pop(key, None)  # not shown: whatever was sent for it does not count
                data.pop(key, None)
                continue
            if key in self._errors:
                continue
            value = data.get(key)
            if value in (None, "", []):
                if item.get("required"):
                    self.add_error(key, REQUIRED)
                continue
            if kind == Kind.SHORT:
                value = " ".join(value.split())
            elif kind == Kind.LONG:
                value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
            elif kind == Kind.DATE:
                value = value.isoformat()
            elif kind == Kind.MULTI:
                value = [option for option in item["options"] if option in value]
            answers[key] = value
        self.answers = answers
        self.contact = self._clean_contact(data)
        return data

    def _clean_contact(self, data) -> dict:
        item = getattr(self, "contact_item", None)
        if item is None:
            return {}
        key = item["key"]
        first = " ".join((data.get(f"{key}_nombre") or "").split())
        last = " ".join((data.get(f"{key}_apellidos") or "").split())
        email = (data.get(f"{key}_correo") or "").strip().lower()
        phone = data.get(f"{key}_telefono") or ""
        try:
            phone = services.clean_phone(phone)
        except services.FormError as error:
            self.add_error(f"{key}_telefono", error.message)
            phone = ""
        given = bool(first or last or email or phone)
        if not given and not item.get("required"):
            return {}
        if not first and f"{key}_nombre" not in self._errors:
            self.add_error(f"{key}_nombre", "Escribe tu nombre.")
        if not email and not phone and not any(f"{key}_{name}" in self._errors for name in ("correo", "telefono")):
            both = item.get("ask_email") and item.get("ask_phone")
            target = f"{key}_correo" if item.get("ask_email") else f"{key}_telefono"
            self.add_error(target, "Escribe tu correo o tu teléfono." if both else "Escribe tu correo." if item.get("ask_email") else "Escribe tu teléfono.")
        if not data.get(f"{key}_acepto"):
            self.add_error(f"{key}_acepto", "Marca la casilla para enviar tus datos de contacto." if item.get("required")
                           else "Marca la casilla para enviar tus datos de contacto, o bórralos.")
        return {"first_name": first, "last_name": last, "email": email, "phone": phone, "consent": item.get("consent", "")}
