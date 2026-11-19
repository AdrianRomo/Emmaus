# Wave 11: prayer requests on the design law

Branch `v2/wave-11-oracion`. A design and front-end pass over `templates/oracion/` and one block of
`static/css/santuario.css`. No view, rule, permission, URL, token or migration changed; feature notes stay in
`docs/v2/wave10/oracion.md`. Synthetic data only.

## Before: what each person came to do, and what was wrong

| Screen (account) | Came to do (the one filled action) | Wrong against the law |
|---|---|---|
| `form` (an adult member) | Write and send: «Enviar mi petición» | `notice--quiet` twice (no rule in the new sheet: the emergency sentences looked like any grey note, the same weight as «Antes de enviar»); `step__actions`; no `form-section` |
| `mine` (the writer) | Read the state and the reply; «Pedir oración» | `lede=`; `notice--quiet`; each request under an 18/700 date heading (a ticket header); state as a bare sentence; delete as an underlined link inside a note (DS §5: a quiet removal is `.btn--link.btn--quieto`, 44px) |
| `inbox` (a pastor) | Open a request (the row; nothing filled) | `lede=`; `notice--quiet`; `row--lead` rows with no lead (hairlines indented under nothing); the door to the switch as a link inside a note; `step__actions` |
| `read` (a pastor) | «Lo leí» | `lede=`; `step__actions`; «Lo leí» reloaded the page (CRAFT §3); state as bare sentences; reply form without `form-section` |
| `terms` (a pastor) | «Encender…» / «Guardar los cambios» | Same h1 as the inbox; `lede=`; `notice--info` and `notice--quiet` (no rule); `step__actions`; the emergency sentences copied by hand instead of the member's own box |
| `unavailable` (a member) | Understand why, and where to turn | `lede=` carrying the page's one fact |

## After

- **The emergency box** (`_emergency.html`, `.oracion__urgencia`): a white field with an ink edge and the phone
  mark, the 911 sentence at body size and 600; drawn nowhere else in the product. Same two sentences, same order.
- **`form`**: order unchanged (emergency, who reads by name, the text, the promises as a list, the button).
  `form-section`, `form-actions`; the textarea keeps `autocomplete="off"`.
- **`mine`**: «Solo tú ves esta página.» as the head's meta; each request is a small date label, the words at
  reading size, the state as a word with its mark («Todavía no la han leído», «Leída el …», «Hablaron en persona»),
  the reply behind a rule, the retention note and a quiet «Borrarla ahora». «Hoy nadie la puede leer» is a vela
  notice (it blocks). Module off: the old markup, byte for byte (first branch of the template).
- **`inbox`**: the writer's circle in the 48px lead; an unread name is heavier; the state word leading the meta line (`CONSISTENCY.md`, rule 1); the
  door to the switch is a row with a chevron. The lists' full width (`oracion--lista`).
- **`read`**: the writer's circle beside the name; the words; `#atendida` holds the states and the two marks,
  which act in place; the reply form is outside that region so a draft is never replaced.
- **`terms`**: h1 «Lo que promete tu iglesia» (the page's `<title>` already said so); the rule about who may switch
  it as the head's meta; the member's own emergency box; `form-section`, `form-actions`.
- **`unavailable`**: emergency box, then «Por ahora no está disponible.» as the title of a vela notice.

Words removed (description, no promise): «Lo que le confiaste a quien pastorea tu iglesia.» (mine), «Lo que
personas de tu iglesia te confiaron para que ores por ellas.» (inbox), «Lo que tu iglesia le promete a quien
escribe una.» (terms; now the h1). Every promise, emergency, retention and reader sentence is kept verbatim.

## In place

`form[data-inline="atendida"]` on «Lo leí» and «Hablamos en persona» (`read.html`). The views already return to
the same page, so nothing server-side changed. The announcement and the trace line are the view's fixed message;
no word of a request is in either. Failure: the buttons come back, the pressed one is marked, one line with
«Reintentar». Not in place: the reply (a field), delete (asks first), the e-mail choice, the switch.

## Revert

`git revert` the wave-11 commit of this branch: templates, the `oracion (wave 11)` block of `santuario.css` and
three assertions of `oracion/tests/test_oracion.py`. No data, no migration.

## For the browser journey (`e2e/community-flow.mjs`, step 16; not edited here)

- `go(r, "main button:has-text('Lo leí')")` waits for a navigation that no longer happens: use `act(...)`.
- Everything else the step relies on is unchanged: `main a:has-text('Pedir oración')`, «Lo lee: Ernesto Salas
  Mejía», the 911 sentence, `textarea[name=body]`, «Enviar mi petición», `main section.block` holding the words
  and `a[href$='/borrar/']`, «Todavía no la han leído», «Leída», `textarea[name=reply]`, «Enviar mi respuesta»,
  the rail's `.badge`, the messages.

## Not done, and why

- No `tel:` link on 911 or the Línea de la Vida: it would help on a phone, but it is a new behaviour on reviewed
  wording and the second sentence can be a church's free line. For the owner to decide.
- After «Lo leí» in place the keyboard focus lands on the list of states with the ink ring around it (as the
  notices' «Enterado» does).
- A very long name in the request's head wraps to several lines on a phone (names wrap, never ellipsis).
