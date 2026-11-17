# 0003 - Authorization compatibility layer: `core/authz.py::has_scope` over the legacy tables, a `RoleAssignment` projection for new scopes

- **Status:** proposed (docs slice P1-1, issue #29); implemented for the church, event and group scopes on 2026-11-17 at the owner's direct request, with the amendments recorded in decision 0008 (rules 2 and 3: the current church also counts churches where the account is a person; only the group scope is projected)
- **Date:** 2026-10-30
- **Deciders:** owner; drafted from the audit (`docs/audits/workstreams/C-domain.md` §3, §8; `A-inventory.md` §7.2, §7.4; `D-security.md` §0, F3, F4, §9.2)

## Context

Authorization has two decision points today, and they answer differently for a user who holds
roles in more than one church:

- **Event scope.** `resolve_access(user, event)` (`events/access.py:255-269`) reads the
  `UserChurchRole` for `event.church_id` (:260) and the user's `EventMembership`: superuser or
  church `church_admin` → Administrador (:261-262); a deactivated membership → no access, even for
  a church pastor (:263-264); an active membership → its role (:265-266); a church `pastor` →
  Pastor without a membership (:267-268). `can_view_sensitive` comes only from an active
  membership with the flag (:69-71). `accessible_events` applies the same rule to lists
  (:272-290). AGENTS.md invariant 2 (`AGENTS.md:63-71`) and the tests it names pin all of it.
- **Church scope.** `get_user_role(user)` (`core/access.py:20-23`) returns the user's first
  `UserChurchRole` by id; every core view (`role_required`, `core/views.py:139-153`) and every
  `visible_*` queryset (`core/access.py:60-132`) takes the church from it. `UserChurchRole` is
  unique per `(church, user)` (`core/models.py:136`), so several churches can be stored but only
  the first can be reached: a participant of church A who becomes admin of church B gets 403 on
  B's pages (F4, `D-security.md:201-204`; C-02, `C-domain.md:185-195`). Event creation has the
  same shape: `administered_churches(user)[0]` (`events/views/home.py:44-47`, F3).
- **New scopes.** Groups and ministries need roles (leader, member) with status and start/end
  dates (vision §5.3, `docs/EMMAUS_PRODUCT_VISION.md:138-146`). Decision 0002 rule 3 forbids
  storing them as `UserChurchRole` or `EventMembership` rows, and maps the vision's
  `RoleAssignment` to "both tables stay the sources of truth; a `RoleAssignment` projection serves
  new scopes" (`docs/decisions/0002-target-domain-and-mapping.md:27`).
- **Constraints.** `events/access.py` and `core/access.py` are not edited before the camp ends
  (roadmap rule 5 and §2, `docs/audits/EMMAUS_EVOLUTION_ROADMAP.md:11`, :27); v2 adds no migration
  under `core/` or `events/` (decision 0005). The Phase 1 gate is "one user holds roles in two
  churches and one event with no leakage (J3 test)" with `events/access.py` untouched (roadmap
  :36; vision J3, `docs/EMMAUS_PRODUCT_VISION.md:194-197`).
- **Precedent for a context held in the session.** `event_view` stores the last event in
  `request.session["current_event"]` (`events/views/common.py:38-39`); `events_home` uses it only
  to choose among `accessible_events` (`events/views/home.py:22-26`). The session chooses;
  `resolve_access` decides.

## Decision

1. **One entry point for new code.** `core/authz.py` (a new file; `core/access.py` is not edited)
   exposes `has_scope(user, church, scope_type, scope_id, action) -> bool`. Every view of a new
   module asks it, and nothing else, whether `user` may do `action` on the scope
   `(scope_type, scope_id)` of `church`. It returns `False` for an anonymous or inactive user, for
   `church=None`, for a scope object that does not exist or belongs to another church (a guessed
   id is a denial, never an exception), and for `scope_type="church"` with `scope_id != church.pk`.
   An unknown `scope_type` or `action` raises `ValueError` (a programming error that tests catch);
   it never returns `True`.
2. **Delegation by scope type.** The scope types are a closed list: `church`, `event`, `group`,
   `ministry`.

   | `scope_type` | Where the answer comes from | `view` | `manage` |
   |---|---|---|---|
   | `church` | the `UserChurchRole` of `(church, user)`, looked up for that church as `events/access.py:260` does; never `get_user_role` | any church role | `church_admin` |
   | `event` | `resolve_access(user, event)`, after checking `event.church_id == church.pk` | `resolve_access(...) is not None` | `can_manage_event` (`events/access.py:73-75`) |
   | `group`, `ministry` | `access.RoleAssignment` rows of the scope with `status="active"` and `start_at <= now` and (`end_at` null or `now < end_at`) | any active row | the scope's leader roles, fixed by the issue that adds the scope's source table |

   For every single-church user (all of `CampFixture`, `events/tests/base.py:29-64`) the church
   answer equals what `get_user_role` gives, and the event answer is `resolve_access`'s exactly,
   including a superuser as Administrador of every event (:261) and archived events reachable by
   URL (F9: inherited, not fixed here). For a user with roles in several churches the church answer
   is per church on purpose; that difference is J3. `is_superuser` adds nothing at church scope, as
   in core today (a superuser without a church role is sent away from every core page,
   `core/views.py:143-146`). Nothing is implied into a new scope: a church role grants nothing in a
   group or ministry, and an event role grants nothing outside its event (invariant 2, extended).
3. **`action` is a closed vocabulary of two words**, a `TextChoices` in `core/authz.py`: `view` =
   may open the scope's pages and learn that it exists; `manage` = may change the scope's settings
   and who holds roles in it. No action answers a question about sensitive data: confidential
   answers, follow-ups and pastoral notes stay behind `EventAccess.can_view_sensitive` and the
   confidential levels (`events/access.py:69-71`, :175-209; invariant 4), and new modules never
   query `Interview`, `Answer` or `FollowUp` (decision 0002 rule 3). A third word is a change to
   this record, with its own parity and denied-path tests.
4. **The projection.** New app `access`, model `RoleAssignment`: `subject_user` (FK `auth.User`,
   CASCADE), `church` (FK, CASCADE), `scope_type` (the four values above), `scope_id`
   (`PositiveBigIntegerField`; every app uses `BigAutoField`, `emmaus/settings.py:153`), `role`
   (the source's value verbatim: `church_admin`, `administrador`, ...), `status` (`active` |
   `inactive`), `start_at`, `end_at` (null = open), `source_table` (the source model's label, e.g.
   `core.UserChurchRole`), `source_id`; unique `(source_table, source_id)`; indexed by
   `(subject_user, scope_type, status)`. Every row derives from exactly one source row:
   - `core.UserChurchRole` → `church` scope, `active`, `start_at = created_at`;
   - `events.EventMembership` → `event` scope, `status` from `is_active` (`events/models.py:387`);
   - later, the membership tables of the new apps (`groups.GroupMembership`, the ministry team
     table) → `group` / `ministry` scope, one row per membership whose `Person` has an account; a
     membership without an account gets no row and is counted as skipped.

   `TutorAssignment` is not projected: it is a supervision link read only by `EventAccess`
   (`events/access.py:99-109`). The projected church and event rows are read by nothing in
   authorization (C-domain S7, `C-domain.md:415`), because `has_scope` delegates those scopes to the
   legacy tables. They exist so that the Phase 4 question (S12, `C-domain.md:420`) is answered with
   a projection that has been checked against its sources every day.
5. **How the projection is rebuilt and kept in sync.**
   - `manage.py rebuild_role_assignments` is a dry run by default (prints, per source and church,
     the rows to create, update and delete, the unchanged ones and the skipped ones with the
     reason); `--apply` writes the difference in one transaction per church; `--check` exits 1 on
     any difference. A second `--apply` changes nothing (roadmap rule 3).
   - `post_save` and `post_delete` receivers for every source, connected in `access/apps.py`
     `ready()` (no file under `core/` or `events/` changes), write or delete the one derived row
     inside the caller's transaction. A `people.Person` save re-derives that person's group and
     ministry rows (an account linked or unlinked).
   - Signals miss `QuerySet.update()`, `bulk_create`, raw SQL and data migrations. No application
     code writes the two legacy tables that way today: the only write that is not a plain `save()`
     is `update_or_create` in `assign_role` (`core/services.py:60`), which saves. Drift on those
     rows cannot change an answer, because `has_scope` does not read them; `--check` finds it.
   - For `group` and `ministry` the projection *is* the answer, so a missed write would be a wrong
     answer. Those sources are written only through their app's service functions, which write the
     source row and its derived row in one `transaction.atomic`; the receivers are the safety net;
     `--check` runs in CI on the synthetic seed and nightly beside `tenant_integrity` (P1-12).
   - `start_at` and `end_at` are compared with `timezone.now()` when read; expiry needs no job.
6. **Explicit current church for multi-church users.** `request.session["current_church"]` holds a
   church pk and is written only by a POST switch view. `core/authz.py::current_church(request)`
   returns that church when the user holds a `UserChurchRole` in it; otherwise it removes the key
   and falls back to `get_user_church(user)` (`core/access.py:26-28`: the first role by id, today's
   behaviour), so a single-church user sees no change. New church-scoped views pass
   `current_church(request)` as the `church` argument; event pages always pass `event.church`,
   never the session value. The session chooses among churches where the user already holds a
   role and `has_scope` still decides, so a forged or stale value grants nothing. The template
   variable `current_church` that `get_role_context` already sets (`core/access.py:36`, shown in
   the bar by `templates/base.html:16`) keeps meaning "church of the first role" while
   `get_user_role` is unchanged; new code does not overwrite it.
7. **Untouched.** `resolve_access`, `accessible_events`, `administered_churches` and
   `get_user_role`: `events/access.py` and `core/access.py` stay byte-identical, and every existing
   test stays unchanged and green.
8. **The gate.**
   - *Parity:* for every user of `CampFixture` plus a church pastor without a membership, a church
     pastor whose membership was deactivated (as in `test_security_review.py:565-575`), a
     superuser, an inactive user and an anonymous user, on both fixture events:
     `has_scope(u, e.church, "event", e.pk, "view")` equals `resolve_access(u, e) is not None`, and
     `"manage"` equals `can_manage_event`; at church scope, `has_scope` equals the role test on
     `get_user_role(u)`.
   - *Denied paths:* an event or group id of another church, `scope_id != church.pk`, an inactive
     membership, an expired `end_at`, `status="inactive"`, `church=None`; an unknown action raises.
   - *Acceptance (J3):* `access/tests/test_j3.py::test_one_user_with_roles_in_two_churches_and_one_event_sees_no_leakage`.
     The fixture's church A has `event` and `other_event`; a second church B has `event_b`. User X
     is `participant` in A (the older row), `church_admin` in B and `coordinador` in `event`. It
     asserts: B `view` and `manage` true; A `view` true and `manage` false; `event` `view` true and
     `manage` false; `other_event` `view` false; `event` asked under church B false; `event_b`
     `manage` true under B (through `resolve_access`) and false under A; `current_church` is A with
     no session value, B after the switch, and A again (key removed) with a forged value for a
     church where X has no role; after X's membership in `event` is deactivated, `event` `view` is
     false while both church answers and both `UserChurchRole` rows are unchanged ("revocation of
     one assignment does not remove the other memberships"); `get_user_role(X).church` is still A.

**Not decided here.** Replacing `UserChurchRole` and `EventMembership` by `RoleAssignment` as the
source of truth (S12, Phase 4; `C-domain.md:420`, roadmap :71). Making `get_user_role` or the
legacy core views honour `current_church` (F4 on the discipleship screens): it edits
`core/access.py`, which waits for the camp to end. The church choice at event creation (F3, camp
code). A `sede` scope (none exists, `D-security.md:73`). Which group and ministry roles `manage`,
and whether a church admin manages every group of the church (P2-1, P2-2). Operator or support
access (P4-3).

## Consequences

- Positive: new modules get one authorization call, deny-by-default, with a closed vocabulary; the
  camp's chokepoints are not edited, so the ~1,420 tests and invariant 2 stay bound to the same
  code; J3 becomes testable without touching `core/access.py`; through the new views, a
  multi-church user reaches every church where they hold a role; the projection builds a drift
  record before Phase 4 is decided.
- Negative / trade-offs: until the camp ends a multi-church user gets two answers: new views follow
  `current_church`, the legacy discipleship screens still follow the first role (F4 remains there,
  and the J3 test of those screens in `D-security.md:261` stays an expected failure); projected
  church and event rows are maintained without deciding anything; the receivers add one write per
  role change; the group and ministry write paths carry a transactional discipline that review
  has to check.

## Alternatives considered

- An adapter in `core/access.py` that unions old and new rows behind a per-church flag
  (`A-inventory.md:377`, :412): edits a frozen chokepoint and makes the camp's answer depend on a
  flag.
- Reading `RoleAssignment` for church and event scopes too: one stale row would change a camp
  answer; delegation keeps those answers where the tests already prove them.
- New `UserChurchRole` choices or `EventMembership` rows for group leaders: forbidden by decision
  0002 rule 3, which keeps a campero's answers away from a later group leader by construction.
- Reading the new apps' membership tables directly: simpler today, but each new scope would grow
  its own status and date rules, and Phase 4 would have no projection to stand on.
- A permission library (`django-guardian`, `rules`): a new dependency for a question the codebase
  already answers in two modules; there is no permission or flag framework today
  (`A-inventory.md:134`).

## Reconsider if

- A new module needs a third action (for example pastoral oversight across groups): amend the
  vocabulary here, with its tests.
- The camp ends: `get_user_role` may then read `current_church` (F4 on the legacy screens), event
  creation may ask for the church (F3), and S12 can be weighed against the projection's drift
  record.
- `rebuild_role_assignments --check` reports drift on the legacy sources more than once a month:
  find the write path that bypasses `save()` before anything reads those rows.
- A superuser needs church tools without a church role: design it as time-bounded, audited support
  access (P4-3), not as an `is_superuser` branch in `has_scope`.

## Follow-up tasks

- [ ] Issue #31 (P1-5): `access` app, `RoleAssignment`, `rebuild_role_assignments`, the receivers,
  `core/authz.py::has_scope`, the parity and denied-path suites and the J3 test above.
- [ ] Issue #32 (P1-6): session `current_church`, the POST switch view, the church switcher in the
  bar. **Re-scope before it starts:** its criteria change `get_user_role` in `core/access.py`,
  which this record leaves untouched; here the session value is read by
  `core/authz.py::current_church` and the new views, and its legacy-screen criterion (B's
  `church_settings` 200) moves to a post-camp issue.
- [ ] After the camp: an issue for F4 on the legacy discipleship screens and F3 at event creation.
- [ ] Add `rebuild_role_assignments --check` to CI and to the nightly integrity run once P1-12 exists.
