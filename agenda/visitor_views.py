"""The pages of signing up for an event without an account (``agenda.visitors``; issue #93, wave 12).

* ``/evento/<slug>/inscribirme/``: the form. It answers only while ``visitors.offered_page`` does; otherwise the
  same 404 as the public page. CSRF: the ordinary check, with the ordinary cookie, set by THIS page; that is why
  the form is not on the event's public page, which stays free of cookies. After a valid POST the answer is one
  redirect to one text, whatever happened to the address, and nothing typed is in it; or, while the event takes no
  more requests (``visitors.taking_requests``), one page that says so, also the same for every address.
* ``/evento/inscripcion/confirmar/<token>/``: a button; only pressing it signs the visitor up (a mail scanner
  that follows links signs up nobody). The page that answers says honestly where the visitor stands and gives
  their private link.
* ``/evento/inscripcion/mia/<token>/``: the private link: where the registration stands, and cancelling (a
  question first), accepting an offered place or leaving the waiting list. Ordinary CSRF check on every POST.

A link that is tampered with, expired, of another kind or of a row that is gone gets one answer (404 with a
plain text), and so does a confirmation link while ``visitors.closed`` says the church or the event no longer
offers this (module off, church inactive, event not active/public/published, option off). The private link
then shows nothing of the event and only lets the visitor cancel or leave the list. Who is signed in is never
looked at, no page here reads a table of people, shows another person or a count, and none is cached. A throttle
per browser session slows down a script at the form; the bounds per event (any session, any address) and per
address live in ``agenda.visitors``.
"""

from django import forms
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from core.forms import TuErrorMessagesMixin
from events.services import throttle
from people import policy

from . import public, questions, services, visitors

PAGE = "agenda/visitor.html"
ADULT = "Soy mayor de edad"
KEEP = "La iglesia puede guardar mi nombre y mi teléfono para invitarme a otras actividades."
KEEP_NAME = "La iglesia puede guardar mi nombre para invitarme a otras actividades."


class VisitorForm(TuErrorMessagesMixin, forms.Form):
    """Who signs up: a name, an address to prove, a phone only when the event asks, and the statement that they
    are an adult. One person per form."""

    first_name = forms.CharField(label="Tu nombre", max_length=120, widget=forms.TextInput(attrs={"autocomplete": "given-name"}))
    last_name = forms.CharField(label="Tus apellidos", max_length=120, widget=forms.TextInput(attrs={"autocomplete": "family-name"}))
    email = forms.EmailField(
        label="Tu correo", max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email", "inputmode": "email", "autocapitalize": "none", "spellcheck": "false"}),
    )
    phone = forms.CharField(label="Tu teléfono", max_length=40, widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}))
    adult = forms.BooleanField(label=ADULT)
    keep = forms.BooleanField(label=KEEP, required=False)  # optional, unticked: «Agregar a personas» needs it

    def __init__(self, *args, asks_phone: bool, **kwargs):
        super().__init__(*args, **kwargs)
        if not asks_phone:
            del self.fields["phone"]
            self.fields["keep"].label = KEEP_NAME  # no phone was asked: the box promises only what is kept
        else:
            self.fields["phone"].error_messages["required"] = "Escribe tu teléfono."
        self.fields["first_name"].error_messages["required"] = "Escribe tu nombre."
        self.fields["last_name"].error_messages["required"] = "Escribe tus apellidos."
        self.fields["email"].error_messages.update({
            "required": "Escribe tu correo.", "invalid": "Escribe un correo completo, por ejemplo nombre@ejemplo.com.",
        })
        self.fields["adult"].error_messages["required"] = (
            "Esta inscripción es solo para personas mayores de edad. Si eres menor de edad, inscríbete en la iglesia."
        )

    def clean_first_name(self):
        return questions.one_line(self.cleaned_data["first_name"])

    def clean_last_name(self):
        return questions.one_line(self.cleaned_data["last_name"])

    def clean_phone(self):
        phone = questions.one_line(self.cleaned_data["phone"])
        if sum(char.isdigit() for char in phone) < 7:
            raise forms.ValidationError("Escribe un teléfono con al menos 7 números.")
        return phone


def _page(request, state: str, *, event=None, status: int = 200, **context):
    church = event.church if event is not None else None
    return render(
        request, PAGE,
        {"state": state, "event": event, "church": church, "confirm_hours": visitors.CONFIRM_HOURS,
         # As the public page: it wears the event's church, whoever reads it.
         "brand_church": church, "theme_church": church, "ui_v2": True, **context},
        status=status,
    )


def _base_url(request) -> str:
    return request.build_absolute_uri("/")


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def sign_up(request, slug: str):
    page = visitors.offered_page(slug)
    if page is None:
        raise Http404
    event = page.event
    back = public.public_path(event)
    if request.method != "POST" and request.GET.get("listo") == "1":
        return _page(request, "asked", event=event, back=back)
    if not visitors.taking_requests(event):  # about the event, the same for everybody: nobody types for nothing
        return _page(request, "full", event=event, back=back, sent=request.method == "POST")
    asked = questions.asked(event, page)
    form = VisitorForm(request.POST or None, asks_phone=page.visitor_phone)
    answers = questions.AnswersForm(request.POST or None, questions=asked)
    if request.method == "POST":
        valid = form.is_valid() and answers.is_valid()  # both, so that every error is shown at once
        valid = answers.is_valid() and valid
        if valid:
            if not request.session.session_key:
                request.session.save()
            wait = throttle.consume(f"visitor-signup:{request.session.session_key}", limit=6, window_seconds=3600, block_seconds=600)
            if wait:
                form.add_error(None, throttle.wait_message(wait))
            else:
                data = form.cleaned_data
                answer = visitors.request(event, first_name=data["first_name"], last_name=data["last_name"],
                                          phone=data.get("phone", ""), email=data["email"], answers=answers.answers(),
                                          contact_consent=data["keep"])
                if answer == visitors.NOT_NOW:  # nothing was stored; the same page for every address
                    return _page(request, "full", event=event, back=back, sent=True)
                return redirect(reverse("evento:visitor_signup", args=[event.slug]) + "?listo=1")
    return _page(request, "form", event=event, back=back, form=form, answers=answers, has_list=page.waiting_list,
                 retention_days=event.retention_days)


def _standing(registration) -> dict:
    """What the private page says about one registration: its state as one word, and what can be done."""
    event = registration.event
    started = services.has_started(event)
    page = None
    if registration.status in services.LISTED:
        from .models import EventPage

        page = EventPage.objects.filter(event=event).first()
    if registration.is_active:
        state = "inscrita"
    elif registration.is_pending:
        state = "pendiente"
    elif services.offer_is_open(registration):
        state = "ofrecido_pausa" if services.list_block(event, page) else "ofrecido"
    elif registration.is_waiting:
        state = "en_espera"
    else:
        state = "cancelada"
    position = services.waiting_position(registration)
    return {
        "registration": registration, "standing": state, "can_cancel": state in ("inscrita", "pendiente") and not started,
        "started": started, "position_text": services.position_words(position) if position else "",
        "offer_hours": policy.waiting_offer_hours(event.church),
        "waits_for": registration.waits_for, "manage_path": visitors.manage_path(registration),
        "public_path": public.public_path(event) if public.public_page(event.slug) is not None else "",
    }


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def confirm(request, token: str):
    row = visitors.for_confirmation(token)
    if row is None or visitors.closed(row.event):  # the church or the event no longer offers this: the dead link
        return _page(request, "invalid", status=404)
    event = row.event
    if request.method != "POST" and row.confirmed_at is None:
        return _page(request, "confirm", event=event)
    state, registration = visitors.confirm(row, base_url=_base_url(request))
    if state == "invalid" or (registration is None and visitors.closed(event)):  # closed meanwhile
        return _page(request, "invalid", status=404)
    if registration is None:
        return _page(request, "refused", event=event, reason=state)
    return _page(request, "result", event=event, fresh=request.method == "POST", **_standing(registration))


def _closed(request, token: str, registration):
    """The private link while ``visitors.closed`` says the church or the event no longer offers this. Nothing of the
    event is shown (not its name, not the church's); the visitor can still cancel (asked first) or leave the waiting
    list: both only take away their own place. No e-mail leaves from here; accepting an offered place is not
    possible from here."""
    message = error = ""
    if request.method == "POST":
        action = request.POST.get("accion", "")
        try:
            if action == "cancelar" and registration.status in services.HOLDING:
                services.cancel(registration, None)
                message = "Cancelaste tu inscripción."
            elif action == "salir" and registration.status in services.LISTED:
                services.leave_waiting(registration, None)
                message = "Saliste de la lista de espera."
        except services.AgendaError as refused:
            error = refused.message
        registration = visitors.for_manage(token)
        if registration is None:
            return _page(request, "invalid", status=404)
    holding = registration.status in services.HOLDING
    can_cancel = holding and not services.has_started(registration.event)
    return _page(request, "closed", message=message, error=error, holding=holding, can_cancel=can_cancel,
                 listed=registration.status in services.LISTED, manage_path=visitors.manage_path(registration),
                 asking=request.method != "POST" and request.GET.get("cancelar") == "1" and can_cancel)


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def manage(request, token: str):
    registration = visitors.for_manage(token)
    if registration is None:
        return _page(request, "invalid", status=404)
    event = registration.event
    if visitors.closed(event):
        return _closed(request, token, registration)
    message = error = ""
    if request.method == "POST":
        action = request.POST.get("accion", "")
        try:
            if action == "cancelar":
                if registration.status in services.HOLDING:
                    services.cancel(registration, None, base_url=_base_url(request))
                    message = "Cancelaste tu inscripción. Tu lugar quedó libre."
            elif action == "aceptar":
                registration = services.accept_offer(registration, None, base_url=_base_url(request))
                message = "Aceptaste tu lugar."
            elif action == "salir":
                offered = services.offer_is_open(registration)
                services.leave_waiting(registration, None, base_url=_base_url(request))
                message = "Dejaste pasar el lugar. Se le ofrecerá a la siguiente persona de la lista." if offered else "Saliste de la lista de espera."
        except services.AgendaError as refused:
            error = refused.message
        registration = visitors.for_manage(token)
        if registration is None:
            return _page(request, "invalid", status=404)
    elif registration.is_offered and not services.offer_is_open(registration):
        # The deadline passed and nobody moved the list yet: release the place and offer it to the next one.
        services.advance(event, base_url=_base_url(request))
        registration = visitors.for_manage(token)
    asking = request.method != "POST" and request.GET.get("cancelar") == "1" and registration.status in services.HOLDING
    return _page(request, "mine", event=event, message=message, error=error, asking=asking, **_standing(registration))
