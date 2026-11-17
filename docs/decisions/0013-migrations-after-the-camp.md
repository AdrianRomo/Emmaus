# 0013 - Migrations after the camp: the freeze on `core` and `events` is lifted

- **Status:** accepted (owner, 2026-11-17, issue #68: "Lift it"). Supersedes 0005.
- **Date:** 2026-11-17

## Context

Decision 0005 forbade migrations under `core/` and `events/` on v2 branches while the camp ran on
`main`. The camp ended on 2026-11-15. The freeze forced `agenda.EventPage` to live beside
`events.Event`; kept forever, that becomes two event models.

## Decision

1. Branches aimed at `v2/foundation` may add migrations in `core/` and `events/`. Emmaus has one
   events architecture: new event behaviour goes on `events.Event` or on tables that hang from it,
   not on a parallel model.
2. `main` adds no more migrations in those apps; schema work happens on `v2/foundation`. The
   contract still owed by `events/0011` is written on v2 with the next `events` migration.
3. The first camp's data stays protected: migrations are additive. The gate
   (`scripts/agents/check-migration-scope.sh`) fails a migration that removes, renames or runs raw
   SQL unless a human labels the PR `risk:high` after a dump. 0005's rule 3 stands (`db_default` on
   new NOT NULL columns, reversible `RunPython`, large backfills as commands).
4. Every migration is tested on PostgreSQL in the gate before a PR is believed.

## Consequences

- An event that is not a camp can be created without the camp's form (first use of this decision).
- `agenda.EventPage` stays as it is for now; folding it into `events` is its own reviewed change.

## Reconsider if

- `main` needs an urgent schema fix before v2 replaces it (then: write it on `main`, merge down at once).
