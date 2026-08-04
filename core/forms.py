from __future__ import annotations

from django import forms
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


class InitialSetupForm(forms.Form):
    church_name = forms.CharField(max_length=180, label="Church name")
    church_slug = forms.SlugField(
        max_length=80,
        label="Church workspace slug",
        help_text="A short lowercase name for this workspace, such as grace-church.",
    )
    admin_name = forms.CharField(max_length=150, label="First admin name")
    admin_email = forms.EmailField(label="First admin email")
    admin_password = forms.CharField(
        label="Create admin password",
        widget=forms.PasswordInput,
        help_text="Use a strong password. Django password validation is enforced.",
    )

    def clean_church_slug(self):
        slug = self.cleaned_data["church_slug"].lower()
        if Church.objects.filter(slug=slug).exists():
            raise ValidationError("That church slug is already in use.")
        return slug

    def clean_admin_email(self):
        email = self.cleaned_data["admin_email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("A user with this email already exists.")
        return email

    def clean_admin_password(self):
        password = self.cleaned_data["admin_password"]
        validate_password(password)
        return password


class ChurchSettingsForm(forms.ModelForm):
    class Meta:
        model = Church
        fields = [
            "name",
            "public_welcome_text",
            "contact_email",
            "basic_disclaimer_text",
            "self_registration_enabled",
            "leaders_can_invite_participants",
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
            "theme_primary_color": "Primary",
            "theme_accent_color": "Accent",
            "theme_secondary_color": "Secondary",
            "theme_background_color": "Background",
            "theme_surface_color": "Surface",
            "theme_text_color": "Text",
            "theme_muted_text_color": "Muted text",
            "theme_border_color": "Borders",
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


class InvitationForm(forms.ModelForm):
    class Meta:
        model = Invitation
        fields = ["email", "role", "assigned_mentor", "path"]

    def __init__(self, *args, church: Church, inviter_role: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.inviter_role = inviter_role
        mentor_ids = church.user_roles.filter(role=Role.MENTOR).values_list("user_id", flat=True)
        self.fields["assigned_mentor"].queryset = User.objects.filter(id__in=mentor_ids).order_by(
            "first_name", "last_name", "email"
        )
        self.fields["assigned_mentor"].required = False
        self.fields["assigned_mentor"].help_text = "Optional. Used only for participant invitations."
        self.fields["path"].queryset = church.paths.filter(is_active=True).order_by("-is_default", "title")
        self.fields["path"].required = False
        self.fields["path"].help_text = "Optional. Participants can be enrolled automatically when they accept."
        if inviter_role == Role.CHURCH_ADMIN:
            self.fields["role"].choices = [
                (Role.PASTOR, "Pastor"),
                (Role.MENTOR, "Mentor"),
                (Role.PARTICIPANT, "Participant"),
            ]
        elif church.leaders_can_invite_participants:
            self.fields["role"].choices = [(Role.PARTICIPANT, "Participant")]
        else:
            self.fields["role"].choices = []

    def clean_email(self):
        return self.cleaned_data["email"].lower()

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        if self.inviter_role != Role.CHURCH_ADMIN and role != Role.PARTICIPANT:
            raise ValidationError("Only church admins can invite leaders.")
        if role != Role.PARTICIPANT:
            cleaned["assigned_mentor"] = None
            cleaned["path"] = None
        return cleaned


class AcceptInvitationForm(forms.Form):
    full_name = forms.CharField(max_length=150, label="Your name")
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput)
    preferred_contact_method = forms.ChoiceField(
        choices=ParticipantProfile.ContactMethod.choices,
        required=False,
        label="Preferred contact method",
    )
    phone_number = forms.CharField(max_length=40, required=False, label="Phone number")
    accept_privacy = forms.BooleanField(
        required=True,
        label="I consent to my church storing information I provide in Emmaus for discipleship follow-up.",
    )
    accept_emergency_disclaimer = forms.BooleanField(
        required=True,
        label="I understand Emmaus is not emergency help, therapy, or a replacement for pastoral care.",
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
            raise ValidationError("Passwords do not match.")
        if password:
            validate_password(password)
        if User.objects.filter(email__iexact=self.invitation.email).exists():
            raise ValidationError("A user already exists for this email address.")
        return cleaned


class PathForm(forms.ModelForm):
    class Meta:
        model = DiscipleshipPath
        fields = ["title", "description", "target_audience", "is_active", "is_default"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }


class LessonForm(forms.ModelForm):
    class Meta:
        model = Lesson
        fields = ["order", "title", "scripture_references", "body", "practical_step", "is_active"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 8}),
            "practical_step": forms.Textarea(attrs={"rows": 3}),
        }


class ReflectionQuestionForm(forms.ModelForm):
    class Meta:
        model = ReflectionQuestion
        fields = ["order", "question_text"]
        widgets = {"question_text": forms.Textarea(attrs={"rows": 3})}


class ParticipantQuestionForm(forms.ModelForm):
    class Meta:
        model = ParticipantQuestion
        fields = ["question_text", "sensitivity_level"]
        labels = {
            "question_text": "What would you like to ask?",
            "sensitivity_level": "How should leaders handle this?",
        }
        help_texts = {
            "sensitivity_level": "Choose sensitive or pastoral care required when the question should be handled more carefully.",
        }
        widgets = {
            "question_text": forms.Textarea(
                attrs={
                    "rows": 8,
                    "placeholder": "Write your question here. A trusted leader from your church will review it.",
                }
            ),
        }


class QuestionResponseForm(forms.ModelForm):
    class Meta:
        model = QuestionResponse
        fields = ["response_body"]
        labels = {"response_body": "Your response"}
        widgets = {"response_body": forms.Textarea(attrs={"rows": 7, "placeholder": "Respond personally and gently."})}


class ParticipantFollowUpForm(forms.Form):
    reason = forms.CharField(
        label="What would you like to talk about?",
        help_text="Share only what you are comfortable sharing here.",
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "I would like to talk with someone about..."}),
        max_length=2000,
    )


class FollowUpRequestForm(forms.ModelForm):
    class Meta:
        model = FollowUpRequest
        fields = ["participant", "assigned_to", "reason", "status", "due_date"]
        widgets = {
            "reason": forms.Textarea(attrs={"rows": 4}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, church: Church, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["participant"].queryset = User.objects.filter(participant_profile__church=church).order_by(
            "first_name", "last_name", "email"
        )
        leader_ids = church.user_roles.filter(role__in=[Role.CHURCH_ADMIN, Role.PASTOR, Role.MENTOR]).values_list(
            "user_id", flat=True
        )
        self.fields["assigned_to"].queryset = User.objects.filter(id__in=leader_ids).order_by(
            "first_name", "last_name", "email"
        )
        self.fields["assigned_to"].required = False


class PastoralNoteForm(forms.ModelForm):
    class Meta:
        model = PastoralNote
        fields = ["body", "visibility"]
        labels = {"body": "Note", "visibility": "Who should be able to see this note?"}
        help_texts = {"body": "Keep notes limited to what is pastorally necessary."}
        widgets = {"body": forms.Textarea(attrs={"rows": 5})}


class AssignMentorForm(forms.Form):
    mentor = forms.ModelChoiceField(queryset=User.objects.none(), required=False)

    def __init__(self, *args, church: Church, **kwargs):
        super().__init__(*args, **kwargs)
        mentor_ids = church.user_roles.filter(role=Role.MENTOR).values_list("user_id", flat=True)
        self.fields["mentor"].queryset = User.objects.filter(id__in=mentor_ids).order_by("first_name", "last_name", "email")


class AIDraftReviewForm(forms.ModelForm):
    review_action = forms.ChoiceField(
        choices=[
            ("approve", "Approve and send"),
            ("edit", "Save edits and send"),
            ("reject", "Reject draft"),
        ]
    )

    class Meta:
        model = AIDraft
        fields = ["edited_final_text", "review_action"]
        widgets = {"edited_final_text": forms.Textarea(attrs={"rows": 8})}
