# Wave 11 — restyle: pastoral approval and the church of the request

Branch `v2/wave-11-aprobaciones`. Design and front-end only: no view, rule, permission, URL, token or migration
changed. Law: `docs/v2/CRAFT.md`, `docs/v2/DESIGN_SYSTEM.md`.

## Inventory: what each person came to do, and what was wrong

| Screen (template) | Who | Came to do (the one filled action) | Wrong against the law |
|---|---|---|---|
| Aprobación pastoral (`resources/approval_queue.html`) | pastor; administration reads | Open what waits and decide it (no filled action: the rows lead) | A `lede` paragraph; rows with no 48px lead; a `btn--secondary btn--small` «Revisar»/«Ver» in every row; a state word repeating the section title; a body-size paragraph before a list |
| One item (`approval_review.html`, `_approval_devotional.html`, `_approval_plan.html`) | pastor decides; administration reads | Read it as it will be published, then «Aprobar y publicar» | The content was a table of labels (Título/Día/Lectura), nothing like the published page; kind as a `lede`; `notice--quiet` (no rule); `step__actions`; the old `details`/`details__body` fold; the decision at the end of a long page with no way to it; `<p class="more">` |
| The approval record (same template, approved) | both | Read who approved, when, with what permission | Same preview problem; `<p class="more">` «Ir a editarlo» |
| `_approval_note.html` (devotional form, plan days, plan publish, public home) | administration | Know where the request stands | `notice--quiet` |
| `_no_pastor.html` | administration | Know why nothing publishes and what to do | Nothing: a warning notice with its two links. Unchanged |
| Devocionales (`devotional_admin.html`) | administration | «Escribir un devocional» | The wave's state sentence plus a state in `row__end` squeezed the title into a narrow column at 390; `<p class="more">` |
| Planes (`plan_admin.html`), pedir aprobación (`plan_publish.html`) | administration | «Escribir un plan» / «Pedir la aprobación pastoral» | `<p class="more">` |
| Devotional, plan, day forms; plan days (`devotional_form.html`, `plan_form.html`, `plan_day_form.html`, `plan_days.html`) | administration | Save | Already on the system (the «pedir aprobación» box, the «what readers stop seeing» warning before saving and the returned-with-reason notice are a `.check` and `.notice--warning`). Only the note's dead class |
| Página pública (`sitio/edit.html`) | administration | «Guardar cambios» | `step__actions` twice |
| Revisar antes de pedir (`sitio/review.html`, `_review_list.html`, `_approval_home.html`) | administration; the pastor reads the list | «Pedir la aprobación pastoral» | A three-line `lede`; no air between the head's note and the first list, nor before the closing notice |
| Horario (`sitio/time_form.html`), recursos (`media_admin.html`, `media_form.html`) | administration | Save | No approval state in them (media links need none, decision 4). Unchanged |
| The church switcher: rail and «Más» (`partials/ui/shell.html`, `shell_end.html`) | accounts of two or more churches | Change church | Nothing: it already is a `rail__link rail__link--button` in `rail__quiet`, as «Ayuda» and «Cerrar sesión». Unchanged, byte for byte |
| The switcher in Mi cuenta (`core/account.html`, redesigned branch) | the same | Change church | A second list hanging under «Dónde participas» with no title; `row--stack` dropped the one-word action under the name on a phone |

## What changed

- **Queue.** No lede. Every row: the mark of its kind in the 48px lead (`_approval_mark.html`), the title is the
  row's link, the chevron, the whole row pressed. No button and no repeated state word: the section says the state,
  with its count. What waits for *this pastor* carries the vela dot (`row--attention`). The returned reason is its
  own line. The sentence about who prepares and who approves moved to the closing note.
- **Review.** The title is the content's title; under it the kind and its line. The preview is drawn with the
  reader's own pieces: the passage in `.verso`, the day and who signs as `.meta`, the words as `.prose`; a plan shows
  what it is about, who prepared it and every day whole (reference in `.verso`, reflection, «Para pensar»). What is
  missing is still said («Nadie lo firma: se publica sin firma.», «Sin cita.», «Sin reflexión.», «Sin descripción.»).
  The decision is the page's one raised surface (`.sign`), reached from the top by «Ir a tu decisión»: on a phone it
  lands with the three permissions, the note and «Aprobar y publicar» on one screen. Returning is a `.fold` under
  it, with its reason. With a plan that cannot be approved yet (no days) the surface holds only the return.
  The administration gets «Editar» in the page head instead of a link at the bottom.
- **Administration lists.** The devotional's state is under its title with its sentence (as the plans' list already
  did); a devotional that is only scheduled now says «se lee desde su día». The way to the pastor's list is a row
  (`_approval_way.html`).
- **Public home.** `form-actions`; the review's explanation is the note under the title.
- **Mi cuenta.** «Cambiar de iglesia» is its own section; «Cambiar» stays at the row's end on every width.

Nothing acts in place here (CRAFT §3): approving publishes, returning has a field, and changing church changes the
whole page and rotates the CSRF token. The offline guard of every POST form holds what was typed and offers
«Reintentar» (tried on the return form).

## CSS

One block in `static/css/santuario.css`, `/* === aprobaciones (wave 11) === */ … /* === fin aprobaciones === */`,
before §15: spacing of the preview, the hairline between a plan's days, the decision surface, two gaps. No token,
colour, font, shadow or icon added. `.sitio-admin .step__actions` became `.sitio-admin__acciones`. The waves had
added no rule of their own for this area, so none was removed.

## Words that changed

| Where | Before | Now |
|---|---|---|
| Queue row | button «Revisar» / «Ver» | the title is the link |
| Queue | state words «Por aprobar», «Devuelto» in rows | the section titles, with the count |
| Review, administration | «Ir a editarlo» | «Editar» (page head) |
| Devotional preview | «Lectura: … Solo la cita: cada quien la lee en su Biblia.» | the passage, and the note «De la lectura se publica solo la cita: cada quien la lee en su Biblia.» |
| Devotional preview | «Nadie: se publica sin firma.» | «Nadie lo firma: se publica sin firma.» |
| Devotional list | «Espera la aprobación pastoral: nadie lo ve todavía.» (and the two others) | the same words after the state word, lower case |
| Mi cuenta | — | heading «Cambiar de iglesia» |

## Not done, and why

- The public home's preview stays a list of facts by name (shared with the administration's review): it is what
  the fingerprint covers; it does not imitate the public page.
- A very long plan still means a long page; «Ir a tu decisión» is the short way, there is no sticky bar (the
  phone's tab bar owns the bottom edge).
