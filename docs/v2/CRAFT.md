# Craft: how Emmaus behaves

The rules that make every screen feel built by the same careful hands. `DESIGN_SYSTEM.md` says what things
look like; this file says how they behave, how precise they are, and how a journey holds together. A screen is
not finished until it passes §8.

## 1. What premium means here

Every detail is deliberate and nothing is there to impress a screenshot. In practice:

- **Truthful.** The interface never says "done" before the server saved. It shows at once that it heard the
  press, then shows the real result.
- **Continuous.** An action changes the place where it was taken. The person stays where they are, sees the new
  state, and can take it back where the rules allow.
- **Quiet.** One filled action per screen. Motion only answers a person or shows what changed. No decoration that
  carries no information.
- **Exact.** One type scale, one spacing scale, one icon family, one geometry for controls, used the same way
  everywhere.

## 2. Interaction states (every interactive component)

| State | Rule |
|---|---|
| Rest | Its role is readable without hover: a button looks pressable, a row that opens shows a chevron. |
| Hover (pointer only) | A colour step on the surface, `--dur-1`. Never movement, never the only signal. |
| Pressed | 1px settle or a darker step, at once (no delay). |
| Focus | Ink ring outside a white gap (`:focus-visible`), never clipped: inside a list or the rail it is drawn inwards. |
| Busy | `aria-busy="true"`: a spinner replaces the label, the button keeps its width, its siblings are disabled. |
| Done | The region shows the new state (a word with its mark, the date confirmed) and how to change it. |
| Failed | The control comes back as it was; one line under it says what happened and what to do (`role="alert"`). |
| Disabled | 45 % opacity and `not-allowed`; never the only way to say why: the reason is written nearby. |

## 3. Actions in place

`form[data-inline="<id>"]` (emmaus.js, "Actions in place") is the one mechanism. The form works without
JavaScript; with it:

1. The pressed button turns busy at once; the form's other buttons wait.
2. The POST goes to the usual view with `siguiente=<this page>`; the view redirects back there
   (`core.nav.safe_next`; another site is ignored).
3. The region with that id is replaced by the same region from the returned page, with a crossfade of
   `--dur-2` (View Transitions, where available). Counts in the rail, tabs and "Más" are taken from that page.
4. The message of the action arrives as a toast (`role="status"`). Focus goes to `[data-inline-focus]` in the
   new region when it was inside the old one.
5. The region missing from the returned page means the thing is done: it leaves. Another page means one more
   step: the browser goes there and carries the message.
6. A network or server failure restores the buttons and writes one line under them.

Use it for answers and toggles that change one region: serving (Sí, puedo / No puedo / Cambiar), «Enterado»,
«Marcar como leído», a day of a reading plan, «Pedir unirme» / «Retirar solicitud», a guide's «Aceptar» /
«Rechazar», «Inscribirme» where no further step can follow. Do NOT use it where the action needs a page of its
own: anything that can lead to the minors' age or authorization step, deleting, publishing, forms with fields.

Optimistic display is never used: Emmaus's actions are fast enough that a truthful busy state reads as
immediate, and a wrong optimistic state about serving or a minor's registration would mislead.

## 4. Motion

| Purpose | Duration | Easing | Notes |
|---|---|---|---|
| Colour, focus, hover | `--dur-1` 120 ms | `--ease` | Controls only. |
| A region changes state | `--dur-2` 200 ms | `--ease` | Crossfade, size morph via the view transition name `accion`. |
| A page changes | `--dur-1` | `--ease` | Cross-document crossfade; the rail and the tabs keep their place (`marco`, `accesos`). |
| Something arrives (toast, sheet, dialog) | `--dur-3` 320 ms in, `--dur-2` out | `--ease-out` in | Rises from where it belongs by 6–12 px; leaves faster than it came. |
| A bar fills (progress, meter) | `--dur-4` 420 ms | `--ease-out` | Once, on first paint. |
| A slow page | after 250 ms | linear-ish | The thin top line; never for an instant answer. |

No entrance animations on sections, no stagger beyond the tally's 8 ms, no springs, no parallax. Expanding
content (a fold, "Cambiar respuesta") opens from its handle; the content fades in, heights do not animate.
`prefers-reduced-motion`: every animation and transition is off; states still change.

## 5. Surfaces

| Surface | When |
|---|---|
| The page (open ground) | Default. Headings, text, open lists. |
| Grouped list | Rows of the same kind that open or act: notices, people, events, campers. One sheet, hairlines inset to the text. |
| Raised surface (the only elevation) | What needs this person now: a serving request, an interrupted interview, a group request for a guide. At most one per screen. |
| Tinted field | A field group in a form; a quiet fact that frames the page (the camp's dates). |
| Sheet / dialog | A focused choice that must not lose the page: choosing a computer, a confirmation. Never a form of more than three fields. |
| Floating | Toasts, the phone's tab bar. Solid, never see-through. |

## 6. Precision

- **Columns.** Inside a list, leading marks (avatar, date tile, icon) share one column width; titles start on one
  line; trailing actions share one right edge. A row's text is optically centred on its leading mark.
- **Metadata** is never smaller than 15 px inside the shell or under 4.5:1 contrast; when it does not fit, it
  wraps or the row grows. No ellipsis on a person's name: names wrap.
- **Numbers** that are compared (counts, progress, times in a column) use tabular figures; prose numbers do not.
- **Buttons** keep their label on one line; on a phone, a pair of answers shares the row 50/50; a lone primary is
  full-width.
- **Icons**: one family (24 px grid, 1.75 px stroke), 20 px beside text, 24 px in navigation; never alone.
- **Touch**: 44×44 px minimum for every target; 8 px between adjacent targets.

## 7. Journeys

| Journey | Entry | Primary action | Feedback | Recovery | Back later |
|---|---|---|---|---|---|
| Serve a date | The home's raised request; Servir | Sí, puedo / No puedo, in place | The request becomes "Vas a servir" / "No puedes", with "Cambiar" | Too late to change: the reason, the answer kept | Servir lists it; the home keeps it until the day |
| Sign up for an event | The home's event; Eventos | Inscribirme | "Te inscribiste" on the event, the home and Eventos | Age or authorization step: the event page explains what is pending | The event shows the state; cancel until it starts |
| Read a notice | The home; Avisos (count) | Open it; «Enterado» if urgent | The dot leaves; the count drops everywhere | — | Avisos keeps it |
| Join a group | Grupos; the home's suggestion | Pedir unirme | "Pediste unirte" with the state | Retirar solicitud | The home's "Lo que pediste" |
| Decide a request (guide) | The home's raised request; A tu cargo (count) | Aceptar / Rechazar, in place | The row leaves; the count drops | — | The group's Personas |
| Resume an interview | Coordinator home: the raised "Sin terminar" | Retomar entrevista | Lands on the question where it stopped | Descartar asks first and says what is lost | The coordinator home until done or discarded |
| Decide follow-up (coordinator) | "Esperan tu decisión" row | Decidir | The count drops | — | Pendientes |
| Authorization of a minor | The registration row (admin); the campero record | Registrar autorización | The state word changes; the full wording stays | Withdraw it | The row's state |
| Correct my data | The home's "Agrega tu teléfono"; Mis datos | Guardar cambios | Toast "Guardamos tus datos" and the home stops asking | Field errors beside the field | Mis datos |

Permission rules are the views', never the shell's: hiding a control is not authorization.

## 8. Before a screen is done

1. It is understood in five seconds by someone new; the main action is the only filled one.
2. Every element aligns to its column; nothing is decorative without information.
3. Every control has its six states (§2), checked with the keyboard and on a phone.
4. Every action states its real result; failure was tried (offline in the browser) and recovers.
5. Empty, one, many, long names, errors: each looked at.
6. 390, 768 and 1440 px looked at as screenshots; axe at zero; no overflow; 44 px targets.
7. Reduced motion: still usable, nothing hidden behind an animation.
