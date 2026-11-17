"""Panorama: how the church is doing, in totals, for who looks after all of it.

Open to the church's pastor and administration (``overview.access``); 404 for everybody else and
while the ``community`` module is off. The page counts; it never names a person, and none of its
links leads to a list of who came. Named attendance for pastors is deliberately not built.
"""

from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from core.authz import Action, has_scope
from people.decorators import module_required
from people.services import ensure_own_person

from . import services
from .access import can_see_overview


@module_required("community")
def panorama(request):
    church = request.church
    if not can_see_overview(request.user, church):
        raise Http404
    viewer = ensure_own_person(request.user, church)
    context = services.build(church, viewer, timezone.localdate())
    context["is_church_admin"] = has_scope(request.user, church, "church", church.pk, Action.MANAGE)
    return render(request, "overview/panorama.html", context)
