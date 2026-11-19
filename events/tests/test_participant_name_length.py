from events.models import EventParticipant

from .base import CampFixture


class NormalisedNameLengthTests(CampFixture):
    """A campero's name that grows when normalised is saved: its comparison form is cut to the column."""

    def test_a_name_of_ligatures_is_saved_with_its_comparison_form_cut(self):
        half = "\ufb03" * 79  # 159 characters with the space; 475 once normalised
        campero = EventParticipant.objects.create(event=self.event, number=98, full_name=f"{half} {half}", age=20, room="1", church_group="A")
        campero.refresh_from_db()
        self.assertEqual(len(campero.full_name), 159)
        self.assertEqual(len(campero.normalized_name), 160)
        campero.full_name = "Ana"
        campero.save(update_fields=["full_name"])
        campero.refresh_from_db()
        self.assertEqual(campero.normalized_name, "ana")
