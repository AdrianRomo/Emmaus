# Santuario, second edition — "se entiende sin leer"

**Status (2026-11-18):** the design system of Emmaus for every church that uses the `community`
module (`<html data-ui="2">`). It is a layer over Atrio (`docs/design-system.md`): Atrio's classes
keep working, Santuario restyles them and adds the shell and the components below. With the module
off nothing of this is served.

| | |
|---|---|
| Stylesheet | `static/css/santuario.css`, loaded after `rotulo.css` by `templates/layout/skeleton.html` |
| Shell | `core/shell.py` builds it; `templates/partials/ui/shell.html` and `shell_end.html` draw it |
| Pieces | `core/templatetags/shell.py`: `{% icon %}`, `{% monogram %}`, `{% date_block %}`, `|tel` |
| Behaviour | `static/js/emmaus.js` (the only script): toasts, the line of a slow page, the "Más" sheet |
| Tests | `core/test_shell.py`; browser: `e2e/community-flow.mjs` and `review-all.mjs` (axe WCAG 2.2 AA, three widths) |

## 1. The rule

**A screen is understood without reading it.** Explanatory copy is a defect of the design, not
help. Each screen has a title that names the place, at most one filled action, and its content
drawn as things: a day, a person, a state, a number.

Five consequences, in the order they are checked:

1. **No lede.** The title and the content say what the page is. A sentence under the title is the
   exception, for a page whose title cannot say it.
2. **Facts, not sentences.** "18 al 19 de noviembre, a las 17:00, en Salón principal." becomes three
   facts with their mark (`.meta`). A state is a word on a pill (`.status`, `.chip`).
3. **One way in.** A row or a card that leads somewhere is pressed anywhere on it. No "Abrir" or
   "Ver" button beside a title that is already a link.
4. **What is secondary is folded.** Advanced options and the "how does this work" of an unfamiliar
   flow go in a `<details class="fold">`, closed.
5. **What must stay, stays, shorter.** Consent and guardian authorization, who sees a piece of
   personal data, what an irreversible action does, an error that needs a specific fix. Never
   removed, never hidden behind a tooltip; one or two plain sentences.

The place it comes from is Atrio's: the white of lime (`--page`), the grey of cantera stone
(`--hundido`), petróleo for action, chile for risk, and the arch of the atrium's door for a person
or a group. Santuario adds two colours **for state only**, nopal (done) and ámbar (waits). Nothing
has a shadow and nothing has a gradient (`core/test_shell.py` enforces it): depth is a layer of
colour and a hairline.

## 2. Tokens

Declared on `:root[data-ui="2"]` (never bare `:root`: a church's own colours are written after the
sheet with the same specificity and must win).

| Token | Value | Use |
|---|---|---|
| `--page` / `--surface` | `#faf8f5` / `#ffffff` | The page; a thing on it (card, list, field) |
| `--hundido` | `#f1ede8` | The rail, quiet panels, a date, a neutral pill |
| `--line` / `--line-control` / `--line-strong` | `#e6e0d8` / ink 24 % / `#7b8a94` | Hairlines; the edge of a button; the edge of a field (3.6:1) |
| `--ink`, `--ink-2`, `--ink-3` | Atrio's | Text; secondary; the lightest allowed (4.9:1 on `--hundido`) |
| `--petroleo`, `--petroleo-tint` | Atrio's; 11 % mix | Action, links, "moving"; follows the church's colour |
| `--noche` / `--noche-2` | `#0f3640` / `#c9dde1` | The one filled panel of a home; toasts |
| `--nopal` / `--nopal-tint` | `#24693f` / `#e4f1e7` | Done, confirmed (5.7:1 on its tint) |
| `--ambar` / `--ambar-tint` | `#7d4b00` / `#faefd8` | Waits for somebody (6.1:1 on its tint) |
| `--chile` / `--chile-tint` | Atrio's | Risk and errors only |
| `--r-control`, `--r-card`, `--r-panel`, `--r-pill` | 10, 14, 18 px, 999 px | A control; a thing; a place; a pill |
| `--dur-1…4`, `--ease`, `--ease-out` | 120, 200, 320, 420 ms | Atrio's three plus one for a bar that fills |

**Type.** Figtree for everything a person reads to decide or act. Newsreader (`--font-voz`) for
**one line per screen**: the page title (`.voice`), plus the question of a next-step panel and a
confirmation. Section titles, figures, dates and names are Figtree.

**Two scales.** Inside the shell (`body:has(> .rail)`) the scale is a step tighter: body 17 px,
small 15, caption 14, section title 19, page title up to 34, controls 44 px (48 the filled one).
At the door (sign-in, public pages) and in the campero's and the interview's screens Atrio's larger
scale stays. Never write a font size by hand: use `--t-caption … --t-h1`.

## 3. The shell

One navigation in three places that always agree (`core/shell.py`).

| Width | What a person sees |
|---|---|
| 761 px and more | `.rail`: the church, the places (they scroll if they are many), and fixed at the foot the person (→ Mi cuenta), Ayuda and Cerrar sesión |
| 760 px and less | A top bar (the church, the arch → Mi cuenta) and `.tabs` at the bottom: three places and "Más"; the current tab's icon sits on a tinted pill |
| "Más" | `#menu-panel`: every place as grouped lists, a modal dialog |

Pages are one measure (`--w-app`, 70 rem) centred in what the rail leaves; a form or a reading page
adds `.narrow` (44 rem) and stays on the left edge, so every page starts on the same line. Inside
an event the event is a place in the rail and, under 1101 px, the tabs.

## 4. Components

Markup first; every class below exists in `santuario.css`.

**Page head** (`events/rotulo/_page_head.html`): `title`, optional `back_url`/`back_label`,
`action_url`/`action_label` (the one filled button), `secondary_url`/`secondary_label`. Do not pass
`lede` unless rule 1's exception applies.

**List** — one white sheet, a hairline between rows. A row whose only link is its title is pressed
anywhere and draws its own chevron (CSS, no markup). Lead it with an arch or a date.

```html
<ul class="rows">
  <li class="row row--lead">
    {% monogram person.full_name %}
    <div class="row__main">
      <p class="row__title"><a href="…">Ana López</a></p>
      <ul class="meta"><li>{% icon "telefono" "sm" %}55 1111 2222</li></ul>
    </div>
    <div class="row__end">{% status "completada" "Con acceso" %}</div>
  </li>
</ul>
```

`row--stack`: the end wraps under the text in a narrow column. `row--attention`: waits for a
decision. `row--unread`: a dot before the title. `rows--plain`: a list inside a card.
A row with buttons at its end is not a link as a whole; its title still is.

**Facts** — `<ul class="meta">` of `<li>{% icon "reloj" "sm" %}17:00</li>`. Icons for facts:
`calendario`, `reloj`, `lugar`, `grupos`, `personas`, `telefono`, `correo`, `candado`, `lectura`,
`lista`, `guia`, `servir`, `info`, `alerta`, `check`. An icon is never alone: its word is beside it.

**Date** — `{% date_block date %}` (weekday, number, month; `aria-hidden`). The row says the date
again only for a screen reader (`<li class="visually-hidden">`) or when it is a range.

**State** — `{% status "<code>" "<word>" %}` or `<span class="chip chip--ok">`. Four meanings:
done (`status--done`, `chip--ok`, nopal), moving (`--progress`, `chip--info`, petróleo), waits for
somebody (`--review`, `chip--wait`, ámbar), risk (`--risk`, `chip--risk`, chile). Not started and
closed are stone. One or two words: "Inscrita", "Por decidir", "Leído".

**Card** — one thing. `<article class="card">` with `.card__title` (its link covers the card),
a `.meta` line and an optional `.card__foot`. A grid of them: `<ul class="grid">`.

**Next step** — `.sign` (tinted) on a work screen; `.sign.sign--lead` (night) on a home, with
`.btn--on-dark`. One per screen, the question in the serif, one button.

**Buttons** — `.btn--primary` once per screen, named by what it does ("Guardar cambios",
"Inscribirme"). Others `.btn--secondary`; `.btn--danger-outline` for risk that is not the main
action; `.btn--link` for "Cancelar". Every button is 44 px tall at least. A sent form's filled
button turns into a spinner by itself.

**Forms** — `partials/ui/field.html`. The label says what goes in the field; `help_text` only for a
format nobody could guess. Group related fields in `<section class="form-section">` with a
`.form-section__title`; end with `<div class="form-actions">`. Advanced fields go in a
`<details class="fold">`.

**Views of a list** — `<ul class="filters">` of links, the current one `aria-current="page"`:
pills. Search: `<form class="search" role="search">` with `.search__input`.

**Data** — `.figures` (tiles: `dt` label, `.figures__value`), `.table` inside `.table-wrap`
(`.table--stack` turns into blocks in a narrow column; give each `td` a `data-label`), `.meter`
(`.meter__track > .meter__fill` with `style="width:62%"` and the number beside it), `.level` (five
marks, `<i class="on">` for each one that counts, with the words in `.visually-hidden`).

**Empty** — a list with nothing: `<p class="empty-line">Sin eventos por ahora.</p>` (it draws the
arch). A page with nothing and a first action: `<div class="empty">` with `.empty__title`, an
optional `.empty__text` and one button.

**A note that has to stay** — `<p class="aside-note">`, one or two sentences, for who sees this or
what happens with it. **A notice in a page** — `.notice` (`--info`, `--success`, `--warning`,
`--danger`). **What just happened** — Django messages; the shell shows them as toasts.

**Steps** — `<ul class="steps">` of `.steps__item` (`--done`) with `.steps__mark`, `.steps__title`,
`.steps__note`: a checklist read from the live data.

**Identity** — `{% monogram name %}` (`"sm"`, `"lg"`): the arch with initials, always beside the
written name. It is the only ornament of the product.

## 5. Words

Mexican Spanish, in tú, the way people talk. Sentence case. No English, no jargon.

| Instead of | Write |
|---|---|
| "En esta sección podrás ver todos los eventos disponibles…" | The title "Eventos" and the events |
| "Quienes forman parte de la iglesia, tengan o no acceso a Emmaus." | Nothing: the list shows them, a pill says "Con acceso" |
| "18 al 19 de noviembre de 2026, a las 17:00, en Salón principal." | Three facts with their mark |
| "Todavía no hay personas registradas. Registra a la primera o trae a quienes…" | "Sin personas todavía." and the button |
| "Abrir", "Ver" beside a linked title | The row is the link |
| "Listo", "Aceptar", "Enviar" | What it does: "Guardar cambios", "Publicar aviso" |
| "Los cambios se guardaron correctamente." | "Guardado." |
| A hint that repeats the label | No hint |

Fixed words: **Eventos** (never "campamentos" for the list), **Aviso**, **Inscribirme**,
**Mi iglesia**, **A tu cargo**, **Más**, **Mis datos** (how the church reaches a person),
**Mi cuenta** (how they sign in), **Pasar lista**, **Entendido**.

An action keeps its name through the flow: the button "Publicar" answers "Publicado.".
Errors say what happened and what to do, without apology.

## 6. Motion

Motion answers a person or says that something changed; nothing moves to decorate. Atrio's rules
stay (`rotulo.css` §13: the crossfade between pages, the rise of the page head and the next step,
the press of a button). Santuario adds: a toast arrives and leaves; a `.meter` fills once; a sent
button turns; the line of a slow page runs (`.nav-progress`, after 250 ms); a row's chevron leans
towards where it leads; the current tab's pill fills. `prefers-reduced-motion` turns it all off.

## 7. Accessibility

WCAG 2.2 AA, checked by axe at 390, 768 and 1440 px on every screen. 44 px targets on every
button, tab, rail link and contact link. One `h1` per page. Colour is never the only signal: a
state always has its word, a mark always has its text. Focus is Atrio's ring; inside a list or the
rail it is drawn inwards so it is never clipped. `forced-colors`: marks become text colour, pills
and arches get a border.

## 8. A church with the module off

`rotulo.css` is not edited, and a church without the module keeps Atrio. Templates are shared, so:
shorter words reach both; **a class that only Santuario styles may be used in a template only when
that template is served only with the module on** (everything under `/mi-iglesia/`) **or inside
`{% if ui_v2 %}`**. Camp and discipleship templates are restyled through the classes they already
have.

## 9. A new page

`docs/design-system.md` §7, plus: the page head without a lede; one filled button; lists as
`.rows`; facts as `.meta`; states as pills; an empty state; add it to `e2e/community-flow.mjs`, its
view name to the right set in `core/shell.py`, and a row to `SCREEN_MATRIX.md`.
