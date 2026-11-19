# Wave 9: people data quality, first slice of #91

Duplicate review and an audited, reversible merge of two people of the **same church**. Branch
`v2/wave-9-personas`. High risk: identity and authorization. Synthetic data only.

## 1. Inventory (written before the code)

Source: `origin/v2/foundation` at `6169e42`. The list of foreign keys was taken from the models by
introspection (every concrete relation whose target is `people.Person`), not by reading names, and
`people/tests/test_merge_inventory.py` repeats that introspection: a foreign key to `Person` that
nobody decided about fails the suite, and at run time a merge is refused while such a table holds
rows of either person.

### 1.1 What `Person` holds

| Field | Notes for a merge |
|---|---|
| `church` (PROTECT) | Never changes. Both records must be of the church of the request. |
| `first_name`, `last_name`, `normalized_name` | The survivor keeps its own. The administrator chooses which record survives, never types a name. |
| `email`, `phone`, `preferred_contact_method` | The contact fields. Equal: nothing to do. Survivor empty: filled from the absorbed record. Both set and different: the administrator picks one of the two values. |
| `birth_date` and the three `birth_date_verified_*` | **Never written by a merge.** `Person.save` clears the verification when the date changes and fires `age_facts_changed`, which re-settles registrations in `agenda`: a merge that wrote a date would change who counts as a minor. See the refusals. |
| `created_from` | The survivor keeps its own. |
| `user` (SET_NULL; unique per church and account) | The account link. Both set: refused. Only the absorbed record has one: it moves to the survivor (the absorbed row is emptied first, the constraint allows one person per church and account). |
| `merged_into` (self, SET_NULL) | See 1.2. |

### 1.2 `merged_into`: a field, not a workflow

Before this slice **nothing wrote it**: no service, view, command or admin action (the Django admin
page of `Person` is read-only). It was only *read*, as "this row is not a person any more":
`people/views.py::_people`, `people/persons.py::own_person`, `people/services.py`
(`ensure_own_person`, `verify_birth_date`, `link_participant`), `people/forms.py` (e-mail taken),
`core/authz.py::churches_of`, `core/shell.py`, `events/services/team.py`, and the person pickers and
guards of `groups`, `serving`, `care`, `agenda` and `notices`. Tests set it by hand. So every screen
already hides a row with `merged_into` set and answers 404 for its id; nothing moved the hidden
row's memberships, registrations or account anywhere. `tenant_integrity` already checks that
`merged_into` stays inside one church.

Consequence used here: "deactivate the absorbed record" is `merged_into = survivor` (there is no
other active flag on `Person`), and after a merge the absorbed row never holds an account, so every
lookup by account ignores it too.

Left alone and reported: `merged_into` is `SET_NULL`. Deleting a survivor would bring the absorbed
row back as an active person. Nothing in the product deletes a `Person` today, and `PersonMerge`
protects both rows (PROTECT), so the delete is refused while the merge record exists.

### 1.3 Campus

`people.CampusAffiliation`: several per person, unique per person and sede, at most one primary per
person. Additive and unread by the camp. Merge: rows for a sede the survivor does not have move; a
row for a sede the survivor already has stays on the absorbed record. If both have a primary sede
the survivor's stays primary and the moved one is demoted (recorded in the manifest, restored by
the undo).

### 1.4 Camp links (`people` ↔ `events`)

`people.EventParticipantLink` ("this campero is this person"): one per campero, one per person and
event, written only by `people.services.link_participant` / `unlink_participant`, after the event,
audited. The camp's `EventParticipant` has no person column and no camp screen reads the link;
questionnaire answers, interviews, follow-ups and pastoral notes hang from the campero, never from
the person.

Merge: both records linked to a campero of the **same event**: refused (two camperos of one event
cannot be one person; a human has to look). Otherwise each link of the absorbed record is undone
and made again for the survivor **through those two services** (their checks, their audit rows).
A link whose event is not over any more refuses the merge. No camp row is read, written or
re-pointed: the answers stay where they were, behind `events/access.py`. Because the services
delete and create, a link changes its id; the undo restores the same campero-to-person pairs.

### 1.5 Every table with a foreign key to `Person`

"Stays" means the row keeps pointing at the absorbed (hidden) record because the survivor already
has the equivalent row; it is counted in the preview and never deleted.

| Table and column | Unique with | A merge… | Why |
|---|---|---|---|
| `people.Person.merged_into` | | Refused while the absorbed record is itself the survivor of a merge that can still be undone. After that window the older rows keep pointing at the absorbed record (a chain, same church). | Undoing the older merge would have to pull rows out of a third record. |
| `people.CampusAffiliation.person` | sede; one primary | Moves; same sede stays; second primary demoted | 1.3 |
| `people.EventParticipantLink.person` | event; campero | Same event in both: **refused**. Else re-linked through the services | 1.4 |
| `groups.GroupMembership.person` | group | Moves. Same group in both: stays if the absorbed one is inactive (history); **refused** if it is active | Source of `access.RoleAssignment`. Two live rows for one human in one group, possibly with different roles, is a question about who guides the group: the administration settles it in the group first. |
| `groups.GroupJoinRequest.person` | group, while pending | Moves. Both pending for the same group: **refused** | A hidden record cannot be left asking a guide for an answer. |
| `groups.GroupAttendance.person` | meeting | Moves; same meeting stays | Both were marked present; the survivor's mark is the same fact. The count of that meeting is not rewritten. |
| `notices.AnnouncementRead.person` | notice | Moves; same notice stays | The survivor's own read state wins. |
| `notices.EmailPreference.person` (one to one) | person | Moves if the survivor has none; else stays | The survivor's own choice wins. |
| `notices.NoticeEmail.person` | notice, version | Moves; same notice and version stays | Delivery ledger; a moved row keeps the person from being mailed twice. Rows that stay are never retried: the sender starts from active people. |
| `agenda.EventRegistration.person` | event | Moves. Same event in both: **refused** (sensitive) | A registration carries a place of the capacity, attendance, the waiting list and, for a minor, the guardian's authorization. |
| `agenda.GuardianConsent` (through its registration) | | Follows its registration, never copied | Its scope is the registration. See the refusals about minors. |
| `resources.DevotionalRead.person` | devotional | Moves; same devotional stays. **Not counted anywhere** | Private to the person: the preview says that private reading marks move and shows no number. |
| `resources.ReadingPlanProgress.person` (and its day marks) | plan | Moves with its marks; same plan stays. **Not counted** | Same. The marks of two progresses of one plan are not combined. |
| `serving.TeamMembership.person` | team | As `GroupMembership` | Source of `access.RoleAssignment` (scope `ministry`). |
| `serving.Assignment.person` | service date | Moves. Same date in both: stays if the date has passed; **refused** if it is today or later | Two open requests to serve for one human on one day. |
| `serving.Unavailable.person` | day | Moves; same day stays | Same fact. |
| `care.FollowUp.person` | | Moves | No free text exists in `care`. |
| `care.FollowUp.owner` | | Moves | The owner is a person with an account; the survivor has the account after the merge. |
| `care.FollowUpStep.by` | | Moves | |
| `espacios.Booking.requested_by` (wave 9, added at integration) | | Moves; counted as "reservas de espacios que pidió" | No rule "one per person". The survivor keeps the requests, and with them what `espacios.access.can_read_details` gives to who asked. The booking's words, state and hours are not read or changed. |
| `feedback.FeedbackGiven.person` | | Moves; not counted | A column nobody reads since `feedback/0002` (kept by decision 0013). See the token below. |
| `people.PersonMerge.survivor`, `.absorbed` (new) | | Not moved | The record of earlier merges. |
| `people.DuplicateDismissal.person_a`, `.person_b` (new) | | Not moved | A dismissal is about that pair of records. |

Not foreign keys, checked on purpose:

* `access.RoleAssignment` names an **account**, not a person, and is derived. It is never copied.
  After the rows move and the account (if any) is placed, the projection is rebuilt for both people
  with the sources' own `sync_assignment` (`groups.services`, `serving.services`), under the locks
  those services take (teams, then memberships). By decision 0016 a row counts only while its source
  membership says the same account, so a stale row could only deny. **Stated in the preview:** when
  a membership that guides a group or coordinates a team reaches a record with an account, that
  account will manage the group or team.
* `feedback.FeedbackGiven.token` is a keyed hash of the event and the person's id, made so that it
  cannot be read back. A merge does not compute or copy it. Consequence: somebody who gave their
  opinion as the absorbed record could give it once more as the survivor while the event's opinions
  are open. Accepted and recorded; the alternative reads who answered.
* `people.ChurchAuditEvent` and `events.AuditEvent` keep ids as text. History is not rewritten.
* Columns to `auth.User` (`added_by`, `registered_by`, `checked_in_by`, `linked_by`, …) are about the
  account that acted and are untouched. The same holds for the two added by the sibling slices of
  wave 9, checked at integration: `espacios.Booking.decided_by` and `resources.ContentApproval.approved_by`
  name an account, so there is nothing to move: the account that decided or approved is the same
  account after a merge, whichever person record it is attached to. `agenda/series.py` holds no person.

### 1.6 Refusals (each with a plain sentence in the preview)

1. Not two different active people of the church of the request (404 at the screen, refused in the service).
2. Somebody said the pair is not the same person (dismissed).
3. No signal joins them now (this slice merges only what the review suggests).
4. Both have an account.
5. Both have a date of birth and they differ.
6. Either may be a minor, or has a guardian's authorization on record, and the two do not have the
   same date of birth written. "May be a minor": under 18 by date of birth, marked as a minor at the
   door of an event, or (with no date of birth) under the event's adult age as a campero. Stricter
   than the brief on purpose: it also refuses when the date is missing.
7. The record that would be absorbed has a date of birth and the survivor does not, or has the
   verified one: the administrator is told to keep the other record. A merge never writes a date of
   birth or a verification.
8. The sensitive conflicting rows of 1.5: a live membership of the same group or team, a pending
   request to the same group, a registration to the same event, an open request to serve on the
   same day, a campero of the same event.
9. The record that would be absorbed received a merge that can still be undone.
10. A table with a foreign key to `Person` that this slice does not know holds rows of either person.

## 2. What was built

* `people.duplicates`: the suggestions. Three exact signals after a plain normalisation (e-mail
  lower-cased; phone as its last ten digits, at least seven; normalised full name **and** date of
  birth). Three queries whatever the church's size. Nothing is stored and nothing runs by itself.
* `people.merge`: `build_preview`, `merge`, `undo`, `dismiss`, `reopen`, `clear_expired`, and
  `TABLES`, the decision of 1.5 as data.
* Models (migration `people/0005`, two `CreateModel` and nothing else): `PersonMerge` (church,
  survivor, absorbed, who, when, manifest, undo state and report) and `DuplicateDismissal`.
* Screens (`people/merge_views.py`, templates `people/duplicates.html`, `merge_preview.html`,
  `merge_detail.html`), a link from Personas, and the three view names in `core/shell.py` so the
  rail marks "Personas".
* `manage.py prune_person_merges [--apply]`: empties the previous contact values of merges older
  than 30 days. Nothing schedules it.
* `tenant_integrity`: two checks (`person_merge_church`, `duplicate_dismissal_church`).
* `people.services.save_person` (the one write path of the screens) now locks the row, refuses a
  record that was merged away and writes only the fields a screen may change, never `user` or
  `merged_into`: a form opened before a merge cannot bring the absorbed record back.

### Routes (all under `/mi-iglesia/personas/`)

| Route | Name | Method |
|---|---|---|
| `duplicados/` | `people:duplicates` | GET: pairs to review, merges of the last 30 days, dismissed pairs |
| `duplicados/descartar/` | `people:duplicate_dismiss` | POST `a`, `b` |
| `duplicados/descartados/<id>/revisar/` | `people:duplicate_reopen` | POST |
| `duplicados/<survivor>/<absorbed>/` | `people:merge_preview` | GET preview; POST merges (`revision`, `elige_<field>`) |
| `uniones/<id>/` | `people:merge_detail` | GET |
| `uniones/<id>/deshacer/` | `people:merge_undo` | POST |

### Permissions

Every route: `people.views.church_admin_required` (the decorator the people screens already use):
signed in, the `people` module on for the current church (404 otherwise, before anything else),
then `core.authz.has_scope(user, church, "church", church.pk, manage)`, which is the church role
`church_admin` (403 otherwise). A church pastor, a group's guide, a team's coordinator and an event
role get 403. People, merges and dismissals are fetched inside `request.church`; the services
check the church again by themselves.

### The manifest

```
version, moved {"<app.Model.field>": [row ids]}, camp_participants [campero ids],
demoted_affiliations [ids], moved_user_id, fields_changed [names],
survivor_before {name: previous value}   <- the only contact data; emptied by the undo or after 30 days
```

The absorbed record keeps its own row and values; nothing about it needs copying into the manifest
except the account id.

## 3. Decisions taken in this slice (and why)

1. **A merged-away id is a 404, never a redirect**, for everybody. It is what every screen already
   did (`merged_into__isnull=True`), so no other app changed, and an old id tells nobody who a
   person was merged into. The administration finds the merge under "Uniones de los últimos 30 días".
2. **Only suggested pairs can be merged.** There is no "merge with…" picker: with no shared signal
   the merge is refused and says to correct the data first. Narrower than the brief asks, on purpose.
3. **A dismissed pair cannot be merged** until somebody puts it back in the review ("Volver a
   revisar", audited). Without that, a mistaken dismissal would have no way out.
4. **The list shows names and which signal matched, not the shared value.** The administration
   reads the values on each person's page and in the preview.
5. **A value shared by more than eight records is not a signal** (the church's own phone on forty
   people would suggest 780 pairs).
6. **Minors: stricter than the brief** (1.6, rules 5 to 7). A merge never writes a date of birth or
   its verification; the administrator corrects the date through "Editar los datos" (which clears
   the verification and re-settles registrations, as always) and then merges.
7. **Registrations to the same event always refuse**, even cancelled ones.
8. **Live duplicates of a membership refuse; past ones stay behind.** The administrator takes the
   duplicate out of the group or team with the existing, audited action and merges after.
9. **Private reading marks and the legacy opinion column move without being counted**, in the
   preview, the merge page and the undo report. If one cannot be put back the report says "algunas
   de sus marcas privadas de lectura", with no number.
10. **Counts in the preview are about one person and shown only to the administration**, which can
    already open each of those rows; `people/policy.py`'s minimum reporting size is for aggregates
    shown to pastors and leaders and does not apply.
11. **Audit rows carry ids only**: `persona_union`, `persona_union_deshecha`,
    `persona_duplicado_descartado`, `persona_duplicado_reabierto`, with `person_id`,
    `other_person_id`, `merge_id`. The summaries name nobody. Moving a camp link also writes the
    two rows its services always write (those name the person and the event, as before).
12. **Both people of a merge are protected from deletion** while its record exists.
13. **The record of a merge is kept** after 30 days (ids only). How long, and whether the hidden
    record's own contact data should be emptied at some point, is a retention question for #76; a
    default was not invented.
14. **Stale preview**: the confirmation carries a digest of both records; if either changed, nothing
    is merged and the preview is shown again.

## 4. Not built, and the order I would build it

1. **A manual "es la misma que…" picker** from a person's page, for duplicates with no shared
   value: the merge engine is ready; it needs its own confirmation of why.
2. **CSV import with preview** before any import exists in production: it should run the same
   signals against the people already there, so an import cannot mass-produce duplicates.
3. **CSV export of people**, with the same field allow-list as the team export of the camp.
4. **Membership lifecycle** (visitor, attender, member, inactive): several of the next items filter on it.
5. **Key dates** (baptism, membership): few fields, no new audience.
6. **Tags**, then **custom fields**: each needs a rule about what may be stored (no health data, no free notes about people).
7. **Households and addresses** (#59), separate from guardian authority and only after #75; a
   merge must then learn those tables (the inventory test will say so).
8. **Member-to-member directory** last: opt-in per person and field, and church approval (Q-C4).

Never in this line of work: matching across churches.

## 5. What could not be verified here

* PostgreSQL: the suite ran on SQLite only (the integrator runs `--postgres --e2e`). The merge uses
  `select_for_update()` on `Person` and `Team` without joins and `of=("self",)` on `PersonMerge`;
  the lock order (people by id, then teams by id, then memberships) follows `serving.services`.
  True concurrency (two transactions at once) is tested only as stale copies and repeated calls.
* The browser look was on the synthetic seed with pairs added by hand (see the final report); no
  screen-reader pass, only axe and the keyboard-reachable controls.
* A church with thousands of people: the suggestions are computed in memory on each visit.

## 6. Left alone, worth a look

* Fixed at integration: `groups.views.profile` ("Mis datos") and `agenda.services.set_birth_date`
  now answer a save that races a merge with a plain message (the second as an `AgendaError`, which
  its view already shows on the form) instead of an error page.
* `Person.merged_into` is `SET_NULL` (1.2).
* Fixed at integration: the demo seed gave every person one phone, so the review showed them all
  as pairs. Each now gets their own invented phone, and `seed_community_demo --duplicados` adds one
  synthetic pair (same e-mail, no account) for the browser journey; the plain seed suggests nobody.
* Somebody may give an event opinion twice across a merge (1.5, the token).
