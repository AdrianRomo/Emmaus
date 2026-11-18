# UX audit for the redesign — what was found, and what changed

**Date:** 2026-11-17. **Scope:** the product as it stood on `v2/slice-1-camp-to-group` (98f9bf3),
read in the code and in a browser on the synthetic seeds. The earlier audits stay valid for the
camp: `docs/auditoria-ux-2026-10.md` (findings UX-n, UI-n, LANG-n, A11Y-n) and `docs/ux-review.md`.
This file covers what those could not: the product as a church platform.

## 1. Findings

| # | Finding | Evidence | State |
|---|---|---|---|
| R1 | **The navigation was a camp's bar stretched over a church.** One horizontal bar held the community links, then the discipleship links, then "Ayuda"; a church administrator had up to ten links in a row with no grouping, and "Campamentos" was a button apart | `core/context_processors.py`, `partials/ui/bar.html` | Fixed: the shell groups by what the person does (`core/shell.py`) |
| R2 | **A guide had no place of their own.** The only way to a group's requests was the home's panel while a request waited, or Grupos → the group → "Personas y solicitudes" | `groups/home.html` at 98f9bf3 | Fixed: "A tu cargo" in the rail and as a tab, with the count that waits |
| R3 | **On a phone everything was behind "Menú".** Two taps to go anywhere | `rotulo.css`, 760 px | Fixed: bottom tabs for the two or three places a person uses; "Más" for the rest |
| R4 | **The home listed things by type, not by time.** "Tus grupos" said the next meeting in a sentence and "Lo que viene en la iglesia" listed events apart; a person could not see their week | `groups/home.html` at 98f9bf3 | Fixed: "Lo que viene", one agenda by day |
| R5 | **A person could not correct their own phone or e-mail.** Only the administration could, in Personas; "Mi cuenta" edits the account's name | `people/views.py`, `core/account.html` | Fixed: "Mis datos"; the home asks when something is missing |
| R6 | **A guide read a phone number they could not tap.** Contact data was a sentence | `groups/group_people.html` at 98f9bf3 | Fixed: `tel:` and `mailto:` links, 44 px |
| R7 | **Every list looked the same.** People, groups, events and requests were all a bold line and a grey line; nothing told a person from a group from a date | all community templates | Fixed: the arch for people and groups, the date block for days |
| R8 | **The camp history read as a list of rows**, not as something the person had lived | `groups/home.html` "Donde has estado" | Fixed: a timeline that also holds the groups they joined |
| R9 | **The product looked like a form.** Atrio is correct and cold: pure white, one grey, 800-weight sans titles on every screen. Right for an interview station; not what a member should meet | browser review | Changed: Santuario's tokens and voice, for churches on the new modules |
| R10 | **Nothing recorded that a group met.** The home computed "next meeting" from the weekday; a guide could not say who came | `groups/models.py` | Fixed: meetings and attendance |
| R11 | **Words for the event list.** The bar says "Campamentos"; for a church with services and retreats that is wrong | `design-system.md` §5 | Fixed: "Eventos" everywhere (bar, page, help, welcomes), by the owner's decision of 2026-11-17 |
| R12 | **The help page described the old bar** ("en la barra de arriba; en un celular está en Menú") | `core/ayuda.html` | Fixed for the community tasks and the event list |
| R13 | **Event screens and church screens are two shells.** Entering an event from the new shell shows Atrio's bar again (with the new tokens) | `events/rotulo/shell_*.html` | Fixed for church-role accounts: the event is a place in the rail. Event-only accounts keep the bar |
| R14 | **No member-facing events.** A member reads "Para inscribirte a un evento, pregunta en tu iglesia" | home | Fixed in a first version: the church publishes an event, a person signs up, the administration checks who came (`agenda`). Creating an event that is not a camp still goes through the camp's form (issue #68) |
| R15 | **No announcements, devotionals, media, photos, notifications, serving.** The directive's home lists them | — | Announcements: fixed (`notices`). Devotionals and media: see `PROGRESS.md`. Serving: fixed (`serving`). Photos and notifications outside Emmaus: **open** (issue #71) |
| R16 | **Tables on a phone scroll sideways** in the camp's administration | `ux-review.md` | Unchanged (Atrio's decision) |

## 2. The fifteen journeys of the directive

"Shipped" means it works end to end, is tested, and was exercised in a browser at three widths.

| # | Journey | State | Entry → completion | Evidence |
|---|---|---|---|---|
| 1 | First-time member onboarding | **Partial** | The administration gives access (one-time link) → the person sets a password → lands on Mi iglesia, which says what is theirs and asks for missing contact data. No guided first-run for a member (the role welcomes exist for staff) | `people/tests/test_people_views.py`, `core/test_shell.py::MyDataTests` |
| 2 | Sign-in and personalised home | **Shipped** | `/cuenta/entrar/` → Mi iglesia | `e2e/community-flow.mjs` part 1 |
| 3 | Finding and joining a group | **Shipped** | Mi iglesia panel or the Grupos tab → the group → "Pedir unirme" → "Ya pediste unirte" | `groups/tests/test_journey.py`, e2e part 1 |
| 4 | A guide reviews a request | **Shipped** | Panel, rail badge or tab → "Aceptar"/"No aceptar" → message; the member sees it on their home | e2e part 2 |
| 5 | Registering for an event | **Shipped** (one person, free events) | Mi iglesia or Eventos → the event → "Inscribirme" → "Te inscribiste"; cancel until it starts | `agenda/tests/test_agenda.py`, e2e part 1 |
| 6 | Viewing and confirming event attendance | **Partial** | The administration marks who came from "Inscritos". A person does not confirm or see their own attendance; no check-in station | `agenda/tests/test_agenda.py` |
| 7 | Guardian managing a child's information | **Not built** | Only the guardian confirmation before a minor's account exists | decision 0008, Q-C2 |
| 8 | Volunteer reviewing assignments | **Shipped** (first version) | Mi iglesia or "Servir" → "Confirmar" or "No puedo"; days they cannot; who coordinates sees the answers | `serving/tests`, e2e parts 1 and 2 |
| 9 | Coordinator reviewing questionnaire results | **v1, unchanged** | The camp's screens, now in the new tokens | `events/tests/` |
| 10 | Tutor following up with assigned people | **v1, unchanged** | — | `events/tests/` |
| 11 | Pastor reviewing church engagement | **Partial** | The camp's summary and the discipleship panorama exist; nothing yet reads groups or attendance for a pastor (Q-C3 is open) | — |
| 12 | Administrator managing people, groups, events | **Shipped for people and groups** | "Administración" in the shell | e2e part 4 |
| 13 | Correcting missing profile information | **Shipped** | The home's quiet panel → "Mis datos" → saved → the panel is gone | `core/test_shell.py::MyDataTests`, e2e part 3 |
| 14 | Discovering a devotional or sermon | **Not built** | — | R15 |
| 15 | A returning camper continues into church life | **Shipped** | Linked from Personas → access → Mi iglesia shows the camp in "Donde has estado" and offers a group | `groups/tests/test_journey.py` |

Also built, not in the directive's list: **accompanying somebody** (the administration asks one person to look for another by a date; that person records "Le llamé" and marks it attended; no text about the conversation is ever stored; `care/tests`, e2e part 2).

Also built, not in the directive's list: **reading a notice** (home or Avisos → the notice → no longer "Nuevo"; `notices/tests/`, e2e part 1).

New in the first round, not in the directive's list: **a guide records who came to a meeting**
(panel "¿Quién vino a …?" or "Reuniones" → "Pasar lista" → "Vinieron 5 de 8"); `groups/tests/test_meetings.py`, e2e part 2.

## 3. Permissions checked for what is new

- The shell lists a link only when the view would answer; every view still checks by itself.
- "Mis datos" changes only the signed-in person's own row and only three contact fields; a posted
  name or date of birth is ignored.
- Attendance: guides of that group and the church's administration read and write; a member of the
  group gets 403, a former camp coordinator 403, another church 404, the module off 404. A member's
  own pages never show who came.
- The audit row of a list carries a count and the group's id, never a name.
- Notices: the whole church's are written by the administration, a group's by who guides it; a group's notice is read by its members, its guides and the administration only. The audit row has no title and no body.
- Serving: who serves sees only their own dates and answers; who coordinates a team sees its people and their answers; a team gives no role anywhere else. Decided through `has_scope` on the `ministry` scope since wave 7 (decision 0016).
- Accompanying: only the owner of a follow-up and the administration see it; the person it is about never does. The module has no free-text field, and its audit rows name nobody.
- Events: a person sees only what the church published; a sign-up creates no camp participant, no membership and no role; "Inscritos" and the event's page editor are the administration's.
