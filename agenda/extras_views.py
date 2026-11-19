"""Two places of an event for the church's administration (issue #93, wave 12): the questions it asks at sign-up
(``agenda.questions``) and the headcount of the gathering (``agenda.headcounts``).

Every view goes through ``church_admin_view`` (church ``manage``) and fetches the event inside ``request.church``:
the address of another church's event, or of another event's question, is a 404."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from events.models import Event
from groups.views import church_admin_view
from people import policy

from . import headcounts, questions, series, services
from .models import EventHeadcount, EventPage, EventQuestion, RegistrationAnswer


def _event(request, slug: str) -> Event:
    return get_object_or_404(Event, church=request.church, slug=slug)


def _page_url(event: Event) -> str:
    return reverse("agenda:page_edit", args=[event.slug])


def _questions_url(event: Event) -> str:
    return reverse("agenda:questions", args=[event.slug])


@church_admin_view
def questions_list(request, slug: str):
    event = _event(request, slug)
    rows = list(EventQuestion.objects.filter(event=event))
    minors = questions.protects_minors(event)
    for row in rows:
        row.not_asked = minors and row.kind == EventQuestion.Kind.TEXT
    full = len(rows) >= questions.MAX_QUESTIONS
    form = questions.QuestionForm(request.POST or None, event=event, initial={"kind": EventQuestion.Kind.YES_NO})
    if request.method == "POST" and not full and form.is_valid():
        try:
            questions.save_question(event, request.user, **form.cleaned_data)
        except services.AgendaError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, "Agregaste la pregunta.")
            return redirect(_questions_url(event))
    return render(request, "agenda/questions.html", {
        "event": event, "questions": rows, "form": form, "full": full, "maximum": questions.MAX_QUESTIONS, "minors": minors,
        "warning": questions.SENSITIVE, "back": _page_url(event),
    })


@church_admin_view
def question_edit(request, slug: str, question_id: int):
    event = _event(request, slug)
    item = get_object_or_404(EventQuestion, event=event, pk=question_id)
    form = questions.QuestionForm(request.POST or None, instance=item, event=event)
    if request.method == "POST" and form.is_valid():
        try:
            questions.save_question(event, request.user, EventQuestion.objects.get(pk=item.pk), **form.cleaned_data)
        except services.AgendaError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, "Guardamos la pregunta.")
            return redirect(_questions_url(event))
    return render(request, "agenda/item_form.html", {
        "event": event, "form": form, "title": "Editar la pregunta de la inscripción", "back": _questions_url(event),
        "back_label": "Volver a las preguntas de la inscripción",
        "delete_url": reverse("agenda:question_delete", args=[event.slug, item.pk]), "delete_label": "Quitar esta pregunta",
    })


@church_admin_view
def question_delete(request, slug: str, question_id: int):
    event = _event(request, slug)
    item = get_object_or_404(EventQuestion, event=event, pk=question_id)
    if request.method == "POST":
        questions.delete_question(item, request.user)
        messages.success(request, "Quitaste la pregunta y sus respuestas.")
        return redirect(_questions_url(event))
    answers = RegistrationAnswer.objects.filter(question=item).count()
    lost = "Nadie la ha respondido todavía." if not answers else (
        "Se borra también la respuesta que ya dio 1 persona. No se puede recuperar." if answers == 1
        else f"Se borran también las respuestas que ya dieron {answers} personas. No se pueden recuperar.")
    return render(request, "groups/confirm.html", {
        "title": "¿Quieres quitar esta pregunta?", "text": f"«{item.text}». {lost}", "action": "Sí, quitar la pregunta",
        "back_url": _questions_url(event),
    })


@church_admin_view
def headcount(request, slug: str):
    """"¿Cuántos vinimos?": the count of one gathering, typed or corrected. Numbers only."""
    event = _event(request, slug)
    row = EventHeadcount.objects.filter(event=event).first()
    started = services.has_started(event)
    occurrence = series.occurrence_of(event)
    back = reverse("agenda:series_admin", args=[occurrence.series_id]) if occurrence is not None else _page_url(event)
    initial = {"total": row.total, "adults": row.adults, "children": row.children} if row is not None else None
    form = headcounts.HeadcountForm(request.POST or None, initial=initial)
    if request.method == "POST" and started and form.is_valid():
        try:
            headcounts.record(event, request.user, **form.cleaned_data)
        except services.AgendaError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, "Guardamos el conteo." if row is None else "Corregimos el conteo.")
            return redirect(back)
    return render(request, "agenda/headcount.html", {
        "event": event, "form": form, "row": row, "started": started, "back": back,
        "back_label": "Volver a la serie" if occurrence is not None else "Volver a la página del evento",
        "page": EventPage.objects.filter(event=event).first(), "minimum": policy.min_reporting_size(request.church),
    })
