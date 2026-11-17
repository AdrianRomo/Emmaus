"""``@module_required("community")``: the view exists only while the church uses the module (decision 0004)."""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import Http404

from core.authz import current_church

from .flags import MODULES, enabled


def module_required(module: str):
    """Signed-in church pages. Off (or no church): 404 for every role, before any authorization
    check, so an off module reveals neither itself nor an object. On: ``request.church`` is set."""
    if module not in MODULES:
        raise ValueError(f"Unknown module {module!r}.")

    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            church = current_church(request)
            if not enabled(church, module):
                raise Http404
            request.church = church
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
