from django import template

from core import tenancy

register = template.Library()


@register.simple_tag(takes_context=True)
def team_screens_open(context) -> bool:
    """Whether "Invitar a alguien" and "Usuarios" would open for this account in the church of this request.
    Those two are discipleship-era screens that act in the church of the account's first role (``core/access.py``):
    the same rule by which the shell hides "Configuración" (``core.context_processors``, ``Resolution.elsewhere``).
    Only the administration is offered them."""
    request = context.get("request")
    if request is None or not getattr(request, "is_church_admin", False):
        return False
    return not tenancy.resolve(request).elsewhere
