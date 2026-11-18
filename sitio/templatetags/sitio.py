"""For the administration's list of resources: which of the rows on the screen are on the public page.

    {% load sitio %}
    {% public_media_ids items as public_ids %}
    {% if item.pk in public_ids %}…{% endif %}

One query per page of the list. ``resources`` knows nothing of the public page: the mark lives here.
"""

from django import template

from sitio import services

register = template.Library()


@register.simple_tag
def public_media_ids(items) -> set[int]:
    return services.public_ids(list(items))
