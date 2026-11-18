"""``/iglesia/<slug>/``: the public home of one church (issue #87, a prototype).

The same rules as the public page of an event (``agenda.public``, decision 0010 phase A): anybody
with the address reads it without an account; it shows only what the administration published on
purpose; it never names or counts a person (the one name it may carry is who preached a resource
the administration marked as public); and everything that is not such a page (no church with that
address, an inactive one, the module off, the home not published) is the same 404, so the address
is no oracle. Who is signed in is never looked at, nothing is written, there is no form and no
throttle, so reading leaves no cookie behind.
"""

from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_safe

from . import services


def public_path(church) -> str:
    return reverse("iglesia:home", args=[church.slug])


def page_context(content: services.Content, **extra) -> dict:
    return {
        "content": content,
        "church": content.church,
        "home": content.home,
        # The page wears its own church, whoever reads it: never the reader's church or shell.
        "brand_church": content.church,
        "theme_church": content.church,
        "ui_v2": True,
        **extra,
    }


@require_safe
def home(request, slug: str):
    found = services.public_home(slug)
    if found is None:
        raise Http404
    return render(request, "sitio/public.html", page_context(services.page_content(found.church, found)))
