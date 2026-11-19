"""What a merge does with what wave 10 added: tags, the stage, the key dates and imported records."""

from datetime import date

from django.urls import reverse

from people import imports, merge, services, tags
from people.models import ChurchAuditEvent, PeopleImport, Person, PersonTag

from .merge_base import MergeFixture


class TagMergeTests(MergeFixture):
    def test_the_survivor_ends_with_the_union_and_the_undo_takes_back_what_moved(self):
        coro, llaves, ujieres = (tags.create_tag(self.church, name, self.admin) for name in ("Coro", "Llaves", "Ujieres"))
        tags.set_tags(self.church, self.keep.pk, {coro.pk, llaves.pk}, self.admin, seen=set())
        tags.set_tags(self.church, self.dup.pk, {llaves.pk, ujieres.pk}, self.admin, seen=set())
        preview = merge.build_preview(self.church, self.keep, self.dup)
        self.assertIn("1 etiqueta", preview.move_lines)
        self.assertIn("1 etiqueta", preview.stay_lines)
        record = self.do_merge()

        def carried(person):
            return set(PersonTag.objects.filter(person=person).values_list("tag__name", flat=True))

        self.assertEqual(carried(self.keep), {"Coro", "Llaves", "Ujieres"})
        self.assertEqual(carried(self.dup), {"Llaves"})  # the one both had stays behind, on the hidden record
        self.assertEqual(tags.carriers(llaves), 1)  # and a hidden record is not counted as a carrier
        self.assert_integrity()
        merge.undo(self.church, record.pk, self.admin)
        self.assertEqual((carried(self.keep), carried(self.dup)), ({"Coro", "Llaves"}, {"Llaves", "Ujieres"}))

    def test_a_tag_deleted_after_the_merge_is_reported_as_not_put_back(self):
        ujieres = tags.create_tag(self.church, "Ujieres", self.admin)
        tags.set_tags(self.church, self.dup.pk, {ujieres.pk}, self.admin, seen=set())
        record = self.do_merge()
        tags.delete_tag(self.church, ujieres.pk, self.admin, seen_count=1)
        record = merge.undo(self.church, record.pk, self.admin)
        self.assertEqual(record.undo_report["not_restored"], {"people.PersonTag.person": 1})
        self.assertIn("1 etiqueta", merge.undo_report_lines(record))


class StageAndDatesMergeTests(MergeFixture):
    def set(self, person, **values):
        Person.objects.filter(pk=person.pk).update(**values)

    def states(self):
        self.keep.refresh_from_db()
        self.dup.refresh_from_db()
        return {row.name: row.state for row in merge.build_preview(self.church, self.keep, self.dup).fields}

    def test_the_survivor_keeps_its_own_and_takes_what_it_did_not_have(self):
        self.set(self.keep, stage="miembro", stage_changed_on=date(2026, 1, 5), baptized_on=date(2010, 5, 5))
        self.set(self.dup, baptized_on=date(2010, 5, 5), arrived_on=date(2008, 1, 1))
        states = self.states()
        self.assertEqual((states["stage"], states["baptized_on"], states["arrived_on"]), ("keeps", "same", "fills"))
        self.assertNotIn("member_since", states)  # neither has it: nothing to say
        record = self.do_merge()
        keep = Person.objects.get(pk=self.keep.pk)
        self.assertEqual((keep.stage, keep.stage_changed_on, keep.baptized_on, keep.arrived_on, keep.member_since),
                         ("miembro", date(2026, 1, 5), date(2010, 5, 5), date(2008, 1, 1), None))
        self.assertEqual(record.manifest["survivor_before"], {"arrived_on": None})
        self.assertFalse(ChurchAuditEvent.objects.filter(action="persona_etapa").exists())
        merge.undo(self.church, record.pk, self.admin)
        self.assertIsNone(Person.objects.get(pk=self.keep.pk).arrived_on)
        self.assertEqual(Person.objects.get(pk=self.dup.pk).arrived_on, date(2008, 1, 1))

    def test_when_they_disagree_the_administrator_chooses_and_nothing_is_guessed(self):
        self.set(self.keep, stage="visita", stage_changed_on=date(2026, 1, 5), member_since=date(2020, 1, 1))
        self.set(self.dup, stage="miembro", stage_changed_on=date(2026, 3, 9), member_since=date(2019, 6, 1))
        states = self.states()
        self.assertEqual((states["stage"], states["member_since"]), ("choose", "choose"))
        with self.assertRaises(merge.MergeRefused):
            self.do_merge()  # no choice made
        self.assertIsNone(Person.objects.get(pk=self.dup.pk).merged_into_id)
        record = self.do_merge(stage=self.dup.pk, member_since=self.keep.pk)
        keep = Person.objects.get(pk=self.keep.pk)
        self.assertEqual((keep.stage, keep.stage_changed_on, keep.member_since), ("miembro", date(2026, 3, 9), date(2020, 1, 1)))
        row = ChurchAuditEvent.objects.get(action="persona_etapa")
        self.assertEqual(row.metadata, {"person_id": keep.pk, "previous": "visita", "value": "miembro"})
        self.assertEqual(record.manifest["survivor_before"], {"stage": "visita", "stage_changed_on": "2026-01-05"})
        merge.undo(self.church, record.pk, self.admin)
        keep.refresh_from_db()
        self.assertEqual((keep.stage, keep.stage_changed_on), ("visita", date(2026, 1, 5)))
        self.assertEqual(ChurchAuditEvent.objects.filter(action="persona_etapa").latest("pk").metadata,
                         {"person_id": keep.pk, "previous": "miembro", "value": "visita"})

    def test_a_stage_set_after_the_merge_is_not_overwritten_by_the_undo(self):
        self.set(self.dup, stage="miembro", stage_changed_on=date(2026, 3, 9))
        record = self.do_merge()
        self.assertEqual(Person.objects.get(pk=self.keep.pk).stage, "miembro")
        services.set_stage(self.church, self.keep.pk, "ya_no_asiste", self.admin, seen="miembro")
        record = merge.undo(self.church, record.pk, self.admin)
        self.assertEqual(Person.objects.get(pk=self.keep.pk).stage, "ya_no_asiste")
        self.assertEqual(record.undo_report["fields_not_restored"], ["stage"])
        self.assertTrue(any("la etapa" in line for line in merge.undo_report_lines(record)))

    def test_the_confirmation_covers_them_and_the_screen_offers_the_choice(self):
        self.set(self.keep, stage="visita", baptized_on=date(2010, 5, 5))
        self.set(self.dup, stage="miembro", baptized_on=date(2011, 6, 6))
        self.keep.refresh_from_db()
        self.dup.refresh_from_db()
        token = merge.build_preview(self.church, self.keep, self.dup).token
        self.client.force_login(self.admin)
        url = reverse("people:merge_preview", args=[self.keep.pk, self.dup.pk])
        page = self.client.get(url)
        for words in ("etapa del registro que se queda", "se bautizó el del registro que se une", 'name="elige_stage"', 'name="elige_baptized_on"',
                      "05/05/2010", "06/06/2011"):
            self.assertContains(page, words)
        # Somebody changes the stage of one record while the preview is open: the confirmation is of something else.
        services.set_stage(self.church, self.dup.pk, "asistente", self.admin, seen="miembro")
        response = self.client.post(url, {"revision": token, "elige_stage": self.dup.pk, "elige_baptized_on": self.dup.pk})
        self.assertContains(response, "No unimos nada.")
        self.assertIsNone(Person.objects.get(pk=self.dup.pk).merged_into_id)
        token = merge.build_preview(self.church, Person.objects.get(pk=self.keep.pk), Person.objects.get(pk=self.dup.pk)).token
        self.client.post(url, {"revision": token, "elige_stage": self.dup.pk, "elige_baptized_on": self.dup.pk})
        keep = Person.objects.get(pk=self.keep.pk)
        self.assertEqual((keep.stage, keep.baptized_on), ("asistente", date(2011, 6, 6)))

    def test_a_merge_still_never_writes_a_date_of_birth(self):
        self.set(self.dup, birth_date=date(1990, 1, 1), baptized_on=date(2010, 5, 5))
        self.assert_refused("Solo el registro que se uniría tiene fecha de nacimiento")
        self.assertNotIn("birth_date", merge.CHOICE_FIELDS)


class ImportedRecordMergeTests(MergeFixture):
    def test_merging_an_imported_record_keeps_it_out_of_the_imports_undo(self):
        rows, _ = imports.read_file("Nombre,Correo\nLía,lia@otra.mx\nPaz,paz@prueba.mx\n".encode())
        record = imports.apply(self.church, rows, self.admin, token=imports.plan(self.church, rows).token)
        lia, paz = Person.objects.get(email="lia@otra.mx"), Person.objects.get(email="paz@prueba.mx")
        Person.objects.filter(pk=lia.pk).update(email="lia@prueba.mx")  # now it shares the e-mail of ``keep``
        lia.refresh_from_db()
        self.do_merge(self.keep, lia)
        self.assertEqual(imports.undo_plan(record).delete, [paz.pk])
        imports.undo(self.church, record.pk, self.admin, token=imports.undo_plan(record).token)
        self.assertTrue(Person.objects.filter(pk=lia.pk, merged_into=self.keep).exists())  # hidden, never deleted by the undo
        self.assertEqual(PeopleImport.objects.get(pk=record.pk).undo_report, {"deleted": 1, "kept": 1})
        self.assert_integrity()
