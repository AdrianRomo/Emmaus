from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, SetPasswordForm
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import (
    AIDraft,
    Church,
    DiscipleshipPath,
    FollowUpRequest,
    Invitation,
    Lesson,
    ParticipantProfile,
    ParticipantQuestion,
    PastoralNote,
    QuestionResponse,
    ReflectionQuestion,
    Role,
)


def use_tu_error_messages(field: forms.Field) -> None:
    """Swap Django's default validation messages (written with "usted") for tú-voice Spanish."""
    messages = {}
    if isinstance(field, forms.EmailField):
        messages["invalid"] = "Escribe un correo electrónico válido, por ejemplo: nombre@ejemplo.com."
    elif isinstance(field, forms.SlugField):
        messages["invalid"] = "Usa solo letras minúsculas sin acentos, números y guiones."
    elif isinstance(field, forms.IntegerField):
        messages["invalid"] = "Escribe un número entero."
        messages["min_value"] = "Usa un número igual o mayor que %(limit_value)s."
        messages["max_value"] = "Usa un número igual o menor que %(limit_value)s."
    elif isinstance(field, forms.DateField):
        messages["invalid"] = "Escribe una fecha válida."
    if isinstance(field, forms.CharField):
        messages["max_length"] = "Escribe como máximo %(limit_value)d caracteres (ahora hay %(show_value)d)."
    if isinstance(field, forms.ChoiceField):
        messages["invalid_choice"] = "Elige una de las opciones de la lista."
    field.error_messages.update(messages)


class TuErrorMessagesMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            use_tu_error_messages(field)


class TuPasswordResetForm(TuErrorMessagesMixin, PasswordResetForm):
    """Django's reset form, labelled and worded in tú-voice Spanish (its own translation uses "usted")."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].label = "Correo electrónico"
        self.fields["email"].error_messages["required"] = "Escribe el correo con el que entras a Emmaus."


class TuSetPasswordForm(TuErrorMessagesMixin, SetPasswordForm):
    """New password, in tú-voice Spanish. The rules mirror AUTH_PASSWORD_VALIDATORS in settings."""

    error_messages = {"password_mismatch": "Las dos contraseñas no coinciden. Escríbelas otra vez."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].label = "Contraseña nueva"
        self.fields["new_password1"].help_text = (
            "Usa al menos 8 caracteres. Que no sea solo números, ni una contraseña muy común, ni parecida a tu nombre o tu correo."
        )
        self.fields["new_password1"].error_messages["required"] = "Escribe tu contraseña nueva."
        self.fields["new_password2"].label = "Escríbela otra vez"
        self.fields["new_password2"].help_text = ""
        self.fields["new_password2"].error_messages["required"] = "Escribe otra vez tu contraseña nueva."


class EmailAuthenticationForm(TuErrorMessagesMixin, AuthenticationForm):
    """Login form labelled for people who sign in with their email address."""

    error_messages = {
        "invalid_login": (
            "Revisa tu correo y contraseña. Si no puedes entrar, pide ayuda a la administración del evento."
        ),
        "inactive": "Esta cuenta está desactivada. Pide ayuda a la administración del evento.",
    }

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        username = self.fields["username"]
        username.label = "Correo electrónico"
        username.error_messages["required"] = "Escribe tu correo electrónico."
        username.widget.attrs.update(
            {
                "autocomplete": "email",
                "inputmode": "email",
                "autocapitalize": "none",
                "spellcheck": "false",
            }
        )
        password = self.fields["password"]
        password.label = "Contraseña"
        password.error_messages["required"] = "Escribe tu contraseña."


class InitialSetupForm(TuErrorMessagesMixin, forms.Form):
    church_name = forms.CharField(max_length=180, label="Nombre de la iglesia")
    church_slug = forms.SlugField(
        max_length=80,
        label="Identificador de la iglesia",
        help_text="Un nombre corto en minúsculas, sin espacios ni acentos, por ejemplo: iglesia-gracia.",
    )
    admin_name = forms.CharField(max_length=150, label="Nombre de la primera persona administradora")
    admin_email = forms.EmailField(label="Correo de la primera persona administradora")
    admin_password = forms.CharField(
        label="Crea la contraseña de administración",
        widget=forms.PasswordInput,
        help_text="Usa una contraseña segura. Emmaus revisa que no sea demasiado corta ni común.",
    )

    def clean_church_slug(self):
        slug = self.cleaned_data["church_slug"].lower()
        if Church.objects.filter(slug=slug).exists():
            raise ValidationError("Ese identificador ya está en uso. Elige otro.")
        return slug

    def clean_admin_email(self):
        email = self.cleaned_data["admin_email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("Ya existe una cuenta con este correo.")
        return email

    def clean_admin_password(self):
        password = self.cleaned_data["admin_password"]
        validate_password(password)
        return password


class ChurchSettingsForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = Church
        fields = [
            "name",
            "public_welcome_text",
            "contact_email",
            "basic_disclaimer_text",
            "self_registration_enabled",
            "leaders_can_invite_participants",
            "discipleship_enabled",
            "custom_theme_enabled",
            "theme_primary_color",
            "theme_accent_color",
            "theme_secondary_color",
            "theme_background_color",
            "theme_surface_color",
            "theme_text_color",
            "theme_muted_text_color",
            "theme_border_color",
        ]
        labels = {
            "theme_primary_color": "Color principal",
            "theme_accent_color": "Color de acento",
            "theme_secondary_color": "Color secundario",
            "theme_background_color": "Fondo",
            "theme_surface_color": "Superficies",
            "theme_text_color": "Texto",
            "theme_muted_text_color": "Texto secundario",
            "theme_border_color": "Bordes",
        }
        widgets = {
            "public_welcome_text": forms.Textarea(attrs={"rows": 4}),
            "basic_disclaimer_text": forms.Textarea(attrs={"rows": 4}),
            "theme_primary_color": forms.TextInput(attrs={"type": "color"}),
            "theme_accent_color": forms.TextInput(attrs={"type": "color"}),
            "theme_secondary_color": forms.TextInput(attrs={"type": "color"}),
            "theme_background_color": forms.TextInput(attrs={"type": "color"}),
            "theme_surface_color": forms.TextInput(attrs={"type": "color"}),
            "theme_text_color": forms.TextInput(attrs={"type": "color"}),
            "theme_muted_text_color": forms.TextInput(attrs={"type": "color"}),
            "theme_border_color": forms.TextInput(attrs={"type": "color"}),
        }


class InvitationForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = Invitation
        fields = ["email", "role", "assigned_mentor", "path"]
        labels = {
            "email": "Correo electrónico",
            "role": "Rol",
            "assigned_mentor": "Mentor asignado",
            "path": "Ruta de discipulado",
        }
        help_texts = {
            "email": "Le enviaremos a este correo un enlace personal para crear su cuenta.",
        }

    def __init__(self, *args, church: Church, inviter_role: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.inviter_role = inviter_role
        mentor_ids = church.user_roles.filter(role=Role.MENTOR).values_list("user_id", flat=True)
        self.fields["assigned_mentor"].queryset = User.objects.filter(id__in=mentor_ids).order_by(
            "first_name", "last_name", "email"
        )
        self.fields["assigned_mentor"].required = False
        self.fields["assigned_mentor"].empty_label = "Sin mentor por ahora"
        self.fields["assigned_mentor"].help_text = "Solo aplica cuando invitas a un participante."
        self.fields["path"].queryset = church.paths.filter(is_active=True).order_by("-is_default", "title")
        self.fields["path"].required = False
        self.fields["path"].empty_label = "Sin ruta por ahora"
        self.fields["path"].help_text = "Si eliges una ruta, el participante quedará inscrito al aceptar la invitación."
        if inviter_role == Role.CHURCH_ADMIN:
            self.fields["role"].choices = [
                (Role.PASTOR, "Pastor"),
                (Role.MENTOR, "Mentor"),
                (Role.PARTICIPANT, "Participante"),
            ]
        elif church.leaders_can_invite_participants:
            self.fields["role"].choices = [(Role.PARTICIPANT, "Participante")]
        else:
            self.fields["role"].choices = []

    def clean_email(self):
        return self.cleaned_data["email"].lower()

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        if self.inviter_role != Role.CHURCH_ADMIN and role != Role.PARTICIPANT:
            raise ValidationError("Solo la administración de la iglesia puede invitar a líderes.")
        if role != Role.PARTICIPANT:
            cleaned["assigned_mentor"] = None
            cleaned["path"] = None
        return cleaned


class AcceptInvitationForm(TuErrorMessagesMixin, forms.Form):
    full_name = forms.CharField(max_length=150, label="Tu nombre completo")
    password1 = forms.CharField(label="Contraseña", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirma tu contraseña", widget=forms.PasswordInput)
    preferred_contact_method = forms.ChoiceField(
        choices=ParticipantProfile.ContactMethod.choices,
        required=False,
        label="¿Cómo prefieres que te contacten?",
    )
    phone_number = forms.CharField(max_length=40, required=False, label="Teléfono")
    accept_privacy = forms.BooleanField(
        required=True,
        label="Acepto que mi iglesia guarde en Emmaus la información que comparta, para darme acompañamiento de discipulado.",
        error_messages={"required": "Para continuar, acepta el uso de tu información."},
    )
    accept_emergency_disclaimer = forms.BooleanField(
        required=True,
        label="Entiendo que Emmaus no es un servicio de emergencias ni de terapia, y que no sustituye el cuidado pastoral.",
        error_messages={"required": "Para continuar, confirma que leíste este aviso."},
    )

    def __init__(self, *args, invitation: Invitation, **kwargs):
        super().__init__(*args, **kwargs)
        self.invitation = invitation
        if invitation.role != Role.PARTICIPANT:
            self.fields.pop("preferred_contact_method")
            self.fields.pop("phone_number")
            self.fields.pop("accept_privacy")
            self.fields.pop("accept_emergency_disclaimer")

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password1")
        confirm = cleaned.get("password2")
        if password and confirm and password != confirm:
            raise ValidationError("Las contraseñas no coinciden.")
        if password:
            validate_password(password)
        if User.objects.filter(email__iexact=self.invitation.email).exists():
            raise ValidationError("Ya existe una cuenta con este correo.")
        return cleaned


class PathForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = DiscipleshipPath
        fields = ["title", "description", "target_audience", "is_active", "is_default"]
        labels = {
            "title": "Nombre de la ruta",
            "description": "Descripción",
            "target_audience": "¿Para quién es?",
            "is_active": "Ruta activa",
            "is_default": "Ruta predeterminada",
        }
        help_texts = {
            "target_audience": "Por ejemplo: personas nuevas, nuevos creyentes o quienes regresan a la iglesia.",
            "is_active": "Los participantes solo ven rutas activas.",
            "is_default": "Las personas nuevas se inscriben en esta ruta si no eliges otra.",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }


class LessonForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = Lesson
        fields = ["order", "title", "scripture_references", "body", "practical_step", "is_active"]
        labels = {
            "order": "Orden",
            "title": "Título de la lección",
            "scripture_references": "Pasajes bíblicos",
            "body": "Contenido de la lección",
            "practical_step": "Paso práctico",
            "is_active": "Lección activa",
        }
        help_texts = {
            "order": "Número de la lección dentro de la ruta.",
            "scripture_references": "Por ejemplo: Juan 1:1-14; Marcos 8:27-30.",
            "practical_step": "Una acción sencilla para practicar durante la semana.",
            "is_active": "Los participantes solo ven lecciones activas.",
        }
        widgets = {
            "body": forms.Textarea(attrs={"rows": 8}),
            "practical_step": forms.Textarea(attrs={"rows": 3}),
        }


class ReflectionQuestionForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = ReflectionQuestion
        fields = ["order", "question_text"]
        labels = {
            "order": "Orden",
            "question_text": "Pregunta de reflexión",
        }
        help_texts = {
            "question_text": "Escribe una pregunta abierta que invite a responder con honestidad.",
        }
        widgets = {"question_text": forms.Textarea(attrs={"rows": 3})}


class ParticipantQuestionForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = ParticipantQuestion
        fields = ["question_text", "sensitivity_level"]
        labels = {
            "question_text": "¿Qué te gustaría preguntar?",
            "sensitivity_level": "¿Cómo deben tratar los líderes tu pregunta?",
        }
        help_texts = {
            "sensitivity_level": "Elige «Delicada» o «Requiere cuidado pastoral» si tu pregunta necesita más cuidado.",
        }
        widgets = {
            "question_text": forms.Textarea(
                attrs={
                    "rows": 8,
                    "placeholder": "Escribe aquí tu pregunta. Un líder de confianza de tu iglesia la va a leer.",
                }
            ),
        }


class QuestionResponseForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = QuestionResponse
        fields = ["response_body"]
        labels = {"response_body": "Tu respuesta"}
        widgets = {
            "response_body": forms.Textarea(
                attrs={"rows": 7, "placeholder": "Responde de forma personal y con amabilidad."}
            )
        }


class ParticipantFollowUpForm(TuErrorMessagesMixin, forms.Form):
    reason = forms.CharField(
        label="¿De qué te gustaría platicar?",
        help_text="Comparte solo lo que te sientas en confianza de escribir aquí.",
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "Me gustaría platicar con alguien sobre…"}),
        max_length=2000,
    )


class FollowUpRequestForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = FollowUpRequest
        fields = ["participant", "assigned_to", "reason", "status", "due_date"]
        labels = {
            "participant": "Participante",
            "assigned_to": "Responsable",
            "reason": "Motivo del seguimiento",
            "status": "Estado",
            "due_date": "Fecha límite",
        }
        help_texts = {
            "assigned_to": "La persona que dará el seguimiento.",
            "reason": "Escribe solo lo necesario para dar un buen acompañamiento.",
        }
        widgets = {
            "reason": forms.Textarea(attrs={"rows": 4}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, church: Church, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["participant"].queryset = User.objects.filter(participant_profile__church=church).order_by(
            "first_name", "last_name", "email"
        )
        self.fields["participant"].empty_label = "Elige a una persona"
        leader_ids = church.user_roles.filter(role__in=[Role.CHURCH_ADMIN, Role.PASTOR, Role.MENTOR]).values_list(
            "user_id", flat=True
        )
        self.fields["assigned_to"].queryset = User.objects.filter(id__in=leader_ids).order_by(
            "first_name", "last_name", "email"
        )
        self.fields["assigned_to"].required = False
        self.fields["assigned_to"].empty_label = "Sin asignar"


class PastoralNoteForm(TuErrorMessagesMixin, forms.ModelForm):
    class Meta:
        model = PastoralNote
        fields = ["body", "visibility"]
        labels = {"body": "Nota", "visibility": "¿Quién puede ver esta nota?"}
        help_texts = {"body": "Escribe solo lo necesario para el cuidado pastoral."}
        widgets = {"body": forms.Textarea(attrs={"rows": 5})}


class AssignMentorForm(TuErrorMessagesMixin, forms.Form):
    mentor = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label="Mentor",
        empty_label="Sin mentor asignado",
        help_text="Elige a la persona que acompañará a este participante.",
    )

    def __init__(self, *args, church: Church, **kwargs):
        super().__init__(*args, **kwargs)
        mentor_ids = church.user_roles.filter(role=Role.MENTOR).values_list("user_id", flat=True)
        self.fields["mentor"].queryset = User.objects.filter(id__in=mentor_ids).order_by("first_name", "last_name", "email")


class AIDraftReviewForm(TuErrorMessagesMixin, forms.ModelForm):
    review_action = forms.ChoiceField(
        label="¿Qué quieres hacer con este borrador?",
        choices=[
            ("approve", "Aprobar y enviar"),
            ("edit", "Guardar cambios y enviar"),
            ("reject", "Rechazar borrador"),
        ],
    )

    class Meta:
        model = AIDraft
        fields = ["edited_final_text", "review_action"]
        labels = {"edited_final_text": "Texto final"}
        help_texts = {"edited_final_text": "Edita el borrador antes de enviarlo. Esta será tu respuesta."}
        widgets = {"edited_final_text": forms.Textarea(attrs={"rows": 8})}
