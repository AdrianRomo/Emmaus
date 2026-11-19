# Wave 11 restyle: agenda (series and e-mail subscriptions)

Branch `v2/wave-11-agenda`, cut from `v2/wave-11-diseno`. The screens of event series (wave 9,
`docs/v2/wave9/series.md`) and of an event's e-mail subscriptions (wave 10,
`docs/v2/wave10/suscripciones.md`) brought to `CRAFT.md` and `DESIGN_SYSTEM.md`. No rule, permission,
token, URL or migration changed; one display fact was added to a view (see "In place").

## 1. Inventory, and what was wrong (written before changing each screen)

| Screen (template) | Who | Came to do (the one filled action) | Wrong against the law |
|---|---|---|---|
| A series (`series_admin.html`) | Church administration | See its dates and say a day is off; the filled action is «Cambiar la serie» | `lede=` with the description; `step__actions`; `notice--quiet`; rows without the 48px lead or a date block; three things at the end of a row (state, «Abrir», a `btn--secondary btn--small`); two links at the end of a past row; the answer reloaded the page; the place repeated on every row |
| Create / change a series (`series_form.html`) | Church administration | «Crear la serie» / «Guardar la serie» | A paragraph as `lede=`; `notice--quiet`; fourteen fields with no `form-section`; seven days stacked; `step__actions` |
| A series for a member (`series.html`) | Person of the church | Open one date | The place as `lede=`; rows without the date block; the state pushed the date onto two lines at 390 |
| Eventos de la iglesia (`admin_list.html`: series, calendar) | Church administration | «Crear un evento» | A `.hint` paragraph stuck to the heading; the calendar's address between two lists, with its paragraph first and `step__actions` |
| Public page of an event (`public.html`: offer, calendar) | Anybody, no account | «Entrar para inscribirme» | Two `<p class="more">` links (bold, underlined, under 44px); the offer stuck to «Programa»; the calendar link at the very end, far from the date |
| Subscription pages (`subscription.html`, seven states) | Anybody, no account | «Pedir los avisos por correo» / «Sí, avísenme por correo» / «Sí, darme de baja» | `step__actions`; six `.row`s used as a bulleted list; `<p class="more">` as the way back; loose paragraphs |
| Public notices (`public_notices.html`) | Church administration | «Publicar el aviso» | `row--lead` without a lead; «Editar» / «Borrar para siempre» as `btn--secondary btn--small`; `notice--quiet`; `step__actions`; `mt-*` spacing; the form stuck to its sentence |
| `notice_mail` in `page_form.html`, series rows in `_event_row.html`, `_event_today.html`, the home | — | — | Looked at: already on the system (a check with its protected help; a line of metadata with its link). Not changed |

States looked at: a series with no place, a long name and a long place, a date called off (with and without
somebody signed up), changed by hand, unpublished and listed to review, dates the series left behind, an ended
series; the form empty, with errors and filled; a member's series with «Te inscribiste» and «Este día no hay»;
the public page with and without notices; every state of the subscription pages; notices in force, retired and
none, with the page public, not public, and the sending late.

## 2. What changed

- **A series**: the head carries the one filled action; the description is a fact of the summary list; every
  date is a row with its day drawn (`date_block`, today filled), the date in words as the way into its page (no
  «Abrir»), its state as a word with its mark, and one trailing text action. A past date opens its page and
  offers «Opiniones» (who signed up is one step further, on that page). The place is said on a row only when it
  is not the series'. Counts beside the section titles.
- **In place** (CRAFT §3): «Este día no hay» / «Sí hay» (`data-inline="fecha-<id>"`,
  `data-inline-also="serie-revisar serie-fuera"`). The view always answers with this same page, so no other step
  can follow. The row then says the real result: the state word, «Ya no aparece en Eventos ni en el calendario
  público.» and, when somebody holds a place or waits in line, «Hay personas inscritas: avísales tú, Emmaus no
  les envía nada.» (the message's own words; `series_views.admin_detail` now marks those rows, one more query).
  «Sí hay» can move a date into «Fechas que no cambiamos» or «Fechas que la serie ya no incluye»: both are
  regions that are always on the page and are refreshed with the row. Focus stays on the row's button. Without
  JavaScript the forms post as before. Ending and resuming stay ordinary forms.
- **The form**: four sections («Qué es», «Cuándo», «Quién lo ve», «Cómo se inscriben»), the days in columns,
  `form-actions`; what a change does stays in a notice, word for word.
- **A member's series**: the place and the rule as facts under the title, «Fechas», rows with the day drawn; the
  state is a line of the row, so the whole row still opens.
- **Eventos de la iglesia**: the series rows keep their state and rule on one line and the next date on another;
  the calendar's address moved to the end and is drawn as the event's public link is (field, «Copiar dirección»,
  then what a subscriber sees).
- **Public page**: «Agregar a mi calendario» under the facts, where the date is; the e-mail offer closes the
  notices; both are text actions of 44px. Still no form, no cookie and no session on GET.
- **Subscription pages**: one look for the seven states (the church above the title, one button, full width on
  a phone); what happens to the address is a plain list, every sentence kept; the way back is a back link above
  the form and an outlined button after «Revisa tu correo».
- **Public notices**: a notice in force is a row that opens (its title leads to edit or retire it); a retired
  one offers its quiet removal, whose page asks first; what stops an e-mail is said in vela.

## 3. Runbook

- Files: `templates/agenda/{series_admin,series,series_form,admin_list,public,subscription,public_notices}.html`,
  `agenda/series_views.py` (`row.has_people`), `static/css/santuario.css` (the block
  `/* === agenda (wave 11) === */ … /* === fin agenda === */`, before §15), `agenda/tests/test_series.py`,
  `agenda/tests/test_calendar.py`.
- No migration, no setting, no new URL. Revert: `git revert` the commit of this branch; nothing else to undo.
- The browser journey (`e2e/community-flow.mjs`, step 7) presses «Este día no hay» and «Sí hay» waiting for a
  navigation; they now act in place, so those two presses must use the journey's `act()` helper.

## 4. What still falls short

- A date's title inside a row that also has an answer is a link of one line of text (under 44px tall), as the
  event rows of «Eventos de la iglesia» already are; the whole row cannot be the target while it holds a second
  control.
- The top of «Avisos públicos» is still three paragraphs: each sentence is a promise the tests protect.
- On a phone, a recent notice with a long title overlaps the place of today's event in Eventos
  (`_recent_notice.html` inside `_event_today.html`); it is outside this area and was left as found.
