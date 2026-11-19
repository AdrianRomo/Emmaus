# Wave 11: Espacios on the design law

The `espacios` app (rooms and equipment, `docs/v2/wave9/espacios.md`) restyled to `DESIGN_SYSTEM.md` and
`CRAFT.md`. Nothing the feature does changed: same rules, permissions, routes and messages. A plain member
and a pastor still read only «Ocupado» and the hours (`espacios.views._shown` blanks the rest on the object);
the override is still offered only on a page that lists the clashes, and the service still demands their ids.

## Inventory (written before the restyle)

| Screen (template) | Who | The person came to | Wrong against the law |
|---|---|---|---|
| Espacios (`list.html`) | Everybody; more for who may ask and the administration | Open the week of a room; the administration: see what waits | A lede; «Volver a Mi iglesia» on a place of the rail; rows without the 48px lead; «Pedir» outlined buttons in rows; three bare `p.more` links; the pending count as a notice |
| The week (`week.html`) | Everybody | See at the door when the room is busy or free; who may ask: ask for a day | A lede; `week-day` had no rule; a pager wider than a phone (31px of sideways scroll at 390); `notice--quiet`; a `p.more` link under every day; seven headings of 18/700 over one grey line each |
| A booking (`_booking.html`) | — | — | No lead; the state squeezed beside the title (`row__state`); «Cancelar» as an outlined small button |
| Pedir / Reservar (`request_form.html`) | Who may ask | Ask for a day and its hours | Three ledes; no `form-section`; `step__actions`; the same hint under both hours |
| Solicitudes (`queue.html`) | Administration | Approve or reject what waits | A lede; no lead; every «Aprobar» filled; approving reloaded the page |
| El empalme (`clash.html`) | Administration | Decide knowing what overlaps | A lede that was a state («No la aprobamos todavía»); `step__actions`; the consequence above the buttons as loose text |
| Rechazar (`reject.html`) | Administration | Write the reason | A lede that was the consequence; `step__actions` |
| Administrar (`manage.html`) | Administration | Add or edit a space | A lede; no lead; two outlined buttons per row |
| Agregar / Editar (`space_form.html`) | Administration | Save the space | A lede; no `form-section`; `step__actions` |
| Uso (`usage.html`) | Administration | Read the hours of a period | A lede; `step__actions`; a long name pushed the page 308px sideways at 390 (table heads do not wrap) and overlapped its own line when stacked |

Cancelling stays `groups/confirm.html` (it asks first; already redesigned).

## What changed

- **A booking row**: the day drawn in the leading slot wherever the day is not already the group's
  (`with_day`); the state as the system's word-with-mark on the line of the hours; «Cancelar» a quiet text
  action (its page asks first). Under «Lo que pediste» the row no longer says the reader asked for it.
- **Espacios**: no lede, no back link. A row is a space with its mark and opens its week (chevron, the whole
  row); «Pedir» lives on the week, where the person sees first whether it is free. The administration reads
  what waits as the system's decision row (the count, «Solicitudes por decidir») and its three pages as rows.
  An administration with no space gets the empty state with «Agregar un espacio».
- **The week**: one group per day, led by the drawn day (today filled, and said: «Hoy, jueves 19 de
  noviembre»); a free day is two short lines; for who may ask, «Pedir» at the end of each day's line (today and later). The week and «Esta semana»
  sit over two buttons, «Anterior» and «Siguiente» (their accessible names say «Semana …»), with «Ir a esta
  semana» between them when away. No sideways scroll at 390.
- **Pedir / Reservar**: three sections (what for; «Día y horas» with the two hours on one line; «Preparar y
  recoger» with its two lists on one line), each hint said once under its legend; what the button really does
  under the buttons. The clash state is unchanged in substance: one summary with the clashes, the filled
  button changes the hours, «Reservar aunque se empalme» apart under a hairline with its sentence.
- **Solicitudes**: the day leads each row; one filled «Aprobar» (the first row); approving acts in place.
- **El empalme, Rechazar**: the state as a notice; `form-actions`; the consequence under the buttons.
- **Administrar**: the name opens the week (as everywhere), «Editar» is the one text action; «Apagado» and
  the bookings to come on their own line.
- **Uso**: the period is one line (two days and «Ver ese periodo»); a long name wraps.

## In place (CRAFT §3)

Approving from the queue: `form[data-inline="solicitudes"]`, only on a row that overlaps nothing when the
list is drawn. The view returns to `siguiente` through `core.nav.safe_next` (the page and filter it was
pressed on; another site is ignored). The row leaves, a line keeps the trace with the view's own message, the
«Por decidir» count, the rail and the tabs follow, focus goes to the next request.

- A row that already overlaps is a plain form: its answer is the clash page.
- A clash that appears after the list was drawn: the server answers the in-place POST with the clash page
  (not a redirect) and emmaus.js shows that page; nothing is approved. Tried in a browser.
- Offline: the button comes back, dashed, with «No se pudo guardar… Reintentar»; the row and the counts stay.
  Tried in a browser, with the keyboard.
- Not in place: rejecting (a field), cancelling (asks first), asking for a booking, the override.

## Copy moved, never dropped

Every consequence and honesty sentence is still on its screen: «Emmaus no te avisa…», «Emmaus no le avisa a
nadie…», «Mientras decides, el horario está apartado» (now with it), the three results of the request form
(under its buttons), «El horario queda libre para alguien más», the override's two sentences, «Este espacio
está apagado…», the usage note (now opening with «Solo cuentan las reservas aprobadas»). Removed as
explanation a screen does not need: «Los salones y el equipo de la iglesia, y cuándo están ocupados», «Quién
pidió qué espacio y para cuándo», «Lo que la iglesia presta para sus actividades…», «Cuánto tiempo estuvo en
uso cada espacio…». The hint of the two hours reads «Las horas, en formato de 24 horas: por ejemplo 18:30.»

## Code outside the templates

- `espacios/views.py::approve`: the redirect goes through `safe_next`. Nothing else.
- `espacios/forms.py`: the hint of the hours and of the time to set up moved from the fields to the form
  (`time_help`, `buffer_help`), so the template says each once. No rule changed.
- `static/css/santuario.css`: the old Espacios rules of the waves' block replaced by one block,
  `/* === espacios (wave 11) === */`. No token, colour, font, shadow or icon added.

## How to see it

`seed_community_demo` gives two spaces and three bookings; the administration is
`admin.comunidad@demo.invalid`, who may ask `guia.grupo@demo.invalid`, a plain member
`valeria.soto@demo.invalid`. Pages under `/mi-iglesia/espacios/`.

## Revert

`git revert` the wave's commits on `v2/wave-11-espacios`: templates, one CSS block, two small Python edits and
their tests; no migration.
