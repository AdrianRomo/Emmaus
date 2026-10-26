from __future__ import annotations

import logging

from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, SetPasswordForm
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.utils.text import slugify

from .colors import contrast_ratio, contrast_with_white, parse_hex
from .logos import UPLOAD_HELP, inspect_logo_upload
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

logger = logging.getLogger(__name__)


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


def person_label(user: User) -> str:
    """How a person reads in a select: "Nombre Apellido", never the e-mail (User.__str__ is the username, an e-mail)."""
    return user.get_full_name() or "Persona sin nombre"


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

    # Django checks the rules on the confirmation field, so "muy corta" hung from "Escríbela otra vez" and the
    # person retyped the confirmation. The rules are about the new password: their errors go there, and
    # "Escríbela otra vez" only says that the two do not match (LANG-14).
    TOO_SIMILAR = "La contraseña se parece demasiado a tu correo o a tu nombre. Elige otra."

    def validate_password_for_user(self, user, password_field_name="new_password1"):
        # SetPasswordForm.clean passes "new_password2"; the rules are checked on the new password whatever it
        # passes. The password is never bound to a local name (Django's own method hides its "password" local
        # from error reports with @sensitive_variables).
        if not self.cleaned_data.get("new_password1"):
            return
        try:
            validate_password(self.cleaned_data["new_password1"], user)
        except ValidationError as error:
            # Django names the field it compared ("tu nombre de usuario", "tu apellidos"); people here sign in
            # with their e-mail and never see a user name (LANG-13).
            self.add_error("new_password1", [
                ValidationError(self.TOO_SIMILAR, code=e.code) if e.code == "password_too_similar" else e for e in error.error_list
            ])


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
    """First setup. The church's short name forms itself from its name when left blank (UX-12), here and while typing
    (emmaus.js, data-slug-from)."""

    # Church.slug is a SlugField without max_length: 50 characters in the database. The field says the same, so neither
    # a typed nor a formed short name is longer than the column (PostgreSQL refuses it).
    SLUG_MAX_LENGTH = Church._meta.get_field("slug").max_length

    church_name = forms.CharField(max_length=180, label="Nombre de la iglesia")
    church_slug = forms.SlugField(
        max_length=SLUG_MAX_LENGTH,
        required=False,
        label="Nombre corto de la iglesia para la dirección",
        help_text="Se forma con el nombre; puedes cambiarlo.",
    )
    admin_name = forms.CharField(max_length=150, label="Nombre de la primera persona administradora")
    admin_email = forms.EmailField(label="Correo de la primera persona administradora")
    admin_password = forms.CharField(
        label="Crea la contraseña de administración",
        widget=forms.PasswordInput,
        help_text="Usa una contraseña segura. Emmaus revisa que no sea demasiado corta ni común.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["church_slug"].widget.attrs["data-slug-from"] = self["church_name"].auto_id

    @staticmethod
    def _slug_taken(slug: str) -> bool:
        return Church.objects.filter(slug=slug).exists()

    def clean_church_slug(self):
        typed = self.cleaned_data["church_slug"]
        if typed:
            slug = typed.lower()
            if self._slug_taken(slug):
                raise ValidationError("Ese nombre corto ya está en uso. Elige otro.")
            return slug
        name = self.cleaned_data.get("church_name")
        if name is None:
            return ""  # the name has its own error; nothing is created
        base = slugify(name)[: self.SLUG_MAX_LENGTH].strip("-")
        if not base:
            raise ValidationError("Escribe un nombre corto con letras, números o guiones.")
        slug, n = base, 2  # taken: the next number, as events do
        while self._slug_taken(slug):
            suffix = f"-{n}"
            slug, n = f"{base[: self.SLUG_MAX_LENGTH - len(suffix)].rstrip('-')}{suffix}", n + 1
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


# (file field, its "Quitar" checkbox)
LOGO_FIELDS = (("logo", "logo_clear"), ("logo_dark", "logo_dark_clear"))
LOGO_ACCEPT = {"accept": "image/png"}


# The church's own colours paint every screen of the church (audit A11Y-06). The primary colour carries white text on
# buttons, so it needs 4.5:1 against white; the accent is a mark beside text, a graphical object, so 3:1 (WCAG 1.4.3,
# 1.4.11). The primary colour is also link text, and the text colours are text, on the page background and on the
# surfaces (fields, bars, panels): 4.5:1 against each.
THEME_MIN_CONTRAST = {"theme_primary_color": 4.5, "theme_accent_color": 3.0}
THEME_CONTRAST_ERROR = "Elige un color más oscuro: necesita un contraste de al menos {} a 1 sobre blanco."
THEME_ON_BACKGROUNDS = ("theme_primary_color", "theme_text_color", "theme_muted_text_color")
THEME_BACKGROUNDS = ("theme_background_color", "theme_surface_color")
THEME_TEXT_MIN_CONTRAST = 4.5
THEME_BACKGROUND_ERROR = (
    "Elige un color que se lea sobre el fondo y las superficies: necesita un contraste de al menos 4.5 a 1 con cada uno."
)


class ChurchSettingsForm(TuErrorMessagesMixin, forms.ModelForm):
    """The church's data, access, logo and colours. The logo is a PNG checked by core.logos (its bytes, not its name).

    With the church's own colours on, the primary and accent colours must read on white (THEME_MIN_CONTRAST).
    """

    logo_clear = forms.BooleanField(required=False, label="Quitar el logotipo")
    logo_dark_clear = forms.BooleanField(required=False, label="Quitar el logotipo para fondo oscuro")

    class Meta:
        model = Church
        fields = [
            "name",
            "public_welcome_text",
            "contact_email",
            "basic_disclaimer_text",
            "logo",
            "logo_clear",
            "logo_dark",
            "logo_dark_clear",
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
            "theme_primary_color": "Color principal",  # also the prefix of its line in the error summary
            "theme_accent_color": "Color de “estás aquí”",
            "theme_secondary_color": "Color secundario",
            "theme_background_color": "Color del fondo",
            "theme_surface_color": "Color de campos y paneles",
            "theme_text_color": "Color del texto",
            "theme_muted_text_color": "Color del texto tenue",
            "theme_border_color": "Color de los bordes",
        }
        help_texts = {
            "theme_primary_color": "Botones, enlaces y cifras. Necesita un contraste de al menos 4.5 a 1 sobre blanco.",
            "theme_accent_color": (
                "El punto de la computadora y el subrayado de la página actual. "
                "Necesita un contraste de al menos 3 a 1 sobre blanco."
            ),
            "theme_secondary_color": "Por ahora no se usa en pantalla.",
            "theme_text_color": "Necesita un contraste de al menos 4.5 a 1 sobre el fondo y sobre las superficies.",
            "theme_muted_text_color": "Necesita un contraste de al menos 4.5 a 1 sobre el fondo y sobre las superficies.",
            "logo": f"{UPLOAD_HELP} Negro sobre transparente se ve bien en todas las pantallas.",
            "logo_dark": (
                f"{UPLOAD_HELP} La versión en blanco, para la presentación en modo noche. Sin ella, "
                "Emmaus pone tu logotipo sobre una placa blanca."
            ),
        }
        widgets = {
            "logo": forms.FileInput(attrs=LOGO_ACCEPT),
            "logo_dark": forms.FileInput(attrs=LOGO_ACCEPT),
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # What is stored now, so a replaced or removed file can be deleted once the new state is saved.
        self._stored_logos = {field: getattr(self.instance, field).name for field, _ in LOGO_FIELDS}

    def _clean_upload(self, field: str, stem: str):
        upload = self.cleaned_data.get(field)
        if isinstance(upload, UploadedFile):
            inspect_logo_upload(upload)
            upload.name = f"{stem}-{self.instance.slug}.png"  # a name of our own; the one sent is not kept
        return upload

    def clean_logo(self):
        return self._clean_upload("logo", "logotipo")

    def clean_logo_dark(self):
        return self._clean_upload("logo_dark", "logotipo-oscuro")

    def clean(self):
        cleaned = super().clean()
        self._cleared_logos = []
        for field, clear in LOGO_FIELDS:
            if not cleaned.get(clear):
                continue
            if isinstance(cleaned.get(field), UploadedFile):
                self.add_error(field, "Elige una sola cosa: sube un logotipo nuevo o marca “Quitar el logotipo”.")
            else:
                self._cleared_logos.append(field)
        if cleaned.get("custom_theme_enabled"):
            self._check_theme_contrast(cleaned)
        return cleaned

    def _check_theme_contrast(self, cleaned):
        # A colour that failed its own validation (not #RRGGBB) is missing from cleaned: parse_hex gives None, no check
        for field, minimum in THEME_MIN_CONTRAST.items():
            ratio = contrast_with_white(cleaned.get(field))
            if ratio is not None and ratio < minimum:
                self.add_error(field, THEME_CONTRAST_ERROR.format(f"{minimum:g}"))
        backgrounds = [parse_hex(cleaned.get(name)) for name in THEME_BACKGROUNDS]
        for field in THEME_ON_BACKGROUNDS:
            colour = parse_hex(cleaned.get(field))
            if colour is None or field in self.errors:
                continue  # one message per field: "más oscuro sobre blanco" already says what to do
            if any(bg is not None and contrast_ratio(colour, bg) < THEME_TEXT_MIN_CONTRAST for bg in backgrounds):
                self.add_error(field, THEME_BACKGROUND_ERROR)

    def save(self, commit=True):
        # Set here: a file field with nothing uploaded is left out of construct_instance (it has a default).
        for field in self._cleared_logos:
            setattr(self.instance, field, "")
        church = super().save(commit=commit)
        if commit:
            for field, _ in LOGO_FIELDS:
                old, new = self._stored_logos[field], getattr(church, field).name
                if old and old != new:
                    storage = church._meta.get_field(field).storage
                    try:
                        storage.delete(old)
                    except OSError as exc:  # the settings are saved; a leftover file only takes space
                        logger.warning("Could not delete the replaced church logo %s: %s", old, exc)
        return church


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
        self.fields["assigned_mentor"].label_from_instance = person_label
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
            raise ValidationError("Las dos contraseñas no coinciden. Escríbelas otra vez.")
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
            "sensitivity_level": "Elige “Delicada” o “Requiere cuidado pastoral” si tu pregunta necesita más cuidado.",
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
        # No "status": a new follow-up is always pending (the model's default); "Marcar como atendido" ends it.
        fields = ["participant", "assigned_to", "reason", "due_date"]
        labels = {
            "participant": "Participante",
            "assigned_to": "Responsable",
            "reason": "Motivo del seguimiento",
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
        self.fields["participant"].label_from_instance = person_label
        leader_ids = church.user_roles.filter(role__in=[Role.CHURCH_ADMIN, Role.PASTOR, Role.MENTOR]).values_list(
            "user_id", flat=True
        )
        self.fields["assigned_to"].queryset = User.objects.filter(id__in=leader_ids).order_by(
            "first_name", "last_name", "email"
        )
        self.fields["assigned_to"].required = False
        self.fields["assigned_to"].empty_label = "Sin asignar"
        self.fields["assigned_to"].label_from_instance = person_label


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
        self.fields["mentor"].label_from_instance = person_label


class AIDraftReviewForm(TuErrorMessagesMixin, forms.ModelForm):
    # No default: sending an AI draft to a participant must be a choice someone makes, never the preselected option.
    review_action = forms.ChoiceField(
        label="¿Qué quieres hacer con este borrador?",
        error_messages={"required": "Elige qué hacer con este borrador: enviarlo, guardar tus cambios y enviarlo, o rechazarlo."},
        choices=[
            ("", "Elige una opción"),
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
