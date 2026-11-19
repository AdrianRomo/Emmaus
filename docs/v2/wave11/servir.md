# Wave 11 — Servir: the wave-10 screens brought to the design law

Branch `v2/wave-11-servir`. A design and front-end pass over what wave 10 added to Servir (`docs/v2/wave10/rotas.md`).
No rule, permission, token, URL or message of the feature changed; no migration.

## Inventory, and what was wrong before touching each screen

| Screen (template) | Who | Came to do (the one filled action) | Against the law |
|---|---|---|---|
| Servir (`home.html`) | Anyone in a team | Answer what waits: «Tomar la fecha» of a teammate's request, else «Sí, puedo» | The request to cover a date was a row with a second filled button (two filled on one screen); answering, withdrawing and the reminder choice reloaded the page; "my requests" rows had no 48px lead and an outlined «Retirar»; links under a date were 20px targets; hour as `G:i` |
| Team (`team.html`) | Coordinator, administration | Decide what waits («Pedir a alguien» / «Dar el visto bueno»), else «Agregar una fecha» | Swaps to approve sat low in a mixed list with their own filled button; rows without lead; state words inside a sentence; one line per role per date; approve/decline reloaded |
| Organizar (`organize.html`) | Coordinator, administration | Save one of three forms (none filled: none is the page's action) | `lede=`, `step__actions`, no `form-section`, notes glued to labels |
| Propuesta (`rotation.html`) | Coordinator, administration | «Confirmar» | `lede=` plus a second lede, `notice--quiet`, `step__actions`, `p.more`, `btn--large`, dates as bare headings, hour as `G:i` |
| Orden del día (`plan.html`) | Coordinator edits; who serves reads | «Agregar al orden» (reader: nothing to press) | `lede=`; the editor was a wrapped line of three unlabelled fields and four buttons per moment; rows without lead; form without `form-section` |
| Pedir un cambio (`swap_form.html`) | Who serves (adult) | «Pedir el cambio» | `lede=`, `notice--quiet`, `step__actions`, small radios instead of the large options |
| ¿Solo personas adultas? (`role_confirm.html`) | Coordinator, administration | «Sí, dejarlo solo para personas adultas» | `lede=`, `step__actions`, rows without lead, the consequence as a grey note |
| Fecha (`date_form.html`) | Coordinator, administration | «Guardar la fecha» | Already redesigned; only the `.need` state and `.choice__extra` spacing |

States looked at: no roles / no members / no rotation (a new team), no proposal, a proposal with reasons, an order
empty with «Copiar del anterior», three moments, a fold open, one and two requests to answer, a request that needs
approval, requests asked (waiting, accepted and waiting for approval), a date nobody serves together with a swap to
approve, "ya no puede cubrir este rol", a 70-character name, a 50-character team name.

## Decisions

- **One raised surface on Servir**: «Esperan tu respuesta» holds the teammates' requests (`.sign.sign--lead.esperan`
  with `.ask` blocks, as on «Mi iglesia»). While one waits it carries the filled button and the first pending date of
  «Tus fechas» is outlined; when none waits the first pending date is filled, as before.
- **Team**: a swap that waits for the coordinator is one more row of «Esperan tu decisión» (with its count); the
  other swaps are the record «Cambios de fecha», with the state as a word leading the row's meta line (`CONSISTENCY.md`, rule 1).
- **Order of a date**: the coordinator sees the list as it is read; a moment's fields open under it («Cambiar»);
  «Subir» and «Bajar» stay visible at the end of the row (reordering behind a fold costs three presses per step);
  «Quitar» is inside «Cambiar», beside «Guardar» (`CONSISTENCY.md`, rules 2 and 7).
- **In place** (`data-inline`, no change to `emmaus.js`): a teammate's answer (region: that request; `also`: «Tus
  fechas» and the other requests), withdrawing a request (region: «Cambios que pediste»; `also`: «Tus fechas»), the
  coordinator's yes/no (region: «Esperan tu decisión»; `also`: the record and the head's buttons), the reminder
  choice (region: its section). Never in place: the proposal, the adults-only confirmation, everything in the order
  editor, roles, rotation, settings.
- Views: `serving.views.team` passes `to_approve` apart from `swaps` and, per date, `needs_missing` / `needs_full`
  (the same facts, grouped). Nothing else in Python.

## Not done / falls short

- A request to cover a date is not on «Mi iglesia» (`groups/home.html` is outside this area): CRAFT §7 enters "Serve
  a date" from the home's raised request; a swap request is reached from Servir only.
- After a request is answered by keyboard its region leaves and focus falls to the page (the mechanism moves focus
  only into a region that stays). The message is announced.
- Organizar has no filled button (three equal saves).
- The order editor keeps three trailing text actions per moment.
