"""Formularios, for people signed in: answering a form of the church, the staff's screens, «Pendientes».

Every view goes through ``groups.views.community_view`` (signed in, the ``community`` module on for
the church of the request or 404, the person the account is in that church). A form is always
fetched inside ``request.church``: an id of another church is a 404, POSTs included, and a
submission, a question, a reader or a task is fetched under its form.

Who (``formularios.access``):

* answering a form of the church: any person of the church, while the form is open;
* creating, editing, publishing, closing, naming readers, exporting, "Agregar a personas",
  assigning a task: the administration (``_admin_form``; 403 otherwise);
* the answers of one form and working on its tasks: the administration and the form's readers
  (``_readable_form``: ``access.can_read``, asked on every request; 403 otherwise);
* «Pendientes»: everybody signed in gets the page; its rows are what ``formularios.queue`` allows.

The public form (no account) is ``formularios.public``.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.paging import paginate
from groups.views import church_admin_view, community_view
from people import flags
from people.models import Person

from . import access, queue, services
from .fields import AnswerForm
from .forms import FormSettingsForm, QuestionForm, TaskAssignForm, TaskNoteForm
from .models import Form, FormTask, Question, Submission

Kind = Question.Kind
KIND_HELP = {
    Kind.SHORT: "Una línea, hasta 200 caracteres.",
    Kind.LONG: "Un párrafo, hasta 1000 caracteres.",
    Kind.CHOICE: "La persona elige una de las opciones que escribas.",
    Kind.MULTI: "La persona marca las opciones que quiera.",
    Kind.YES_NO: "Dos opciones fijas: Sí y No.",
    Kind.DATE: "Un día del calendario.",
    Kind.CONTACT: "Nombre y correo o teléfono, con una frase que la persona acepta. Solo en formularios públicos, una vez.",
}


# ---- fetching -----------------------------------------------------------------------------------
def _form(request, form_id: int) -> Form:
    return get_object_or_404(Form.objects.select_related("church", "current_version", "task_owner"), pk=form_id, church=request.church)


def _admin_form(request, form_id: int) -> Form:
    """A form of this church, for its administration: 404 for another church's, 403 for anybody else."""
    form = _form(request, form_id)
    if not request.is_church_admin:
        raise PermissionDenied
    return form


def _readable_form(request, form_id: int) -> Form:
    """A form of this church whose answers this person reads right now."""
    form = _form(request, form_id)
    if not access.can_read(request.user, form, is_church_admin=request.is_church_admin):
        raise PermissionDenied
    return form


def _submission(form: Form, submission_id: int) -> Submission:
    return get_object_or_404(Submission.objects.select_related("version", "person"), pk=submission_id, form=form)


def _decorate(form: Form) -> Form:
    form.phase = services.phase(form)
    form.phase_label, form.phase_mark = services.PHASE_LABELS[form.phase]
    return form


def _public_url(request, form: Form) -> str:
    return request.build_absolute_uri(reverse("formulario:fill", args=[form.public_token]))


def _error(django_form, error: services.FormError) -> None:
    django_form.add_error(error.code if error.code in django_form.fields else None, error.message)


# ---- people of the church: answering ------------------------------------------------------------
def _open_member_forms(church):
    today = services.church_today(church)
    return Form.objects.filter(
        church=church, audience=Form.Audience.MEMBERS, status=Form.Status.PUBLISHED, current_version__isnull=False,
        opens_on__lte=today, closes_on__gte=today,
    ).select_related("current_version", "church").order_by("closes_on", "id")


def open_member_form_count(church) -> int:
    return _open_member_forms(church).count()


@community_view
def member_list(request):
    forms = list(_open_member_forms(request.church))
    for form in forms:
        form.answered = services.answered_recently(form, request.person)
    can_manage = request.is_church_admin or bool(access.readable_form_ids(request.user, request.church))
    return render(request, "formularios/list.html", {"forms": forms, "can_manage": can_manage})


@community_view
def fill(request, form_id: int):
    """A form of the church, for a person of the church. Not open (a draft, another audience, before
    its first day, after its last, closed): the same 404 as a form that does not exist."""
    form = _form(request, form_id)
    if form.is_public or not services.is_open(form):
        raise Http404
    version = form.current_version
    answer = AnswerForm(request.POST or None, schema=version.schema)
    changed = full = False
    if request.method == "POST":
        if request.POST.get("version") != str(version.pk):
            changed = True  # published again while this person was answering: they read the new one first
            answer = AnswerForm(schema=version.schema)
        elif answer.is_valid():
            try:
                services.submit(form, version.pk, answers=answer.answers, person=request.person, actor=request.user)
            except services.FormChanged:
                return redirect("formularios:fill", form.pk)  # shows the form as it is now, or its 404
            except services.FormFull:
                full = True
            else:
                return redirect("formularios:thanks", form.pk)
    return render(request, "formularios/fill.html", {
        "form_row": form, "version": version, "form": answer, "items": list(answer.bound_items()), "changed": changed, "full": full,
        "delete_after": services.delete_after(form), "answered": services.answered_recently(form, request.person),
    })


@community_view
def thanks(request, form_id: int):
    form = _form(request, form_id)
    if form.is_public or form.current_version is None or form.status == Form.Status.DRAFT:
        raise Http404
    return render(request, "formularios/thanks.html", {"title": form.current_version.title, "member": True})


# ---- «Pendientes» -------------------------------------------------------------------------------
@community_view
def pending(request):
    rows = queue.rows(request.user, request.church, request.person, is_church_admin=request.is_church_admin)
    return render(request, "formularios/queue.html", {"rows": rows})


# ---- the staff: the list ------------------------------------------------------------------------
@community_view
def manage(request):
    """The forms this person works with: all of them for the administration, the ones they read for
    a guide or a coordinator. Anybody else: 403."""
    forms = access.readable_forms(request.user, request.church, is_church_admin=request.is_church_admin)
    if not request.is_church_admin and not forms.exists():
        raise PermissionDenied
    page = paginate(request, forms.select_related("current_version", "church").order_by("-created_at", "-id"))
    for form in page:
        _decorate(form)
        form.answers = form.submissions.count()
        form.open_tasks = form.tasks.filter(status=FormTask.Status.OPEN).count()
    return render(request, "formularios/manage.html", {"forms": page})


@church_admin_view
def loose_tasks(request):
    tasks = queue.unowned_tasks(request.church).select_related("form", "owner").order_by("due_on", "id")
    page = paginate(request, tasks)
    today = services.church_today(request.church)
    for task in page:
        task.is_overdue = bool(task.due_on and task.due_on < today)
    return render(request, "formularios/loose_tasks.html", {"tasks": page})


@church_admin_view
def create(request):
    today = services.church_today(request.church)
    form = FormSettingsForm(request.POST or None, initial={"opens_on": today})
    if request.method == "POST" and form.is_valid():
        try:
            row = services.create_form(request.church, request.user, **form.values())
        except services.FormError as error:
            _error(form, error)
        else:
            messages.success(request, "Creaste el formulario como borrador. Ahora agrega sus preguntas.")
            return redirect("formularios:detail", row.pk)
    return render(request, "formularios/settings_form.html", {
        "form": form, "form_row": None, "warning": services.SENSITIVE_WARNING, "minors": services.MINORS_OUT,
    })


@community_view
def edit(request, form_id: int):
    row = _admin_form(request, form_id)
    if row.status == Form.Status.CLOSED:
        raise Http404
    initial = {
        "title": row.title, "purpose": row.purpose, "audience": row.audience, "opens_on": row.opens_on, "closes_on": row.closes_on,
        "retention_days": row.retention_days, "creates_task": row.creates_task, "task_due_days": row.task_due_days,
        "task_owner": str(row.task_owner_id or ""),
    }
    form = FormSettingsForm(request.POST or None, form=row, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_form(row, request.user, **form.values())
        except services.FormError as error:
            _error(form, error)
        else:
            messages.success(request, "Guardamos los cambios.")
            return redirect("formularios:detail", row.pk)
    return render(request, "formularios/settings_form.html", {
        "form": form, "form_row": row, "warning": services.SENSITIVE_WARNING, "minors": services.MINORS_OUT,
        "published": row.current_version_id is not None,
    })


@community_view
def detail(request, form_id: int):
    """The builder: what the form is, its questions as a draft, what is published, and the doors to
    everything else. The administration's."""
    row = _decorate(_admin_form(request, form_id))
    questions = list(row.questions.select_related("show_if_question"))
    return render(request, "formularios/detail.html", {
        "form_row": row, "questions": questions, "kinds": [(value, label, KIND_HELP[value]) for value, label in Kind.choices
                                                             if value != Kind.CONTACT or row.is_public],
        "unpublished": row.status != Form.Status.CLOSED and services.has_unpublished_changes(row),
        "problems": services.publish_problems(row) if row.status != Form.Status.CLOSED else [],
        "public_url": _public_url(request, row) if row.is_public and row.current_version_id else "",
        "is_open": services.is_open(row), "delete_after": services.delete_after(row),
        "readers": services.readers_of(row), "answers": row.submissions.count(),
        "warning": services.SENSITIVE_WARNING, "minors": services.MINORS_OUT, "max_questions": services.MAX_QUESTIONS,
        "owner_name": _owner_name(row.church, row.task_owner),
    })


def _owner_name(church, user) -> str:
    if user is None:
        return ""
    person = Person.objects.filter(church=church, user=user, merged_into__isnull=True).first()
    return person.full_name if person else (user.get_full_name() or "Una cuenta que ya no es de esta iglesia")


# ---- the staff: questions -----------------------------------------------------------------------
def _dropped(request, dropped: int) -> None:
    if dropped:
        messages.warning(request, "Una pregunta dependía de lo que cambiaste y ahora se muestra siempre. Revísala." if dropped == 1
                         else f"{dropped} preguntas dependían de lo que cambiaste y ahora se muestran siempre. Revísalas.")


@community_view
def question(request, form_id: int, question_id: int | None = None):
    row = _admin_form(request, form_id)
    if row.status == Form.Status.CLOSED:
        raise Http404
    existing = get_object_or_404(Question, pk=question_id, form=row) if question_id else None
    kind = existing.kind if existing else (request.POST.get("tipo") if request.method == "POST" else request.GET.get("tipo")) or ""
    if kind not in Kind.values:
        raise Http404
    initial = {}
    if existing:
        initial = {
            "label": existing.label, "required": existing.required, "options": "\n".join(existing.options),
            "ask_email": existing.ask_email, "ask_phone": existing.ask_phone, "consent": existing.consent,
            "show_if": f"{existing.show_if_question_id}|{existing.show_if_value}" if existing.show_if_question_id else "",
        }
    elif kind == Kind.CONTACT:
        initial = {"consent": services.default_consent(row.church), "ask_email": True, "label": "Tus datos para contactarte"}
    form = QuestionForm(request.POST or None, form=row, kind=kind, question=existing, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            _saved, dropped = services.save_question(row, request.user, question=existing, kind=kind, **form.values())
        except services.FormError as error:
            _error(form, error)
        else:
            messages.success(request, "Guardamos la pregunta en el borrador. Quien responde no la ve hasta que publiques.")
            _dropped(request, dropped)
            return redirect("formularios:detail", row.pk)
    return render(request, "formularios/question_form.html", {
        "form": form, "form_row": row, "question": existing, "kind": kind, "kind_label": Kind(kind).label,
        "kind_help": KIND_HELP[kind], "warning": services.SENSITIVE_WARNING,
    })


@community_view
@require_POST
def question_delete(request, form_id: int, question_id: int):
    row = _admin_form(request, form_id)
    get_object_or_404(Question, pk=question_id, form=row)
    try:
        dropped = services.delete_question(row, request.user, question_id)
    except services.FormError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, "Quitamos la pregunta del borrador.")
        _dropped(request, dropped)
    return redirect("formularios:detail", row.pk)


@community_view
@require_POST
def question_move(request, form_id: int, question_id: int):
    row = _admin_form(request, form_id)
    get_object_or_404(Question, pk=question_id, form=row)
    direction = request.POST.get("hacia", "")
    if direction not in ("arriba", "abajo"):
        raise Http404
    try:
        _dropped(request, services.move_question(row, request.user, question_id, up=direction == "arriba"))
    except services.FormError as error:
        messages.error(request, error.message)
    return redirect(reverse("formularios:detail", args=[row.pk]) + "#preguntas")


# ---- the staff: publishing and closing ----------------------------------------------------------
@community_view
def publish(request, form_id: int):
    """The form as people will see it, then one button. The button carries a token of everything
    this page states: if the draft changed meanwhile, the page is shown again instead of publishing."""
    row = _admin_form(request, form_id)
    if row.status == Form.Status.CLOSED:
        raise Http404
    stale = False
    if request.method == "POST":
        try:
            _row, new = services.publish(row, request.user, request.POST.get("visto", ""))
        except services.StalePreview:
            stale = True
            row = _admin_form(request, form_id)
        except services.FormError as error:
            messages.error(request, error.message)
            return redirect("formularios:detail", row.pk)
        else:
            messages.success(request, "Publicaste el formulario." if new else "El formulario ya estaba publicado con estas preguntas.")
            return redirect("formularios:detail", row.pk)
    preview = AnswerForm(schema=services.draft_schema(row))
    return render(request, "formularios/publish.html", {
        "form_row": row, "form": preview, "items": list(preview.bound_items()), "problems": services.publish_problems(row),
        "token": services.preview_token(row), "stale": stale, "delete_after": services.delete_after(row),
        "republish": row.current_version_id is not None,
    })


@community_view
def close(request, form_id: int):
    row = _admin_form(request, form_id)
    if row.status != Form.Status.PUBLISHED:
        raise Http404
    if request.method == "POST":
        try:
            services.close_form(row, request.user)
        except services.FormError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, "Cerraste el formulario. Ya no recibe respuestas.")
        return redirect("formularios:detail", row.pk)
    return render(request, "groups/confirm.html", {
        "title": f"¿Cerrar «{row.title}»?",
        "text": f"Dejará de recibir respuestas hoy y no se puede volver a abrir. Sus respuestas se borran {row.retention_days} días después.",
        "action": "Sí, cerrar el formulario", "back_url": reverse("formularios:detail", args=[row.pk]),
    })


# ---- the staff: readers -------------------------------------------------------------------------
@community_view
def readers(request, form_id: int):
    row = _admin_form(request, form_id)
    if request.method == "POST":
        try:
            if request.POST.get("quitar", "").isdigit():
                if not services.remove_reader(row, request.user, int(request.POST["quitar"])):
                    raise Http404
                messages.success(request, "Quitamos ese lector. Los pendientes que tenía de este formulario quedaron sin responsable.")
            else:
                services.add_reader(row, request.user, request.POST.get("lector", ""))
                messages.success(request, "Agregamos el lector. Emmaus no le avisa: avísale tú.")
        except services.FormError as error:
            messages.error(request, error.message)
        return redirect("formularios:readers", row.pk)
    current = services.readers_of(row)
    taken = {f"{item['row'].scope_type}:{item['row'].scope_id}" for item in current}
    return render(request, "formularios/readers.html", {
        "form_row": row, "readers": current, "choices": [choice for choice in services.scope_choices(row.church) if choice[0] not in taken],
    })


# ---- the staff: answers and their tasks ---------------------------------------------------------
@community_view
def submissions(request, form_id: int):
    row = _decorate(_readable_form(request, form_id))
    page = paginate(request, row.submissions.select_related("version", "person", "task", "task__owner"))
    today = services.church_today(row.church)
    for submission in page:
        task = getattr(submission, "task", None)
        submission.task_row = task
        if task is not None:
            task.is_overdue = bool(task.is_open and task.due_on and task.due_on < today)
            task.is_mine = task.owner_id == request.user.pk
    return render(request, "formularios/submissions.html", {
        "form_row": row, "submissions": page, "total": row.submissions.count(), "delete_after": services.delete_after(row),
    })


@community_view
def submission(request, form_id: int, submission_id: int):
    row = _readable_form(request, form_id)
    found = _submission(row, submission_id)
    task = FormTask.objects.filter(submission=found).select_related("owner").first()
    if task is not None:
        task.is_overdue = bool(task.is_open and task.due_on and task.due_on < services.church_today(row.church))
        task.owner_name = _owner_name(row.church, task.owner)
    can_add = bool(
        request.is_church_admin and row.is_public and found.person_id is None and found.contact_first_name
        and flags.enabled(row.church, "people")
    )
    return render(request, "formularios/submission.html", {
        "form_row": row, "submission": found, "answers": services.describe(found), "task": task,
        "note_form": TaskNoteForm(initial={"note": task.note}) if task else None, "can_add": can_add,
        "delete_after": services.delete_after(row),
    })


@community_view
@require_POST
def task(request, form_id: int, submission_id: int):
    """Mark the task done, open it again, or write its note: whoever reads the form's answers."""
    row = _readable_form(request, form_id)
    found = get_object_or_404(FormTask, submission=_submission(row, submission_id), form=row)
    action = request.POST.get("accion", "")
    try:
        if action == "hecho":
            services.set_task_status(found, request.user, FormTask.Status.DONE)
            messages.success(request, "Marcaste el pendiente como hecho.")
        elif action == "abrir":
            services.set_task_status(found, request.user, FormTask.Status.OPEN)
            messages.success(request, "El pendiente está abierto otra vez.")
        elif action == "nota":
            services.set_task_note(found, request.user, request.POST.get("note", ""))
            messages.success(request, "Guardamos la nota.")
        else:
            raise Http404
    except services.FormError as error:
        messages.error(request, error.message)
    return redirect("formularios:submission", row.pk, submission_id)


@community_view
def task_assign(request, form_id: int, submission_id: int):
    row = _admin_form(request, form_id)
    found = get_object_or_404(FormTask.objects.select_related("owner"), submission=_submission(row, submission_id), form=row)
    form = TaskAssignForm(request.POST or None, form=row, initial={"owner": str(found.owner_id or ""), "due_on": found.due_on})
    if request.method == "POST" and form.is_valid():
        try:
            services.assign_task(found, request.user, form.owner_user(), form.cleaned_data["due_on"])
        except services.FormError as error:
            _error(form, error)
        else:
            messages.success(request, "Guardamos quién da seguimiento y para cuándo.")
            return redirect("formularios:submission", row.pk, submission_id)
    return render(request, "formularios/task_assign.html", {"form": form, "form_row": row, "submission_id": submission_id})


@community_view
def add_person(request, form_id: int, submission_id: int):
    """"Agregar a personas": first whoever of the church already has that e-mail or phone, then the
    one button that makes a new record. The administration's, in a church that uses Personas."""
    row = _admin_form(request, form_id)
    found = _submission(row, submission_id)
    back = reverse("formularios:submission", args=[row.pk, found.pk])
    if not flags.enabled(row.church, "people") or not row.is_public or not found.contact_first_name:
        raise Http404
    if found.person_id is not None:
        return redirect(back)
    similar = services.similar_people(found)
    if request.method == "POST":
        existing = None
        chosen = request.POST.get("persona", "")
        if chosen:
            if not chosen.isdigit():
                raise Http404
            existing = get_object_or_404(Person, pk=int(chosen), church=request.church, merged_into__isnull=True)
        try:
            person = services.add_to_people(found, request.user, existing=existing)
        except services.FormError as error:
            messages.error(request, error.message)
            return redirect("formularios:add_person", row.pk, found.pk)
        messages.success(request, f"{person.full_name} está en las personas de la iglesia. No se le dio acceso a Emmaus ni se le envió nada.")
        return redirect(back)
    return render(request, "formularios/add_person.html", {"form_row": row, "submission": found, "similar": similar, "back": back})


@community_view
@require_POST
def export(request, form_id: int):
    """The answers of this form as a CSV file: the administration's, and written down in the activity log."""
    row = _admin_form(request, form_id)
    # Excel opens UTF-8 correctly with a BOM, as the camp's exports do.
    response = HttpResponse("\ufeff" + services.export_csv(row, request.user), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="formulario-{row.pk}-respuestas.csv"'
    response["Cache-Control"] = "no-store"
    return response
