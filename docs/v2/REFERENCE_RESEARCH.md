# Reference research — Subsplash and ChurchSuite

**Date:** 2026-11-17. **Method:** public pages only, about 25 fetches, time-boxed. No sign-in, no
access restriction bypassed, no source code read, no screenshots or assets kept. REA's browser tools
were available and not used: plain fetching was enough for Subsplash, and for ChurchSuite the block
is a deliberate refusal of automated access, which was not worked around.

**Evidence levels.** **V** = read on the vendor's page. **I** = inferred from marketing copy.
**S** = search-result summary of a ChurchSuite support article (medium confidence).
**V-3p** = read on a church's own public guide to the product.

**Not accessible:** `churchsuite.com` and `support.churchsuite.com` answer HTTP 403 to automated
fetches (the earlier session saw the same). Everything about ChurchSuite below is S or V-3p.

## What was observed

| Area | Pattern | Product | Evidence |
|---|---|---|---|
| Member home | A welcome, then a strip of upcoming featured events, then messages the church sent you | ChurchSuite | S, V-3p |
| Member home | The menu is nouns with "My": details, events, groups, rotas, giving. A section appears only when it applies to the person | ChurchSuite | V-3p |
| Member home | Media first; personal things behind the avatar; groups behind messaging | Subsplash | V |
| Member home | Neither shows a ranked "next action" or a profile-completion prompt | both | absence, not proof |
| Groups | Visibility (public/unlisted) and enrollment (open / request / closed) are separate settings | Subsplash | V |
| Groups | The finder hides full and closed groups by default; filters by location, frequency, day, time | Subsplash | V |
| Groups | The exact address is for members only | Subsplash | V |
| Groups | One-click join for a signed-in member; optionally lands as "pending" for a leader to activate | ChurchSuite | S |
| Groups | Leader abilities are granted by role: attendance, edit details, manage members; a weekly nudge to submit attendance; pending members are not on the register | ChurchSuite | S |
| Events | Sign-up with pre-filled details, a summary, confirm; "my sign-ups" as its own tab; check-in as a tick-off register | ChurchSuite | S |
| Events | Ticket types with caps, custom questions, registrants added to the people database | Subsplash | V |
| Staff | A person's profile gathers contact, household, groups, registrations, form fills and workflow cards | Subsplash | V |
| Staff | Follow-up as staged boards: owner, due date, checklist; "my cards" by due date | Subsplash | V |
| Serving | Next dates, unavailability and accept/decline/swap in one place; "unavailable that day" is not "decline this one" | both | S, V |
| Onboarding | Invitation by e-mail with a set-password link; empty sections are hidden, not explained | ChurchSuite | V-3p |

## What Emmaus took, and how it differs

| Pattern | In Emmaus | Status |
|---|---|---|
| Hide what does not apply | The shell lists a section only when the person can open it; Mi iglesia draws a section only when it has something to say | Shipped |
| What is coming first | "Lo que viene": the person's group meetings and the church's events in one list by day | Shipped |
| A ranked next step | Neither reference has one. Mi iglesia keeps Atrio's rule: one panel, one question, one button | Shipped (original) |
| Members-only address | Already a rule of slice 1 (`meeting_place`) | Shipped |
| Request-and-approve, pending explicit to both sides | Slice 1; the guide now also sees the count in the rail and can call or write from the row | Shipped |
| Leader work where the leader already is | "A tu cargo" in the shell; "Personas / Reuniones" views | Shipped |
| Attendance by the leader, members only | "Pasar lista": who is in the group that day; a request that waits is not on the list | Shipped |
| Prompt to record attendance | The home asks "¿Quién vino a …?" when the last meeting has no list. No e-mail nudge | Shipped (in-app only) |
| Structured day and time driving filters | Day and time are fields; the list filters by kind only. Day/sede filters wait for a church with enough groups to need them | Partial |
| Enrollment vs visibility as two settings | One setting today (`accepts_requests`); unlisted groups and instant join are not built | Not built |
| Capacity, full groups | Not built; decide on a waitlist first (Subsplash has none) | Not built |
| Event sign-up, "my events", check-in | Not built (roadmap P3-8, issue #61) | Not built |
| Follow-up boards, serving schedules, giving | Not built (blueprint P2/P3) | Not built |

Nothing was copied: no layout, wording, icon or asset. The patterns above are common product ideas;
their Emmaus form is described in `DESIGN_SYSTEM.md`.
