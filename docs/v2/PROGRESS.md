# Emmaus v2 — progress

Newest first. "Verified" means a command was run and its result is quoted; anything else says so.

## 2026-11-18, night — Wave 9: the owner delegates product decisions; five slices (branch `v2/wave-9`)

The owner asked the building session to take the product decisions and keep building
([decision 0017](../decisions/0017-product-decisions-delegated-and-wave-9.md)). Five slices were built in
parallel, each in its own worktree, then joined, reviewed by two independent read-only reviewers, fixed
and gated. Detail, routes, permissions and what is not built: [`wave9/`](wave9/).

| Slice | Issue | What exists now |
|---|---|---|
| The church of the request | #32, #37 | An account in more than one church chooses where it works (`core/tenancy.py`, `POST /cuenta/iglesia/`); every request checks the choice again; brand and theme follow it. One-church accounts and camp screens do not change. |
| Pastoral approval of content | #99 | Administration prepares, a pastor approves (`resources/approvals.py`, queue at `…/recursos/aprobaciones/`): devotionals, reading plans and the welcome of the public home. |
| Rooms and equipment | #94 | App `espacios`: spaces, requests, approval, clash rule with setup and teardown, usage report. Never public. |
| Series of events and calendar | #93, first part | `agenda.EventSeries` writes ordinary events eight weeks ahead (job `series_eventos`); a public `.ics` per church and per public event. |
| Duplicate records of people | #91, first part | Suggested pairs, a preview, an audited join inside one church, undo for 30 days (`people/merge.py`); job `uniones_personas` clears old contact values. |

- **Migrations (all additive):** `agenda/0007`, `espacios/0001`, `people/0005`, `resources/0003`, `resources/0004`
  (RunPython with reverse), `sitio/0002` (same).
- **Found by the reviews and fixed before the pull request:** undoing a join left another person confirmed
  at an event that protects minors; a form opened in church A wrote into church B after a switch in another
  tab (a switch now invalidates every open form); "Sí hay" brought a date back public with an old hour and
  place; the public home's "facts" accepted long authored text and any link (now short, at most 12 service
  times, map links of known map sites only); a booking in a clock-change gap answered 500; the week of a
  space hid a setup that crossed midnight; a stale preview could join records after one gained a role;
  a possible deadlock between a join and a team change. Also: dates of moments were shown in UTC after
  18:00 (`groups/templatetags/community.py`), and a series listed today's date after its hour had passed.
- **Gate on `69da375`:** lint, check, migrations, deploy-check, migration-scope (6 added, all additive),
  pip-audit PASS; SQLite 2927 passed, 6 skipped; PostgreSQL 2922 passed, 11 deselected. Browser gate on
  the final tree: OK, community flow 484 checks, 0 problems (three earlier runs failed on journey steps
  that still used wording and markup from before the polish round; the steps were corrected, not the product).
- **Not verified:** two real connections racing on PostgreSQL (locks are tested with stale copies and call
  order); the switcher in a browser journey (the seed has no account in two churches; 45 unit tests cover
  it); a real calendar client subscribing to the feed; anything on a host. Most new screens were looked at
  by the builders; the integrator looked at the series screen and the clash form at 390 and the pastor's
  review at 1440.
- **Known limits, on purpose:** the discipleship-era screens (settings, users, paths, invitations) follow the
  account's first church and answer 404 in another; an administrator can invite a pastor, so the separation
  of duties holds only while administration does not appoint itself; event screens wear the reader's theme.
- **Deploy notes, when a host is updated:** `migrate`; recreate `web` and `scheduler` (two new jobs run inside
  `run_scheduled_jobs`); a pastor approves the content listed as published before approvals existed.

## 2026-11-18, afternoon — Public notices of an event (issue #96, first slice; branch `v2/issue-96-public-event-notices`)

The owner's roadmap review (#90, merged as #100) chose, for parents without an account (#59), notices
that are explicitly published for one event and read by anybody with the event's public address.

- **Built:** `agenda.EventNotice` (`agenda/0006`, one `CreateModel`); the administration's screen
  `…/eventos/<slug>/avisos-publicos/` (publish, edit, retire, and "Borrar para siempre" for a retired
  one); the section "Avisos" on `/evento/<slug>/#avisos` and on the members' page of the event (latest
  20 in force, hour in the event's zone, an "Editado" mark); a WhatsApp link that carries the church's
  name and the address, never the text. A members' notice (`notices.Announcement`) is another table and
  cannot reach a public page.
- **Not built, on purpose:** subscriptions, any e-mail to an address without an account, any other
  channel. Those stay in #96 for the leadership (purpose, expiry).
- **Rules kept:** the public view asks no table of people and sets no cookie; one identical 404 while
  the page is not public; audit rows carry ids, never the text; a duplicated event starts with no notices.
- **Review (independent, read-only):** no way to read a notice one should not, no authorization hole.
  Fixed before the commit: an edit from a stale copy brought a retired notice back; an unknown time zone
  answered 500 on three pages; the share link was offered with no notice in force; the copy implied that
  something is sent. Added after the review: deleting a retired notice for good.
- **Test defect fixed, older than this change:** four tests of `core/test_scheduler.py::SchedulerPassTests`
  failed in the half hour after each even hour from 08:00 to 22:00 (Mexico City), because the fixture
  leaves a camp with automatic cutoffs on. Proven with a shifted clock on the base and on this branch.
- **Gate:** lint, check, migrations, deploy-check, pip-audit PASS; SQLite 2612 passed, 6 skipped;
  PostgreSQL 2607 passed, 11 deselected; e2e PASS, community flow 300 checks, 0 problems.
- **Not verified:** two real transactions racing on PostgreSQL (the lock is tested with a stale copy
  only); the delete confirmation, the edit form and a date with its year were not looked at as images.

## 2026-11-18, morning — Wave 8: new issues #83–#87 and the owner's comment on #80 (branch `v2/wave-8`)

Wave 7 (pull request #82) is merged. Another session delivered #83 (search in Recursos) as pull
request #88; its required checks passed and it was merged here, then its search field was given
the design system's field and button.

### Shipped

| Piece | Source | Where |
|---|---|---|
| Reading plans written by the church, with progress only the reader sees (details in the next section) | #84 | `resources/plans.py` |
| A prototype of the church's public home at `/iglesia/<slug>/`: service times, public events, resources marked public; nothing about people; a review before publishing | #87 | app `sitio`, `docs/v2/discovery/public-home.md` |
| The scheduler pass also mails notices whose day came and expires waiting-list offers; a heartbeat per job, `scheduler_status`, and the product says so when scheduled notices are not being mailed | #80 (owner's comment) | `core/scheduler.py`, `run_scheduled_jobs`, `EMMAUS_PUBLIC_BASE_URL` |
| The owner's acceptance test for scheduled delivery, automated as written | #80 | `notices/tests/test_scheduled_delivery.py` |
| Workshop drafts for children's check-in and for giving in Mexico: options, sources, what a professional must confirm, the cases to refuse | #85, #86 | `docs/v2/discovery/` |

### Verification

`scripts/agents/quality-gate.sh --postgres --e2e` on commit `91e460f`, 2026-11-18T06:35Z (the commit
after it holds only these documents):

```
step            status           secs  detail
lint            PASS                0  All checks passed!
check           PASS                0  System check identified no issues (0 silenced).
migrations      PASS                1  No changes detected
deploy-check    PASS                0  check --deploy OK: only accepted warnings [security.W005 security.W021]
migration-scope PASS                0  migration scope OK: 4 migration(s) added or changed since c57e9c0, all additive
pip-audit       PASS                2  No known vulnerabilities found
tests-sqlite    PASS              260  2578 passed, 6 skipped
tests-postgres  PASS              398  2573 passed, 11 deselected (PostgreSQL 16, throwaway container)
e2e             PASS              465  browser gate OK
overall: PASS
```

- The community journey in that run: 272 checks, 0 problems.
- An independent read-only review found no leak on the public home and none of reading progress,
  and ten defects, fixed with tests before this gate. The ones that mattered were in the
  scheduler: it moved the waiting list and mailed an offer for a church whose module was off; a
  failed heartbeat write could stop the cutoffs; a refused address was retried every five minutes
  for three days (now a backoff and five attempts); a pass where every send failed still counted
  as running, so the product kept promising the e-mail.
- Looked at by me as screenshots: the public home at 390 (a church with its own theme and logo)
  and at 1440 (the demo church). The agents looked at their own screens on their own scratch
  databases; every new page passed axe at 390 and 1440.
- Not verified: real SMTP and a hung mail server; the `--loop` process of the scheduler; a
  heartbeat table that is really missing; anything in a container or on a host.

### To deploy this wave (a person, on the host)

`migrate`; set `EMMAUS_PUBLIC_BASE_URL=https://<host>` (no path; the host in `ALLOWED_HOSTS`; a
malformed value stops startup, an empty one is valid and means no scheduled e-mail); recreate
`web` and `scheduler` (the compose file's scheduler command changed to `run_scheduled_jobs --loop
300`); check with `manage.py scheduler_status`. To turn the community jobs off and keep the
cutoffs: `EMMAUS_SCHEDULED_COMMUNITY_JOBS=false`. To go back: the previous command line,
`run_report_cutoffs --loop 300`.

### Not done, and why

- #85 and #86 are drafts for the people who decide; nothing of either is built. #87 is a
  prototype: installability, push, live links and native apps wait for evidence from real use
  (`docs/v2/discovery/public-home.md`).
- Any church administrator can confirm a plan's rights; decision 0014 names pastoral leadership.
- A parent without an account subscribing to an event's announcements (#59) is a proposal on the
  issue, not built: one option would put a notice's text in an e-mail.
- Alerting when `scheduler_status` fails is host work (#52). Removing `EMMAUS_PUBLIC_BASE_URL` by
  mistake raises no alert there; only the product's line shows it.

## Wave 8, detail — Reading plans written by the church (issue #84, branch `v2/wave-8-planes`)

"Planes de lectura" under "Para crecer": several days in order, each with a Scripture reference, an
optional reflection and an optional question, all written by the church. Code: `resources/plans.py`
(every rule and write), `resources/plan_views.py`, migration `resources/0002_reading_plans`.

Rules, each with a test in `resources/tests/test_plans.py`:

- **No Bible text.** Only references and the church's words are stored; nothing is fetched. The day
  form says so. No free-text answer from a member exists anywhere.
- **Publishing** needs the administration's confirmation ("Confirmo que este plan lo escribió nuestra
  iglesia o que tenemos permiso para publicarlo"), stored as `rights_confirmed_by` / `rights_confirmed_at`,
  and at least one day. It is audited. A published plan's days can be corrected and days can be added
  (audited, under the same confirmation); a day is removed only while the plan is a draft.
- **Retiring.** A retired plan disappears, by list and by address, for everybody who had not started
  it, and nobody can start it. Whoever had started it (finished or not) keeps every day, the marks,
  "Ya lo leí" and the line on Mi iglesia. Leaving a retired plan is final. The administration can
  publish it again with a new confirmation.
- **Pace.** Days are never tied to a date. Resuming goes to the first day without a mark. The last
  mark shows "Terminaste este plan": no score, streak, percentage or comparison.
- **Privacy.** Progress is the reader's alone: the administration's pages, Panorama and the audit log
  carry nothing derived from it (byte-compared in the tests), there is no count of any kind, the
  tables are not in the Django admin, and nothing is e-mailed or notified.
- **Tenants.** Another church's plan, day or progress is a 404; `tenant_integrity` has two new checks
  (`reading_plan_progress_person_church`, `reading_plan_day_read_plan`).

Seed: `seed_community_demo --eventos` adds one published three-day plan; the plain seed is unchanged.

Revert: revert the commit, or `manage.py migrate resources 0001` (drops the four new tables and
nothing else) before removing the code. `EMMAUS_V2_MODULES=false` hides every screen at once.

Not built: reordering days, deleting a plan, more than one plan in the "Continuar" line (the most
recently started unfinished one is shown).

## 2026-11-18, last — Wave 7: the deviation closed and the small list (branch `v2/wave-7`)

Wave 6 (pull request #81) is merged into `v2/foundation`; issues #49 and #50 are closed. Every
issue still open needs the owner, the host or an approval (the table in `AGENT_HANDOFF.md`).

### Shipped

| Piece | Where |
|---|---|
| Who coordinates a serving team is decided by `has_scope` (scope `ministry`, decision 0016). A role by scope never outlives or contradicts the membership it comes from, for teams and for groups | `core/authz.py`, `access/sources.py`, `serving`, `groups` |
| `tenant_integrity` checks every table added since it was written (24 tables, 21 new checks) | `people/management/commands/tenant_integrity.py` |
| The camp's access requests say nothing about an account that is not of this church, on the list and on approval | `events/services/team.py`, `events/views/admin.py` |
| A member's first-run welcome on Mi iglesia | `core/onboarding.py` |
| No list stops in silence: pages of 50 with the total, or a short list that says it is short | `core/paging.py`, `partials/ui/pager.html` |
| "Ahora y después" on the camp's four homes, from the event's programme | `agenda/now_next.py` |

### Verification

`scripts/agents/quality-gate.sh --postgres --e2e` on commit `c267acc`, 2026-11-18T03:27Z (the commit
after it holds only these documents):

```
step            status           secs  detail
lint            PASS                0  All checks passed!
check           PASS                0  System check identified no issues (0 silenced).
migrations      PASS                0  No changes detected
deploy-check    PASS                1  check --deploy OK: only accepted warnings [security.W005 security.W021]
migration-scope PASS                0  migration scope OK: 1 migration(s) added or changed since 86224ae, all additive
pip-audit       PASS                1  No known vulnerabilities found
tests-sqlite    PASS              248  2491 passed, 6 skipped
tests-postgres  PASS              369  2486 passed, 11 deselected (PostgreSQL 16, throwaway container)
e2e             PASS              475  browser gate OK
overall: PASS
```

- The community journey in that run: 256 checks, 0 problems.
- An independent read-only review found five defects, fixed with tests before this gate. The
  serious one: a membership deleted outright (for instance by the documented undo of
  `link_people`) left its role row granting control of the team; groups had the same hole from
  before this branch. Now the rows are deleted with their source and `has_scope` believes a row
  only while its source agrees. Also: a `Person` save could write back a role a service had just
  ended; approving a request typed with a superuser's address answered differently from any other
  outside account.
- Looked at by the agent as screenshots on its own scratch database: Mi iglesia with the welcome
  (member, guide), Avisos paginated, the coordinator's, pastor's and administration's homes with
  the strip. I did not look at wave 7's screens myself.
- Not verified: lock ordering of the authorization hooks under two real PostgreSQL connections;
  "Entendido" and the live refresh of the strip watched in a browser.

### Not done, and why

- After deploying where serving teams already exist: `manage.py rebuild_role_assignments --apply`
  once after `migrate`; until then a coordinator is refused on their own team (fail-closed).
- Approving a request for an address that has an account elsewhere adds that account and shows
  its own name, as "Agregar persona" does: inherent to reusing accounts.
- Everybody who already uses Mi iglesia meets the new welcome once.
- Nothing else on the open list can be built without the owner, the host or an approval.

## 2026-11-18, later — Wave 6: what was "next", and camp issues #49, #50, #52 (branch `v2/wave-6`)

Wave 5 (pull request #79) is merged into `v2/foundation`; issues #16, #46, #47, #48, #51, #53 and
#72 are closed.

### Shipped

| Piece | Source | Where |
|---|---|---|
| Notices by e-mail, by each person's preference ("Solo los urgentes" by default): a content-free mail, never twice for the same version; `send_notice_emails` for scheduled notices | 0012 | `notices/email.py` |
| "Políticas de la iglesia": a church's own choices, platform floors enforced on read and on write, a review before saving, one audit row per change | 0015 | app `policies`, `people/policy.py` |
| The church's policies reach the event page's default and the retention of attendance (church by church) | 0015 | `agenda`, `prune_attendance` |
| Waiting list: a freed place is offered, with a deadline; accepting goes through `standing`; `expire_waiting_offers` | 0011 | `agenda` |
| A public event page without signing in: the event, its programme and FAQ, nothing about people | 0010, phase A | `agenda/public.py`, `/evento/<slug>/` |
| "Pedir que alguien le acompañe" for a visitor | 0014 | `agenda`, `care` |
| The team's CSV import with a review before saving, and nicknames of coordinadores in the campero import | #50 | `events` |
| A practice campero that never counts, an eleven-step computed "Preparación", "ya practicó" in Equipo | #49 | `events` |
| One JSON line per request (route name, method, status, ms, coarse role) and one per unhandled exception | #52, application half | `core/request_log.py` |

### Verification

`scripts/agents/quality-gate.sh --postgres --e2e` on commit `3c113a4`, 2026-11-18T02:20Z (the commit
after it holds only these documents):

```
step            status           secs  detail
lint            PASS                0  All checks passed!
check           PASS                1  System check identified no issues (0 silenced).
migrations      PASS                0  No changes detected
deploy-check    PASS                1  check --deploy OK: only accepted warnings [security.W005 security.W021]
migration-scope PASS                0  migration scope OK: 6 migration(s) added or changed since f3550e5, all additive
pip-audit       PASS                2  No known vulnerabilities found
tests-sqlite    PASS              233  2393 passed, 6 skipped
tests-postgres  PASS              355  2388 passed, 11 deselected (PostgreSQL 16, throwaway container)
e2e             PASS              475  browser gate OK
overall: PASS
```

- The community journey in that run: 256 checks, 0 problems. A first run failed in the browser
  stage on a step I had added myself (it asked to duplicate a camp as a simple event, which is
  correctly a 404).
- An independent read-only review found no leak of the practice campero, no way onto the confirmed
  list without `standing`, nothing about people on the public page, and thirteen defects, all fixed
  with tests before this gate. The serious ones: the team import (and "Agregar persona", which
  already had it) could show on screen the activation link of an account another church had
  created; a stale policy review could write back a privacy minimum somebody had just raised; the
  church's minors policy did not reach simple events; editing an old notice mailed everybody;
  sending had no timeout; the waiting list kept moving after sign-up was switched off.
- Looked at by me as screenshots: Inscritos with a pending minor (1440), Políticas de la iglesia
  (390), Panorama as a pastor (1440). The agents looked at their own screens on their own scratch
  databases; every new page passed axe at 390 and 1440.
- Not verified: real SMTP (timeouts, a hung server); two real connections on PostgreSQL for the
  waiting list, the policy review and the e-mail claims; the frozen and started sentences of the
  waiting list by eye; the practice label on the kiosk screens; anything on a host.

### Not done, and why

- Commands that exist and are **not scheduled anywhere**: `remind_follow_up_owners`,
  `prune_attendance`, `resettle_registrations` (run once after deploying). Since wave 8 the
  `scheduler` service runs `send_notice_emails` and `expire_waiting_offers` on every pass
  (`run_scheduled_jobs`, issue #80; `docs/deployment.md`, "Scheduled jobs"); alerting on
  `scheduler_status` is host work.
- #52 stays open for shipping the log lines and alerting on the host.
- Defaults taken without asking are in issue #80; the one that most changes the product is how
  quiet Panorama became under the strict privacy rule.
- Known leftovers: the access-requests list still tells an event's administrator which church a
  requester's existing account belongs to; `django.request` still prints "Not Found: /path".
- Not started: #58 (site map and workshops), #60 (photos, needs approval), guardian accounts
  (phase B, behind #75), households, check-in, payments, giving, #32 and #37 (multi-church session).

## 2026-11-18 — Wave 5: safeguards from the owner's second round, and seven older issues (branch `v2/wave-5-safeguards`)

The owner answered #75, #76 and #77 (decision 0015), asked to keep closing issues and to merge
pull requests as needed. #73, #74, #78, #62 and #43 are merged into `v2/foundation` (never `main`,
nothing deployed); each waited for the branch's required checks.

### Shipped

| Piece | Source | Where |
|---|---|---|
| One function decides if a registration is confirmed when an event protects minors: only an age verified by staff, or a valid authorization (paper or in person, same event and policy version, taking part allowed, not withdrawn) | 0015 | `agenda.services.standing`, `people.services.verify_birth_date` |
| Turning the rule on, moving the event's date or changing a person's date of birth re-settles the registrations; `resettle_registrations` does it for a deploy | 0015 | `agenda` |
| Visitors: an explicit "mayor / menor de edad" at the door; a review queue "Edad por confirmar" | 0015 | `agenda` |
| The authorization says what it allows (taking part, transport, photographs); a call or a message does not confirm | 0015 | `agenda.GuardianConsent` |
| "Anular registro" for a walk-up; walk-ups respect the page unless the administration overrides with a reason; never the minors' rule | 0015 | `agenda` |
| A material edit of an urgent notice asks for "Enterado" again; who may publish urgent comes from the policy | 0015 | `notices` |
| Panorama: an exact number only when it and its complement are at least 5; each group's rhythm; "attendance to record" instead of a fixed 21 days | 0015 | `overview`, `groups` |
| Opinions: results from 5 answers; who answered kept as a keyed hash; "Quiero que alguien me busque" apart | 0015 | `feedback` |
| `people/policy.py`: the one place for what a church may set, with platform floors | 0015 | `people` |
| Follow-ups of a camp get an owner by default, never somebody who may not see a confidential one; "sin responsable" on the homes; bulk assign; a content-free reminder command | #46 | `events` |
| Interrupted interviews: a prompt for the coordinador, per coordinador for the tutor, listed before an event ends; nothing is discarded by itself | #47 | `events` |
| "Publicar y usar en entrevistas nuevas" in one step, "En uso", a warning when a newer version is not in use | #48 | `events` |
| "Duplicar" an event as a template, camp or simple, never its people or their data | #51 | `events`, `agenda` |
| Icons and a web manifest; a pastor's bar that does not wrap; aborted view transitions are not JavaScript errors | #53, #72, #16 | static, templates |

### Verification

`scripts/agents/quality-gate.sh --postgres --e2e` on commit `0cf99d8`, 2026-11-18T01:01Z (the commit
after it holds only these documents):

```
step            status           secs  detail
lint            PASS                0  All checks passed!
check           PASS                0  System check identified no issues (0 silenced).
migrations      PASS                1  No changes detected
deploy-check    PASS                0  check --deploy OK: only accepted warnings [security.W005 security.W021]
migration-scope PASS                1  migration scope OK: 6 migration(s) added or changed since 71cbeae, all additive
pip-audit       PASS                1  No known vulnerabilities found
tests-sqlite    PASS              162  2089 passed, 6 skipped
tests-postgres  PASS              264  2084 passed, 11 deselected (PostgreSQL 16, throwaway container)
e2e             PASS              469  browser gate OK
overall: PASS
```

- The community journey in that run: 244 checks, 0 problems.
- An independent read-only review found no authorization or tenant-isolation defect and eight
  others, all fixed with tests before this gate: an authorization survived "Anular registro" and
  later confirmed the wrong child; rows confirmed before this code were never re-evaluated;
  visitors were confirmed by an unticked box; moving an event's date did not re-settle; a stale
  `Person` could keep or restore a verification; `feedback/0001` had been rewritten in place
  (now an additive `0002`); Panorama and the "Enterado" count still gave small numbers by complement.
- Looked at by the agents as screenshots, each on its own scratch database: Inscritos with both
  queues, Confirmar edad, the authorization form, Anular, the door form with an override, the
  visitor's age page, the notice's edit form with the question, Panorama (390, 768, 1440), the
  group form, the camp's homes of the four roles, the questionnaire pages, both duplicate forms.
  Every one passed axe at 390 and 1440. I did not look at wave 5's screens myself.
- During those checks three agents loaded pages on another agent's scratch server by mistake (a
  shared port); one changed a synthetic event's status there and restored it. Only throwaway
  synthetic databases were involved. Later briefs require checking the port and the server's
  working directory first.
- Not verified: the camp's changed screens inside the new shell; real e-mail for the reminder;
  two real connections racing on a verification and a sign-up; anything on a host.

### Not done, and why

- **Release gates stay open:** #75 (the authorization's wording, accepted evidence and retention
  are provisional: the form says so) and #76 (retention periods; no individual pastoral access).
- At the door, most walk-ups will need the override, because they arrive after sign-up closed.
- A group needs ten people before Panorama gives an exact attendance number.
- Next, already being built: notices by e-mail with preferences, a screen for church policies,
  a practice campero and readiness checklist (#49), the team's CSV import (#50). Then the waiting
  list, a public event page (#59, phase A), the second half of visitor follow-up.

## 2026-11-17, night — Wave 4: the owner's decisions, built (branch `v2/wave-4-decisions`)

The owner answered issues #64 and #66–#71 in one message. Their answers are decisions 0009–0014;
the issues are closed. The two earlier branches were pushed as draft pull requests #73 and #74
after scanning the diff for secrets, personal data and migration risk.

### Shipped (working, tested, for churches with the `community` module on)

| Piece | Decision | Where |
|---|---|---|
| The migration freeze on `core/` and `events/` is lifted; the gate fails only what is not additive | 0013 | `scripts/agents/check-migration-scope.sh` |
| A simple event, created in the agenda without the camp's form; it never opens a camp screen | 0013 | `agenda/simple.py`, `events/0013` (`Event.camp_tools`) |
| Event FAQ and programme | 0014 | `agenda` |
| Walk-ups: somebody who arrived is registered without an account; a visitor can be added to Personas | 0011 | `agenda` |
| A minor's registration waits ("Pendiente de autorización") until a guardian's authorization is recorded | 0010 | `agenda.GuardianConsent` |
| Notice levels (normal, importante, urgente), "Enterado" with a count, "Compartir por WhatsApp" with a generic text | 0012 | `notices` |
| Panorama: totals of groups, events, serving and follow-ups for the pastor; no names | 0009 | app `overview` |
| "Tu asistencia" on a member's group page; `prune_attendance` (12 months identifiable, 24 as totals; dry run) | 0009 | `groups` |
| Opinions after an event: three questions, once, without a name; results after 14 days and from 3 answers | 0014 | app `feedback` |

Three slices were built by sub-agents in their own worktrees from written specifications, then
merged, wired into the shell, the home and the admin list, seeded and looked at here.

### Verification

`scripts/agents/quality-gate.sh --postgres --e2e` on commit `b44daaa`, 2026-11-17T23:35Z (the commit
after it holds only these documents):

```
step            status           secs  detail
lint            PASS                0  All checks passed!
check           PASS                0  System check identified no issues (0 silenced).
migrations      PASS                1  No changes detected
deploy-check    PASS                0  check --deploy OK: only accepted warnings [security.W005 security.W021]
migration-scope PASS                0  migration scope OK: 15 migration(s) added or changed since f72d4f6, all additive
pip-audit       PASS                2  No known vulnerabilities found
tests-sqlite    PASS              134  1853 passed, 6 skipped
tests-postgres  PASS              208  1848 passed, 11 deselected (PostgreSQL 16, throwaway container)
e2e             PASS              443  browser gate OK
overall: PASS
```

- The community journey in that run: 244 checks, 0 problems. It now visits Panorama, the FAQ, the
  programme, the opinions and the walk-up page, and creates a simple event.
- An independent read-only review found no authorization or tenant-isolation defect and ten
  others, fixed in `b44daaa` with tests. The serious ones: recognizing a visitor as a known minor
  confirmed them without an authorization; opinions could be paired with people by row order, and
  results that grew live showed each new answer; the invitation to give an opinion vanished when
  anybody else was marked absent; saving attendance from an old page marked later walk-ups absent.
- Looked at as rendered screenshots by me: the events' admin list (390, 1440), Inscritos with
  visitors and a pending minor (1440), Panorama as a pastor (1440), Mi iglesia with an important
  notice and the opinion invitation (390). The agents looked at about a third of their own
  screenshots; every new page passed axe at 390 and 1440 in their runs.
- Not verified: 768 px on the agenda's new pages; the opinion form and its results by eye; a
  real two-connection race on capacity; `prune_attendance` against real volumes; Codex review;
  anything on the sandbox or production hosts; CI on the pull requests.

### Not done, and why

- **Two release gates** (decision 0014): minors' registration (#75) and named pastoral access to
  attendance (#76, not built). A member types their own date of birth and nothing verifies it.
- Waiting list, e-mail notices, church-level policy settings, household sign-up, payments,
  guardian accounts, photos, check-in, giving: later in the owner's order (handoff, "Next").
- `serving` still decides who coordinates by its own rows.
- Small defaults taken without asking are listed in #77.

## 2026-11-17, last — Redesign, wave 3: serving and accompanying (same branch)

### Shipped (working, tested, for churches with the `community` module on)

| Piece | Where |
|---|---|
| Servir: teams, who coordinates and who serves, dates, "Confirmar" / "No puedo", days a person cannot | app `serving` |
| Acompañar: the administration asks one person to look for another by a date; steps from four fixed kinds; no free text | app `care` |
| "A tu cargo" holds Servir and Acompañar with their counts; the administration gets "Equipos de la iglesia" and "Seguimiento de personas" | `core/shell.py` |
| Mi iglesia shows the next dates to serve and a quiet panel for who has people to look for | `groups/views.py::home` |
| "Pedir que alguien la acompañe" on a person's page | `templates/people/person_detail.html` |

Both modules were built by sub-agents from a written specification, merged, wired, seeded and
checked here in a browser. The coordinator's and the tutor's screens were also looked at inside the
shell (an event role whose account is a person of the church), which wave 2 had left open.

### Verification

`scripts/agents/quality-gate.sh --postgres --e2e` on commit `9b60887`, 2026-11-17T10:48Z (the commit after it
holds only these documents):

```
step            status           secs  detail
lint            PASS                0  All checks passed!
check           PASS                1  System check identified no issues (0 silenced).
migrations      PASS                0  No changes detected
deploy-check    PASS                1  check --deploy OK: only accepted warnings [security.W005 security.W021]
migration-scope PASS                0  migration scope OK: no core/ or events/ migration added since f72d4f6
pip-audit       PASS                0  No known vulnerabilities found
tests-sqlite    PASS              117  1734 passed, 6 skipped
tests-postgres  PASS              189  1729 passed, 11 deselected (PostgreSQL 16, throwaway container)
e2e             PASS              457  browser gate OK
overall: PASS
```

- The community journey in that run: 213 checks, 0 problems. It now also confirms a date to
  serve, checks that who serves cannot open the team's page or somebody's follow-up, reads the
  coordinator's count, and records a step on a follow-up.
- A second independent review (read-only sub-agent) of the two modules found no authorization or
  tenant-isolation defect and five others, fixed in `9b60887` with tests: the administration's
  count told an administrator that a follow-up about herself existed; moving a date asked again
  who had said no and could not the new day; a member being removed could keep a date given at
  the same moment; a non-numeric id was a server error; a dead link on one's own page.
- Looked at as rendered screenshots: Servir (390, 1440), a team (390, 1440), a follow-up (390,
  1440), the coordinator's home in the shell (390, 1024, 1440).
- Not verified: the same as wave 2.

### Not done, and why

Unchanged from wave 2, minus serving and follow-ups: the pastor's view of engagement, guardians and
households, check-in for children, photos, FAQ, the programme, giving and notifications outside
Emmaus are not built; an event that is not a camp is still created by the camp's form (#68).
`serving` decides who coordinates by its own rows, not through `has_scope` (handoff, item 4).
Nothing is pushed (#64).

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
2. **Integrity checks for the new tables** in `tenant_integrity` (done in wave 7), and
   `rebuild_role_assignments --check` in CI. S, low.
3. **Events for people** (P3-8, #61): an event that is not a camp, a public page, "Inscribirme"
   from Mi iglesia, "Mis eventos". L, high (camp-adjacent): needs a short design note first.
4. **Avisos** (#57): announcements by audience on Mi iglesia, with the notification preferences
   they need. M.
5. **A screen for the church activity log**, and the church switcher for accounts in two churches
   (the POST view over `core.authz.switch_church`). S each.
