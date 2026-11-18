# 0016 - Serving teams in the authorization layer: the `ministry` scope

- **Status:** accepted: closes the deviation noted in `docs/v2/AGENT_HANDOFF.md` (what's next, item 3)
- **Date:** 2026-11-17
- **Deciders:** owner (wave 7). Applies decision 0003 (rules 3 to 5) and decision 0008 (rule 5) to `serving`.

## Context

Decision 0003 says every authorization decision of the new modules goes through
`core/authz.py::has_scope`, and that the group and ministry scopes are answered by the
`access.RoleAssignment` projection. `serving` shipped before the `ministry` scope existed: its views
asked `serving.services.coordinates(person, team)`, a read of `TeamMembership`. The answer was
right, but it was a second place that decides who may manage something, which no check compared
with anything.

## Decision

1. **Scope type `ministry`**, the name decisions 0002, 0003 and 0008 reserved; its `scope_id` is a
   `serving.Team` id. A choices-only migration (`access/0002`).
2. **One row per `serving.TeamMembership` whose person has an account** (source table
   `serving.TeamMembership`), with the membership's role verbatim (`coordina`, `sirve`). The row is
   `active` while the membership is active **and the team is not archived**; `start_at` is the
   membership's `created_at`, `end_at` stays empty. A person without an account holds no row.
3. **`has_scope(user, church, "ministry", team_id, manage)` is the role `coordina`**
   (`core.authz.MINISTRY_MANAGE_ROLES`); `view` is any role in force. Nothing in `serving` asks for
   `view` today: who serves reads their own dates from the `serving` rows, as before.
4. **The church administration manages every team of its church through its church role**, exactly
   as it does groups: nothing is implied into the team scope, and the views say
   `is_church_admin or has_scope(ministry, manage)`. An event role, a group role and a church
   pastor's role grant nothing in a team; a team role grants nothing outside its team.
5. **Kept in sync by the services, proved by the command.** `serving.services.set_member`,
   `remove_member` and `archive_team` write the derived row in the same transaction;
   `manage.py rebuild_role_assignments` compares both sources (`--check`, `--apply`) and also
   reports rows whose source nobody declares. Three rules hold for both projected scopes, group
   and ministry (`access/sources.py`; added after the review of this change):
   - **The write reads the database, not the caller.** `sync_assignment` re-reads the membership,
     its team and the person's account under a lock on the membership and derives the row from
     that; a copy loaded earlier (a view's person, a hook's list of memberships) cannot write back
     a role that ended meanwhile or miss an account linked meanwhile.
   - **A `Person` save re-derives that person's rows only when the account changed**, judged
     against the row in the database before the save, and under the locks the services take, in
     their order: each team, then its membership.
   - **A deleted source takes its row with it.** The projection has no foreign key to its source,
     so `post_delete` receivers on `TeamMembership` and `GroupMembership` (Django sends them for
     rows deleted by the cascade from a person, a team or a group) delete the derived row, and
     deleting a `Team` or a `Group` deletes every row of that scope id.
6. **What is shown still reads `serving`**: a person's dates, counts, the names of who coordinates.
   The "open the team" link on the person's page reads the projection
   (`serving.access.managed_team_ids`), so what is offered equals what is allowed.
7. **Amendment to decision 0003, rule 5 ("the projection *is* the answer"): the projection answers
   only while its source agrees.** For the group and ministry scopes `has_scope` (and the lists
   `managed_group_ids` / `managed_team_ids`) keep a role row only if the membership it names as
   its source still exists, is active (for a team: in a team that is not archived), and says the
   same account, scope, church and role (`access.sources.backed_by_source`). A row that outlived
   or drifted from its source denies; it never grants. Cost: still one query per decision, with
   one correlated `EXISTS` in it (the membership by primary key, joined to its team or group and
   to its person, both by primary key). The source can only take away: a membership without its
   row still grants nothing, so a missed write fails closed and `--check` finds it.

## Consequences

- Positive: one question decides who manages a team; a missed write is found by
  `rebuild_role_assignments --check`; the serving tests assert after every test that the projection
  equals the rows.
- Positive: no screen and no answer changed for anybody.
- Negative: `TeamMembership` must be written only through `serving.services` (or followed by
  `rebuild_role_assignments --apply`). A `QuerySet.update()` on it, or on `Person.user`, leaves the
  projection stale until then; by rule 7 a stale row can no longer grant, it can only deny somebody
  who should have been let in.
- Negative: every `Person` save that writes `user` costs one small read before it (the account the
  row held), in exchange for not re-deriving roles on every save.
- Deploy: run `manage.py rebuild_role_assignments --apply` once after migrating. Until it runs,
  whoever already coordinates a team gets 403 on it (deny by default); the administration is not
  affected.

## Reconsider if

- Teams gain roles between "coordina" and "sirve", or a pastor is to oversee teams (a new decision,
  as for groups).
- Teams can be un-archived: the service that does it must re-derive the team's rows.
