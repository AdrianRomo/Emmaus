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

## Second pass — rendered public pages with REA (2026-11-17)

**Method.** REA's `inspect_web_page` against a throwaway headless browser profile: the accessibility
tree of nine public pages of Subsplash and Planning Center (added as a third reference, because its
member surface is publicly documented). No sign-in, no form submitted, no screenshot or asset kept.
ChurchSuite's hosts were not touched: they refuse automated access and that was not worked around.
Two Subsplash URLs were 404 (`/product/church-app`, `/product/giving`); one Planning Center help
article rendered empty. No Latin American product was observed: two searches found only translated
US products, so anything said here about LATAM is judgement, not evidence.

**Evidence levels.** **REA** = read in the rendered page's accessibility tree. **search** = a
search-result summary, page not opened.

| Area | Pattern | Product | Evidence |
|---|---|---|---|
| Sign-up | Two modes per event: head-count only, or one form per person; "Add person" for more | Subsplash | search |
| Sign-up | Categories of attendee with their own form; caps; households; a waiting list for a full event | Planning Center | REA |
| Sign-up | Staff register walk-ups and record cash or cheque | Subsplash | REA |
| "Mine" | One list of the person's own things: sign-ups, group requests, serving requests | Planning Center | REA |
| Check-in | Three modes: a volunteer's station (name search, new person on the spot), a parent's self check-in, a classroom roster | Planning Center | REA |
| Check-in | Pre-check from the phone from an hour before; a code on the phone matches the child's label and expires at check-out or two hours after | Subsplash | REA |
| Check-in | The guardian's copy leaves out the child's room and contact details | Planning Center | REA |
| Media | Tagged by series, speaker, topic and passage; "continue" for the person; notes with blanks to fill | both | REA |
| Devotional | No reading plan or "today" card found in either | both | absence |
| Notices | Audience, read state, schedule and expiry: not described publicly by either | both | absence |
| Giving | One-time or recurring, funds, statements in the profile | Planning Center | REA |
| Navigation | Leaders reach the admin product through a door from the member app; they do not see admin screens inside it | Planning Center | REA |
| Accessibility | Both marketing sites have gaps: unnamed images (Planning Center), no `main` landmark and a heading order that starts below `h1` (Subsplash) | both | REA |

### Decisions taken from it

| Decision | Why | State |
|---|---|---|
| A sign-up is one tap for the signed-in person, with the facts above the button | Common to all three; the church here has no ticket types yet | Built (`agenda`) |
| No waiting list, no household sign-up, no walk-ups, no payments in the first version | Each needs a product answer; filed as issue #69 | Not built |
| "Mi iglesia" is the one list of the person's own things: groups, requests, sign-ups, notices | Planning Center's "Me"; Emmaus already worked this way | Built |
| Notices carry audience, a date to appear, a date to expire and a read state | Neither reference documents these; the camp's team asked for them (#57) | Built (`notices`) |
| A devotional of the day, with a private "ya lo leí" and no streaks or totals | Not found in the references; invariant 7 forbids scoring anything spiritual | In progress (`resources`) |
| Media as links by series and speaker, opened on the other site | Hosting video is out of scope (blueprint P4) and the site's CSP forbids embeds | In progress (`resources`) |
| The administration's tools are a section of the same shell, not another product | A church administrator here is also a member; one frame is simpler than a door | Built (`core/shell.py`) |
| Child check-in waits for households and the guardians decision; when built, the pick-up code pattern is a safety requirement, not an option | Subsplash and Planning Center agree on it | Not built (issue #67) |
| Pages keep landmarks, one `h1` and named images; checked by axe in the gate | The references' own gaps | Built |
