# 0017 - Product decisions delegated to the building session, and what wave 9 decided

- **Status:** accepted
- **Date:** 2026-11-18
- **Deciders:** owner (the delegation); the building session (each decision below, for the owner to change).

## Context

After the roadmap review (#90, `docs/v2/CAPABILITY_REVIEW_2026-11-18.md`) every open issue waited on a
decision. The owner then said: take the product decisions, work as a product staff engineer, and keep
building until the platform is finished.

## The delegation and its limits

The building session decides product and engineering questions. It takes the conservative option
(less data shown, fewer people who can see it, off by default, reversible), records it here or in a
later record and on the issue, and keeps building. Any decision below can be changed by the owner by
saying so on its issue.

Not delegated, because they are not the owner's alone to give:

- the church leadership's approvals before production use with minors (#75) and of attendance
  retention (#76): work is built behind those gates and the gates stay open;
- legal and accounting choices for giving (#86);
- anything on a host, any deploy, and `main`.

## Decisions of wave 9

Detail, routes and what is not built: `docs/v2/wave9/`.

### The church of the request (#32, #37)

1. An account that is part of more than one church chooses where it works; the session keeps only
   the church's id and every request checks it again. A stale or forged value falls back to the
   default and is dropped. Signing in clears the choice.
2. The switcher exists only for such accounts. One-church accounts and camp screens do not change.
3. The discipleship-era screens (settings, users, paths, invitations) keep following the account's
   first role: in another church they answer 404. This denies; it never shows another church's data.
   Moving them to the church of the request is later work.

### Pastoral approval of content (#99)

4. Administration prepares; a person with the church's pastor role approves for publication. With no
   active pastor nothing new is published, and the screen says why. There is no fallback.
5. One approval record: who, when, which version, and one of three permitted uses (the church's own
   work, with the author's permission, public domain or open licence).
6. A material edit of a devotional or a reading plan takes it out of publication until it is approved
   again; the edit screen says so before saving.
7. On the public home only the welcome text is authored content. Service times, address, map and
   phone are facts: changing them needs no new approval.
8. Content published before approvals existed stays published and is listed for the pastor to
   approve one by one. Nobody is recorded as its approver by guess.
9. Links to media hosted elsewhere need no rights approval, only their publication step.

### Rooms and equipment (#94)

10. Under the community module; never on a public page.
11. Administration, pastors, guides of a group and coordinators of a team request; administration
    approves, rejects and cancels. Other people of the church see a space as "Ocupado", never what
    for: a title can reveal a pastoral matter. Pastors read "Ocupado" too.
12. Two live bookings of one space never overlap, setup and teardown included, unless administration
    approves over the clashes it was shown. No repeating bookings yet.

### Series of events and the calendar (#93, first part)

13. A series is a rule that creates ordinary events eight weeks ahead. A date changed by hand, or
    with sign-ups, is never moved by a change of the series: it is listed for the administrator.
14. A new series is unpublished, not public and takes no sign-up.
15. The calendar feed of a church carries only what its public pages already show, and not the
    description. No people, no counts.
16. Not built yet: sign-up without an account, ticket kinds, questions, payments, family booking.

### Duplicate records of people (#91, first part)

17. Emmaus suggests pairs only by the same e-mail, the same phone, or the same name and birth date.
    Nothing is joined automatically, never across churches.
18. Only administration joins two records, after a preview. A join is refused when both have an
    account, when a minor or an authorization is involved unless both carry the same birth date,
    when both took part in the same camp, and on the conflicts listed in `docs/v2/wave9/personas.md`.
    Camp answers are never joined.
19. A join can be undone for 30 days. The absorbed record is hidden, not deleted; its id answers 404.
20. Not built yet: import and export of people, custom fields, tags, key dates, membership stages,
    the directory between members, households.

## Earlier defaults, confirmed

The defaults listed in #80 stand as the roadmap review recorded them. What remains there is church
policy (who publishes urgent notices, retention wording), which each church sets in "Políticas de la
iglesia" above the platform's floors, and the release gates above.

## Consequences

Issues are closed when their slice merges, with the decisions taken written on them. Epics stay open
with what is left. A decision the owner reverses is changed in a new record, not by editing this one.
