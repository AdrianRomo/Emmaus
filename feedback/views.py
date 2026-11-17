from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from django.views.decorators.http import require_POST

from care import services as care
from care.models import FollowUp
from events.models import Event
from groups.views import church_admin_view, community_view

from . import services
from .forms import FeedbackForm


@community_view
def give(request, slug: str):
    """The three questions. 404 for whoever may not answer (was not there, another church, too late):
    the page does not say which, and somebody who already answered is thanked."""
    event = get_object_or_404(Event, church=request.church, slug=slug)
    if not services.can_give(request.person, event):
        if services.is_open(event) and services.has_given(request.person, event):
            return render(request, "feedback/thanks.html", {"event": event, "asked": _asked_contact(request)})
        raise Http404
    form = FeedbackForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.give(request.person, event, **form.cleaned_data)
        except services.FeedbackError as error:
            messages.info(request, error.message)
        return redirect("feedback:give", slug=event.slug)
    return render(request, "feedback/give.html", {"event": event, "form": form})


def _asked_contact(request) -> bool:
    return FollowUp.objects.filter(
        church=request.church, person=request.person, reason=FollowUp.Reason.CONTACT, status__in=FollowUp.OPEN
    ).exists()


@community_view
@require_POST
def contact(request):
    """"Quiero que alguien de mi iglesia me busque": apart from the opinion and not tied to any
    event. It opens a follow-up without an owner, which the administration assigns."""
    if not _asked_contact(request):
        care.create(request.church, request.user, person=request.person, reason=FollowUp.Reason.CONTACT)
    messages.success(request, "Listo. Alguien de tu iglesia te va a buscar.")
    return redirect("community:home")


@church_admin_view
def results(request, slug: str):
    event = get_object_or_404(Event, church=request.church, slug=slug)
    return render(request, "feedback/results.html", {"event": event, "results": services.results(event)})
