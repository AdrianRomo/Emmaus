# 0010 - Minors signing up for events, and a guardian's authorization

- **Status:** accepted as architecture (owner, 2026-11-17, answering issue #67). **Release gate:** church leadership and a Mexican privacy adviser approve the exact authorization policy before it is used in production with minors.
- **Date:** 2026-11-17
- **Deciders:** owner; church leadership and safeguarding/privacy review for the policy.

## Decision

A hybrid model. A minor may start a registration; for an event that requires a guardian's
authorization it stays **pending** until the authorization is verified and recorded.

1. The event's page says whether minors need authorization (`agenda.EventPage.requires_guardian_consent`).
2. A person under 18 on the event's first day who signs up gets a registration "Pendiente de
   autorización". It holds a place, is not confirmed, and cannot be marked as attended. Nothing turns
   it into a confirmed registration except a recorded authorization.
3. The authorization is recorded in Emmaus even when it was given on paper or in person
   (`agenda.GuardianConsent`): who authorized and their relationship, how it was verified, who of the
   church recorded it and when, and its scope (that event). A parent needs no account.
4. Guardian accounts come in phases. **A, now:** public event information, FAQ, programme, contact.
   **B, later:** a verified guardian relationship, the child's logistics, authorization and family
   registration. **Never:** private interviews, spiritual questionnaire answers, safeguarding
   reports or confidential pastoral notes.
5. A household relationship never grants access to a minor's records by itself. Guardian authority
   is verified separately and ends, or needs new authorization, at adulthood.

## Consequences

- Positive: the church has an auditable record without forcing parents into the product.
- Negative: staff have one more step for minors; an unknown birth date has to be asked at sign-up.

## Not decided here

- The wording of the authorization and what counts as verification (church leadership, privacy adviser).
- Guardian accounts (phase B), households, children's check-in and pick-up.
