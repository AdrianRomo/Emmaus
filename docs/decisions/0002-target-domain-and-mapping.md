# 0002 - Target domain for the next version, and how it maps onto what exists

- **Status:** proposed (first implementation slice of the Phase 0 audit, `docs/audits/EMMAUS_EVOLUTION_ROADMAP.md` §8)
- **Date:** 2026-10-28
- **Deciders:** owner; drafted from the audit's domain and gap workstreams (`docs/audits/workstreams/C-domain.md` §8, `E-gaps.md` §6)

## Context

The vision (`docs/EMMAUS_PRODUCT_VISION.md` §5) describes a conceptual model with `Tenant`,
`Campus`, `AuthIdentity`, `Person`, `IdentityLink`, `CampusAffiliation`, `RoleAssignment`,
`Group`, `Ministry`, `Event`, `EventRegistration`, `EventAssignment`, `FormDefinition`,
`Program`, `ContentItem`, `Workflow`, `ConsentRecord`, `AuditEvent`, and says it is a target,
not a schema. The audit found that most of it already exists under other names, that one piece
is missing entirely (a person independent of an account or an event), and that the camp tables
must not change while the camp runs on `main`.

## Decision

| Vision concept | Existing model | Verdict |
|---|---|---|
| `Tenant` | `core.Church` | **reuse as is**; it is already the FK root of every aggregate |
| `Campus` | `events.Sede` | **reuse**; `(church, slug)` unique, `active`; moved to `core` only in Phase 4 if ever |
| `AuthIdentity` | `auth.User` (e-mail as username) | **reuse**; harden e-mail uniqueness later (roadmap S11) |
| `Person` | none (`EventParticipant` is per event, `ParticipantProfile` is per account) | **new** `people.Person`: church-scoped, account optional (`user` nullable, unique per church), never auto-created for a campero |
| `IdentityLink` | none | **`Person.user`**, unique per `(church, user)`: one account is one person per church; a person in two churches is two rows. A separate table only if one account must ever map to several persons in one church |
| `CampusAffiliation` | `EventParticipant.sede` (per event) | **new** later; interim `Person.primary_sede` |
| `RoleAssignment` | `core.UserChurchRole` (church) + `events.EventMembership` (event) + `events.TutorAssignment` | **adapt**: both tables stay the sources of truth; a `RoleAssignment` projection serves **new** scopes (group, ministry); `events/access.py` stays the chokepoint |
| `Group` / `GroupMembership` | none | **new** `groups` app, subject = `Person` |
| `Ministry` / `TeamMembership` | none | **new**, Phase 3 |
| `Event` / `EventRegistration` | `events.Event` / `events.EventParticipant` | **reuse**; a campero ↔ person link lives in a separate table (`EventParticipantLink`), never as a column on `EventParticipant` before the camp ends |
| `EventAssignment` | `EventMembership`, `TutorAssignment` | **reuse as is** |
| `FormDefinition` / `FormVersion` / `Submission` | `Questionnaire` / `QuestionnaireVersion` (+ sections, questions, options) / `Interview` + `Answer` (+ `AnswerRevision`) | **reuse**; the camp form engine is the form engine |
| `Program` / `Enrollment` | `DiscipleshipPath` / `Enrollment` | **reuse**; re-point to `Person` additively in Phase 3 |
| `ContentItem` / `Series` | none | **new**, Phase 3, links only |
| `Workflow` / `WorkflowTask` | `events.FollowUp` (camp) and `core.FollowUpRequest` (church) | **adapt** later; the camp one is untouched |
| `ConsentRecord` | `events.ConsentRecord` | **adapt** after the camp: notice hash, excerpt, purpose; a person-level record with the same vocabulary |
| `AuditEvent` | `events.AuditEvent` | **adapt** after the camp: nullable `church`; interim church-level table in a new app |

Rules that come with the mapping:

1. **Never auto-link people across churches**, and never link by name, e-mail or phone alone. Every
   campero ↔ person link is a human-confirmed, audited action after the event; `normalized_name`
   is a warning aid, not a key (vision §5.2).
2. **New data lives in new apps** (`people`, `access`, `groups`, `public`) with foreign keys to
   `Church`, `auth.User`, `Event` and `EventParticipant`, until the camp ends (decision 0005).
3. **Group leadership is never a `UserChurchRole` or an `EventMembership`**, and group views never
   query `Interview`, `Answer` or `FollowUp`; that is what keeps a campero's answers away from a
   later group leader (vision J2 step 7) by construction.
4. A `Person` linked from a camp record must not outlive the camp's retention on the camp's
   account: the purge deletes the link and the camp record, never the person; the person carries
   its own erasure rule.

## Consequences

- Positive: no rename, no rewrite; the 1,420-test safety net of the camp stays bound to the same
  models; every step is additive with a rollback; the vocabulary is settled before groups exist.
- Negative / trade-offs: two role systems coexist with a projection for a while; the camp ↔ person
  bridge is manual until a self-service claim is designed (open question Q-P2); a few vision
  names (`IdentityLink`, `EventRegistration`) do not appear as tables.

## Alternatives considered

- A custom user model or a `Tenant` rename: churn with no camp benefit; `Church` already is the tenant.
- Adding `person` columns to `EventParticipant` now: a camp-table migration during the event.
- Replacing `UserChurchRole`/`EventMembership` with `RoleAssignment`: breaks the authorization
  chokepoint the camp depends on; projection first, replacement in Phase 4 if still wanted.

## Reconsider if

- A church needs one account to act as several persons in the same church (then `IdentityLink` becomes a table).
- The camp ends and `v2/foundation` becomes `main` (the camp-table steps S1, S4, S6, S10 become possible).

## Follow-up tasks

- [ ] Issue #22: `people` app with `Person` (additive, invisible).
- [ ] Issue #23: `link_people` backfill command.
- [ ] Decision 0003 (authorization compatibility layer), 0004 (feature flags), 0005 (migration governance across branches) — the next docs slice.
