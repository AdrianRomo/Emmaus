# Santuario — the next step of Atrio

**Status (2026-11-17):** implemented as a layer over Atrio, served only to a church that uses the
`community` module. The owner's directive calls the direction "Contemporary Editorial Sanctuary";
in the code and here it is **Santuario**. Atrio (`docs/design-system.md`) stays the source of truth
for every component this file does not mention: Santuario changes tokens, adds the application
shell and a few components, and removes nothing.

| | |
|---|---|
| Stylesheet | `static/css/santuario.css`, loaded after `rotulo.css` by `templates/layout/skeleton.html` when `ui_v2` is true (`<html data-ui="2">`) |
| Who gets it | Signed in: an account whose current church has `community` on (`core/context_processors.py`). Signed out: the public pages, when the church they speak for has it on |
| Shell | `core/shell.py` builds it; `templates/partials/ui/shell.html` and `shell_end.html` draw it; `templates/base.html` uses it instead of the bar |
| Pieces | `core/templatetags/shell.py`: `{% icon %}`, `{% monogram %}`, `{% date_block %}`, `|tel` |
| Tests | `core/test_shell.py`; browser: `e2e/community-flow.mjs` (axe WCAG 2.2 AA, three widths, keyboard) |

With the module off nothing of this is served: no stylesheet, no attribute, the same bar
(`core/test_shell.py::test_without_the_module_nothing_of_the_redesign_is_served`,
`people/tests/test_screens_unchanged.py`).

## 1. The idea

Atrio is the open door of the church: white, one colour for action, sentences as titles.
Santuario is what is behind the door. It keeps petróleo for action and chile for risk, and adds
three things, each taken from the place and none from a template:

1. **Cal y cantera.** The page is the white of limewash (`#faf8f5`), not paper white; what is
   quiet (the rail, a side panel, a date) is the warm grey of cantera stone (`#f1ede8`).
2. **A voice.** What Emmaus says to a person (titles, questions, greetings) is set in a serif,
   Newsreader at 560. What a person reads to decide or act (labels, data, buttons) stays in Figtree.
3. **The arch.** A person or a group is drawn as the door of the atrium: round above, square on the
   ground, with their initials. It is the only ornament, and it is always beside the written name.

Boldness is spent in one place: the home's next step is the only filled panel (night petróleo).
Everything around it stays quiet: no cards with shadows, no gradients, no pills, no eyebrows.

## 2. Tokens

Declared on `:root[data-ui="2"]`, the same specificity as the church's own colours, which
`skeleton.html` writes after the sheet so they still win (`test_the_church_colours_still_win…`).

| Token | Value | Use | Contrast |
|---|---|---|---|
| `--page` | `#faf8f5` | The page | ink 15.3:1, `--ink-3` 5.4:1, petróleo 6.6:1 |
| `--surface` | `#ffffff` | Fields, the current place in the rail, a row that waits for a decision | — |
| `--hundido` | `#f1ede8` | The rail, `.quiet`, `.date-block`, hover | `--ink-3` 4.9:1 |
| `--line` | `#e3ddd6` | Separators | — |
| `--noche` / `--noche-2` | `#0f3640` / `#c9dde1` | The home's next step and its secondary text | white 12.9:1; 9.2:1 |
| `--font-voz` | Newsreader, then Iowan/Palatino/Georgia | Titles | — |
| `--r-panel` | 16 px | Panels; controls keep `--r-control` (10 px) | — |
| `--rail-w`, `--tabs-h` | 17 rem (14.5 rem under 1100 px), 4.25 rem | The shell | — |

Everything else is Atrio's (`--ink`, `--petroleo`, `--chile`, the type scale, spacing, motion).

**Type.** Newsreader (OFL, `static/fonts/newsreader-display-latin.woff2`, 35 KB): the optical size
fixed at 36, weights 400–600, a 199-character Latin subset cut with fontTools. It is not preloaded:
only titles use it, so a title may settle a moment late and the text never waits. `.voice`,
`.sign__title` and `.confirm__title` take it everywhere under `data-ui="2"`, the camp's screens
included; the projector deck keeps Figtree 800 because it was fitted glyph by glyph.

## 3. The shell

One navigation in three places that always agree, because one function builds them.

| Width | What a person sees |
|---|---|
| 761 px and more | `.rail`: the church (logo or "Emmaus" and the name), the sections, then the person (arch, name, role → Mi cuenta), Ayuda, Cerrar sesión |
| 760 px and less | The rail becomes a top bar (the church, the arch → Mi cuenta); `.tabs` fixed at the bottom: Mi iglesia, Grupos, a third place that depends on the person, and "Más" |
| "Más" | `#menu-panel`: everything, as a modal dialog (the existing `emmaus.js` behaviour: focus moves in, Tab stays, Escape closes, focus returns) |

Sections, in order, each only when it has something: what is everybody's (**Mi iglesia**,
**Grupos**, **Eventos** for who works in an event), **A tu cargo** (each group the person guides,
with a count of requests waiting), **Discipulado** (the module's existing links), **Administración**
(Personas, Grupos de la iglesia, Configuración). The third tab is "A tu cargo" for a guide,
"Eventos" for event staff, "Mi cuenta" otherwise.

Rules:

- The shell decides what is *shown*; the views decide what is *allowed* (decision 0004, rule 4).
- The current place is lifted to white with a 3 px mark in `--mark` and `aria-current="page"`;
  exactly one place is marked.
- Icons are a closed family of eleven (24 px grid, 1.75 px strokes), only in the navigation, always
  beside their word, `aria-hidden`. Nothing else in the product has an icon.
- Pages are anchored to the rail, not centred: every page starts on the same line.
- The event shells (`shell_coordinator`, `shell_leader`, focus, kiosk) keep Atrio's bar and wear
  the new tokens. Moving them into the shell is the next step of the migration (`SCREEN_MATRIX.md`).

## 4. Components added

| Component | Where | Rule |
|---|---|---|
| `.sign--lead` + `.btn--on-dark` | Mi iglesia, a group's page | The one filled panel of a screen a member lands on. Any other next step keeps Atrio's tinted `.sign` |
| `.hero` | Mi iglesia | The greeting, up to 56 px; the lede says the date in words |
| `.mono` (`--sm`, `--lg`) | Rows of people and groups, group heads, the rail | Decorative; never without the name |
| `.dates` + `.date-block` | "Lo que viene", meetings | The day drawn (weekday, number, month) and said again in the row's text; today's is filled |
| `.camino` | "Donde has estado" | A real sequence in time, so it is drawn as a line; the current step is filled |
| `.row--lead`, `.row__contact` | Lists | A row led by an arch or a date; phone and e-mail as `tel:`/`mailto:` links with 44 px targets |
| `.quiet` | Side column | A stone panel for what is useful and not urgent; never the main action |
| `.cols` | Mi iglesia | Reading column and a quiet column; one column under 1100 px |
| `.heading-row` | Section title with one link | — |
| `.group-head`, `.meeting` | A group | The arch beside the title; the next meeting for who is part |
| `.badge` | Rail, tabs | A count that waits for the person; the only filled circle |
| `.filters` (Atrio) | Group kinds; "Personas / Reuniones" | Views of the same thing |

Rows that end in controls stack by the width of the **column** (a container query at 38 rem), not
of the window: beside the rail the column is narrow long before the window is.

## 5. Motion, accessibility, print

- No new motion rule. Atrio's thirteen apply (the page head and the next step rise on load);
  the rail and tabs only change colour over `--dur-1`. `prefers-reduced-motion` turns it all off.
- axe-core, WCAG 2.0–2.2 A and AA: zero violations on every community and people screen at 390,
  768 and 1440 px (`e2e/community-flow.mjs`, 2026-11-17).
- Targets: 44 px on every tab, rail link, contact link and button (checked in the browser gate).
- One `h1` per page; rail section labels are paragraphs that name their list (`aria-labelledby`).
- Focus: Atrio's ring; on the night panel the ring is white with a night gap.
- `forced-colors`: arches, quiet panels and badges get a border. Print: no rail, no tabs.

## 6. Words fixed here

**"Eventos"** is the shell's word for the list of events (Atrio's bar says "Campamentos"; the
page's title stays "Campamentos y eventos"). **"A tu cargo"** is where a guide finds their groups.
**"Más"** is the phone's panel. **"Mis datos"** is how the church reaches a person; **"Mi cuenta"**
is how they sign in. **"Pasar lista"** is recording who came; a meeting says "Vinieron 5 de 8".

## 7. Not built yet

Tables, charts, dialogs, calendar month views and media components have no Santuario treatment
beyond the tokens: they are Atrio's. They get one when the module that needs them is built.
