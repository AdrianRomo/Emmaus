from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

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
        if services.is_open(event) and event.feedback_given.filter(person=request.person).exists():
            return render(request, "feedback/thanks.html", {"event": event})
        raise Http404
    form = FeedbackForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.give(request.person, event, **form.cleaned_data)
        except services.FeedbackError as error:
            messages.info(request, error.message)
        return redirect("feedback:give", slug=event.slug)
    return render(request, "feedback/give.html", {"event": event, "form": form})


@church_admin_view
def results(request, slug: str):
    event = get_object_or_404(Event, church=request.church, slug=slug)
    return render(request, "feedback/results.html", {"event": event, "results": services.results(event)})
