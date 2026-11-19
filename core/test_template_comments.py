"""Django's {# … #} comment ends on its own line: one that spans lines is printed as text on the page (it reached an
administration's home once). Longer notes use {% comment %}."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class NoMultilineShortCommentsTests(SimpleTestCase):
    def test_every_short_comment_closes_on_its_line(self):
        roots = [Path(d) for d in settings.TEMPLATES[0]["DIRS"]]
        offenders = []
        for root in roots:
            for path in root.rglob("*.html"):
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    rest = line
                    while "{#" in rest:
                        rest = rest[rest.index("{#") + 2:]
                        if "#}" not in rest:
                            offenders.append(f"{path.relative_to(root)}:{number}")
                            break
                        rest = rest[rest.index("#}") + 2:]
        self.assertEqual(offenders, [], "use {% comment %} for a note longer than one line")
