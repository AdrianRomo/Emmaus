"""The directory between members: the list, a person's own choice, and the church's switch.

Every view is behind ``groups.views.community_view`` (signed in, the ``community`` module on for the
church of the request, and a person of that church: 404 otherwise) and then asks its own rule again,
on every request, never from a link that was or was not shown:

* **the list** (``directory``): ``people.directory.may_read``. Everybody else gets the 404 of a page
  that does not exist: who has not chosen to appear, a visitor, somebody with no stage, a known minor,
  anybody of another church, and everybody while the church's switch is off.
* **"Aparecer en el directorio"** (``mine``): the person of the request, about themselves. No address,
  field or form here names another person, so there is nothing an administrator could post for somebody.
* **the switch** (``policy``): who holds the church role of pastor in this church; the administration
  gets a 404.

No view exports, lists every address or builds a link with more than one person in it. The list is
counted against the account on every view (``directory.VIEW_LIMIT``) and never cached.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render

from core.paging import paginate
from events.services import throttle
from groups.views import community_view

from . import directory as rules
from .forms import DirectoryPolicyForm

QUERY_MAX = 80


def _no_store(response):
    response["Cache-Control"] = "no-store"
    return response


@community_view
def directory(request):
    """The people of this church who chose to appear, searched by name only, a short page at a time."""
    church, user = request.church, request.user
    if not rules.may_read(user, church, request.person):
        raise Http404
    context = {
        "is_pastor": rules.is_pastor(user, church),
        "in_it": rules.is_listed(request.person) and rules.may_take_part(request.person),
        "may_choose": rules.shows_choice(church, request.person),
    }
    wait = throttle.consume(f"directory:{church.pk}:{user.pk}", **rules.VIEW_LIMIT)
    if wait:
        minutes = max(1, -(-wait // 60))
        context["wait"] = f"Espera {minutes} minuto{'' if minutes == 1 else 's'} y vuelve a abrirlo."
        return _no_store(render(request, "people/directory.html", context, status=429))
    query = " ".join(request.GET.get("q", "").split())[:QUERY_MAX]
    page = paginate(request, rules.search(church, query), per_page=rules.PAGE_SIZE)
    context.update({"page": page, "rows": rules.rows_for(church, page.object_list), "query": query})
    return _no_store(render(request, "people/directory.html", context))


@community_view
def mine(request):
    """"Aparecer en el directorio": the person's own choice. Choosing is two steps (what to show, then the
    preview "Así te verán" with what is accepted); leaving is one press and needs nothing else."""
    church, person = request.church, request.person
    if not rules.shows_choice(church, person):
        raise Http404
    offered = rules.is_on(church) and rules.may_take_part(person)
    step = request.POST.get("paso", "") if request.method == "POST" else ""
    if request.method == "POST":
        if step == "salir":
            try:
                left = rules.opt_out(church, person, request.user)
            except rules.DirectoryError as error:
                messages.error(request, error.message)
                return redirect("community:profile")
            if left:
                messages.success(request, "Dejaste de aparecer en el directorio. Borramos lo que habías elegido mostrar.")
            return redirect("directorio:mine" if rules.shows_choice(church, person) else "community:profile")
        if step not in ("ver", "confirmar", "cambiar") or not offered:
            raise Http404
        fields = rules.clean_fields(request.POST.getlist("mostrar"))
        if step == "confirmar":
            try:
                rules.opt_in(church, person, request.user, fields=fields, token=request.POST.get("visto", ""))
            except rules.DirectoryError as error:
                if error.code != "stale":
                    messages.error(request, error.message)
                    return redirect("community:profile")
                step, stale = "ver", error.message
            else:
                shown = ", ".join(["tu nombre", *(rules.FIELD_LABELS[name] for name in fields)])
                messages.success(request, f"Ya apareces en el directorio de tu iglesia con: {shown}.")
                return redirect("directorio:mine")
        else:
            stale = ""
        if step == "ver":
            person.refresh_from_db()
            row = rules.preview(church, person, fields)
            return _no_store(render(request, "people/directory_preview.html", {
                "row": row, "fields": fields, "token": rules.preview_token(person, fields, row), "stale": stale,
                "empty": _chosen_but_empty(fields, row), "version": rules.WORDING_VERSION,
            }))
    else:
        fields = None
    entry = rules.entry_of(person)
    current = bool(entry and entry.listed and entry.wording_version == rules.WORDING_VERSION)
    if fields is None:  # what the person had chosen, kept for the form even when a new wording hides them
        fields = tuple(name for name in rules.FIELDS if entry is not None and getattr(entry, f"show_{name}"))
    entry_row = None
    if entry is not None and current and offered:
        entry.person = person
        entry_row = rules.rows_for(church, [entry])[0]
    return _no_store(render(request, "people/directory_mine.html", {
        "offered": offered, "switch_on": rules.is_on(church), "row": entry_row,
        "has_choice": bool(entry and entry.listed), "outdated": bool(entry and entry.listed and not current),
        "options": [{"name": name, "label": label, "checked": name in fields} for name, label in OPTION_LABELS],
    }))


# The four choices as the form names them (``mostrar=<name>``); the name is always shown and is not one of them.
OPTION_LABELS = (
    ("phone", "Mi teléfono"), ("email", "Mi correo"), ("groups", "Los grupos a los que pertenezco"),
    ("teams", "Los equipos en los que sirvo"),
)
_EMPTY = {
    "phone": "Marcaste tu teléfono, pero no tienes uno anotado: no aparece nada. Si lo agregas en «Mis datos», aparecerá.",
    "email": "Marcaste tu correo, pero no tienes uno anotado: no aparece nada. Si lo agregas en «Mis datos», aparecerá.",
    "groups": "Marcaste tus grupos, pero hoy no estás en ninguno: no aparece nada. Si entras a uno, aparecerá.",
    "teams": "Marcaste tus equipos, pero hoy no sirves en ninguno: no aparece nada. Si entras a uno, aparecerá.",
}


def _chosen_but_empty(fields, row) -> list[str]:
    return [_EMPTY[name] for name in fields if not getattr(row, name)]


@community_view
def policy(request):
    """What the church offers its people, and the switch. For a pastor of this church only."""
    church = request.church
    if not rules.is_pastor(request.user, church):
        raise Http404
    form = DirectoryPolicyForm(request.POST if request.method == "POST" and request.POST.get("paso") == "encender" else None)
    if request.method == "POST":
        step = request.POST.get("paso", "")
        if step == "apagar":
            try:
                changed = rules.switch_off(church, request.user)
            except rules.DirectoryError:  # the role ended between the two lines above
                raise Http404 from None
            if changed:
                messages.success(request, "Apagaste el directorio entre miembros. Nadie lo ve; lo que cada persona eligió se queda guardado.")
            return redirect("directorio:policy")
        if step != "encender":
            raise Http404
        if form.is_valid():
            try:
                rules.switch_on(church, request.user, accepted=form.cleaned_data["accepted"], wording=request.POST.get("texto", ""))
            except rules.DirectoryError as refused:
                form.add_error("accepted" if refused.code == "accepted" else None, refused.message)
            else:
                messages.success(request, "Encendiste el directorio entre miembros. Quedó anotado que tú aceptaste lo que la iglesia ofrece.")
                return redirect("directorio:policy")
    return render(request, "people/directory_policy.html", {"form": form, "state": rules.state(church), "version": rules.WORDING_VERSION})
