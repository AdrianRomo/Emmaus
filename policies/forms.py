from django import forms

from people import policy


def _number(field: str, label: str, help_text: str, too_low: str, too_high: str) -> forms.IntegerField:
    low, high = policy.BOUNDS[field]
    return forms.IntegerField(
        label=label, help_text=help_text, min_value=low, max_value=high,
        widget=forms.NumberInput(attrs={"inputmode": "numeric", "min": low, "max": high}),
        error_messages={"required": "Escribe un número.", "invalid": "Escribe un número entero.", "min_value": too_low, "max_value": too_high},
    )


class ChurchPolicyForm(forms.Form):
    """The six choices of "Políticas de la iglesia". Each hint says what the value changes and,
    where the platform holds a minimum, which one and why. The bounds are ``people.policy.BOUNDS``."""

    min_reporting_size = _number(
        "min_reporting_size", "Mínimo de personas para mostrar un total",
        "Con menos personas que este número, Emmaus no muestra el desglose de asistencia, los resultados de las "
        "opiniones de un evento ni cuántas personas vieron un aviso. Puedes subirlo hasta 20. El mínimo es 5: con "
        "menos, un total deja ver qué hizo o qué dijo cada persona.",
        "Emmaus no permite menos de 5: con menos personas, un total deja ver qué hizo o qué dijo cada una.",
        "Escribe 20 o menos: con un mínimo más alto casi ningún total se podría mostrar.",
    )
    feedback_open_days = _number(
        "feedback_open_days", "Días para dar una opinión",
        "Cuántos días tienen quienes fueron a un evento para opinar, contados desde su último día. Entre 7 y 30.",
        "Escribe 7 o más, para que las personas tengan al menos una semana.",
        "Escribe 30 o menos.",
    )
    urgent_notice_pastors = forms.BooleanField(
        required=False, label="Los pastores también pueden publicar avisos urgentes",
        help_text="La administración de la iglesia siempre puede publicarlos. Si lo enciendes, también podrá quien "
                  "tenga el rol de pastor. Un aviso urgente le pide «Enterado» a cada persona.",
    )
    minors_need_consent_by_default = forms.BooleanField(
        required=False, label="Los eventos nuevos piden autorización para menores de edad",
        help_text="Al crear la página de un evento, «Los menores de edad necesitan autorización de su madre, padre o "
                  "tutor» aparece ya encendido. Lo puedes cambiar en cada evento. No cambia los eventos que ya existen.",
    )
    attendance_identifiable_months = _number(
        "attendance_identifiable_months", "Meses que se conserva quién asistió",
        "Pasado este tiempo, el borrado deja de cada reunión de grupo solo sus totales y quita quién asistió. "
        "Entre 6 y 24. El mínimo es 6, para que quien guía un grupo pueda ver sus últimos meses; el máximo es 24, "
        "para no guardar nombres más tiempo del necesario.",
        "Escribe 6 o más: con menos, quien guía un grupo no podría ver sus últimos meses.",
        "Escribe 24 o menos: Emmaus no guarda quién asistió por más de dos años.",
    )
    attendance_aggregate_months = _number(
        "attendance_aggregate_months", "Meses que se conservan los totales",
        "Pasado este tiempo, el borrado quita la reunión completa. Entre 12 y 60, y nunca menos que los meses "
        "que se conserva quién asistió.",
        "Escribe 12 o más, para poder comparar un año con el anterior.",
        "Escribe 60 o menos.",
    )

    def clean(self):
        cleaned = super().clean()
        names, totals = cleaned.get("attendance_identifiable_months"), cleaned.get("attendance_aggregate_months")
        if names is not None and totals is not None and totals < names:
            self.add_error(
                "attendance_aggregate_months",
                f"Escribe {names} o más: los totales no se pueden conservar menos tiempo que quién asistió.",
            )
        return cleaned
