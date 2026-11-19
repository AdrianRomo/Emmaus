from datetime import datetime, timezone as dt_timezone

from django.template import Context, Template
from django.test import SimpleTestCase, override_settings


@override_settings(TIME_ZONE="America/Mexico_City", USE_TZ=True)
class DatesOfMomentsTests(SimpleTestCase):
    """A moment late in the evening is a date of that day here, not of the next day in UTC."""

    def test_the_date_filters_read_a_moment_in_the_local_zone(self):
        moment = datetime(2026, 11, 19, 2, 50, tzinfo=dt_timezone.utc)  # 20:50 on the 18th in Mexico City
        out = Template("{% load community %}{{ m|long_date }}|{{ m|day_month }}|{{ m|weekday_date }}").render(Context({"m": moment}))
        self.assertEqual(out, "18 de noviembre de 2026|18 de noviembre|miércoles 18 de noviembre")
