# Wave 11 — Formularios y «Pendientes», restyled to the design law

Branch `v2/wave-11-formularios` (from `v2/wave-11-diseno`). A design and front-end change of the `formularios`
app (feature notes: `docs/v2/wave10/formularios.md`): templates, the area's block in `static/css/santuario.css`,
two actions wired in place. No rule, permission, token, model or address changed; no migration.

## Inventory, and what was wrong (written before changing each screen)

| Screen (template) | Who | The person came to (the one filled action) | Wrong against the law |
|---|---|---|---|
| «Pendientes» (`queue.html`) | any signed-in person; rows only for staff | see what waits and go there (no filled action: every row is a way out) | lede; «Volver a Mi iglesia» on a place of the rail; rows without the 48px lead; the empty state a long grey sentence |
| Formularios (`list.html`) | a person of the church | open a form to answer it (rows) | lede; back link on a place of the rail; rows without lead; «Ya respondiste hoy» lost in a sentence |
| Answer a form (`fill.html`, `_questions.html`, `_purpose.html`) | a person of the church | «Enviar mis respuestas» | `notice--quiet` (no rule); `step__actions`; an option's label heavier than its question; questions packed 20px apart |
| Thanks (`thanks.html`) | a person of the church | know it was received | the result written as a lede; a button inside a paragraph |
| Public form, its errors, its thanks (`public.html`, `public_thanks.html`) | anybody with the link | «Enviar mis respuestas» | the same as the member's form; a small button on a door; thanks as a lede, paragraphs without air |
| Forms of the church (`manage.html`) | administration; readers (their forms) | administration: «Crear un formulario»; reader: open the answers | lede; back link and «Ver Pendientes» duplicating the rail; rows without lead, a link inside the facts; state at the far end without the chevron |
| Create / edit (`settings_form.html`) | administration | «Crear el borrador» / «Guardar cambios» | lede; nine fields in one run with no `form-section`; `notice--quiet`; `step__actions` |
| The builder (`detail.html`) | administration | publish what waits («Revisar y publicar»); otherwise «Ver las respuestas» | two filled buttons at once; the purpose as a lede; four controls on every question row («Subir», «Bajar», «Editar» button, «Quitar»); rows without lead; an `h3` dressed as a row title; `step__actions`; reordering reloaded the page; closing drawn as an ordinary button |
| A question (`question_form.html`) | administration | «Agregar al borrador» / «Guardar la pregunta» | lede; `step__actions` |
| Review and publish (`publish.html`) | administration | «Publicar así» | lede; the preview indistinguishable from the page's own form; `step__actions` |
| Readers (`readers.html`) | administration | «Agregar lector» | lede; a paragraph before the list; outlined «Quitar» buttons in rows, removing at once; `step__actions` |
| Answers (`submissions.html`) | administration and readers | open an answer (rows) | lede; rows without lead; `mt-8` wrappers and `forms__bulk` (no rule); «borrar todas» drawn as an ordinary button |
| One answer (`submission.html`) | administration and readers | «Marcar como hecho» | «Una respuesta» as the title and the form as a lede (the person was a table row); contact as table cells; `forms__answers` (no rule); `notice--quiet`; four `step__actions`; marking done reloaded the page; delete drawn as a plum link |
| Assign (`task_assign.html`) | administration | «Guardar cambios» | lede; `step__actions` |
| Add to people (`add_person.html`) | administration | «Agregar a personas de la iglesia» (only when nobody is alike) | lede; `btn--secondary btn--small` in rows; `step__actions` |
| Tasks without owner (`loose_tasks.html`) | administration | «Asignar» on a row | lede; rows without lead; `btn--secondary btn--small` «Asignar» |

States looked at: empty, one, many, long names and titles, errors, held («por revisar»), same contact, overdue,
done, closed form, draft without questions, published with and without unpublished changes; as the administration,
a guide who reads one form, an adult member, and nobody (public).

## What changed

- **«Pendientes»**: title only; one list on the row specification, the mark of the place a thing belongs to in the
  leading slot (the rail's own icons), the neutral title as the way in, the place and the day under it, the vela
  dot on an overdue one. Nothing was added to a row: it shows only what `formularios.queue.Row` gives. Still
  read-only, and its line says so. Empty: the system's empty state.
- **Answering** (member and public): what it is for and until when it is kept come first (a `.notice`); one group
  per question, 32px apart; the question is 17/600 and its options 17/430; the contact block is a fieldset whose
  fields keep the small label; `form-actions`, the button full-width on a phone and large on the public door.
  Conditional questions are untouched: always drawn, with their hint, no script.
- **Thanks** (both): the result as the «done» line (`.hecho`), then what happens with it.
- **Forms of the church**: rows with lead; the state's mark and word beside the counts; no link inside a row.
- **Create / edit**: three `form-section`s («Lo que lee quien responde», «Quién responde y cuándo», «Las
  respuestas»); the create screen's lede moved, whole, into its closing note.
- **The builder**: what waits to be published is the page's one raised surface at the top («Es un borrador: nadie
  lo ve todavía» / «Hay cambios sin publicar» / «Antes de publicar» with what is missing); it holds the only filled
  button. In sync, «Ver las respuestas» is the filled one. The purpose is a fact of «El formulario». A question is
  led by its number; its title opens it; «Subir» / «Bajar» are its trailing actions; «Quitar» moved to the
  question's page as a quiet removal that asks first. «Qué no preguntar» closes the questions, where they are
  written. «Copiar enlace» under the public link. Readers as rows with «Cambiar los lectores» at the section's
  head. «Cerrar el formulario hoy» in the outlined danger style (its page still asks).
- **Review and publish**: «Así lo verá quien responda.» is the section's title; the preview is set apart by a rule
  on its edge.
- **Readers**: the administration as the first row («Siempre las lee»); «Quitar» quiet, asking first with its
  consequence; the privacy sentences as the list's note; adding as a `form-section`.
- **Answers**: a row is a person (circle, name, when); the task as the state's mark and word with its day and who
  has it; the held bucket's explanation as a notice; «Borrar todas…» outlined danger; the retention sentence as the
  list's note.
- **One answer**: the head is the person (circle, name, form, day, version, contact as `tel:` / `mailto:` links),
  as on a follow-up; each question over its answer; «Lo que se le dijo» (purpose and consent, quoted); the task as
  one region; the administration's two actions together at the end, delete in the outlined danger style.

## Actions in place (CRAFT §3)

| Action | Region | Fails safely |
|---|---|---|
| «Marcar como hecho» / «Abrir otra vez» on the answer's page | `#seguimiento`; the rail's «Pendientes» count comes from the same answer | offline or a server error: the button comes back, marked, with one line and «Reintentar»; 12 s: the page is asked again. Without script it is the same POST and redirect as before |
| «Subir» / «Bajar» of a question | `#preguntas-lista`, and `#cabeza-formulario` and `#estado-publicacion` (the publish state and the filled button change with it); focus goes to the question that moved (`?movida=` on the view's redirect, the only server-side line changed) | the same; a condition dropped by the move is the server's warning, shown as a toast |

Not in place, on purpose: the note (a field), accepting a held answer, assigning, publishing, closing, deleting,
removing a reader or a question, adding a person. **Marking done from «Pendientes» was not built**: that list is
read-only by the feature's own rule and promise («Aquí no se cambia nada»), its rows carry no task to act on, and a
button on some rows would take the chevron from them; the task is one press away and closes in place there.

## CSS

One block, `/* === formularios (wave 11) === */ … /* === fin formularios === */`, replaces wave 10's rules of this
area. No token, colour, font, shadow or icon added. Rules now given to classes that had none: `forms__answers`
(question over answer). Removed from the templates: `forms__bulk`, `notice--quiet`, `step__actions`, `mt-8`, `mt-2`.
`choice__input` was never used by this area's templates.

## Tests

`formularios/tests/test_design.py` (new, 5 tests): the two in-place actions are the same posts and come back to
their page; «Pendientes» has no form or button; no template of the area keeps an old mark; the public form keeps
its seal, its honeypot and one filled button. No existing test needed a change.

## For the browser journey (`e2e/community-flow.mjs`, step 13; not edited here)

- `go(guide.page, "main button:has-text('Marcar como hecho')")` waits for a page load that no longer happens: the
  button acts in place. It needs `act(guide.page, …)`. The message «Marcaste el pendiente como hecho.» is then in
  the hidden `role="status"` line (still found by `getByText`), not in a toast.
- Everything else step 13 reads is unchanged: «Crear un formulario», «Crear el borrador», «Datos de contacto», «Una
  opción», «Agregar al borrador», `#enlace-publico`, `select[name=lector]`, «Agregar lector», the links of the
  answers, «Mismo contacto que otra respuesta.» and «Pendiente para» once each per row, the link «Cambiar
  responsable o fecha», `select[name=owner]`, «Guardar cambios», `main li.row:has(a[href=…])` in «Pendientes»,
  `input[name=note]`, «Guardar la nota», «Enviar mis respuestas», `main .error-message`, `h1`.
- Gone from the screens (nothing in the journey used them): «Volver a Mi iglesia» on «Pendientes» and Formularios;
  «Volver a Formularios» and «Ver Pendientes» on the forms of the church; the «Editar» and «Quitar» buttons of a
  question row (the title opens it; «Quitar esta pregunta» is on its page); the link «N respuestas» inside a row of
  the forms of the church; «Ver las respuestas» on a draft never published. The answer's `h1` is now who answered
  (it was «Una respuesta»).

## Still short of CRAFT §8

- A date field shows the browser's own format (`mm/dd/yyyy` in the captures' Chromium): the system's, not this area's.
- After an action in place the focus ring is drawn around the whole «Dar seguimiento» heading (the mechanism's
  `data-inline-focus`, as on the interview's follow-up).
- Not checked: a screen reader by ear; a real phone; a list of more than one page of answers or forms.
