# 0005 - Migration governance across `main` and `v2/foundation` while the camp runs

- **Status:** accepted with the gate guard in this PR
- **Date:** 2026-10-28
- **Deciders:** owner; drafted from the audit (`docs/audits/workstreams/A-inventory.md` finding M-1, `C-domain.md` C-15)

## Context

`main` runs the camp and keeps receiving fixes; `v2/foundation` is the base for the next version
and is never merged into `main`. Both apps' migration graphs are linear today (`core` 0001–0010,
`events` 0001–0012) and one contract is still owed by `events/0011` (the column
`events_event.questionnaire_version_id`). If both branches add `core/` or `events/` migrations,
the next `main → v2` merge produces two leaves per app, needs `makemigrations --merge`, and a data
step written on one side may run before or after a schema step from the other. The host runs
`migrate --noinput` on every container start, so order matters.

## Decision

1. Until the camp ends, a change aimed at `v2/foundation` adds **no file under
   `core/migrations/` or `events/migrations/`**. New tables live in new apps with foreign keys to
   `Church`, `auth.User`, `Event`, `EventParticipant`. The owed 0011 contract is written on
   `main`, off-hours, dump first, and merged down.
2. `scripts/agents/check-migration-scope.sh` enforces it in the gate and in the CI `checks` job
   on pull requests. A reviewed camp fix that must carry a migration labels the PR `area:camp`
   (CI passes `ALLOW_CAMP_MIGRATIONS=true`); locally the same variable skips the check.
3. Every new NOT NULL column carries `db_default`; every `RunPython` has a reverse or a refusal
   guard; large backfills are management commands (dry-run default, `--apply`, idempotent).

## Consequences

- Positive: no merge migrations, no ordering surprises on the v2 stack, the camp's schema is frozen by construction.
- Negative: a few things that belong on `events` (`AuditEvent.church`, consent provenance, the campero ↔ person link) wait for the camp to end or live in an interim table in a new app.

## Reconsider if

- The camp ends (lift the guard, write the contract migration on `main`, merge down).

## Follow-up tasks

- [ ] Write `events/0013` (the 0011 contract) on `main` after the camp.
