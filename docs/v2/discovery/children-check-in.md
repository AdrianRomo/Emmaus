# Children's check-in and authorized pickup — discovery draft (issue #85)

**Status:** draft for a workshop with ministry staff. Nothing here is decided. Approval belongs to church
leadership and the safeguarding team (decision 0014); the privacy points need a Mexican privacy adviser.
**Date:** 2026-11-17. **Depends on:** #59 phase B (verified guardian), #75 (safeguarding review).

**Evidence labels.** **V** = read on the vendor's public page on 2026-11-17. **S** = search-result summary,
page not opened (ChurchSuite refuses automated fetches, HTTP 403). **I** = my inference or proposal.
Sources are numbered at the end. No product was signed into; nothing was copied.

## 1. What the established products do

| Pattern | Product | Evidence |
|---|---|---|
| A 4-character code that changes at every check-in, on the child's label and on a label for the adult; the adult's label carries no identifying information; one label per household by default; it cannot be reprinted | Planning Center | V [1], S [1] |
| The profile lists household adults, "Trusted People" (may pick up, live elsewhere) and "Not Authorized" people, shown on a staffed station | Planning Center | V [1] |
| Without the label: "you need a protocol"; the page suggests checking ID and looking up who checked the child in | Planning Center | V [1] |
| Paper fallback: handwritten matching numbers, a printed class list "in case computers are down", a different marker colour each week so old labels are not reused. No offline mode is described | Planning Center | V [2] |
| The code is also shown in the member app for every active check-in (since April 2026) | Planning Center | V [3] |
| Child badge and guardian receipt carry the same code ("lock and key"); without the receipt the child stays in the room while staff check the authorized/unauthorized guardian list with photos, and may ask for an ID | KidCheck | V [4][5] |
| The phone shows a code that matches the child's label; it disappears at check-out or two hours after the event | Subsplash | V [6] |
| A parent's QR filters the list to their children; a per-session code, auto-generated or typed by hand for pre-numbered wristbands | ChurchSuite | S [7] |

All four use the same core: **a random code issued per service, matched at release, plus a list of who
may collect, plus a human fallback.** None publicly describes offline release or custody disputes.

## 2. Mechanisms compared (I, except where cited)

| | Matching label, random code per service | QR / code on the guardian's phone | Name + photo ID against a list | Guardian's PIN |
|---|---|---|---|---|
| Security | Good: short-lived, reveals nothing if lost. A bearer token: whoever holds it passes | Same token, behind an account; can be screenshotted and forwarded | Ties release to a person, not a token; depends on the volunteer's care | Weak: long-lived, shared in families, overheard at the door |
| Friction | Low; a paper slip to keep for 90 minutes | Lowest for those with the app; needs battery and signal | Slow in a queue | Low |
| Cost | A label printer per door, or pre-numbered wristbands/stickers and a pen (zero hardware) [2][7] | None | None | None |
| Accessibility | Works without a phone or reading; large digits help | Excludes people without a smartphone or data | Excludes adults without an ID at hand (grandparents) | Memory burden |
| Offline | Works: two pieces of paper match | Fails without signal unless the code was already on screen | Works with a printed list | Fails (needs the server) |

**Proposal (I):** the code is the normal path and paper-first (stickers or wristbands; a printer is optional,
prices not researched). The phone shows the same code as a convenience, never as the only copy. Name + ID
against the list is the fallback and the rule whenever the collector is not the person who dropped off.
No PIN.

## 3. Walk-through script

Act it out at a real classroom door. Synthetic households (invented, `@demo.invalid`): **Familia Robles
Marín** — Ana Marín and Jorge Robles, verified guardians of Lucía (6) and Mateo (3); tía Carmen Marín.
**Familia Ibarra** — Sofía Ibarra, verified guardian of Emilio (5); Raúl N., the father, not in Emmaus.
Roles: *recepción* (check-in desk), *maestra* (room volunteer), *coordinación* (ministry lead on duty).

| # | Situation | Who does what | The system shows | Recorded | When it fails |
|---|---|---|---|---|---|
| 1 | **Arrival.** Ana brings Lucía and Mateo | Recepción finds the family, ticks each child and room, hands Ana one slip; each child gets a label with name, room, the code and the alert Ana chose to print | «Lucía Robles · Salón 2 · Código 7K4P» and, only on the staff screen, who may collect | Child, service, room, time, who dropped off, who registered, a hash of the code | Child not in Emmaus: a first-visit entry with the adult's name and phone; release only to that same adult. Printer down: handwritten code on both stickers |
| 2 | **Authorized pickup.** Jorge collects with the slip | Maestra types or compares the code; the screen names the children it covers; she confirms Jorge is on the list | «Entregar a: Ana Marín, Jorge Robles» · button «Entregar a Jorge» | Time, which adult, which volunteer, method `code` | Code matches but the adult is not on the list: do not release; call coordinación (see 5) |
| 3 | **One-off pickup.** Ana cannot return; tía Carmen will | Before the service ends, Ana adds Carmen for today from her verified account, or tells coordinación in person. Carmen shows an ID; the code alone is not enough for a one-off | «Solo hoy: Carmen Marín (tía). Pídele una identificación» | Who authorized, when, scope "today", who checked the ID (no ID number, no photo) | A call or WhatsApp to the volunteer is not an authorization (decision 0015, point 5): coordinación calls Ana back on the phone number already on file |
| 4 | **Change of authority; custody dispute.** Sofía says Raúl must not collect Emilio | Coordinación (never the room volunteer) records «Solo las personas de su lista». No document is uploaded, no reason is written | Room screen: «Entrega solo a las personas de la lista. Cualquier otra: llama a coordinación» | Who set it, when; an audit row for each change | Raúl arrives, even with a slip: the child stays inside, coordinación comes to the door, the volunteer does not argue. What the church may lawfully do if a parent with legal rights insists is **for a lawyer**, not this document |
| 5 | **Lost slip / dead phone.** Ana has no code | Maestra keeps the child in the room; looks Ana up by name; Ana is on the list and dropped off today; a second team member confirms | «Sin código: verifica identidad con otra persona del equipo» | Method `list+id`, both volunteers | Not on the list, or nobody recognizes the adult: coordinación; never a new code at the door |
| 6 | **Offline handoff.** The classroom has no signal | When check-in closes, recepción prints «Hoja del salón»: child, code, who dropped off. Maestra crosses out and writes the time and the adult. Coordinación enters it afterwards | Paper only; later «Capturado después, de la hoja» | Late entry: time written, who captured it | The sheet is stale by design: offline, release **only to the adult who dropped off, with the matching slip**. Anyone else waits for coordinación, who carries changes to the room in person |

Questions to ask the team at each step: who is at the door when the queue is longest; what the volunteer
says to a refused adult (agree the sentence in advance: «Dame un momento, voy por la coordinadora»); who
is "coordinación" when the lead is teaching; what happens to a child nobody collects.

## 4. Minimal data and retention (proposal, I)

**Relation to what exists.** `agenda.GuardianConsent` is one guardian's yes for one event registration: the
guardian is a typed name, not a person, and its scope is participation, transport and photographs. It is
not a pickup list and must not become one. Check-in needs, from #59 phase B, a `Guardianship` (adult ↔
minor, created by staff after verification, audited). A pickup authorization is a further record granted
only by a verified guardian or by coordinación; a household link grants nothing (decision 0010, point 5).

| Store | Do not store |
|---|---|
| Per check-in: child, service, room, times in/out, who dropped off, who collected, the volunteers, the method (`code`, `list+id`, `coordination`, `paper`) and a fixed-choice reason for an exception | Custody papers, court orders, photographs of identity documents, ID numbers, the reason for a restriction |
| Per child: adults who may collect (standing or one day, with an expiry), who granted it; a restriction flag without explanation | A named "not authorized" person (an accusation about a third party with no account); see the open decision |
| One short alert the guardian chose («Alergia: nuez»), fixed choices plus at most one short line, with a separate tick to print it on the label | Diagnoses, medication, behaviour notes, free-text comments by volunteers |
| The code: only a hash, valid for that service | The code in clear after the service; any long-lived PIN |

**Who sees it.** The room's team: today's children in their room, the list and the alert, only during the
service. Coordinación: every room today and the exceptions. Church administration: the audit. A guardian:
their own children's check-ins. Nobody: a child's attendance history as a ranking or a report by name.

| Item | Product default (to validate) | Platform floor (a church cannot lower it) |
|---|---|---|
| Code validity | Until release, or 2 hours after the service ends (as [6]) | Never reusable in another service; never shown after release |
| One-off authorization | Ends with that service | Cannot be open-ended |
| Check-in and release log | 12 months, then counts only | Not editable; kept at least 90 days so an incident can be reviewed; at most 24 months (decision 0015's figure, to review with the adviser) |
| Alert | Until the guardian removes it; asked again each year | Never in exports, e-mails or reports |
| Incident | Separate process, outside this module (0015, point 6) | — |

## 5. Denial tests (each one a future automated test)

1. A wrong code releases nobody and names no child. 2. Repeated wrong codes from one station are throttled
and listed for coordinación. 3. A code from last Sunday, or from this morning's earlier service, is refused.
4. A code stops working once every child it covers has been released (covers a photographed slip after
the family left; a sibling still in another room can still be collected). 5. A photographed code presented by an adult who did not drop off and is not on the list: refused,
escalated. 6. A valid code plus a child with «solo las personas de su lista» and an adult not on it:
refused. 7. A one-off authorization from yesterday: refused. 8. A revoked authorization: refused from the
moment of revocation, including on a screen opened before it (the release re-checks on the server).
9. An adult of the same household without a guardianship or authorization: refused. 10. A guardian whose
child turned 18, or whose guardianship was ended: no access. 11. A volunteer assigned to another room
cannot see or release this room's children; one from another church gets 404, including by guessed id.
12. A volunteer whose turn ended cannot open the room list. 13. A room volunteer cannot add an authorized
person or lift a restriction. 14. A release captured from paper is marked as late and cannot overwrite a
release already recorded online. 15. A sheet printed before a revocation: the capture flags the
conflict for coordinación instead of accepting it silently. 16. A child cannot be checked into two rooms,
or released twice. 17. A message or call noted by a volunteer never creates an authorization. 18. The
alert and the pickup list never appear in an export, a notification or the guardian's slip.

## 6. Open decisions

| Decision | Options | Who decides | Recommended default and why |
|---|---|---|---|
| Main mechanism | Code on paper / phone only / ID only | Safeguarding team | Paper code, phone as a copy: works for everyone and without signal |
| Printer | Label printer / pre-numbered wristbands or stickers | Church leadership (budget) | Start with stickers: no hardware to prove the flow first |
| Who may add an authorized adult | Verified guardian online / only coordinación in person | Safeguarding team | Both, and one-offs always need an ID at the door |
| Custody disputes | Positive list only / named "not authorized" people | Church leadership, with a lawyer and the privacy adviser | Positive list plus restriction flag: no accusation stored about a third party |
| Release without a code | Two team members / coordinación only | Safeguarding team | Two team members when the adult dropped off; otherwise coordinación |
| Offline rule | Only the adult who dropped off / the full printed list | Safeguarding team | Only the adult who dropped off: the paper cannot know about a revocation |
| The alert | None / fixed choices / one short line | Safeguarding team, privacy adviser | Fixed choices plus one line; health data is sensitive data in Mexico (to confirm) |
| Retention periods and the notice to parents | Table above | Privacy adviser, church leadership | As proposed, until the adviser says otherwise |
| First visitors without a verified guardian | Refuse / accept with release to the same adult only | Church leadership | Accept, same adult only: turning a family away is worse |
| Age range and who is "coordinación" | Per church | Church leadership | A church policy (`people/policy.py`), not code |
| Build order | Before or after #59 phase B | Product owner | After: without a verified guardian the list has no trustworthy source |

## Sources (all read 2026-11-17)

1. Planning Center, "Security": https://help.planningcenter.com/en/138190-security.html
2. Planning Center, "Backup plan: when technology breaks": https://help.planningcenter.com/en/138287-backup-plan--when-technology-breaks.html
3. Planning Center changelog, 2026-04-22: https://www.planningcenter.com/changelog/check-ins/improved-all-security-codes-in-church-center
4. KidCheck, "Check-out procedures": https://kidcheck.com/tutorial/check-out-procedures/
5. KidCheck FAQ, guardian without a receipt: https://kidcheck.com/faq/guardian-doesnt-receipt-check-child
6. Subsplash, "Pre-Check Overview": https://support.subsplash.com/s/article/Pre-Check-Overview
7. ChurchSuite (search summary only; 403 to automated fetch): https://support.churchsuite.com/article/695-check-in-using-scanned-qr-codes and https://support.churchsuite.com/article/392-child-check-in

**Not verified:** printer and label prices; any vendor's behaviour offline beyond [2]; whether Planning
Center blocks or only warns on a "Not Authorized" match (the page does not say); Mexican law on releasing
a child to a parent without a court order, and on health data of minors — for the lawyer and the adviser.
