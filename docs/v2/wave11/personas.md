# Wave 11, restyle: Personas (the data screens of waves 9 and 10)

Branch `v2/wave-11-personas`, cut from `v2/wave-11-diseno`. A design and front-end pass: the screens that waves 9
and 10 added to Personas are brought to `DESIGN_SYSTEM.md` and `CRAFT.md`. What each feature does, its rules,
permissions, tokens, addresses and promises are unchanged. Feature notes: `../wave9/personas.md`,
`../wave10/personas.md`. Everyone who sees these screens is the church's administration, except «Mis datos».

## 1. Inventory, and what was wrong (written before the change)

| Screen (`templates/people/`) | What the person came to do (the one filled action) | Against the law, before |
|---|---|---|
| `person_list` (filters and tools) | Find a person; «Registrar a una persona» | An Atrio `filter-bar` of two selects and «Buscar» (DS §8: capsule search, views as capsules with their count, a secondary filter folded); four `<p class="more">` links stacked under the list, 28px targets |
| `duplicates` | Open a pair to review it (the row); no filled button | A `lede`; rows without the 48px lead; three controls per row («Revisar» and «Abrir» as `btn--secondary btn--small`, two name links of 24px); a dismissal reloaded the page; the dismissed list's pager moved the first list's page |
| `merge_preview` | «Unir estos registros» | A `lede`; the two records could not be compared (one sentence per record, then a list of the survivor's values, then boxed radios in another section); `step__actions`; `choice__input` (no rule anywhere); a `.more` link |
| `merge_detail` | Read what was done; «Deshacer la unión» (outlined) | `step__actions`; label and value stacked on a wide screen |
| `import` | «Revisar el archivo» | A `lede`; `step__actions`; the form without a section; rows without lead, «Abrir» buttons |
| `import_preview` | «Importar N personas» | A `lede`; rows without lead; `step__actions`; `notice--quiet` (no rule); a `.more` link |
| `import_detail` | Read it; «Deshacer: borrar N personas» | `step__actions`; an irreversible deletion drawn as a neutral outlined button |
| `export` | «Descargar el archivo» | A `lede`; `step__actions` |
| `tags` | «Crear etiqueta» | A `lede`; rows without lead; «Borrar» as `btn--secondary btn--small`; `step__actions` |
| `tag_delete` | «Sí, borrar la etiqueta» | `step__actions`; an irreversible deletion filled in the church's colour |
| `person_detail` (stage, tags) | Read the person; no filled action in these sections | `step__actions`; up to 30 tags in one column |
| `person_form` (stage, key dates) | «Guardar cambios» / «Registrar» | Already on `form-section` and `form-actions`: looked at, unchanged |
| `groups/profile.html` (the two dates) | «Guardar mis datos» | The two dates sat under «Cómo contactarte», which they are not |

States looked for on each: empty, one, many, long names (a 73-character name, a 40-character tag), errors and
refusals, the stale-preview warnings.

## 2. What changed

- **`person_list`**: the list's tools are the camper list's components: `form.search` (sent with Enter; it keeps the
  other filters), the stage as `ul.filters` capsules with their count, the tags under one line («Etiqueta: todas»,
  open when one is chosen). The heading says what is filtered and offers «Quitar filtros». The four ways out are a
  section «Cuidar la lista» of rows that open, each with its mark. `people.views.person_list` now also gives the
  template the capsules (`stage_views`, `tag_views`: label, address, count); the filtering itself is the same code.
- **`duplicates`**: a pair is one row: its title opens the review, its one trailing action is «No son la misma
  persona». Dismissed pairs come next (they are what a dismissal changes), then the merges, each a row that opens.
- **`merge_preview`**: the two records are one table of four columns (the field, the record that stays, the one
  that joins it, what the union leaves). The choice between two values is made in the row. The date of birth and
  the access of BOTH records are shown (before, only the survivor's): two records cannot be compared with one of
  them hidden; both were already on each person's page. What moves is a list of counts. Every warning stays: the
  stale preview, the roles an account gains, the siblings with their own required confirmation, the registrations
  that are settled again, the request that closes, what is never touched, the refusals. «Nada cambia hasta que
  confirmes» sits on the button it speaks about.
- **`merge_detail`, `import_detail`, `import_preview` counts**: label beside value while there is room (`.ficha`).
  «Deshacer: borrar N personas» is `btn--danger-outline`; «Sí, borrar la etiqueta» is `btn--danger`.
- **`import_preview`**: the order is untouched (counts, se registran, ya existen, repetidas, con errores, confirm);
  each row is led by its section's mark; headings carry their count.
- **`tags`**: each tag is led by how many people carry it; «Borrar» is the quiet removal whose page asks first.
- **`profile`**: «Tus fechas en la iglesia» is its own section; each date says who reads it (the form's own hint).
- Sentences that were a `lede` and carried a promise were kept, next to what they speak about.

## 3. Actions in place (CRAFT §3)

| Action | Region | When it fails |
|---|---|---|
| «No son la misma persona», on a row of `duplicates` | `#duplicados` (both lists and the count) | The button comes back marked, one line with «Reintentar»; a refusal is a toast; without JavaScript the same POST returns to the page |
| «Volver a revisar» | `#duplicados` | The same |

Both views return to `siguiente` only when it is the review itself (`merge_views._back_to_the_review`); anything
else is the review's first page. Not in place, on purpose: changing a stage and saving tags (forms with a field),
merging, undoing, importing, exporting, deleting a tag, and «No son la misma persona» on the preview (it leaves the
page for the list).

## 4. To revert

`git revert` the commit(s) of this branch: templates, one CSS block (`/* === personas (wave 11) === */`), two
views (`people/views.py::_list_views`, `people/merge_views.py::_back_to_the_review`) and their tests. No migration,
no data.
