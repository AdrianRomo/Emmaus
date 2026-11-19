# Wave 11: one hand over the seven areas

The seven areas of wave 11 (agenda series and subscriptions, espacios, formularios, oración, personas,
aprobaciones, servir) were restyled in parallel. This pass made them do one thing where they differed. Each
rule below says where it comes from: `DESIGN_SYSTEM.md` (DS), `CRAFT.md`, or the screens of the design
session, which show how the law is applied. Where the law was silent, the rule is what those screens do
most, and it is written here so the next screen follows it. No rule, permission, URL, token or sentence
changed.

## The rules

### 1. A row's state word leads its meta line

`<p class="row__meta row__meta--con-marca">{% status … %}<span>the facts</span></p>`, the state first.
The row's end is kept for its one action or its chevron, so nothing moves when the row stacks at 390.

- Source: `agenda/_admin_row.html` l.12 (Eventos), `notices/list.html` l.50, `care/_row.html` l.11. DS §5's
  sample still shows a state in `row__end`; the design session's own lists moved it to the meta line.
- Exception, as the design session drew it: the person's own answer to a request (`serving/home.html`
  «Tus fechas», `groups/_serving_state.html`) sits at the end with the other answer beside it.
- Changed: `oracion/inbox.html`, `people/person_list.html`, `serving/team.html` «Cambios de fecha».

### 2. What a row ends in

| The row… | Ends in | Source |
|---|---|---|
| opens, and nothing else | nothing: the title is the link, the row draws its chevron | DS §5, CRAFT §2 |
| has one more way or act | one text action, `.btn--link` (its words end on the margin) | DS §5 |
| can be taken out | `.btn--link.btn--quieto`; its page asks first when something is lost | DS §5 |
| is ordered by hand | the pair «Subir» / «Bajar» as text actions, nothing else; removing lives where the item is changed | `formularios/detail.html` |
| waits for an answer of this person | the two answers as small capsules (`.btn--small`), the affirmative one first | CRAFT §6 (a pair of answers), `serving/home.html` (design branch l.37–38), `groups/group_people.html` l.32–33 |
| is the first one that waits, and the next step is one | a filled `.btn--primary.btn--small`; the rows after it, a `.btn--link` | DS §5 ("the one suggested next row") |

`btn--small` exists only for an answer in a row and for the pager's «Anterior» / «Siguiente»
(`partials/ui/pager.html`; the week of a space uses the same pair). Outside a row a button has its full size.

- Changed: `serving/team.html` («Que coordine», «Que solo sirva», «Agregar», «Para coordinar», the rows after
  the first «Pedir a alguien»), `serving/home.html` («Quitar» a day, the reminders' choice),
  `serving/organize.html` («Guardar» a role), `serving/plan.html` («Guardar» a moment at full size).
- Not changed, and still outlined small capsules against DS §5: the design session's own rows
  (`agenda/_admin_row.html` «Inscritos», `agenda/registrations.html`, `groups/group_people.html` «Agregar» /
  «Nombrar guía», `resources/plan_list.html`, `people/from_event.html`). They are the reference; the owner
  decides whether DS §5 or those screens give way.

### 3. Several decisions that wait: one filled action

A list of pending decisions fills only the affirmative answer of its **first** row; every other row is
outlined. That button is the screen's one filled action: while anything waits, the page head's action is
outlined, and a second waiting list on the same screen fills nothing (`forloop.first and not <the list
above>`).

- Source: CRAFT §1 (one filled action per screen), DS §5; `groups/group_people.html` l.33,
  `groups/home.html` l.50/65/77, `events/coordinator/pending.html` l.24. The coordinator's «Esperan tu
  decisión» is the other form of the same rule: a `.decision` count that leads to the list, never a button.
- The espacios queue and Servir's «Esperan tu decisión» / «Esperan tu respuesta» already did this. Changed:
  `espacios/queue.html` puts «Aprobar» before «Rechazar», as Servir and Mi iglesia put the yes first.

### 4. Ending, deleting, cancelling: by consequence

| Consequence | Drawn as | Source |
|---|---|---|
| It cannot be undone (a record is deleted, a form closes for good) | on the thing's own page, `.btn--danger-outline`; a page that only confirms has the filled button «Sí, …» and «No, volver» as `.btn--link` | `events/participant_detail.html` l.86, `serving/date_form.html` l.79, `agenda/annul.html` l.15–16 |
| It can be resumed or undone (retire, end, stop publishing) | quiet text, `.btn--link.btn--quieto`, after a sentence that says what stays | `notices/detail.html` l.81, `resources/plan_days.html` l.49, `sitio` «Dejar de publicar la página» |
| Cancelling one's own request | quiet text, `.btn--link.btn--quieto` | `agenda/detail.html` l.58, `espacios/_booking.html` |
| Inside a row, whatever the consequence | quiet text; its page asks | DS §5, `agenda/registrations.html` «Anular registro» |

- Changed: «Terminar la serie» (`agenda/series_admin.html`) was a neutral outline and is quiet text: the
  series can be resumed. «Cerrar el formulario hoy» and «Deshacer: borrar N personas» keep the chile
  outline: neither can be undone. Espacios «Cancelar» and the tag's «Borrar» already followed the rule.

### 5. Chevrons

A row whose only way out is its title draws a chevron at every width (the CSS does it; never hide it by
stacking a state under the text). A row with an action at its end has no chevron: its title is the link.

- Source: CRAFT §2, DS §5. Changed: the prayer inbox lost its chevron at 390 because its state made the row
  stack; rule 1 fixed it.

### 6. Links and text actions

A link inside a sentence, or the closing line of a notice, is the church's colour, underlined. A way that
stands alone on the page is a text action (`.btn--link`), never an underlined link.

- Source: DS §8 "Links"; `groups/home.html` l.25 (the link that closes a notice).
- Changed: «Dejar mejor a… como el registro que se queda» (`people/merge_preview.html`) is a `.btn--link`
  that wraps, because it carries a name. Links inside notices stay as they were.

### 7. A list ordered by hand

See rule 2. Changed: `serving/plan.html`. A moment's row ended in «Subir», «Bajar» and «Quitar»; «Quitar»
moved inside «Cambiar», beside «Guardar», and a list of one moment has no end at all. Rows keep the
hairline of every list at every width.

### 8. A table that is read sits inside the column

A table whose rows are pressed keeps the system's 8px bleed, like a row's hover surface. A table that is
only read (the comparison of two records) keeps its rules and its words on the column's edges.

- Changed: `.comparar` in `santuario.css` ("consistencia").

### 9. The line under a title

`lede=` is the name of the thing the page acts on, or the thing's own description (`agenda/*` forms). A
sentence that explains the page is not a lede: one fact goes in `meta=`, the rest in an `.aside-note`.

- «Tus peticiones» already does this where the design system is served. The `lede=` that remains in
  `oracion/mine.html` is the branch served with the module off, which stays byte for byte.

### 10. A state and its facts wrap as one line

In `row__meta--con-marca` the word keeps its line and the facts wrap beside it, never under it (CRAFT §6:
metadata wraps; the row grows). Formularios drew it first; the same two declarations now cover Servir,
Espacios, Oración, Personas and the series ("consistencia" in `santuario.css`).

### 11. A rail longer than its screen

The rail's list scrolls (the design session's rule: "a rail taller than its screen never hides a place
without a sign"). The sign is a hairline on the edge where the list is cut, which leaves when that end is
reached; and the place of the current page is brought into view when the page opens (`emmaus.js`), so the
rail never opens on a list that hides where the person is. No link is folded away from a role that needs
it. The phone's «Más» panel is a page that scrolls and needed nothing.

## Also brought to one rule

- **Dates in words.** A date in a sentence is written out through the shared filters (`day_month`,
  `long_date`, `weekday_date`, `day_and_hour`); abbreviations ("mar 24") live only in the date block. The
  approval screens formatted their own; they use `day_month` now, with the same output.
- **`form-actions`.** The action first, then the way out as `.btn--link` («Cancelar», «No, volver»,
  «Volver sin…»); a quiet removal is the last thing. The seven areas already agree.
- **Empty states.** A screen with nothing at all uses `partials/ui/empty.html` (a title, at most one action);
  a section with nothing is one `.empty-line`. The structure agrees everywhere.
- **Section titles.** `h2.heading`, with `<span class="count">` when the section is a list that is counted;
  `h2.form-section__title` in a form; the question of a raised surface is `.sign__title`.
- **The trace after an action in place.** `.hecho` is the view's own message, in the past tense and in the
  action's words ("Confirmaste el lunes 23", DS §9; CRAFT §3.4). The markup agrees; see below for the words.

## Left as it is

- The wording of empty lines differs ("Sin fechas todavía." beside "Todavía no hay reservas decididas."):
  a change of words, outside a pass of look and structure.
- A few messages open with «Listo:» or are one word («Guardado.») where the rule is the action said back in
  the past tense (`agenda/series_views.py`, `people/merge_views.py`, `serving/views.py`): also words.
- The filled button of a confirming page is the church's colour on the shared `groups/confirm.html` and
  chile on `agenda/annul.html` and `people/tag_delete.html`. The design session's own screens do both.
- The order of a pair of answers in `groups/group_people.html` («No aceptar» first) is the design session's;
  every other pair puts the yes first.
