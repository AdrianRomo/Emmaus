# Emmaus v2 — progress

Newest first. "Verified" means a command was run and its result is quoted; anything else says so.

## 2026-11-17, later — Redesign, wave 2: "Eventos", the camp in the shell, notices, events for people, devotionals and resources (same branch)

The owner answered the first round: Emmaus is a church system, the word is "Eventos", keep going,
and put what needs a decision in issues. This entry is what was added; the entry below is wave 1.

### Shipped (working, tested, for churches with the `community` module on unless it says otherwise)

| Piece | Where |
|---|---|
| "Eventos" replaces "Campamentos" in the bar, the page, the welcomes, the help and the user guide; "Crear evento" (**for every church, module on or off**) | templates, `core/onboarding.py`, `docs/user-guide-es.md` |
| An event is a place in the shell: its name, the person's role, the role's links; tabs on a phone; under 1101 px the top bar and tabs | `events/rotulo/shell_*.html`, `partials/ui/shell*.html` |
| Avisos: for the whole church (administration) or one group (who guides it); appear and expire by date; "Nuevo" until opened; a count in the menu | app `notices` |
| Events for people: the administration publishes an event's page; a person signs up and cancels; capacity; "Inscritos" with contact data and attendance | app `agenda` |
| The devotional of the day with a private "Ya lo leí"; resources (sermons, music, studies) as links by series and speaker | app `resources` |
| Mi iglesia shows notices, the devotional, published events with "Inscribirme", and recent resources | `groups/views.py::home` |
| A second research pass with REA on rendered public pages, and the decisions taken from it | `REFERENCE_RESEARCH.md` |
| The browser gate reviews every existing screen a second time with the modules on | `scripts/agents/e2e-gate.sh` |

Three of the modules were built by sub-agents in their own worktrees from a written specification,
then merged, wired into the home and the shell, seeded, and checked here in a browser.

### Verification

`scripts/agents/quality-gate.sh --postgres --e2e` on commit `dbbdb31`, 2026-11-17T10:07Z (the commit after it
holds only these documents):

```
step            status           secs  detail
lint            PASS                0  All checks passed!
check           PASS                0  System check identified no issues (0 silenced).
migrations      PASS                0  No changes detected
deploy-check    PASS                0  check --deploy OK: only accepted warnings [security.W005 security.W021]
migration-scope PASS                0  migration scope OK: no core/ or events/ migration added since f72d4f6
pip-audit       PASS                3  No known vulnerabilities found
tests-sqlite    PASS              103  1686 passed, 6 skipped
tests-postgres  PASS              168  1681 passed, 11 deselected (PostgreSQL 16, throwaway container)
e2e             PASS              440  browser gate OK
overall: PASS
```

The community journey in that run: 175 checks, 0 problems.

- Two gate runs in between **failed, and were right to**. First: editing or retiring a notice
  crashed on PostgreSQL (a row lock over a nullable join; SQLite does not notice), and an import was
  left unused (fixed in `131b735`). Second: slice 1's byte-identity test compared two renders of a
  screen with a clock and they straddled a minute (the test masks clocks now, `dbbdb31`).
- An independent review of the whole change by a sub-agent (read-only) found no authorization or
  tenant-isolation defect and five others, all fixed in `1f73713` with tests: a group with nobody
  printed a QuerySet; correcting a past list deleted who came and has since left, and rewrote that
  day's head-count; stray newlines with the module off; "Mis datos" told whether an e-mail was
  somebody else's; the shell could be built for another of the account's churches.
- With the module off, 14 pages for five roles were rendered on `v2/slice-1-camp-to-group` and on
  this branch from the same database and compared: the same bytes, apart from the "Eventos" words.
- Browser, modules on: the journey now also reads a notice, reads the devotional, opens the
  resources, signs up for an event, passes a group's list, and audits every new screen at 390, 768
  and 1440 px (axe WCAG 2.0–2.2 A and AA, overflow, one `h1`, 44 px targets).
- The modules-on pass of `review-all.mjs`: 243 page/width checks, the same three rows flagged as in
  the modules-off pass (the page that is a 403 on purpose).
- Looked at as rendered screenshots: the home with every new section (390, 1440), an event's page
  (390), the devotional (390), the event administration's home in the shell (390, 1024, 1440).
  `SCREEN_MATRIX.md` says the rest.
- Not verified: the sandbox or production hosts; e-mail; two real PostgreSQL connections racing
  for the last place of an event (the order lock, count, insert is tested, on SQLite); the
  coordinator's, tutor's, interview and campero screens by eye with the module on.

### Not done, and why

- **Not the whole product.** Volunteer scheduling, follow-up tasks, the pastor's view of
  engagement, guardians and households, check-in for children, photos, FAQ, the event programme,
  giving, notifications outside Emmaus: not built. Issue #71 proposes an order.
- **An event that is not a camp** is still created by the camp's form (issue #68).
- **Nothing pushed, no pull request** (issue #64). **Codex review** not run.
- **Per-task rules stretched**, as before: one branch, several modules, far over 400 lines.

### Needs the owner

Issues #64 to #71 (decisions, each with the default now in place) and #72 (a bug in Atrio's bar,
seen on the way). Q-C1 to Q-C4 of slice 1 are unchanged.

### Next

`AGENT_HANDOFF.md`, "Next, in order".

## 2026-11-17 — Redesign, wave 1: Santuario, the shell, Mi iglesia, groups and meetings (branch `v2/redesign-santuario-shell`)

Stacked on `v2/slice-1-camp-to-group`. The owner's directive of this date asks for a product-wide
redesign; this entry is what of it exists. What does not is listed below and in `UX_AUDIT.md`.

### Shipped (working, tested, for churches with the `community` module on; nothing changes with it off)

| Piece | Where |
|---|---|
| Santuario: a token and component layer over Atrio, a serif voice for titles, the arch monogram | `static/css/santuario.css`, `docs/v2/DESIGN_SYSTEM.md` |
| The shell: side rail, phone tabs, "Más"; sections by what the person may open; "A tu cargo" with the count that waits | `core/shell.py`, `templates/partials/ui/shell*.html` |
| Mi iglesia rebuilt: one next step, "Lo que viene" by day, groups, requests, the way so far, missing contact data | `groups/views.py::home`, `templates/groups/home.html` |
| "Mis datos": a person corrects their own phone, e-mail and preferred contact | `groups/views.py::profile` |
| Groups: own first, filter by kind, the group page, the guide's work with tappable contact data | `templates/groups/` |
| Meetings and attendance: "Pasar lista", "Reuniones", the home's "¿Quién vino a …?" | `groups/models.py` (migration `groups/0002`), `groups/services.py::record_meeting` |
| Help page tasks for the new navigation and "Mis datos" | `templates/core/ayuda.html` |
| Reference research, UX audit, screen matrix, handoff | `docs/v2/` |

### Verification

`scripts/agents/quality-gate.sh --postgres --e2e` on commit `bdc6ef4`, 2026-11-17T08:43Z (the commit after it
holds these documents and two corrected figures in a stylesheet comment):

```
step            status           secs  detail
lint            PASS                0  All checks passed!
check           PASS                1  System check identified no issues (0 silenced).
migrations      PASS                0  No changes detected
deploy-check    PASS                1  check --deploy OK: only accepted warnings [security.W005 security.W021]
migration-scope PASS                0  migration scope OK: no core/ or events/ migration added since f72d4f6
pip-audit       PASS                2  No known vulnerabilities found
tests-sqlite    PASS               93  1610 passed, 6 skipped
tests-postgres  PASS              153  1605 passed, 11 deselected (PostgreSQL 16, throwaway container)
e2e             PASS              237  browser gate OK
overall: PASS
```

The community journey in that run: "community flow OK: 102 checks, 0 problems".

- Browser, modules on (`e2e/community-flow.mjs`): the camp-to-group journey, the phone's tabs and
  "Más" by keyboard, "Mis datos", and passing a list, with axe (WCAG 2.0–2.2 A and AA), horizontal
  overflow, one `h1` and 44 px targets at 390, 768 and 1440 px on every community and people screen.
- Looked at as rendered screenshots in this session: Mi iglesia (guide, former camper) at 390, 768
  and 1440; Grupos, a group, the guide's "Personas" and "Reuniones", "Pasar lista", Mi cuenta, the
  event list, sign-in and the event administration's home at one or more widths. `SCREEN_MATRIX.md`
  says which, and which screens were **not** looked at with the module on.
- Found and fixed while looking: a row with two buttons overflowed by 60 px at 768 px beside the
  rail (rows now stack by the column's width); pages started on different lines (now anchored to
  the rail); the home repeated the rail's "A tu cargo" and "Administrar" (removed from the home).
- Not verified: anything on the sandbox or production hosts; real e-mail; a real church logo in the
  rail (the seed has none; the slot is Atrio's `church_brand`); a screen reader by ear.

### Not done, and why

- **The camp is not inside the shell.** Entering an event shows Atrio's bar (with the new colours
  and titles for church-role accounts). It is the next wave; it touches invariant 5.
- **No events for members, announcements, devotionals, media, photos, notifications, serving,
  check-in, giving.** None has a model yet. The home shows only what is real.
- **Guardian accounts**: not built (Q-C2 is open).
- **Discipleship and most camp screens were not looked at with the module on**; they pass their
  tests and the modules-off browser review, which does not load the new stylesheet.
- **Codex review** not run. **Per-task rules stretched again** on the owner's instruction: one
  branch for several pieces, well over 400 changed lines.
- **A dependency by another name**: one font file (Newsreader, OFL, 35 KB). No package was added.

### Needs the owner

1. Nothing is pushed. The earlier note said "until the owner says so"; the new directive asks for
   pull requests, and the first branch is 7,000 lines the owner had asked whether to split. Say
   "push" and both go up as drafts (slice 1 against `v2/foundation`, this one stacked on it).
2. Q-S1 to Q-S4 in `AGENT_HANDOFF.md` (the serif on camp screens, "Eventos" vs "Campamentos", who
   sees attendance, one flag or two).
3. Q-C1 to Q-C4 of slice 1, unchanged.

### Next

`AGENT_HANDOFF.md`, "Next, in order".

## 2026-11-17 — Slice 1: a former campero joins a group (branch `v2/slice-1-camp-to-group`)

### What was found first

- `v2/foundation` already had the audit, the roadmap, decisions 0001/0002/0005 and `people.Person`.
- Six task worktrees from 2026-10-30 held finished, uncommitted, never-gated work (issues #23, #29,
  #30, #34, #36, #38) and blocked #33 and #35 behind them. It was brought into this branch
  unchanged, one commit per issue, and tested. One test of #38 failed at night (the seed made no
  team report outside the report window); fixed.
- The camp ended on 2026-11-15, so "link camperos after the event" is now possible.

### Shipped (working, tested, behind per-church flags that are off by default)

| Piece | Where | Roadmap |
|---|---|---|
| Decisions 0003 (authorization), 0004 (flags), 0008 (community rules and open questions) | `docs/decisions/` | P1-1 |
| People backfill, sede affiliation, content-free notices, second-church seed, tenant integrity command | `people/`, `core/notify.py` | P1-3, P1-4, P1-8, P1-10, P1-12 |
| Module flags with kill switch and `set_church_module` | `people/flags.py`, `people/decorators.py` | P1-7 |
| Church activity log | `people/audit.py` | P1-9 |
| `has_scope`, `current_church`, group-role projection, `rebuild_role_assignments` | `core/authz.py`, `access/` | P1-5, part of P1-6 |
| Groups: list, page, "Pedir unirme", the guide's requests and members, the administration's screens | `groups/`, `templates/groups/` | P2-1, P2-2, P2-3 |
| Mi iglesia home; accounts with no role land on it | `groups/views.py::home`, `core/views.py::dashboard` | P2-4 |
| Join-request notices that never claim an unsent e-mail | `groups/views.py` | P2-5 |
| Campero ↔ person link, made from Personas after the event | `people/models.py`, `people/views.py` | P2-6 (without touching a camp screen) |
| J2.7: a guide opens no camp URL of a former campero | `groups/tests/test_journey.py` | P2-7 |
| Person → account with a one-time link on screen; guardian confirmation for minors | `people/services.py::grant_access` | P2-8 |
| Spanish copy, help page tasks, design-system entries | `templates/`, `docs/design-system.md` | P2-9 |
| Synthetic community seed and the browser journey | `seed_community_demo`, `e2e/community-flow.mjs` | Phase 2 gate |

### Acceptance scenarios of the directive

| # | Scenario | State | Evidence |
|---|---|---|---|
| 1 | A camp participant keeps their camp data and joins a group as the same person | **Shipped** | `groups/tests/test_journey.py::test_a_former_campero_joins_a_group_under_the_same_person`; browser journey |
| 2 | A former coordinator has no permissions over a group | **Shipped** | `…::test_a_former_coordinator_holds_nothing_over_a_group`; `access/tests/test_authz.py::test_nothing_is_implied_into_a_group` |
| 3 | A tutor sees only assigned people; pastor/admin only what is authorised | **Already present in v1**, unchanged; parity with it is tested | `access/tests/test_authz.py::ParityTests`; existing `events/tests/test_access.py` |
| 4 | Guardian access | **Not built** (no guardian accounts). Only the confirmation before a minor's account | `people/tests/test_people_views.py::test_a_campero_who_was_a_minor_needs_the_guardian_confirmation` |
| 5 | Two tenants cannot read, change or enumerate each other | **Shipped for the new modules** (v1 already tested) | `groups/tests/test_views.py::TenantIsolationTests`, `people/tests/test_people_views.py::test_people_and_events_of_another_church_do_not_exist`, `access/tests/test_authz.py::J3Tests` |
| 6 | A non-technical visitor understands home, registers for an event, finds a group, on a phone | **Partial**: home and groups at 390 px with zero axe violations. Event registration and anonymous visitors are not built; the home says so | `e2e/community-flow.mjs` |
| 7 | Sensitive answers cannot leak through a presentation | **Already present in v1**, unchanged. No new screen reads an answer | `groups/tests/test_journey.py::test_a_group_guide_cannot_open_anything_of_the_camp` |
| 8 | A missing dependency shows a truthful state | **Shipped** for e-mail (no SMTP: "lo verá cuando entre a Emmaus"; access link shown, "Emmaus no se lo envió") and for event registration ("Todavía no se puede hacer desde aquí") | `…::test_notices_say_an_email_went_out_only_when_one_did` |

### Verification

`scripts/agents/quality-gate.sh --postgres --e2e` on commit `e422cf0` (the commits after it are documentation only), 2026-11-17T07:50Z:

```
step            status           secs  detail
lint            PASS                0  All checks passed!
check           PASS                0  System check identified no issues (0 silenced).
migrations      PASS                0  No changes detected
deploy-check    PASS                1  check --deploy OK: only accepted warnings [security.W005 security.W021]
migration-scope PASS                0  migration scope OK: no core/ or events/ migration added since f72d4f6
pip-audit       PASS                3  No known vulnerabilities found
tests-sqlite    PASS               90  1582 passed, 6 skipped
tests-postgres  PASS              146  1577 passed, 11 deselected (PostgreSQL 16, throwaway container)
e2e             PASS              223  browser gate OK
overall: PASS
```

- The suite on `v2/foundation` before this branch passed too (run at the start of the session).
  This branch adds about 160 tests: `access/tests/`, `groups/tests/`, and five files in `people/tests/`.
- Browser gate, three parts, all on synthetic data: `station-flow.mjs` (the camp's shared-computer
  interview), `review-all.mjs` (every existing screen per role at three widths, modules **off**),
  `community-flow.mjs` (modules **on**: 59 checks, 0 problems; axe WCAG 2.2 AA with zero violations,
  no horizontal overflow, one `h1`, 44 px targets, at 390 and 1440 px).
- The first browser runs found and fixed: no space under the next-step panel, capitalised month
  names, and a phone-width overflow on rows with two buttons (now `.row--stack`).
- Not verified: anything on the sandbox or production hosts; real e-mail delivery (no SMTP here).

### Not done, and why

- **No deploy.** Not to the sandbox either: it follows `v2/foundation`, and merging and deploying
  are the owner's (`AGENTS.md`). Steps: `docs/v2/MIGRATION_AND_ROLLOUT.md`.
- **Nothing pushed, no pull request opened** until the owner says so.
- **The repository's per-task rules were stretched on the owner's instruction**: one branch for
  several issues, far more than 400 changed lines, and issue #31 (`risk:high`, "needs owner go")
  implemented. The commits are split by issue where the work was separable.
- **Decision 0004 rule 6** asks for a comparison against the merge base over every screen; the
  test here compares the "off" states with each other over nine representative screens.
- **Codex review** (`scripts/agents/codex-review.sh`) not run.
- **ChurchSuite** could not be read (HTTP 403): its column in the feature matrix is unverified.
- Church and event roles are not projected into `RoleAssignment` (decision 0008, rule 3).
- `tenant_integrity` has no checks yet for the new tables (group ↔ person church, link ↔ event church).

### Needs the owner

1. Q-C1: should "took part in event X" outlive the 365-day purge? (Today the camp history
   disappears from the person's home when the camp's data is purged.)
2. Q-C2: is the administrator's confirmation enough before a minor gets an account?
3. Q-C3 / Q-C4: pastors seeing every group; members seeing each other.
4. Whether to open the pull request as one, or split (foundations / access / groups / people screens).
5. The six stale task worktrees and their issues (#23, #29, #30, #34, #36, #38, and #31, #33, #35
   which this branch also covers) can be closed once this merges; nothing was deleted.

### Next slice (in order)

1. **Group meetings and attendance** (blueprint P1): a guide records who came; the member's home
   shows real meetings instead of the computed next weekday. M, medium.
2. **Integrity checks for the new tables** in `tenant_integrity`, and `rebuild_role_assignments
   --check` in CI. S, low.
3. **Events for people** (P3-8, #61): an event that is not a camp, a public page, "Inscribirme"
   from Mi iglesia, "Mis eventos". L, high (camp-adjacent): needs a short design note first.
4. **Avisos** (#57): announcements by audience on Mi iglesia, with the notification preferences
   they need. M.
5. **A screen for the church activity log**, and the church switcher for accounts in two churches
   (the POST view over `core.authz.switch_church`). S each.
