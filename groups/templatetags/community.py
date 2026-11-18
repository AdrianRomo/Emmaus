from django import template
from django.utils import formats

register = template.Library()


LONG = r"j \d\e F \d\e Y"
SHORT = r"j \d\e F"


@register.filter
def date_range(start, end=None) -> str:
    """"13 al 15 de noviembre de 2026", "28 de noviembre al 2 de diciembre de 2026", or one day."""
    if not start:
        return ""
    # Spanish writes months in lower case; Django's es-MX names come capitalised.
    last = formats.date_format(end or start, LONG).lower()
    if not end or end == start:
        return last
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day} al {last}"
    first = formats.date_format(start, SHORT if start.year == end.year else LONG).lower()
    return f"{first} al {last}"


@register.filter
def weekday_date(value) -> str:
    """"jueves 19 de noviembre"."""
    return formats.date_format(value, "l " + SHORT).lower() if value else ""


@register.filter
def day_month(value) -> str:
    """"17 de noviembre"."""
    return formats.date_format(value, SHORT).lower() if value else ""


@register.filter
def long_date(value) -> str:
    """"17 de noviembre de 2026"."""
    return formats.date_format(value, LONG).lower() if value else ""


@register.simple_tag(takes_context=True)
def page_href(context, number, param="") -> str:
    """The address of page ``number`` of the list on this screen: the same filters, another page
    (partials/ui/pager.html). The first page carries no number, so it is the address people already know."""
    param = param or "pagina"
    query = context["request"].GET.copy()
    query.pop(param, None)
    if number and int(number) > 1:
        query[param] = number
    return "?" + query.urlencode()
