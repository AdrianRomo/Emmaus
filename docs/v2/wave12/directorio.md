# Wave 12: the directory between members (issue #91, third part)

Branch `v2/wave-12-directorio`. **High risk**: it shows people's contact data to other people.
Synthetic data only. It answers decision 0008's open question Q-C4 ("may members see each other?")
conservatively: yes, but only those who chose to, only what each chose, and only to each other.
This is a default a church can read before it opts in; it is not the church leadership's or a
privacy reviewer's approval of the wording, and #91 stays open for that.

## 1. What was built

| Piece | Where |
|---|---|
| `DirectoryPolicy` (one row per church: on/off, who accepted, when, wording version) and `DirectoryEntry` (one row per person: appears or not, four separate fields, when accepted, wording version) | `people/models.py`, migration `people/0010_directory` (two `CreateModel`, one check constraint inside the second) |
| Every rule and every write | `people/directory.py` |
| The three screens | `people/directory_views.py`, `people/directory_urls.py`, `templates/people/directory*.html`, `_directory_row.html`, `_directory_wording.html` |
| The "known minor" rule, moved from `oracion.access` (which re-exports it), plus the same rule as a list filter | `people/minors.py` |
| Merge decision | `people/merge.py` (`NOTHING_MOVES`) |
| Navigation | `core/shell.py` (`DIRECTORY_VIEWS`, one block) |
| "Mis datos": the row «Aparecer en el directorio» and a truthful sentence about who sees the phone | `groups/views.py::profile` (one line), `templates/groups/profile.html` |
| "Políticas de la iglesia": says on or off, and that it is not changed there | `policies/views.py`, `templates/policies/form.html` |
| Audit actions `directorio_politica`, `directorio_eleccion`; metadata key `fields` | `people/audit.py` |
| Seed flag `--directorio` | `groups/management/commands/seed_community_demo.py` |
| CSS: one class (`.directorio__texto`) in the block `=== directorio (wave 12) ===` | `static/css/santuario.css` |
| Tests (50) | `people/tests/test_directory.py` |

Nothing was added to the Django admin, to the people export, to the import, to `tenant_integrity`
(an entry has no church column of its own: it is its person's) or to the scheduler.

## 2. Routes and who may use each

All under `/mi-iglesia/directorio/`, all behind `groups.views.community_view` (signed in; the
`community` module on for the church of the request; a person of that church; otherwise 404, or the
sign-in page when signed out). No address, field or form names a person.

| Route | Name | Who | Rule |
|---|---|---|---|
| `` (GET, `?q=`, `?pagina=`) | `directorio:list` | Who is in the directory; the church's administration; its pastors. Only while the switch is on | `people.directory.may_read`, asked in `directory_views.directory` |
| `aparecer/` (GET; POST `paso=ver`, `confirmar`, `cambiar`, `salir`) | `directorio:mine` | The person of the request, about themselves | `directory.shows_choice` to open it; `directory.opt_in` / `opt_out` → `_own_locked` refuse any account that is not that person |
| `politica/` (GET; POST `paso=encender`, `apagar`) | `directorio:policy` | Who holds the church role of pastor in this church | `directory.is_pastor` (= `resources.approvals.can_approve`) in the view, and `_pastor_or_refuse` in each write |

Everybody else gets the 404 of a page that does not exist. For the list that is: somebody who has
not chosen to appear, a visitor, a person with no stage, "ya no asiste", a known minor, anybody of
another church, and everybody (administration and pastors included) while the switch is off. For
the switch: the administration, a guide, a member, a pastor of another church (who reaches only
their own church's switch).

## 3. The rules, as the code holds them

* **Who appears** (`directory.visible_entries`, the one read path, evaluated on every request): the
  church's switch on under the wording in force; the person's entry `listed` under the wording in
  force; stage `miembro` or `asistente`; an active account; not joined into another record; not a
  known minor. Nothing is cached and nothing is written when one of these changes: the person is
  simply not in the next query.
* **Who reads**: the same people (reciprocity), plus administration and pastors, who do not appear
  unless they chose to.
* **What a row holds**: `directory.Row`, a plain object with the name and only the chosen values. A
  screen is never handed a `Person`, so a field that was not chosen cannot reach a template. The
  list and the person's preview are drawn by the same partial from the same function.
* **Search**: `Person.normalized_name` only. A phone, an address, a group or a team typed there
  finds nobody.
* **Consent record**: `accepted_at`, the four booleans and `wording_version` on the entry; one audit
  row per choice with `person_id`, `value` and `fields` (the fixed words `phone`, `email`, `groups`,
  `teams`), never a value.

## 4. Product decisions taken here, and why

Given by the brief and built as given: off twice by default; per field; reciprocity; minors never;
name-only search, paged, no export; opting out immediate and total; the switch off hides and keeps
choices; a wording version; the merge rule; no export column; the shell rule.

Decided where the brief did not, always towards less shown and fewer who see:

1. **Appearing needs the same stage as reading.** A visitor or a person with no stage is not
   offered the choice at all, so nobody can be listed without being able to see who else is. The
   cost is real and the pastor's screen says it: in a church where the administration has not
   written stages, nobody can take part until it does.
2. **An account is needed to stay listed.** If the account is deactivated or removed from the
   record the person disappears: they could no longer take their choice back themselves.
3. **Choosing is two steps with a token.** The form saves nothing; «Ver cómo me verán» shows the row
   exactly as others will see it and the full wording; «Aparecer así en el directorio» carries a
   fingerprint of the wording, the fields and every value shown. If a phone, an e-mail, a group or a
   team changed meanwhile, nothing is saved and the preview is shown again.
4. **The button is the consent; there is no checkbox for the person.** The wording sits directly
   above the one filled button. The pastor's acceptance is a checkbox, as in prayer requests.
5. **Leaving clears the choices** (a database check constraint: no field without `listed`). Leaving
   needs nothing but being that person: not the switch, not a stage. Somebody who is hidden by a
   rule (switch off, stage changed, a record that now says minor, a new wording) still reaches the
   page, which then offers only the way out.
6. **One wording version for both acceptances.** A new `WORDING_VERSION` switches every church off
   until a pastor accepts again, and then hides each person until they accept again. Their marks
   stay on the form.
7. **Values follow the record.** The directory shows the phone and e-mail on record now. If the
   person edits them, or the administration corrects them, or a merge changes them, the directory
   changes too. The wording says so, and "Mis datos" says so beside the fields once they are shown.
   Not built: freezing the value at the moment of consent.
8. **Groups and teams are all or nothing per kind**: every active group (or team) of the church the
   person is in today, by name, including ones joined later. The wording says so. Decision 0008
   rule 6 (members do not see a group's list) still holds for everybody who did not choose this.
9. **No page per person and no id anywhere.** Rows open nothing; a phone and an address are links
   of that one person. Nothing can be guessed or enumerated by address.
10. **Reads are not audited.** A log of who looked at the directory would itself be data about
    people; the throttle is the control.
11. **Merge**: the entry stays on the record it was given on. The survivor keeps its own; the
    absorbed record is hidden, so its choice is dropped for as long as the merge holds, and the undo
    brings it back untouched. When the absorbed record was the one with the account, the account
    moves to a survivor with no entry: that person stops appearing and stops reading until they
    choose again. Nothing is counted or shown in the merge preview (its token is the same with and
    without an entry).
12. **The pastor's door**: while the switch is off, a pastor alone gets «Directorio entre miembros»
    under "A tu cargo"; while on, the link is «Directorio» for every reader and the switch is a row
    under the list.
13. **The count on the list** ("31 personas", the pager's total) is the length of a list every
    reader can read row by row, not a statistic about people; no other number is shown, and the
    pastor's screen shows none.

### The throttle, and its worst case

Every view of the list (a page, a search) counts against the account and church:
`VIEW_LIMIT = 15 views per hour, then a 20-minute wait that doubles`, pages of 20
(`events.services.throttle`, replayed in a script for an account that asks again the second it may).

| After | Views | People at most |
|---|---|---|
| the first sitting | 15 | 300 |
| 1 hour | 21 | 420 |
| 3 hours | 48 | 960 |
| 24 hours | 291 | 5,820 |

So a member can read a directory of up to 300 people in one sitting. That is what a directory is
for, and the wording tells each person that whoever sees them can write their data down. What the
limit prevents is a fast, quiet copy of a large church and scripted searching. The administration
and pastors are counted like everybody.

## 5. Verified

* `people/tests/test_directory.py`: the happy path, every denial in the brief (each paired with the
  same request answering for somebody who may), CSRF, escaping, the stale preview, a stale copy of a
  person, the switch off and on again, a wording bump, the throttle, merge and undo, an account of
  two churches, the seed, and that the list filter for minors agrees with `known_minor` in nine
  cases.
* In a browser (Playwright, synthetic seed plus 27 extra synthetic people), at 390, 768 and 1440 px
  with axe: the list with many rows and a second page, a search with one result, with none and with
  a very long name and address, the empty list of a church where nobody chose, the list as a pastor,
  the choice for the first time and when already listed, the preview with every field marked (two
  of them empty), the switch off, its error, and on, "Mis datos" and the home. Axe reported nothing
  and nothing overflowed.
* By hand in that browser at 390 px: the choice with the keyboard only (Space on a checkbox, Enter
  on the button), the confirmation while offline (it is not sent, says so, and «Reintentar» saves
  it), the wait screen after 15 views, and leaving with the keyboard.

## 6. Not built

* A photo, an address, a household, a birthday: none exists in the directory on purpose.
* A page per person, a download, "copy all", an e-mail to several people.
* Freezing the phone or e-mail at the moment of consent (decision 7).
* A notice to the person when a merge or a stage change takes them out; they see it in "Mis datos".
* A retention rule for entries of records joined more than 30 days ago (they are read by nothing).
* The choice page with the `community` module off: with the module off nobody is shown at all.

## 7. Not verified

* PostgreSQL: SQLite only. The locks are `select_for_update()` on a single table each.
* True concurrency (two requests at once); tested as stale tokens and stale copies.
* The screens looked at by eye are named above. Not looked at: the three "you are hidden" notices of
  the choice page (switch off, stage changed, new wording), which are covered by tests only; the
  administration's policies page; a screen reader; reduced motion (the screens add no motion).
* The wording has had no legal or pastoral review.

## 8. Recommended journey steps for `e2e/community-flow.mjs` (with `--directorio`)

1. The pastor opens `/mi-iglesia/directorio/politica/`: on, with «Apagarlo»; axe at three widths.
2. `fernanda.directorio@demo.invalid` opens Directorio from the rail: four rows, Gerardo with a
   `tel:` link, Hilda with none; searching "5551" finds nobody.
3. `julia.directorio@demo.invalid`: no «Directorio» in the rail and 404 on the list; "Mis datos" →
   «Aparecer en el directorio» → mark the phone → «Ver cómo me verán» → «Aparecer así en el
   directorio»; the rail now lists Directorio and the list has five rows.
4. Julia presses «Dejar de aparecer»: the list answers 404 again.
5. `valeria.soto@demo.invalid` (no stage; 17 at the camp): no row in "Mis datos", 404 on all three.
6. The administration: 404 on the switch; "Políticas de la iglesia" says «encendido».
