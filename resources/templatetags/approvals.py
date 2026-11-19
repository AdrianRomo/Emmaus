from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def team_screens_open(context) -> bool:
    """Whether "Invitar a alguien" and "Usuarios" would open for this account in the church of this request.
    Both act in the church of the request (``core.views.role_required``), so they open for its administration,
    the only one offered them."""
    request = context.get("request")
    return request is not None and bool(getattr(request, "is_church_admin", False))
