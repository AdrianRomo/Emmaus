# 0004 - Feature flags: per-church module flags that gate views, held in `people.ChurchSettings` while the camp runs

- **Status:** proposed (docs slice P1-1, issue #29); issue #33 implements it
- **Date:** 2026-10-30
- **Deciders:** owner; drafted from the audit (`docs/audits/workstreams/A-inventory.md` §1.9, §7.3; `C-domain.md` §8; roadmap rule 4)

## Context

- **No flag framework.** There is no flag library in `requirements.txt`; the pattern is a
  `BooleanField` on `Church`, checked in context processors and views (`A-inventory.md:121-134`).
- **The precedent gates navigation only.** `Church.discipleship_enabled` (`core/models.py:44-48`)
  decides whether leaders see the discipleship bar and where `/dashboard/` sends a pastor
  (`core/context_processors.py:46-50`); "the other core views do not check it, so the discipleship
  URLs stay reachable for users with a church role" (`docs/architecture.md:27`). For a new module
  that would leave an "off" module one typed URL away.
- **How it was turned on.** The precedent is `default=False` without `db_default`
  (`core/models.py:44`). A data migration turned it on only for churches already using the module,
  idempotently, with a no-op reverse (`core/migrations/0010_discipleship_flag_for_churches_in_use.py:11-24`).
- **`db_default` alone is a trap in Python.** On an unsaved instance a `BooleanField(db_default=False)`
  without `default` holds a `DatabaseDefault` expression, and `bool()` of it is `True` (checked on
  the pinned Django 5.2.18, `requirements.txt:3`). The house style sets both:
  `Event.questionnaires_in_order` has `default=False, db_default=False` (`events/models.py:167-172`).
- **No `core` migration during the camp.** `Church` cannot gain a column while the camp runs
  (decision 0005; roadmap rule 2, `docs/audits/EMMAUS_EVOLUTION_ROADMAP.md:8`). Roadmap S2 holds the
  flags "as a `people.ChurchSettings` one-to-one row during it" (:60). `people` is already on
  `v2/foundation` (`people/models.py`, `docs/architecture.md:45-53`).
- **Env switches exist.** `env_bool` (`emmaus/settings.py:13-17`) reads `LLM_ENABLED` (:189).
- **Screen identity is a house test style.** Camp tests assert that a template "renders the bytes
  it always did" (`events/tests/test_multi_questionnaires_admin.py:530-542`); invariant 5 keeps the
  screens of N=1-questionnaire events unchanged (`AGENTS.md:78-81`); `e2e/all_urls.py` lists every
  screen per role on the synthetic seed.
- **Roadmap rule 4** (`EMMAUS_EVOLUTION_ROADMAP.md:10`) and the audit's flag strategy
  (`A-inventory.md:387-393`) set the direction this record makes exact.

## Decision

1. **Storage.** `people.ChurchSettings`, one-to-one to `Church` with `on_delete=CASCADE` (a
   settings row must never block the demo purge, which deletes churches,
   `events/management/commands/purge_demo_data.py:226`), one boolean per module. Created lazily: a
   church with no row reads every flag as off; reading never creates a row (a GET writes nothing);
   the first write creates it.
2. **Field and name.** Each flag is
   `<module>_enabled = models.BooleanField("Usar el módulo de <nombre>", default=False, db_default=False)`:
   `db_default` so that older code (a rollback) can still insert a row, `default` so that an
   unsaved instance reads `False` and not a truthy `DatabaseDefault`. `<module>` is lowercase ASCII
   snake_case, one per module that has views, and the same key is passed to the helper and the
   decorator. No negative names (`*_disabled`); no flags for behaviour inside a module (those are
   settings, like `leaders_can_invite_participants`, `core/models.py:43`). The first two flags are
   `people_enabled` and `community_enabled` (issue #33). The audit's other candidate names
   (`groups_enabled`, `public_pages_enabled`, `person_records_enabled`; `A-inventory.md:389`,
   roadmap :60) become fields only with their module's first view, under this convention.
3. **One read path.** `people.flags.enabled(church, module) -> bool` returns `False` when
   `settings.EMMAUS_V2_MODULES` is false, when `church` is `None` or when there is no row; otherwise
   the field. An unknown module raises `ValueError`, so a typo fails a test instead of shipping a
   module that can never be turned on. Nothing else reads the fields.
4. **Flags gate views, not only navigation.** Every view of a module is wrapped in
   `people.decorators.module_required("<module>")`, which raises `Http404` while the flag is off,
   before any authorization check: an off module answers the same 404 to every role and reveals
   neither the module nor the object. The church checked is the one the request acts in:
   `core/authz.py::current_church(request)` on signed-in church pages (decision 0003), the church
   of the URL's slug on public pages, `event.church` on event pages. A module's navigation entries
   appear only when the same helper says on; navigation never stands in for the view check. A
   flag never widens access (with the flag on, `has_scope` still decides) and never switches an
   authorization rule, so the audit's `role_assignments_enabled` (`A-inventory.md:389`, :412) is
   not created: decision 0003 has no union to switch. Each gated view gets a denied-path test with
   the flag off (vision §15, `docs/EMMAUS_PRODUCT_VISION.md:339`). Camp views and the existing core
   views check no new flag.
5. **Kill switch.** `EMMAUS_V2_MODULES = env_bool("EMMAUS_V2_MODULES", True)` in
   `emmaus/settings.py`, beside `LLM_ENABLED`. When false, every flag reads as off for every church
   whatever the rows say, and the rows are not changed. The default is true because the per-church
   flags are already off: the switch changes nothing until a church turns a module on. It is
   process-level (applied at restart) and exists to run the v2 stack with the camp's exact
   behaviour, for comparison, or to turn every module off at once without touching the database.
6. **One screen-identity test per flag.** With the flag off, every page in `e2e/all_urls.py`, for
   every role it lists, renders the same HTML (CSRF tokens masked) as at the pull request's merge
   base, on the same synthetic seed. The reference is rendered from the merge base in the same run
   and never committed, so a camp fix merged down from `main` cannot make it stale. A test fails
   when `ChurchSettings` has an `*_enabled` field that the identity test does not cover. Unit tests
   cover the helper (no row, row off, row on, kill switch off, unknown module) and the decorator
   (404 when off, the view runs when on). `review-all.mjs` keeps running with every flag off as the
   browser gate's default (`A-inventory.md:392`).
7. **Removal one release after general enablement.** Once a module's flag has been on for every
   active church through one full release (the deploy after the one that turned the last church
   on), a pull request of its own removes it: the decorator and navigation checks go, the field is
   dropped by a migration of the app that holds it (decision 0005 rule 3), and its identity-test
   entry goes. The kill switch goes with the last flag.
8. **Moved to `Church` after the camp.** When decision 0005's guard is lifted, the flags still alive
   move by expand/contract: a `core` migration adds the same fields to `Church` (`default=False,
   db_default=False`); a `people` data migration after it copies each church's values, in the
   `core/0010` style (idempotent, only turns flags on, no-op reverse because `ChurchSettings` still
   holds the values); `people.flags.enabled` switches its read in the same release;
   `ChurchSettings` is dropped one release later. The helper is the only API, so no view changes.

**Not decided here.** Who may turn a module on and from which screen (decided by the issue that
adds the first such screen; until then no screen does it). Whether `discipleship_enabled` should
also gate its views: that edits core views, so it waits for the camp, and the flag stays on `Church`
outside this record. Flags per sede, per group or per user. A flag library.

## Consequences

- Positive: a new module is invisible and unreachable for every church until it is turned on; each
  flag carries proof that the existing screens are unchanged while it is off; no `core` migration
  while the camp runs; one environment variable restores the camp's exact behaviour on the v2
  stack; the read path is one function, so moving the storage to `Church` touches no view.
- Negative / trade-offs: one more query per gated request; until the move, church-level flags live
  in a `people` table and church settings live in two places (`Church` and `ChurchSettings`; the
  name also resembles `core.forms.ChurchSettingsForm`, `core/forms.py:220`, which edits `Church`);
  rendering the merge base makes the identity step slower; `discipleship_enabled` keeps its weaker,
  navigation-only gate.

## Alternatives considered

- Flags on `Church` now (`A-inventory.md:389`; roadmap S2): a `core` migration during the camp
  (decision 0005).
- A flag library (`django-waffle`): a new dependency and a second mechanism beside the existing
  booleans, with no per-church need a field does not meet.
- Navigation-only gating, like `discipleship_enabled`: every module URL stays reachable
  (`docs/architecture.md:27`).
- 403 instead of 404 while off: tells a visitor that the module exists.
- A committed HTML snapshot as the identity reference: goes stale with every camp fix merged from
  `main`.
- Kill switch off by default: harmless for the camp, but every v2 environment would need a
  configuration step before any module could be tried; the per-church default already keeps
  everything off.

## Reconsider if

- A module must be on for part of a church (a sede, a pilot group): flags per scope, probably
  through decision 0003's scopes.
- More than five flags are alive at once: removal is not happening; review the release cadence.
- The camp ends: move the flags to `Church` (decision 8).
- The identity step takes more than a few minutes in CI: run it with the browser gate or nightly.

## Follow-up tasks

- [ ] Issue #33 (P1-7): `people.ChurchSettings` with `people_enabled` and `community_enabled`,
  `people.flags.enabled`, `people.decorators.module_required`, `EMMAUS_V2_MODULES`, the
  screen-identity test and the unit tests above. Its criteria say `BooleanField(db_default=False)`;
  add `default=False` (decision 2).
- [ ] After the camp: move the live flags to `Church` (decision 8); decide whether
  `discipleship_enabled` gates its views.
