from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    if mapping is None:
        return None
    return mapping.get(key)


@register.filter
def status_class(value):
    value = str(value or "").lower()
    if value in {"answered", "completed", "accepted", "approved"}:
        return "chip chip--done"
    if value in {"open", "pending", "draft", "in_progress"}:
        return "chip chip--progress"
    if value in {"escalated", "pastoral_care_required", "sensitive"}:
        return "chip chip--alert"
    if value in {"expired", "rejected", "cancelled", "closed"}:
        return "chip chip--pending"
    return "chip chip--info"


@register.filter
def in_list(value, values):
    return value in values


@register.filter
def display_name(user, fallback="Sin asignar"):
    if not user:
        return fallback
    full_name = ""
    get_full_name = getattr(user, "get_full_name", None)
    if callable(get_full_name):
        full_name = get_full_name()
    return full_name or getattr(user, "email", "") or fallback


@register.filter
def path_title(summary, fallback="Sin ruta asignada"):
    enrollment = getattr(summary, "enrollment", None)
    path = getattr(enrollment, "path", None)
    return getattr(path, "title", "") or fallback
