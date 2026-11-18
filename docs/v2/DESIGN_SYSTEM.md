# «Ventanas»: the design system of Emmaus

**Status (2026-11-18):** the system for every church that uses the `community` module
(`<html data-ui="2">`), chosen through a three-way exploration and two independent critiques
(`REDESIGN_CHECKLIST.md`). It restyles Atrio's classes (`docs/design-system.md`) and adds a few; with
the module off nothing of it is served. **How it behaves** (states, actions in place, motion,
journeys, the done checklist) is `CRAFT.md`; read both.

| | |
|---|---|
| Stylesheet | `static/css/santuario.css`, after `rotulo.css` (`templates/layout/skeleton.html`) |
| Shell | `core/shell.py` → `templates/partials/ui/shell.html`, `shell_end.html` |
| Pieces | `core/templatetags/shell.py`: `{% icon %}`, `{% monogram %}`, `{% date_block %}`, `{% event_arch %}`, `|tel` |
| Behaviour | `static/js/emmaus.js`: actions in place (`data-inline`), toasts, dialogs, the slow-page line |
| Reference screens | `templates/groups/home.html` (member), `templates/events/coordinator/home.html` + `_home_live.html` (coordinator) |

## 1. The idea

One quiet system. Open rows on a light ground; only what needs this person now is raised. A
gathering appears in a small arch that holds the church's photo or, without one, the sky of its
hour. **Shape is meaning:** an arch is a gathering, a circle is a person, a capsule is an action, a
rounded rectangle is a field. Members get warmth (the gathering, the day, the devotional's words);
leaders get precision (counts, queues, tables). Same tokens, type, shapes and controls for both.

## 2. Tokens

On `:root[data-ui="2"]`. The church's own colours (`--petroleo`, `--mark`, `--page`, `--surface`,
`--ink`, `--ink-3`, `--line`) are written after the sheet and win; everything else is mixed from them.

| Token | Default | Role |
|---|---|---|
| `--ink` (tinta) | `#1b2140` | Text; "done" |
| `--ink-2` (tinta-2) | `#5d6173` | Metadata, outlines (5.7:1). No lighter text exists |
| `--page` (cal) | `#f7f6f4` | The ground |
| `--surface` (blanco) | `#ffffff` | The raised surface, fields, a row's hover |
| `--petroleo` (tinte) | `#a01f58` | Actions and selection only. The church's colour |
| `--ambar` / `--ambar-tint` (vela) | `#9c5208` / `#fbead2` | What blocks somebody: a missing authorization or coordinator, «Importante» |
| `--chile` / `--chile-tint` | `#b8321c` / `#fbe6e1` | Risk and errors only |
| `--fill`, `--fill-2`, `--line`, `--line-strong`, `--line-control` | ink at 6/10/12/24/58 % | Quiet fields, hairlines, a control's edge (3.8:1) |
| `--elevacion` | one shadow | Only for the raised surface (`.sign`). The only depth |
| `--raya` | stripes | "In progress" |
| `--lead` | 48px | The leading slot of every row |
| radii | 12 field · 14 row · 20 raised/sheet · pill | |

Counts are neutral (`.badge`: ink on `--fill-2`). Colour is never the only signal.

## 3. Type

Mona Sans (OFL, 66 KB subset); two widths: 100 % for everything, 110 % for the page's title, the counts
that decide and the wordmark. Its default figures everywhere: its tabular figures draw a slashed zero.
Source Serif 4 italic only for a devotional's words (`.verso`). Three weights: 430, 600, 700. Inside the
shell and at the door:

| Step | Size / line | Weight, width | Use |
|---|---|---|---|
| caption | 13/16 | 600 | Table heads, tab labels, counts. Never body or metadata |
| small | 15/20 | 430 · 600 | Metadata, labels, buttons |
| body | 17/24 | 430 · 600 titles | Text, row titles |
| heading | 18/24 | 700 | Section titles (`.heading`) |
| question | 22/28 | 600 | The raised surface's question (`.sign__title`) |
| title | 28/34 → 32/38 from 1100px | 700, 110 % | The page's one title (`.voice`); no other words on the page are larger |
| numeral | 28–48 | 700, 110 % | Counts that decide (`.decision__cifra`, `.avance__n`): a figure, the one thing that may be larger than the title |

The interview's and the campero's screens keep Atrio's larger sizes in the same family; the
projector's deck keeps Figtree. Names wrap, never ellipsis.

## 4. Surfaces

| Surface | Markup | When |
|---|---|---|
| Ground | — | Default |
| Raised | `.sign` (`.sign--lead` on a home) | What needs this person now. One per screen; several asks go inside it as `.ask` blocks with a hairline between |
| Notice | `.notice` (`--warning`, `--danger`, `--success`) | A fact the page must say; soft field with its mark |
| Sheet / dialog | `<dialog class="dialog">` | A focused choice; a bottom sheet on a phone |
| Toast | Django messages | What just happened (emmaus.js) |

Cards, panels and boxes around lists do not exist. `.card`, `.quiet`, `.meeting`, `.form-section`
are open.

## 5. Rows (the one list specification)

```html
<ul class="rows">
  <li class="row row--lead">
    {% monogram person.full_name %}               {# or {% date_block day %}, {% event_arch hour %}, an icon #}
    <div class="row__main">
      <p class="row__title"><a href="…">Ana López Ruiz</a></p>
      <p class="row__meta">Cabaña 3, 14 años</p>
    </div>
    <div class="row__end">{% status "completada" "Hecha" %}</div>   {# or one action #}
  </li>
</ul>
```

- A 48px leading slot on **every** row of a screen, so text starts on one line across sections.
- A row whose only way out is its title is pressed anywhere and draws its chevron (CSS). No
  "Abrir"/"Ver" buttons.
- One trailing action at most: a `.btn--link` (its words end on the margin), or a filled
  `.btn--primary.btn--small` for the one suggested next row. A quiet removal is
  `.btn--link.btn--quieto` and its page asks first.
- Hairlines start where the text starts. Hover lifts the row to white.
- `row--stack`: the end wraps under the text in a narrow column. `row--attention`: a vela dot
  (waits for a decision). `row--unread`: a dot in the church's colour.
- A decision that waits (a count that leads somewhere): `<a class="decision">` with
  `.decision__cifra`, `.decision__texto` (`__titulo`, `__nota`), `.decision__ir`; `decision--bloqueo`
  makes the numeral vela.

## 6. People and gatherings

- `{% monogram name %}`: a circle (40px; `"sm"` 32, `"lg"` 56), one neutral tone for everybody.
- `{% event_arch hour %}`: the arch of a gathering; its sky is the hour (`sky_of`: morning, midday,
  afternoon, dusk, night, none). At most one large arch per screen; **never on operational screens**.
  The arch will hold the church's photo of a gathering when uploads exist (issue #60 decides).
- `{% date_block day %}`: the day (weekday over the number), today filled ink. Rule: **the arch
  when the hour matters (today), the day when the date matters (later)**.
- The compact event: `.evento` > arch, `.evento__texto` (`__cuando`, `__titulo`, `__donde`),
  `.evento__pie` (the state or action).
- Who signed up is never shown to members (the list is the administration's); places left are,
  where the event page already shows them.

## 7. State

`{% status code word %}`: the word with a 14px circle. Filled ink = done; half filled = in progress;
grey ring = not started; ink ring with its centre = waits for a decision; filled grey = closed; filled
chile = risk. The camp's line of arches and its key use the same half for in progress (`--raya`).
A guardian's authorization is never a state circle: a check with «Autorizada», or vela «Falta».
`status--important` (via `notices/_level.html`) is the one capsule, in vela. One or two words:
"Hecha", "En curso", "Por decidir", "Leído".

## 8. Controls

- **Buttons**: capsules, 44px minimum (50 for a phone's main pair). One `.btn--primary` per screen,
  named by its outcome ("Sí, puedo", "Retomar entrevista", "Guardar cambios"). Others outlined
  `.btn--secondary`; `.btn--link` in the church's colour; `.btn--danger(-outline)` for risk. Two
  answers on a phone share the row 50/50 (`.sign__actions--pair`).
- **Fields**: 48px, 12px radius, 1.5px `--line-control` edge; focus is the one ring of every control
  (3px ink outside a 2px gap) with an ink edge. Labels 15/600 above; a hint only for a format nobody
  could guess. A one-field form says its error once, at the field (the error summary stays for screen
  readers only).
- **Checks and radios**: drawn 22px marks inside a 44px target.
- **Links**: a link inside a sentence is the church's colour, underlined; a way to a whole list at a
  section's head («Ver todos») is the church's colour without underline; a way back («Volver a…») carries
  a chevron before it; a row's title is the row's link (ink, no underline); a name in a table that opens
  its record is ink, underlined.
- **Filters**: capsules with their count; the current one filled ink. Secondary filters (a sede) fold
  under one line («Sede: todas»).
- **Search**: a capsule field with its mark (`form.search`).
- **Forms**: `.form-section` with `.form-section__title`; `.form-actions` at the end with the one
  primary. Advanced options in `<details class="fold">`.

## 9. Words

Mexican Spanish, tú, sentence case, no jargon. A screen needs no paragraph to be understood.
Fixed words: Eventos, Aviso, Inscribirme, Mi iglesia, A tu cargo, Más, Mis datos, Mi cuenta, Pasar
lista, Esperan tu respuesta, Esperan tu decisión, Por entrevistar, Sin terminar. An action keeps its
name through the flow; its toast says it back ("Confirmaste el lunes 23"). Consent, authorization,
privacy and irreversible-action wording is never shortened away (`CRAFT.md`).

## 10. A church with the module off

`rotulo.css` is not edited. Templates under `/mi-iglesia/` are served only with the module on and use
this system freely. Shared templates (`core/`, `events/`, `registration/`, `errors/`) put new markup in
`{% if ui_v2 %}…{% else %}<the old markup, byte for byte>{% endif %}` with no stray whitespace
(`people/tests/test_screens_unchanged.py` and the byte-level tests prove it), or use only classes that
look right unstyled.

## 11. A screen is done when

`CRAFT.md` §8 passes: five-second test, one filled action, the row spec, every state tried (busy,
done, failed, empty, many, long names), 360/390/820/1440 looked at, axe at zero, no overflow, 44px
targets, reduced motion. Add it to `e2e/community-flow.mjs` and a row to `SCREEN_MATRIX.md`.
