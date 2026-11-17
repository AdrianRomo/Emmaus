# Feature fit matrix

State of Emmaus on branch `v2/slice-1-camp-to-group` (2026-11-17) against the blueprint's pillars.
**Status**: Present / Partial / Missing / Not planned. **Effort** as in the roadmap (S ≤ one session,
M two to three, L a week, XL more). Roadmap IDs refer to `docs/audits/EMMAUS_EVOLUTION_ROADMAP.md` §7.

**External reference.** The Subsplash column says what its public product pages described when read
on 2026-11-17 through a summarising fetch (paraphrased, not checked against the raw pages).
ChurchSuite's site refused automated reading (HTTP 403), so that column is **unverified** everywhere:
it needs a manual read. Nothing here is copied from either product.

| Capability | Emmaus | Pri | Effort / risk | Depends on | Subsplash (public pages) | ChurchSuite |
|---|---|---|---|---|---|---|
| Church = tenant, sede = campus | **Present** (`core.Church`, `events.Sede`; isolation tested by guessed id) | P0 | — | — | multi-campus mentioned, no detail | unverified |
| Person independent of account and event | **Present** (`people.Person`, backfill, sede affiliation) | P0 | — | — | unified profiles, custom fields, import, merge | unverified |
| Scoped roles (church / event / group) | **Present** (`core/authz.has_scope`, `access.RoleAssignment` for groups) | P0 | — | — | not described | unverified |
| Camp record ↔ person, history kept | **Partial**: linked one by one after the event; lost at the 365-day purge (Q-C1) | P0 | S / decision | owner | n/a | n/a |
| Per-church module flags + kill switch | **Present** | P0 | — | — | n/a | n/a |
| Church-level audit log | **Partial**: written; no screen to read it yet | P1 | S / low | — | not described | unverified |
| Member home ("Mi iglesia") | **Present**: groups, requests, upcoming events, camp history | P1 | — | — | member app home | unverified |
| Small groups: directory, join request, guide approval | **Present** | P1 | — | — | group finder, join requests, visibility, limits | unverified |
| Group meetings and attendance | **Missing** (only the weekly day and the next date) | P1 | M / medium | groups | attendance tracking, leader mobile tools | unverified |
| People directory for staff | **Partial**: list, search, record, account; no visibility by consent, no merge | P1 | M / medium | — | profiles, households | unverified |
| Households, guardians | **Missing** | P2 | L / high (minors) | Q-C2, #59 | households linking parents and children | unverified |
| Events beyond the camp; public registration | **Missing** (`Event.kind` exists; every event is a camp inside) | P1 | L / high | P3-8, #61 | registration, limits, paid tickets | unverified |
| Event programme, notices, map, workshops | **Missing** | P1 | M each | #55–#58 | add-to-calendar only | unverified |
| Check-in | **Missing** | P2 | L | events | QR kiosks, tags, security PINs | unverified |
| Camp hierarchy, live questionnaires, presentation mode, privacy-suppressed reports | **Present** (v1, unchanged) | P0 | — | — | forms only; no equivalent described | unverified |
| Follow-up tasks / workflows | **Partial**: two separate lists (camp `FollowUp`, discipleship `FollowUpRequest`); no board | P2 | L / high | P3-4 | Kanban boards, owners, due dates, automations | unverified |
| Notifications and consent-led e-mail | **Partial**: content-free notices (`core/notify.py`); no preferences, no notification centre | P1 | M | SMTP on the host | push, chat, e-mail, SMS | unverified |
| Announcements | **Missing** | P1 | M | #57 | push announcements | unverified |
| Discipleship paths, lessons, questions to a mentor | **Present** (v1 module, per-church flag) | P2 | — | — | not described | unverified |
| Sermons / media library | **Missing**; plan is links to an external host | P2 | M / low | P3-6 | upload once, publish everywhere; live streaming | unverified |
| Prayer requests | **Missing** | P2 | M / high (privacy) | — | not found | unverified |
| Forms builder | **Partial**: the camp questionnaire engine (versions, conditions, sensitivity levels) | P2 | L | — | custom forms triggering workflows | unverified |
| Volunteers, service planning | **Missing** | P2 | L | P3-5 | plans, scheduling, accept/decline | unverified |
| Photos, highlights, post-event feedback, FAQ | **Missing** | P2 | M each / consent | #54, #55, #60 | not found | unverified |
| Giving / payments | **Missing**, not started on purpose | P3 | XL / legal review | provider research | online, in-app and text giving | unverified |
| WhatsApp channel | **Missing** | P3 | L / consent, templates | notifications | not found | unverified |
| Tenant provisioning, branding, import/export | **Partial**: per-church theme and logo; CSV export of camperos; one church per install in practice | P3 | L / high | P4-1, P4-2 | CSV import | unverified |
| Native apps, TV, site builder, own streaming | **Not planned** (responsive web first) | P4 | XL | usage | branded apps, TV, site builder | unverified |
| Spanish-first product | **Present**: every screen in Mexican Spanish, tú voice | P0 | — | — | not found on the pages read | unverified |
| Safeguarding of minors' data | **Partial**: confidential levels, audited access, guardian authorisation at the camp; guardian confirmation before a minor's account | P0 | — | Q-C2 | not a named feature | unverified |

## What sets Emmaus apart

- A camp role, a church role and a group role are separate facts with separate ends; nothing
  carries over when a context ends.
- Sensitive answers have field-level visibility with an audited, explicit grant; reports shown to a
  room suppress small groups.
- A notice never carries a name or an answer, and a screen never says an e-mail went out when none did.
- Linking a youth's camp record to their person is a decision a human makes, never a name match.
