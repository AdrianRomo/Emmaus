# 0015 - Second round: safeguards before defaults, and "church policies" as one place

- **Status:** accepted (owner, 2026-11-18, answering issues #75, #76 and #77). Amends 0009, 0010, 0011, 0012 and 0014.
- **Date:** 2026-11-18

## Principle

Product decisions can be built now, on synthetic data. Safeguarding and privacy policies need a
documented approval before production. Issues #75 and #76 stay open as release gates.

## Minors (amends 0010)

1. Verification is administrative, not staff-only registration: a young person may ask to sign up
   alone. A date of birth the person typed is never enough to skip the authorization.

   | Situation | For an event that protects minors |
   |---|---|
   | Adult, age verified by staff | Confirmed |
   | Adult by a self-declared date only | Pending: age to be verified |
   | Unknown date of birth | Pending: age to be verified |
   | Known minor | Pending: guardian's authorization |
   | Minor with a valid authorization | Confirmed |
   | Authorization withdrawn | Not authorized to take part |

2. Staff verify an age by an approved method, with an audit row. No photographs of identity documents.
3. Turning the rule on re-evaluates every registration of the event, unknown dates included. What
   cannot be verified goes to a review queue; nothing stays confirmed by default. This is a software
   safety requirement, not a church preference.
4. The authorization is per event, with separate choices: taking part, transport organized by the
   church, photographs. Emergency or medical information is a separate, restricted matter.
5. What counts: a signed paper form, a verified guardian account, an authenticated electronic
   authorization; in person when the church documented how identity and authority were checked. A
   call or a WhatsApp message alone is not sufficient evidence.
6. Retention to review with the adviser: 24 months after the event, with a separate process for incidents.

## Attendance (amends 0009)

No individual pastoral access in v2. #76 stays open only for the privacy policy and the retention
periods; it does not block the aggregate dashboard. Before `prune_attendance` is scheduled: validate
the periods, check aggregates cannot re-identify, test deletion against backups and audit needs.

## Events (amends 0011)

| Behaviour | Decision |
|---|---|
| A simple event and a camp do not convert into each other | Keep. Later: "Duplicar como plantilla" |
| Walk-ups from the event's first day | Keep |
| Staff pre-registering a visitor before the event | Later |
| A mistaken walk-up cannot be removed | Change now: "Anular registro", audited |
| Walk-ups ignore publication, sign-up mode and closing date | Change: an explicit override by authorized staff, with a reason. Never over the minors' rule. Capacity holds unless explicitly overridden |
| An authorization survives a cancel and a new sign-up | Keep only for the same event and the same scope, not expired, not withdrawn |

## Opinions after an event (amends 0014)

14 days, three questions, one answer per person; results after the window closes and from **5**
answers. "Without a name" is not "anonymous": the duplicate check must not keep a readable link to
the person, and the screen describes the real guarantee. An event that never reaches the minimum
shows no individual answers. Wanting to be contacted is a separate, optional form.

## Avisos (amends 0012)

Urgent stays with the church administration; the permission should be granted explicitly. A
material edit of an urgent notice asks for "Enterado" again (versions); fixing a typo does not. The
count stays hidden for a very small audience, with the same threshold as the rest.

## Panorama (amends 0009)

A minimum reporting size of **5**: below it, "Sin desglose de asistencia por privacidad". The
inactivity flag follows each group's own rhythm (weekly, every two weeks, monthly) and tells "no
meeting was due" from "the attendance was not recorded".

## Church policies

Decisions that vary by church live in one place (`people/policy.py` today, a screen under
"Configuración de la iglesia" later): registration rules, who publishes urgent notices, meeting
rhythms, reporting thresholds, retention. Safeguards, access to sensitive data and privacy minimums
have platform floors a church cannot lower, and changing a sensitive setting leaves an audit row.

## Order

1. Block unsafe confirmations of minors · 2. Version urgent notices · 3. Panorama privacy and
rhythm · 4. Manual registrations (annul, override) · 5. Opinions.
