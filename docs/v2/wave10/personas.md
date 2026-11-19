# Wave 10: people data quality, second slice of #91

Membership stage, key dates, tags, export, import with preview and undo, and what the merge engine
of wave 9 does with each. Branch `v2/wave-10-personas2`. Synthetic data only. Everything is under
the `people` module and for the church's administration, except two fields in "Mis datos".

## 1. What was built

| Part | Where | Notes |
|---|---|---|
| Stage | `Person.stage`, `Person.stage_changed_on`; `people.services.set_stage` | Visita / Asistente / Miembro / Ya no asiste, or empty ("Sin anotar") |
| Key dates | `Person.baptized_on`, `arrived_on`, `member_since`; `people.forms.KeyDatesMixin` | In "Editar los datos" (three) and "Mis datos" (two) |
| Tags | `people.Tag`, `people.PersonTag`; `people/tags.py` | At most 30 per church, 40 characters, one line |
| List filters | `people.views.person_list` | `?etapa=` (a stage or `sin-anotar`), `?etiqueta=<id>`, with `?q=` |
| Export | `people/export.py`, `people.data_views.people_export` | CSV, 15 columns, throttled, audited with the row count |
| Import | `people/imports.py`, `people/import_views.py`; `people.PeopleImport`, `Person.imported_in` | Preview, token, undo within 24 hours |
| Merge | `people/merge.py` (`TABLES`, `EXTRA_FIELDS`) | Tags move; stage and dates are chosen like contact fields |
| Integrity | `tenant_integrity`: `person_tag_church`, `person_import_church` | One more table read (31) |

`Person` had nothing equivalent to a stage: `created_from` is where the record came from, and the
only "active" notion is `merged_into`. So the stage is a new column, not a reuse.

### Migrations (all additive)

| File | Operations |
|---|---|
| `people/0006_person_stage` | AddField `stage`, AddField `stage_changed_on` |
| `people/0007_person_key_dates` | AddField `baptized_on`, `arrived_on`, `member_since` |
| `people/0008_tags` | CreateModel `Tag`, CreateModel `PersonTag`, two AddConstraint |
| `people/0009_people_import` | AlterField `created_from` (one more choice, no column change), CreateModel `PeopleImport`, AddField `imported_in`, AddIndex |

### Routes (under `/mi-iglesia/personas/`)

| Route | Name | Method |
|---|---|---|
| `?q=&etapa=&etiqueta=` | `people:list` | GET |
| `<id>/etapa/` | `people:stage` | POST `etapa`, `antes` |
| `<id>/etiquetas/` | `people:person_tags` | POST `etiqueta` (several), `antes` |
| `etiquetas/` | `people:tags` | GET list; POST `nombre` creates |
| `etiquetas/<id>/borrar/` | `people:tag_delete` | GET question; POST `personas` deletes |
| `exportar/` | `people:export` | GET what the file holds; POST the CSV |
| `importar/` | `people:import` | GET; POST a file gives the preview; POST `accion=confirmar` imports |
| `importar/ejemplo.csv` | `people:import_sample` | GET |
| `importaciones/<id>/` | `people:import_detail` | GET, also the preview of the undo |
| `importaciones/<id>/deshacer/` | `people:import_undo` | POST `revision` |

"Mis datos" (`/mi-iglesia/mis-datos/`, `groups.views.profile`) gained two fields; its route and
permission did not change.

### Permissions

Every route above: `people.views.church_admin_required` (`people/views.py`), the decorator the
people screens already used: signed in, the `people` module on for `request.church` (404 otherwise,
before anything else), then `core.authz.has_scope(user, church, "church", church.pk, MANAGE)`.
Somebody who may not manage (a member, a guide, a coordinator, a pastor who is not an
administrator) gets **403 on the screens whose address names no row** (the list, "Registrar", the
duplicate review, tags, export, import) and **404 on every address that names one** (a person, a
merge, a dismissed pair, a tag, an import, an event, a link), whether the row exists or not and
whatever its church: the same page, byte for byte. People, tags and imports are fetched inside
`request.church`; the services check the church again. Nothing in `core/authz.py`, `access/`,
`events/access.py` or `core/access.py` reads a tag (a test reads those sources).

## 2. Decisions taken, and why

### Stage

1. **Empty is a value.** Nobody is given a stage by a migration or by a guess; the list filters
   "Sin anotar".
2. **Its own write.** The edit form never carries the stage of a person who exists: a form opened
   before a change would write the old stage back. The person's page has one select that posts the
   stage the screen showed; if the row says something else, nothing is written.
3. **`stage_changed_on` is the day somebody set it**, not the day a person became anything. "Es
   miembro desde" is the key date for that.
4. **Audit**: `persona_etapa` with `person_id`, `previous`, `value` (the stage words, or
   `sin_etapa`), no name in the summary. A merge or its undo that changes the survivor's stage
   writes the same row.
5. **No automatic transition, no inference from attendance** (decision 0009, #76): no code but
   `set_stage`, the creation form, an import and a merge writes it.
6. **Panorama was left alone.** A row of totals by stage would need `overview.services.shares`
   over four parts that add up to the whole, a new audience (pastors who are not administrators)
   and a query in a screen whose query count is pinned. Not cheap, and the stage is for the
   administration only in this slice.
7. **The list shows the number of results of a filter** ("3 resultados") to the administration,
   which can open each of those rows: as in wave 9, the minimum reporting size is for aggregates
   shown to pastors and leaders.

### Key dates

8. **The person may say their baptism and their arrival, not their membership.** Membership is
   what the church says. "Mis datos" neither shows nor accepts `member_since` or the stage.
9. **Only the administration reads them**: the person's page, the export and the merge preview.
   Not the guide's list, not the group, not other members. The lede of "Mis datos" says so.
10. A date must have happened and be after 1900. Nothing compares it with the date of birth.
11. The birth date and its verification rules were not touched.

### Tags

12. **Explicit through table** (`PersonTag`), so the merge inventory names it.
13. **Unique per church without regard to case or accents** ("Coro" and "córo" are one).
14. **Delete, not rename**, in this slice. Deleting asks one question with the number of people
    who carry it; the POST confirms that number.
15. **Audit rows never hold a tag's name** (a person typed it): `tag_id` and `count`.
16. **The warning** about sensitive matters is the hint of the field where a tag is typed.
17. **Tags are not shown in the list's rows**, only on the person's page and as a filter.
18. A person's checkboxes post the set the screen showed; if it changed, nothing is written.

### Export

19. **The whole church, not the filtered list.** One meaning for "the list was downloaded" in the
    audit row.
20. **Columns**: Nombre, Apellidos, Correo, Teléfono, Medio de contacto preferido, Fecha de
    nacimiento, Edad confirmada por el equipo, Acceso a Emmaus (sí/no, never the account's
    address), Se registró desde, Etapa, Etapa anotada el, Se bautizó el, Llegó a la iglesia el, Es
    miembro desde, Etiquetas. `people/export.py` imports no model of another app.
21. **A page before the file**: what it holds, what it never holds, and that the file leaves
    Emmaus's care. The download is a POST.
22. **Throttle**: 5 per hour per administrator and church, then a 10-minute wait
    (`events.services.throttle`). Cells go through `events.services.exports.safe_cell`.

### Import

23. **The file is not stored, and neither is the session used.** The camp's team import keeps the
    cells in the session (database-backed here), which would leave up to 1 MB of personal data at
    rest. Instead the preview carries the parsed cells back in a hidden field made with
    `django.core.signing.dumps(compress=True)`, bound to the church and the account, valid for
    two hours. The confirmation brings them back; a changed field fails its signature. Cost: a
    large file makes a heavy preview page (sent with `no-store`).
24. **The token** (`revision`) covers what the preview says about every row: what happens to it,
    whom it matches, and each value it would write. The plan is made again under the church's
    lock and compared. A second confirmation therefore shows the preview again ("ya existe").
25. **Matching is `people.duplicates.keys_of`**, against the active people of this church only.
    Stricter than the duplicate review in one way: a value shared by more than eight people still
    blocks the row (the review ignores it). In doubt, not created.
26. **"Actualizar teléfono/correo vacíos" was not built.** A phone match can be a relative, and
    filling an e-mail onto the wrong person is worse than leaving it empty. An import changes
    nobody.
27. **Rows repeated inside the file** by the same signals, or word for word when there is
    nothing to compare: only the first is created. Relatives who share a phone are caught by
    this; the preview says to register them by hand.
28. **An unknown tag is an error of the row.** An import never creates a tag.
29. **Minors.** The manual form applies no rule at creation except that a typed date is a claim.
    The import does the same through `Person.save` (never `bulk_create`), the preview counts the
    minors and says the date stays unconfirmed, and the rules that protect a minor afterwards
    (guardian's yes for an account, verified age for a registration) are untouched.
30. **Audit with counts only**: `personas_importadas` (`import_id`, `count`); no
    `persona_creada` row per person, because those name the person.
31. **Undo is partial and says so.** Within 24 hours it deletes the people the import created
    that are still active, have no account, and that no row of any table points at
    (`merge.relations_to_person()`, so a module added later is covered). Tags do not keep a
    person. The rest stay and are counted; the page lists them before the undo. An edit of a
    person's own fields does not keep them.
32. **Preferred contact method** of an imported person: e-mail if there is one, else phone, else
    "Sin preferencia".
33. The import's own cells protected by our export (`'+52 …`) are read back as typed, so an
    exported list uploaded again creates nobody.
34. Helpers reused: `events.services.participants.csv_rows` and `FORMULA_STARTS`,
    `events.services.exports.safe_cell`, `core.services.full_name_to_parts`. The header match is
    the same four lines as `team_import._canonical_headers`, which is bound to its own aliases.

### Merge

35. `people.PersonTag.person`: moves; a tag both carry stays on the absorbed record; the undo
    takes back exactly what moved. A tag deleted meanwhile is reported as not put back.
36. Stage and key dates behave as contact fields: equal, kept, filled from the other record, or
    chosen between the two values. The stage travels with its `stage_changed_on`. The previous
    values live in the manifest for 30 days like the contact values.
37. The confirmation token covers the stage and the dates.
38. `Person.imported_in` points away from `Person`, so it is not in the inventory. A record that
    took part in a merge is never deleted by an import's undo.

## 3. Not built

* **Member-to-member directory**: a per-field opt-in table (person, field, visible) the person
  edits in "Mis datos", off by default, behind a church policy in `policies`, never for minors.
* **Households and guardians** (#59, gated by #75): a `Household` with memberships and a separate
  guardian-authority table; the merge inventory and the import's undo would learn both tables.
* **Custom fields**: a church-defined field with a closed type list (text ≤ 80, date, yes/no,
  choice), a per-field audience, and the same warning as tags; values in their own table.
* Also not built: renaming a tag, exporting a filtered list, "fill empty phone/e-mail" on import,
  a Panorama row by stage, a retention rule for `PeopleImport` rows (ids and counts only).

## 4. Not verified

* PostgreSQL: SQLite only. `select_for_update(of=("self",))` is used on `PeopleImport`; the other
  locks have no joins. True concurrency is tested as stale tokens and repeated calls.
* A 1 MB file's preview in a real browser (tested through the test client with 2,000 rows).
* No screen-reader pass; axe and widths only.

## 5. Left alone, worth a look

* `tenant_integrity`'s table count (31) and `people/audit.py` are shared with other builders.
* The manual form does not refuse a date of birth in the future; the import does.
* `Tag` and `PeopleImport` are not registered in the Django admin.

## 6. After the independent review (integration branch)

1. **A cell is refused, never cleaned or cut** (`people/text.py`, `people/imports.py`). A control
   character (below U+0020, U+007F to U+009F, a bidi control, U+2028/2029) or more than 200
   characters makes its row an error in the preview, and the cell is not carried to the
   confirmation. Before, a NUL passed the preview (a 500 on PostgreSQL at the confirmation) and a
   cut cell could be a different, valid value (a tag list that read as its first tag; another
   e-mail address).
2. **The normalised length is the one measured**: a tag's name (40) and a person's full name (250,
   in the import and in "Registrar a una persona"). Compatibility letters grow when normalised. A
   `DataError` in `create_tag` or `imports.apply` is a sentence, never an error page.
3. **A tag's name refuses `,` `;` `|` and control characters**, so every tag that can be created
   comes back from an export.
4. **404 for every address that names a row** for whoever may not manage (see Permissions); it
   replaces the 403-for-rows-that-exist rule, which let a member count people, merges and imports.
5. **A participant role with no path** goes from the dashboard to "Mi cuenta" with a sentence
   instead of to "Mi camino" (404), and the rail does not draw the discipleship links
   (`core/views.py::dashboard`, `core/context_processors.py`).
6. The undo's page says everything that is deleted with a person: tags, stage, key dates, a
   confirmed age, and corrections made by hand since.

Left alone: other writers of a person's name (`ensure_own_person`, a campero brought from an
event) do not measure the normalised length.
