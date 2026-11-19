"""The public home of the church as a kind of content a pastor approves (``resources.approvals``).

What the approval covers is what the church wrote for the page (``services.authored_digest``). What the
pastor reads before approving is the whole review list, the events and the resources included, and the
form carries the fingerprint of that list: if anything in it changed meanwhile, nothing is approved.
"""

from django.urls import reverse

from resources.approvals import ContentKind

from . import services
from .models import PublicHome


class HomeKind(ContentKind):
    key = services.HOME_KIND
    label = "Página pública"
    words = "la página pública de la iglesia"
    preview_template = "sitio/_approval_home.html"

    def get(self, church, object_id, *, lock=False):
        found = PublicHome.objects.filter(church=church, pk=object_id)
        found = found.select_for_update(of=("self",)) if lock else found
        return found.select_related("church").first()

    def many(self, church, ids):
        return {row.pk: row for row in PublicHome.objects.filter(church=church, pk__in=ids).select_related("church")}

    def published(self, church):
        return list(PublicHome.objects.filter(church=church, published=True).select_related("church"))

    def digest(self, obj):
        return services.authored_digest(obj.church, obj)

    def revision(self, obj):
        return services.page_content(obj.church, obj).digest()

    def is_published(self, obj):
        return obj.published

    def publish(self, obj, actor):
        services.go_public(obj, actor)

    def title(self, obj):
        return "Página pública de la iglesia"

    def line(self, obj):
        return "Lo que lee cualquier persona con el enlace, sin cuenta."

    def preview(self, obj):
        return {"content": services.page_content(obj.church, obj), "home": obj, "church": obj.church}

    def edit_url(self, obj):
        return reverse("sitio:edit")

    def published_note(self, obj):
        return "Cualquier persona con el enlace puede leerla."
