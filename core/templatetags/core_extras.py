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
        return "badge badge-success"
    if value in {"open", "pending", "draft", "in_progress"}:
        return "badge badge-attention"
    if value in {"escalated", "pastoral_care_required", "sensitive"}:
        return "badge badge-care"
    if value in {"expired", "rejected", "cancelled", "closed"}:
        return "badge badge-muted"
    if value in {"normal"}:
        return "badge badge-soft"
    return "badge badge-soft"


@register.filter
def in_list(value, values):
    return value in values


@register.filter
def display_name(user, fallback="Not assigned"):
    if not user:
        return fallback
    full_name = ""
    get_full_name = getattr(user, "get_full_name", None)
    if callable(get_full_name):
        full_name = get_full_name()
    return full_name or getattr(user, "email", "") or fallback


@register.filter
def path_title(summary, fallback="No path assigned"):
    enrollment = getattr(summary, "enrollment", None)
    path = getattr(enrollment, "path", None)
    return getattr(path, "title", "") or fallback
