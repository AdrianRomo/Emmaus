# 0011 - Event registration: walk-ups, waiting list, households, payments

- **Status:** accepted (owner, 2026-11-17, answering issue #69)
- **Date:** 2026-11-17

## Decision

| Feature | Decision | When |
|---|---|---|
| Walk-up registration | Yes | Now |
| Waiting list | Yes | Next |
| Household registration | Yes | After guardian relationships (0010, phase B) |
| Paid events | Yes, recorded by staff first | Later |

1. A visitor never needs an Emmaus account to attend. Staff register them with the minimum (a name,
   optionally a phone), respecting capacity, the authorization of minors (0010) and age rules.
2. A waiting list offers a freed place, with an expiry and a confirmation by the person. It never
   silently turns a pending registration into a confirmed one.
3. Payments: the data model must be able to hold cash, transfers, refunds and online payments in
   MXN. No financial processing is built until the church approves its operational and accounting
   requirements.

## Consequences

- `agenda.EventRegistration` holds either a person of the church or a guest's name.
- A guest becomes a person of the church only by an explicit action of the administration.
