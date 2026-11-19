from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from core.models import Role
from core.services import assign_role
from events.tests.base import make_user
from formularios import public, services
from formularios.fields import AnswerForm
from formularios.models import Form
from groups.tests.base import CommunityFixture

MEMBERS, PUBLIC = Form.Audience.MEMBERS, Form.Audience.PUBLIC
OTHER_ANSWER = "No puedo los jueves"


def url(name, *args):
    return reverse(f"formularios:{name}", args=args)


class FormFixture(CommunityFixture):
    """The community fixture plus a pastor who is not the administration, and builders of forms.

    ``leader`` guides ``group``; a form that names that group as a reader is theirs to read."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.today = services.church_today(cls.church)
        cls.pastor_user = make_user("pastor-sin-admin@prueba.mx", "Pablo", "Pastor")
        assign_role(cls.pastor_user, cls.church, Role.PASTOR)
        cls.pastor_person = cls._person(cls.church, cls.pastor_user)

    def draft(self, audience=MEMBERS, church=None, actor=None, **changes) -> Form:
        values = {
            "title": "Convivio de aniversario", "purpose": "Para organizar la comida.", "audience": audience, "retention_days": 90,
            "opens_on": self.today - timedelta(days=1), "closes_on": self.today + timedelta(days=10), **changes,
        }
        return services.create_form(church or self.church, actor or self.admin, **values)

    def publish(self, form) -> Form:
        form.refresh_from_db()
        return services.publish(form, self.admin, services.preview_token(form))[0]

    def member_form(self, reader=False, **changes) -> Form:
        """Published, for the church's people: yes/no, an option shown only on "Sí", a short text."""
        form = self.draft(**changes)
        goes, _ = services.save_question(form, self.admin, kind="si_no", label="¿Vas a venir?", required=True)
        services.save_question(form, self.admin, kind="opcion", label="¿Qué traes?", options=["Guisado", "Postre"], required=True,
                               show_if_question_id=goes.pk, show_if_value="Sí")
        services.save_question(form, self.admin, kind="corto", label="¿Algo más?")
        if reader:
            services.add_reader(form, self.admin, f"group:{self.group.pk}")
        return self.publish(form)

    def public_form(self, **changes) -> Form:
        """Published, public: a required contact block (e-mail and phone) and a short text."""
        form = self.draft(audience=PUBLIC, **changes)
        services.save_question(form, self.admin, kind="contacto", label="Tus datos", required=True, ask_email=True, ask_phone=True,
                               consent="Acepto que me contacten.")
        services.save_question(form, self.admin, kind="corto", label="¿Qué necesitas?")
        return self.publish(form)

    @staticmethod
    def keys(form) -> list[str]:
        return [item["key"] for item in form.current_version.schema]

    def member_data(self, form, **changes) -> dict:
        goes, brings, more = self.keys(form)[:3]
        return {"version": form.current_version_id, goes: "Sí", brings: "Postre", more: OTHER_ANSWER, **changes}

    def public_data(self, form, email="visita@demo.invalid", phone="", text="Informes", **changes) -> dict:
        contact, what = self.keys(form)[:2]
        return {
            "version": form.current_version_id, f"{contact}_nombre": "Vera", f"{contact}_apellidos": "Visita", f"{contact}_correo": email,
            f"{contact}_telefono": phone, f"{contact}_acepto": "on", what: text, "sello": self.seal(form), **changes,
        }

    @staticmethod
    def seal(form, seconds_ago=10) -> str:
        """The seal of a public page drawn ``seconds_ago``: old enough to be sent, as a person's is."""
        return public.new_seal(form, issued=timezone.now() - timedelta(seconds=seconds_ago))

    def answer(self, form, data=None, person=None, **kwargs):
        """Through the same two steps a view takes: validate against the version, then ``submit``."""
        form.refresh_from_db()
        if data is None:
            data = self.public_data(form) if form.is_public else self.member_data(form)
        checked = AnswerForm(data, schema=form.current_version.schema)
        assert checked.is_valid(), checked.errors
        if not form.is_public:
            person = person or self.member
        return services.submit(form, form.current_version_id, answers=checked.answers, contact=checked.contact, person=person, **kwargs)
