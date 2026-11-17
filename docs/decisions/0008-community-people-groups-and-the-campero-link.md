# 0008 - Community: who is "of the church", who manages a group, and the campero ↔ person link

- **Status:** proposed with its implementation (branch `v2/slice-1-camp-to-group`); the owner asked for this slice directly on 2026-11-17, which is the go that decisions 0003 and 0004 and issue #31 were waiting for. Merging the pull request accepts it.
- **Date:** 2026-11-17
- **Deciders:** owner. Numbers 0006 (Django admin) and 0007 (consent provenance) stay reserved by the roadmap.

## Context

Decisions 0002, 0003 and 0004 fixed the vocabulary, the authorization entry point and the flags, and
left open the questions that only the first module with screens can answer (0003 "Not decided here":
which group roles manage, whether a church administrator manages every group; 0004: who turns a
module on; 0002: how the campero ↔ person link is made). The camp ended on 2026-11-15, so the link
can now be made "after the event". `docs/v2/EMMAUS_V2_PRODUCT_BLUEPRINT.md` asks for one person,
one profile, many contexts, with no permission surviving its context.

## Decision

1. **Being "of the church" is `Person.user`, not a church role.** The community screens open for
   an account that is a person of the current church (`people.persons.own_person`). No
   `UserChurchRole` is created for a member: `Role.PARTICIPANT` stays what it is, a participant of
   the discipleship module. An event role alone (a coordinator, a tutor) does not make an account
   a person: those accounts get 404 on the community screens until the administration links them.
   An account that holds a church role gets its person on its first visit
   (`people.services.ensure_own_person`), because staff accounts predate `Person`.
2. **Amendment to decision 0003, rule 6.** `core.authz.current_church` chooses among the churches
   where the account holds a church role **or is a person**, in that order. The session value is
   still validated against that list on every request.
3. **Amendment to decision 0003, rules 4–5: only the group scope is projected for now.**
   `access.RoleAssignment` holds rows whose source is `groups.GroupMembership`. Church and event
   rows are not written and no receiver hangs from `UserChurchRole` or `EventMembership`: those
   rows would be read by nothing, and a receiver would add a write inside camp transactions for no
   user value. `has_scope` delegates those scopes exactly as 0003 says. The rows and receivers can
   be added when Phase 4 (S12) is actually weighed; `rebuild_role_assignments` is already built
   around a list of sources. The `ministry` scope type is added with the ministries module.
4. **Group roles.** `lider` ("Guía del grupo") and `integrante`. `has_scope(group, view)` is any
   active role; `manage` is `lider`. A guide sees who asked to join and who is in the group, with
   their contact data (each of them either asked or is in), decides requests, and takes members
   out. A guide never sees the church's directory, never names another guide and never removes one.
5. **The church administration manages groups through its church role, not through a group
   role.** `church_admin` (church scope `manage`) creates, edits and archives groups, names and
   removes guides, adds people, and may decide requests (a church starting out has groups without
   a guide who can sign in). Nothing is implied *into* the group scope: `has_scope(admin, church,
   "group", id, …)` is false, and the views say `is_church_admin or has_scope(group manage)`
   explicitly. A church `pastor` is a person of the church like any other on these screens: pastoral
   oversight of groups is a separate decision (open question below), not a default.
6. **What a person of the church may read about a group:** its name, kind, description, audience,
   day and time, sede and who guides it. **Where it meets is for its members and the
   administration** (it is often a home). Members do not see the list of other members.
7. **A join request carries no free text.** It is a person and a group; the person is told in the
   panel that the guide will see their name and how to reach them. A declined request shows for 30
   days on the person's home, without a reason. Notices are `core/notify.py` kinds: content-free,
   and the screen says an e-mail went out only when the backend delivered one.
8. **The campero ↔ person link** is `people.EventParticipantLink(participant, person, event,
   linked_by, linked_at)`: one campero is one person, one person is one campero per event, both of
   the same church, written only by `people.services.link_participant` /
   `create_person_from_participant`, one campero at a time, by a church administrator, **after the
   event** (closed, archived, or its last day passed), each one audited (`persona_vinculada`).
   Same-name people of the church are shown as a hint; nothing is linked by showing them. Only the
   name and the sede cross over. The screens live under `/mi-iglesia/personas/desde-un-evento/`
   and read camperos through `resolve_access(...).participants()`: no camp screen changed.
9. **Retention.** The link is deleted with the campero (`on_delete=CASCADE`) when the event's
   retention purge runs; the person stays (decision 0002, rule 4). After the purge the person's
   home no longer lists that event. This follows the documented rule; see the open question.
10. **Person → account.** "Dar acceso" creates an account with no password, no church role and no
    event role, and shows a one-time password link on screen (Django's reset token; nothing is
    e-mailed, and the screen says so). If an account already uses the person's e-mail, it is linked
    only after the administrator answers "Sí, es la misma persona" (audited `cuenta_vinculada`);
    one account is one person per church. A person who was under the event's adult age at a linked
    event (and has no birth date saying otherwise) needs the administrator's confirmation that a
    parent or guardian agrees; the confirmation is stored in the audit row.
11. **Who turns a module on** (decision 0004, "Not decided here"): an operator, with
    `manage.py set_church_module <slug> <module> on|off`, audited. No screen does it yet.
12. **Church-level audit** lives in `people.ChurchAuditEvent` until `events.AuditEvent` may gain a
    church column (roadmap S1). Its actions and metadata keys are closed lists; a summary names
    people by name only and never holds contact data.

## Consequences

- Positive: a camp role, a church role and a group role are three separate facts about one
  account, and each ends where its scope ends; the camp's tables, chokepoints and screens are
  untouched; a member needs no role to exist; every new URL is 404 with the flag off.
- Negative / trade-offs: bringing a whole camp into Personas is one click per campero (by design:
  decision 0002 forbids automatic linking); `Person.user` without a role is a new kind of account
  that the legacy screens describe poorly (the event list tells them "no events"; their home is
  Mi iglesia); church flags live in a `people` table until they can move to `Church`.

## Open questions for the owner

- **Q-C1 Retention vs history.** Should "this person took part in Campamento X" survive the
  event's 365-day purge as a fact of the person (name of the event and dates only)? Today it does
  not. Keeping it means a small person-level record with its own erasure rule.
- **Q-C2 Accounts for minors.** Is the administrator's recorded confirmation enough, or does a
  guardian need their own verified step before a minor gets an account (issue #59)?
- **Q-C3 Pastoral oversight of groups.** Should a church `pastor` see every group's roster and
  requests read-only?
- **Q-C4 Members seeing members.** Should members of a group see each other's names?

## Reconsider if

- A second church needs a different rule for who manages groups: make rule 5 a per-church setting.
- Linking a camp one by one proves too slow for a real camp: add a reviewed batch ("these N have
  no same-name person: create them all"), still confirmed by a person and audited per row.
