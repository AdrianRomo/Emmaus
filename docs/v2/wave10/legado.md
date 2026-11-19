# Wave 10 — the discipleship-era screens follow the church of the request

Branch `v2/wave-10-legado`. High risk: authorization. No migration. Synthetic data only. Completes the
part of #32 that wave 9 left as "later work" (decision 0017, point 3;
[`wave9/iglesia-actual.md`](../wave9/iglesia-actual.md)).

## What changed, in one paragraph

Until now `core/access.py::get_user_role` (the account's first church role by id) decided which
church the 27 screens behind `core.views.role_required` acted in, and wave 9 answered 404 while the
account worked in another church. Now the role is **the one the account holds in the church of the
request** (`core.tenancy.resolve(request).role`), `role_required` puts it on the request
(`request.church`, `request.church_role`) and every query of those views is scoped to it. An account
can administer church A and be a mentor in church B: in A it gets the administration's screens with
A's data, in B the mentor's screens with B's data, and nothing of one under the other.

## The rule and where it lives

| Piece | File | What it does |
|---|---|---|
| `Resolution.role` | `core/tenancy.py` `resolve` | The `UserChurchRole` of the account in `church_choices(request)[0]`; `None` for a person of that church with no role. Only an account with **no active church at all** falls back to its first role (a role in a church that was closed: wave 9, decision 6). |
| `role_required` | `core/views.py` | Signed in; role in the request church among the allowed ones, else 403; no role here but a role elsewhere: 404; no role anywhere: the old message and the redirect to the landing page. Sets `request.church`, `request.church_role`. |
| `role_in`, `role_record=` | `core/access.py` | `role_in(user, church)`; every helper (`visible_participant_profiles`, `visible_questions`, `visible_notes`, `visible_ai_drafts`, `can_answer_question`, `has_pastoral_access`, `has_leader_access`, `get_role_context`) takes the acting role. Left out, they answer with the first role as before (commands, code with no request). The filtering logic itself is unchanged: one check, not a parallel one. |
| `_own_profile` | `core/views.py` | A participant's screens exist only in the church of their `ParticipantProfile` (one per account). Elsewhere: 404. |
| `listed_events`, `church_for_new_event` | `core/tenancy.py` | See "Events of my churches". |
| `_event_page`, `event_shell` | `core/context_processors.py`, `core/shell.py` | See "Event pages". |

A form opened in A and sent after the session moved to B was already refused for every view that
resolves the church (`core.authz._church_changed`: CSRF rotation, `StaleChurch`). `role_required`
resolves through the same function, so the settings form, the path forms and the rest are covered;
`WritesTests.test_a_settings_form_opened_in_one_church_never_saves_into_the_other` proves it.

`core/access.py` **is edited** (additively). Wave 9 left it alone citing decision 0003, rule 7; the
brief for this slice asks for the move and a second copy of the visibility rules in another module
would have been a parallel check, which is worse. `events/access.py` is not edited.

## View by view

Tests are in `core/test_legacy_church.py` unless another file is named. "List" =
`ListsTests.test_each_list_shows_only_the_church_of_the_request` (each list is opened in A, in B and
back in A, and must show its own church's text and not the other's). "Id" =
`GuessedIdTests.test_a_row_of_the_other_church_is_not_found_by_its_id` (first proved 200 in the row's
own church, then 404 from the other). "POST" =
`GuessedIdTests.test_a_post_to_a_row_of_the_other_church_changes_nothing` (404, and the rows, the
counts and the outbox are checked afterwards).

Before, every row below was scoped to `get_user_role(user).church` (the first role) and answered 404
in the "elsewhere" state. "Now" is the church of the request.

| View (roles) | Reads / writes | Scoped now by | Proof |
|---|---|---|---|
| `church_settings` (admin) | reads and saves the `Church` | `request.church` is the form's instance | List; `WritesTests.test_the_settings_are_saved_in_the_church_of_the_request`; the open-form test |
| `user_list` (admin, pastor) | `UserChurchRole` of the church | `filter(church=request.church)` | List |
| `create_invitation` (admin, pastor, mentor) | writes `Invitation`; form lists mentors and paths | `InvitationForm(church=request.church)`, `invitation.church` | `WritesTests.test_what_is_created_belongs_to_the_church_of_the_request` |
| `invitations_list` (same) | `Invitation` | `request.church.invitations` | List |
| `church_dashboard` (admin, pastor) | counts and short lists of profiles, questions, follow-ups, notes | every query `church=request.church` | List |
| `participants_list` (admin, pastor) | `ParticipantProfile` | `visible_participant_profiles(user, request.church_role)` | List |
| `participant_detail` (admin, pastor, mentor) | profile; writes `PastoralNote`, `FollowUpRequest`; lists the person's questions and follow-ups | profile through `visible_participant_profiles(…, role)`; notes through `visible_notes(…, role)`; **new:** the person's questions and follow-ups are filtered to `profile.church` (they were the user's rows of any church) | Id; POST (note and follow-up); `ParticipantTests.test_the_church_sees_only_what_the_person_wrote_in_it`; `RoleOfTheRequestTests.test_administering_one_church_opens_no_person_of_it_from_the_other` |
| `assign_mentor` (admin, pastor) | writes `ParticipantProfile.assigned_mentor` | `church=request.church`; the form offers that church's mentors | Id; POST; `GuessedIdTests.test_a_form_never_offers_or_accepts_a_person_of_the_other_church` |
| `questions_inbox`, `escalations` (admin, pastor) | `ParticipantQuestion` | `visible_questions(user, role)` | List; `ListsTests.test_escalations_are_the_churchs` |
| `question_detail` (all four roles; three addresses) | question, responses, AI drafts; writes responses, status, follow-up, draft review | `visible_questions`, `visible_ai_drafts`, `can_answer_question`, `has_*_access`, all with the acting role | Id (both addresses); POST (respond, escalate, generate, review); `…test_a_draft_of_the_other_church_is_not_reviewed_through_a_question_of_this_one`; `RoleOfTheRequestTests.test_a_mentor_of_one_church_answers_only_there` |
| `follow_up_list` (admin, pastor, mentor; two addresses) | `FollowUpRequest`; writes one | `filter(church=role.church)`; form limited to the church's participants and leaders | List; `GuessedIdTests.test_a_form_never_offers_or_accepts…`; `WritesTests` |
| `complete_follow_up` (same) | writes status | `church=role.church` | Id; POST |
| `notes_list` (admin, pastor) | `PastoralNote` | `visible_notes(user, role)` | List |
| `path_list`, `path_create`, `path_edit`, `path_detail` (admin, pastor) | `DiscipleshipPath` | `request.church.paths`, `church=request.church` | List; Id; POST; `WritesTests` |
| `lesson_create`, `lesson_edit`, `reflection_question_create` (admin, pastor) | `Lesson`, `ReflectionQuestion` | `path__church=request.church` | Id; POST; `WritesTests` |
| `mentor_dashboard` (mentor) | own participants, their questions, follow-ups | helpers with the acting role; follow-ups `church=request.church` | `RoleOfTheRequestTests.test_the_role_is_the_one_held_in_the_church_of_the_request` |
| `participant_dashboard`, `participant_lesson`, `participant_questions`, `ask_question`, `request_follow_up` (participant) | the account's own path, answers, questions, follow-up requests | `_own_profile` (profile in this church or 404); questions, follow-ups and answers also filtered by church; the lesson's path must be this church's | `ParticipantTests` (four tests: opens in its church; 404 and nothing written in the other, for GET and POST) |
| `dashboard` | redirects | role in the request church; a participant whose path is in another church goes to "Mi iglesia" | `RoleOfTheRequestTests.test_the_bar_offers…`; `core/test_current_church.py::LegacyDiscipleshipTests` |
| `account` | role line, events | `resolve(request).role`; `listed_events` | `EventListTests.test_a_church_role_lists…` |
| `events_home` welcome (`events/views/home.py`) | `onboarding.for_church_role` | role in the request church | `EventListTests.test_the_welcome_is_the_one_of_the_role_held_here` |
| Help (`ayuda`) | `current_church.discipleship_enabled` | the request church (already), now consistent with what opens | — |

Role denials of these screens stay 403 **before** the object lookup. The answer is the same for every
id (an existing one, another church's, an invented one), so it reveals nothing; only somebody with an
allowed role in the church reaches the lookup, and there another church's id is 404.

### One-church accounts: compared against the base branch

Before touching the code I rendered 73 screens for eight one-church accounts with the base tree
(`seed_dev_data` + `seed_community_demo --panorama`: administrator, pastor, mentor and participant of a
discipleship church; administrator, pastor, guide and member of the community church), and rendered
them again at the end. Masking only the CSRF token, the clock (`HH:MM`) and the idle countdown,
**71 of 73 are byte-identical**. The two that differ are the 403 page in the community church, which
is the wording change of point 5 below. Among the 71: `church_settings` (both churches), `user_list`,
`path_list`, `path_detail`, `invitations_list`, `church_dashboard`, `participants_list`,
`participant_detail`, `mentor_dashboard`, `participant_dashboard`, `account` (seven accounts), `ayuda`,
the event list (six accounts), an event's admin home, and every redirect of `dashboard`.

Not identical on purpose, and outside that sample: a participant **role without a profile** used to
get a server error on the participant screens (`RelatedObjectDoesNotExist`); it is now a 404.

## Events "of my churches": each call site

| Call site | Before | Now |
|---|---|---|
| `events/views/home.py::events_home` (the list, and the automatic way in) | `accessible_events(user)`: every church where the account is administrator or pastor, plus its event roles | `tenancy.listed_events`: the events of the request church, plus the events where the account holds an **event role of its own** (active `EventMembership`), whatever their church. For an account of several churches the request church's come first and an event of another church says "Es un evento de «iglesia»." |
| same, `can_create` | administers any church | administers the request church |
| `events/views/home.py::event_create` | created in `administered_churches(user)[0]`, **whatever church the account was working in** | `tenancy.church_for_new_event`: the request church, only for who administers it; 403 otherwise |
| `core/views.py::dashboard` (`has_events`) | any accessible event | `listed_events` |
| `core/views.py::account` (rows) | any accessible event | `listed_events` |
| `core/context_processors.py` (`show_events_link`) | role or any accessible event | role in the request church, or `listed_events` |
| `events/views/duplicate.py:68`, `events/views/admin.py:1125,1296` | `administered_churches(user).filter(pk=event.church_id)` | **unchanged**: the question is about the event's own church |
| `people/persons.py::persons_for` | every church of the user | **unchanged**: no view calls it |

`listed_events` authorizes nothing. An event left out of the list still opens by its address under
`events.access.resolve_access`, and comes back to the list by switching church. A superuser and an
account whose request has no church keep the full list and the old creation rule.

## Event pages (`_event_page`)

`event_view` already puts the event on the request. When the event's church **is** the request church
nothing changes. When it is another church:

- **Colours:** the event's church's when the reader is part of that church too (it is among
  `churches_of`); a reader who only holds a role in the event keeps the colours they had.
- **Rail:** the general links were the reader's own church's, drawn under the event church's logo. They
  are replaced by `core.shell.event_shell`: the event church's name, the event's own section, and
  "Eventos donde sirves". No "Mi iglesia", "Avisos", "Configuración"… of another church.
- The old bar's "Discipulado" link (accounts without the new shell) is not drawn in that state: it
  would lead to the reader's church, not the event's.

`EventPageTests`: own church unchanged; reader of both churches; event role only; account with no
church.

**This changes a one-church account in one case:** somebody of church B (community module on) who
holds an event role in an event of church A now sees the reduced rail on that event's pages. That is
what point 3 of the brief asks for.

## `people:` 404 before 403 (`people/views.py::_names_a_row_here`)

`church_admin_required` answered 403 before looking anything up. Now, for somebody who may not manage:
404 when the address names a person, a pair, a merge, a dismissed pair, an event or a link that is not
of the request church (or does not exist, or was merged away), 403 only when every row named is of
this church. Covers `detail`, `edit`, `grant_access`, `access_link`, `unlink`, `merge_preview`,
`merge_detail`, `merge_undo`, `duplicate_reopen`, `from_event`, `from_event_link`.
`people/tests/test_people_views.py::NotFoundBeforeForbiddenTests`.

**Trade-off, taken because the brief asks for it:** a signed-in person of the church who is not its
administration can now tell which person ids exist in *their own* church (403 against 404). They
learn a number, never a name. Ids of other churches are indistinguishable from invented ones.

Not changed, and with the same order: `groups.views.church_admin_view` (used by `agenda`, `care`,
`espacios`, `serving`, `resources`, `policies`, `sitio`, `feedback`) answers 403 for any id to a
non-administrator. It reveals nothing (same answer for every id) and those apps belong to other
slices of this wave.

## The 403 page

With the community module on: "pide a la administración de tu iglesia o del evento que te dé acceso."
With it off the page is byte-identical to before (`core/test_camp_flow.py:270` still pins the camp's
sentence). No snapshot in `people/tests/test_screens_unchanged.py` pins this page. `ForbiddenPageTests`.

## Seed: `seed_community_demo --dos-iglesias`

- Church `iglesia-demo-norte`, "Iglesia del Norte (demostración)": people, community and discipleship
  modules on; its own colours (they pass the settings form's contrast checks: tested); one group.
- `admin.norte@demo.invalid`: its administrator.
- `dos.iglesias@demo.invalid`: administrator of the first church (its first role, so it starts there)
  and, in the second, **mentor** and guide of the group "Jóvenes del Norte".
- Idempotent; the plain seed is unchanged (tested both ways); `tenant_integrity` and
  `rebuild_role_assignments --check` are clean after it. `groups/tests/test_seed_two_churches.py`.

## Audit rows for switches

One `iglesia_elegida` row per account and church per hour (`core.views.SWITCH_AUDIT_EVERY`). The
switch itself is never refused or delayed; inside the hour it is only not written again. Two requests
at the same instant may both write (one row too many). `SwitchAuditTests`.

## Product decisions taken

1. No role in this church, but a role in another: **404**, not "your account is not linked to any
   church" (false) and not 403 (the page is not theirs to be denied).
2. A participant's path does not follow them to another church: 404 there, and the bar does not offer
   it. An account has one `ParticipantProfile`; a second path would need a model change.
3. An event reached only through a church role in another church is **not listed** while working
   elsewhere (rather than listed with a label): a church screen shows the current church. Events with
   an own event role stay, labelled.
4. "Crear evento" only for who administers the request church. Before, an administrator of A working
   in B created the event in A (or in whichever came first) without being told.
5. Event pages of another church: reduced rail for everybody; colours only for members of that church.
6. The audit cap is by account, church and hour, with no lock.
7. The seed's two-church account is a mentor in the second church (not only a person), so the browser
   can show both roles; the second church has the discipleship module on for that reason.
8. `templates/resources/_no_pastor.html`: the administration is now offered "Invitar a alguien" and
   "Usuarios" in whichever of its churches it works; the sentence for who cannot open them says "pídelo
   a la administración de la iglesia" (it said "a quien la administra como su iglesia principal").

## Routes and permissions

No new route. Changed rules:

| Route | Who | Rule |
|---|---|---|
| the 27 views of `core/views.py` under `role_required` | the roles each already named, **held in the request church** | `core/views.py::role_required` → `core/tenancy.py::resolve` |
| `GET/POST /eventos/crear/` | the administration of the request church | `core/tenancy.py::church_for_new_event` |
| `people:*` with an id | unchanged (church `manage`); denial is 404 for a foreign id | `people/views.py::church_admin_required`, `_names_a_row_here` |
| `POST /cuenta/iglesia/` | unchanged | audit row capped |

## Mutation checks (each rule removed, the tests run, the rule restored)

Eighteen, all caught. In `core/views.py`: `role_required` back on the first role; `path_detail`,
`lesson_edit` and `complete_follow_up` without the church filter; `question_detail` and
`participant_detail` reading with the first role; `_own_profile` without the church check; the
person's questions unfiltered in `participant_detail`; the mentor's follow-ups without the church (this
one survived the first run: a test was added, `…test_the_role_is_the_one_held_in_the_church_of_the_request`);
the 404 for "no role here" removed; an audit row on every switch. In `core/tenancy.py`: `listed_events`
returning everything; `church_for_new_event` back to the first administered church. In
`core/context_processors.py`: the bar built with the first role; `_event_page` disabled. Also:
`people` 403 before the lookup; the 403 page with one sentence; the seed without the mentor role.

## Looked at in a browser, and not

Playwright (the repo's, `bypassCSP`), scratch SQLite, `seed_community_demo --dos-iglesias --eventos`,
as `dos.iglesias@demo.invalid`, at 390px and 1440px, no horizontal scroll and no JS error on any: in
the first church "Mi iglesia", "Configuración", the event list, "Mi cuenta", a camp's home; after
switching, "Mi iglesia", the mentor's home, the 403 page, the event list, "Mi cuenta", "Ayuda", and the
first church's camp opened from the second (reduced rail, first church's name). Screenshots read by
eye: the mentor's home (1440), the 403 page (390), the event list (390), the camp page from the other
church (1440). The rest were checked by the numbers the script prints (status, overflow, `h1`, church
name, primary colour, rail links), not by eye.

Not verified: axe on any screen; PostgreSQL; the browser gate; the "Más" panel on an event page of
another church; the event list row that says "Es un evento de…" (by test only); an event-role-only
reader in the browser (by test only); the old bar (module off) for an account of two churches.

## Not built

- A second path for an account that is a participant in two churches.
- Remembering the church across sign-ins; a picker at sign-in.
- The same 404-before-403 order for `groups.views.church_admin_view`.
- `templates/policies/form.html` and `templates/sitio/edit.html` still test `church_elsewhere`, which
  nothing sets any more (always "Volver a Configuración", which now opens): a dead condition in files
  of other slices.
- `Resolution.role_church` is kept as a property for `tenancy.current_church`.

## Recommended browser journey (for the integrator; `e2e/` not edited)

Seed with `--dos-iglesias`, sign in as `dos.iglesias@demo.invalid`:

1. "Mi iglesia" shows the first church; the rail has "Configuración" and "Cambiar a Iglesia del Norte
   (demostración)". Open "Configuración": 200, the first church's name in the form.
2. Press "Cambiar a…": the page is the second church's "Mi iglesia", in its colours (`--petroleo` is
   `#7a3b12`), with "Jóvenes del Norte" under "A tu cargo", a "Discipulado" section with "Inicio" and
   "Seguimientos", and no "Administración".
3. Visit `/church/settings/` by address: 403 with "la administración de tu iglesia o del evento";
   `/mentor/`: 200.
4. Open "Eventos donde sirves": no event of the first church; no "Crear evento".
5. With a settings form of the first church open in a second tab, switch in the first tab and send the
   form: "Esta pantalla ya no está al día", and neither church's name changed.
6. Switch back: "Configuración" is there again and `/mentor/` is 403. axe and 390px overflow on steps
   1, 2 and 4.
