"""One decision for every foreign key to a person or an account (issue #98; the model is
``people/tests/test_merge_tables.py``)."""

from django.apps import apps
from django.test import SimpleTestCase

from cuenta_datos import registry


class InventoryTests(SimpleTestCase):
    def test_every_relation_to_a_person_or_an_account_has_a_decision(self):
        """A new foreign key to ``Person`` or to the account model fails here until somebody says whether its
        rows are the person's own (a category) or the church's records (``NOT_THEIRS``, with the reason)."""
        self.assertEqual(registry.undecided_relations(), [])
        decided = set(registry.registered()) | set(registry.NOT_THEIRS)
        self.assertEqual(sorted(decided), registry.relations_to_people())  # and nothing decided that no longer exists
        self.assertGreaterEqual(len(registry.relations_to_people()), 120)

    def test_nothing_is_both_the_persons_and_the_churchs(self):
        self.assertEqual(set(registry.registered()) & set(registry.NOT_THEIRS), set())
        listed = [relation for category in registry.CATEGORIES for relation in category.relations]
        self.assertEqual(len(listed), len(set(listed)))  # one category answers for a relation

    def test_a_relation_nobody_decided_about_is_reported(self):
        known = registry.NOT_THEIRS.pop(("care.FollowUp", "person"))
        self.addCleanup(registry.NOT_THEIRS.__setitem__, ("care.FollowUp", "person"), known)
        self.assertEqual(registry.undecided_relations(), [("care.FollowUp", "person")])

    def test_every_reason_is_written_and_every_relation_names_a_real_column(self):
        for (label, name), reason in registry.NOT_THEIRS.items():
            self.assertGreater(len(reason), 40, (label, name))
            apps.get_model(label)._meta.get_field(name)
        for category in registry.CATEGORIES:
            self.assertTrue(category.title and category.why and category.icon, category.key)
            for label, name in category.relations:
                apps.get_model(label)._meta.get_field(name)

    def test_what_is_written_about_a_person_is_never_a_category(self):
        """The records the brief names as the church's own stay out, whatever else changes."""
        for relation in [
            ("care.FollowUp", "person"), ("people.PersonTag", "person"), ("core.PastoralNote", "participant"),
            ("core.FollowUpRequest", "participant"), ("people.ChurchAuditEvent", "actor"), ("events.AuditEvent", "actor"),
            ("events.Answer", "answered_by"), ("events.Interview", "conducted_by"), ("events.FollowUp", "created_by"),
            ("oracion.PrayerRequest", "only_reader"),
        ]:
            self.assertIn(relation, registry.NOT_THEIRS)

    def test_only_the_two_kinds_of_text_are_optional_and_private_categories_are_never_counted_for_the_staff(self):
        self.assertEqual({c.key for c in registry.CATEGORIES if c.text_kind}, {"oracion", "formularios"})
        for key in ("asistencia", "avisos", "lectura", "oracion", "discipulado"):
            self.assertFalse(registry.BY_KEY[key].staff_count, key)
