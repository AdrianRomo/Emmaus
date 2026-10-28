# Emmaus — Product Vision, Domain Blueprint & Roadmap

> **Status:** Product north star / planning reference — v1.0  
> **Updated:** 2026-10-27  
> **Audience:** Product owners, church leadership, designers, Claude, Codex, and engineers  
> **Scope:** Direction and principles, **not** a claim that these capabilities are already implemented.

## 0. How to use this document

This is the long-term reference for **what Emmaus should become**. Use it when auditing, designing, prioritizing, implementing, or reviewing changes. It is not a license to rewrite the application or implement all features immediately.

**Before changing code:** inspect the actual repository, confirm current behavior, identify conflicts between this vision and the existing implementation, and propose incremental, testable changes. The existing camp workflows have near-term operational priority. If the code or documented business rules conflict with this vision, **report the conflict and propose an approach** rather than silently assuming the vision describes the current system.

**Non-negotiable:** protect the upcoming camp, its users, questionnaires, role-specific dashboards, historical data, and current operational processes. No disruptive migrations or role changes without an explicit, reviewed rollout and rollback plan.

## 1. Product mission

**Emmaus is a connected home for church life.**

Emmaus helps someone discover a church, join events and small groups, access sermons and devotionals, participate in discipleship, and find meaningful opportunities to serve. It helps pastors, leaders, volunteers, and administrators coordinate church operations without turning people into permanent roles or forcing everyone into a complicated back-office interface.

### Strategic direction

- **Church first:** begin with one real church and validate day-to-day usefulness with actual people and leaders.
- **SaaS ready:** design the fundamental boundaries to serve multiple independent churches later, each with its own campuses (*sedes*), people, settings, and data. Do not prematurely build a full SaaS commercial stack.
- **Event continuity:** preserve and generalize the existing camp system. The camp becomes one type of experience within a wider church platform, not the organizing principle of all users and permissions.
- **Human-centered:** church/community relationships are the goal; software should enable them, not substitute for them.

### Product positioning

A Spanish-first, mobile-first platform combining ChurchSuite-inspired church administration with unusually simple member experiences, flexible participation across events, groups, and ministries, and thoughtful pathways for connection and discipleship.

**Inspiration, not replication:** ChurchSuite is a reference for capability coverage and proven workflows (People/Address Book, My ChurchSuite, Small Groups, Calendar, Rotas, Flows, multi-site operations). Emmaus should not copy its exact UI, proprietary content, or implementation. Evaluate patterns against actual user needs.

## 2. Product principles and guardrails

1. **People are not roles.** `Campero`, `tutor`, `coordinador`, `pastor`, `servidor`, `líder de grupo` are contextual statuses, titles, or assignments—not permanent identity classes.
2. **One person, multiple simultaneous relationships.** A person may attend one campus, participate in a group, serve in a ministry, and tutor at a camp, each with independent lifecycle and privileges.
3. **Roles and permission scopes are explicit.** An assigned title does not imply broad access to a person's data. Authorization must account for tenant, resource, context, action, and time.
4. **Separate identity from church records.** An authenticated account is distinct from a tenant-scoped `Person` record. Visitors and children can have records without login. Future cross-church login must not share church data by default.
5. **Churches are isolated.** Each church is a tenant with separate data and configuration. Tenant isolation must be enforced server-side and tested.
6. **Camp reliability comes first.** Avoid a rewrite, disruptive migrations, surprise UX changes, or permission regressions before the event.
7. **Approachable by default.** Spanish-first copy, clear information architecture, accessible typography, calm/credible visual design, and mobile usability for people unfamiliar with software.
8. **One product, role-aware experiences.** Public discovery, personal church life, leadership operations, and SaaS operations are distinct experiences sharing one coherent product foundation.
9. **Incremental delivery.** Validate a complete real-world user journey before expanding horizontally into many modules.
10. **No spiritual scoring.** Never quantify someone's faith, maturity, or worth from attendance, devotional progress, or engagement signals. Use operational metrics, not spiritual judgments.
11. **Privacy and safeguarding are foundational.** Minors, guardian relationships, pastoral care, personal data, and sensitive questionnaire responses require intentionally limited access, consent, and retention.
12. **Configuration before custom code.** Church names, sedes, labels, forms, teams, and eligible modules should be configurable where meaningful, not hardcoded for a single church.

## 3. Target users

| Persona | Core jobs to be done | Experience |
| --- | --- | --- |
| Visitor / first-timer | Find service times, a campus, an event, a contact path | Public `Explorar` |
| Attendee / member / campero | Join events, find community, see relevant information | `Mi Iglesia` |
| Small-group participant | Discover groups, request to join, view meetings/resources | `Mi Iglesia` |
| Small-group leader / co-leader | Manage own group, memberships, requests and meetings | Contextual `Liderazgo` |
| Volunteer / ministry leader | Discover serving opportunities, receive assignments, coordinate teams | `Mi Iglesia` + contextual `Liderazgo` |
| Event participant / tutor / coordinator | Register, complete authorized forms, manage scoped camp responsibilities | Event-specific experience |
| Pastor / campus leader | See authorized activity, requests, needs, and follow-up actions | `Liderazgo` |
| Church administrator | Manage people, campuses, groups, events, and church settings | `Liderazgo` |
| Emmaus operator | Support tenant provisioning, configuration, reliability and billing | Separate `Emmaus Console` |

Titles vary by church. The product must allow church-configurable labels and assignments rather than assuming a universal denomination or hierarchy.

## 4. Four experiences, one ecosystem

### 4.1 Explorar — public and low-friction

- Church description, locations/sedes, service schedule, contact options.
- Public events and sign-up where appropriate; recent sermons and series.
- Explore before login wherever safe and practical.
- Contextual invitations to register, request follow-up, or join a group.

### 4.2 Mi Iglesia — member-facing

- Personal home with relevant upcoming events, groups, resources, serving assignments, and announcements.
- Own profile, contact preferences, consents, registrations, memberships, and history as appropriate.
- Group discovery and join requests; optional devotional and discipleship content.
- Simple language and few navigation choices; no irrelevant administrative dashboards.

### 4.3 Liderazgo — church operations

- Role-scoped screens and actionable tasks for pastors, administrators, group/ministry leaders, tutors, and coordinators.
- People and affiliations, events, forms, groups, serving, follow-up, communications, and appropriate dashboards.
- A user can enter authorized operational contexts from their regular account; avoid a forced account-switching model.

### 4.4 Emmaus Console — SaaS operator

- Independent tenant administration, environment health, plans, feature toggles, support tooling, audit controls.
- Platform operators **do not** receive routine visibility into private church records. Support access requires explicit, time-bounded controls and logging.
- Later-phase commercial capability, not an MVP prerequisite beyond fundamental tenant isolation.

## 5. Organizational model and domain boundaries

### 5.1 Conceptual hierarchy and relationships

```text
Emmaus Platform
  └── Tenant / Church (an independent customer & data boundary)
        ├── Campuses / Sedes (0..many)
        ├── People (tenant-scoped records; may or may not have a login)
        ├── Households / Guardians (optional, privacy controlled)
        ├── Groups (small groups / communities; may span campuses)
        ├── Ministries & Teams (may be church-wide or campus-scoped)
        ├── Events (may be church-wide or campus-scoped)
        ├── Programs (discipleship, courses, devotionals)
        ├── Content (sermons, series, resources)
        └── Workflows, Forms, Communications & Reporting
```

A person can have **multiple campus affiliations** (with an optional primary or home sede). A group, ministry, or event can have a sponsoring scope and relationships to several campuses. Do not force every resource into a strict single-campus tree.

### 5.2 Core concepts

| Concept | Purpose / invariants |
| --- | --- |
| `Tenant` | Church/customer boundary; owns configuration and records |
| `Campus` | Local sede, service location, and local leadership configuration |
| `AuthIdentity` | Login identity; does not automatically confer church membership or data access |
| `Person` | Church-owned contact/profile record (including people without accounts) |
| `IdentityLink` | Explicit mapping of authenticated account to authorized tenant-scoped person record(s) |
| `CampusAffiliation` | Person-to-sede relation, status, dates, optional primary designation |
| `RoleAssignment` | Subject + role/capabilities + scope + lifecycle; revocable and auditable |
| `Group` / `GroupMembership` | Ongoing small-group community, participant/leader/co-leader assignments |
| `Ministry` / `TeamMembership` | Ongoing area of service and its volunteer teams |
| `Event` / `EventRegistration` | Time-bounded event and participant registration; camp is a configured event type |
| `EventAssignment` | Coordinator, tutor, check-in staff etc. for specific event/time/context |
| `FormDefinition` / `FormVersion` / `Submission` | Configurable questionnaires with versioned responses and scoped visibility |
| `Program` / `Enrollment` | Discipleship/course/devotional participation and optional progress |
| `ContentItem` / `Series` | Sermons, media references, resources, teaching series |
| `Workflow` / `WorkflowTask` | Assignable step-based processes, due dates, permissions, completion states |
| `ConsentRecord` | Purpose, basis/consent, version, timestamps, withdrawal and communication preferences |
| `AuditEvent` | Security-relevant access/changes, permission grants, sensitive-record operations |

**This is a target conceptual model, not a mandated database schema.** The audit should first map existing models and recommend the smallest viable evolution. Shared auth identities and tenant-specific people require cautious merge/linking logic; never auto-merge across churches using just names, emails, or phone numbers.

### 5.3 Role and authorization model

- Role definitions may be configurable, but permissions must be explicit and centrally enforced.
- At minimum, evaluate `tenant`, `scope_type`, `scope_id`, `subject`, `action`, `start_at`, `end_at`, and status. Additional attributes may be needed for privacy.
- Scope examples: platform, organization, campus, ministry, group, event, form, own resources.
- Treat **data access** separately from **operational authority**: organizing an event does not automatically authorize viewing every sensitive form response.
- Roles can overlap. Ending an event role removes that context's privileges, not group/ministry membership or church affiliation.
- Use deny-by-default, least privilege, server-side checks on every resource operation, and tests against cross-tenant and cross-scope leakage.
- Consider delegation, emergency access, audit trails, and access review only with explicit safeguards.

### 5.4 Households and minors

- Support a child's `Person` record without login, with documented guardianship and authorized communication/contact channels.
- Require appropriate guardian consent/permissions and safeguarding workflows before exposing child records or enabling self-service features.
- Avoid public discovery of minors, unrestricted attendee lists, or automatic direct messaging to minors.
- Define a clear policy for sensitive questionnaire data, pastoral-care notes, retention, exports and deletion before rollout.
- Review applicable law and church policy with qualified legal/privacy guidance. This document is not legal advice.

## 6. Capability map

The following describes **potential product capabilities**, not a commitment to implement all at once.

| Module | Foundation / MVP | Later extensions |
| --- | --- | --- |
| Personas | Tenant-scoped profiles, contact preferences, consent, sede affiliations | Households, import/export, deduplication with review, advanced directory |
| Eventos / Experiencias | Existing camp stability, registrations, role-specific workflows, forms, scoped dashboards | Configurable event types, ticketing, waitlists, reusable templates |
| Grupos | Group profiles, discovery, leader assignment, join requests, membership | Attendance, group communications, curriculum, group lifecycle |
| Seguimiento | Assigned request handling, status, owner, reminders | Configurable multi-step Flows-like workflows and automations |
| Mi Iglesia | Home, relevant activity, profile, registrations, groups | Personalized learning/serving and advanced preferences |
| Ministerio / Servicio | Minimal discovery or interest forms (if validated) | Teams, shift scheduling/rotas, training, availability |
| Contenido | Public links to existing sermons/series | Media management, playlists, resource libraries |
| Discipulado | After MVP: programs and voluntary enrollments | Facilitators, cohorts, mentoring, reading plans, resources |
| Comunicación | Email/announcements/notifications for essential tasks | Segmentation, approved WhatsApp integration, team discussions |
| Operaciones | Relevant action dashboards and operational reporting | Rooms, resources, service planning, consolidated BI |
| Donaciones | **Out of MVP** | Payment provider integrations and financial reconciliation |
| SaaS Console | Tenant boundaries/configuration needed for foundation | Self-service onboarding, subscriptions, billing, metering, support |

**Camp-specific functions already present:** questionnaires, camperos, tutores, coordinadores, pastores, role-based dashboards, real-time reporting, and presentation-ready summaries must be inventoried during the audit. Whether each is generalized, wrapped, or retained temporarily depends on actual code and risk.

## 7. Reference journeys and acceptance outcomes

### J1 — Visitor to connected attendee
1. Person discovers a church/sede and public service/event information.
2. Person optionally registers or requests follow-up with clear consent.
3. Authorized welcome team receives the request; status can be tracked.
4. Person later accesses relevant events/groups without duplicating their record.

### J2 — Camp to small group (**first product milestone**)
1. Campero registers and participates through current camp workflows.
2. Authorized staff see only the event-specific data they need.
3. Event concludes; event privileges expire or become inactive, history is retained according to policy.
4. Participant can still access Mi Iglesia, explore groups and request to join.
5. Authorized group leader approves/declines the request.
6. Participant sees group details and upcoming activities.
7. Previous sensitive camp questionnaire answers are **not** automatically exposed to the new leader.

### J3 — One person, multiple roles
- Same person is an attendee of Sede Norte, a small-group leader, a worship volunteer, and a camp tutor.
- They see the correct tools in each context without account duplication or cross-scope privilege escalation.
- Expiration/revocation of one assignment does not remove the other memberships.

### J4 — Ministry volunteering
- Person discovers a serving opportunity, expresses interest, completes appropriate onboarding, and receives authorized assignments.
- Ministry leader has scoped team tools and no automatic access to confidential pastoral information.

### J5 — Pastor and church administrator
- An authorized leader sees useful operational indicators (pending requests, event completion, staffing gaps) by permitted campus/organization.
- Can delegate actionable tasks rather than only viewing charts.
- Dashboards respect aggregation thresholds and data sensitivity.

### J6 — Second tenant isolation
- Church B can be created without deploying a new codebase.
- Members, forms, questionnaires, staff, configuration, and reporting of Church A are never accessible to Church B without an explicit, separately designed consent-based flow.

## 8. First MVP boundaries

### Must have

- Stable existing camp system and preserved data.
- Core tenant/campus/person relationships compatible with future multi-church growth.
- Contextual roles and safe authorization for at least church, campus, event, and group scopes.
- Member-facing home and public discovery sufficient for the post-camp journey.
- Small groups: creation, leaders, basic discovery, requests, membership.
- Essential notifications/follow-up for group joining and event continuity.
- Spanish interface, mobile usability, consent/privacy, auditability, regression coverage.

### Explicitly not in the first MVP

- Complete ChurchSuite parity; full donations/payments/financial accounting.
- Native iOS/Android apps; full livestream infrastructure; proprietary WhatsApp/chat clone.
- Full volunteer scheduling engine/rotas; complex facilities and child check-in systems.
- AI-driven assessments of spiritual status, automated pastoral judgments, or broad AI access to sensitive records.
- Self-service billing and exhaustive SaaS plan management (beyond necessary tenant boundaries).

## 9. UX and content direction

**Experience goals:** Calm, human, modern and technologically credible—without a generic AI-generated SaaS aesthetic. Favor good typography, considered spacing, restrained color, clear hierarchy, strong contrast, consistent interaction states, and purposeful visuals over dense analytics cards and decorative gradients.

- **Spanish-first:** all member-facing and church operator UI text in natural Spanish. Keep localization architecture ready for other languages.
- **Mobile-first:** optimize church attendance, event registration, group joining, serving, and quick leader workflows on phones.
- **Accessible:** keyboard support, screen reader semantics, readable type, contrast, clear error states, meaningful form labels, manageable cognitive load.
- **Progressive disclosure:** personal actions first; administrative tools only in authorized contexts.
- **Task-first dashboards:** next steps, assigned work, and decisions before vanity metrics.
- **Low friction:** public browsing without login when possible; collect only necessary data at the point of need.
- **Sensitive experiences:** questionnaires and pastoral-care contexts must use careful, respectful language and consent explanations.
- **Design validation:** interview/test with nontechnical participants, tutors, pastors, and leaders; don't rely on designer intuition alone.

Potential member navigation: `Inicio`, `Explorar`, `Mi actividad`, `Perfil`. Adapt through usability testing, not as a fixed requirement.

## 10. Technology direction (provisional, audit-dependent)

- Prefer **evolving the existing stack** over rewriting for aesthetic/architectural purity.
- Prefer a **modular monolith** for initial growth unless the repository demonstrates a compelling need for distributed services.
- Use relational integrity and strong tenant isolation; evaluate row-level security and defense-in-depth where supported. Do not assume the current database technology.
- Have a clear authorization service/policy approach; do not rely solely on frontend route guards or UI visibility.
- Use versioned schema migrations and expand/contract changes for riskier migrations; backup, rollback, and testing are required.
- Implement structured, purpose-specific audit logs that avoid exposing secrets and sensitive questionnaire answers.
- Use pluggable integrations for communications and media; defer expensive ownership of streaming/messaging infrastructure.
- Establish monitoring, error tracking, backups, restore testing, and environment separation appropriate to production readiness.
- For realtime dashboards, verify existing data flows and freshness requirements rather than assuming websocket/event-stream architecture.

## 11. Phased delivery and stage gates

### Phase 0 — Repository and product audit (next action)

**Deliver:** current-state inventory, capability matrix, code evidence, security/permission review, UX findings, integration/dependency risks, data migration options, candidate architecture decisions, prioritized backlog, cost drivers and unknowns.

**Gate:** agreement on what exists, what is fragile, what must be preserved for camp, and a reviewed safe release plan.

### Phase 1 — Domain and authorization foundations

Safely introduce or adapt tenants, sedes, tenant-scoped person records, identity links, contextual role assignments and central access checks. Create migration and regression tests.

**Gate:** a user can hold multiple scoped roles with no privilege leakage; camp flows still work.

### Phase 2 — Community MVP

Deliver public church discovery, Mi Iglesia, small groups and join requests, leader tools, basic follow-up, and camp-to-group continuity.

**Gate:** a real former campero can join an ongoing small group without manual admin intervention, and core nontechnical tasks succeed in usability testing.

### Phase 3 — Recurring church engagement

Expand ministries/service, volunteer coordination, content, sermons/series, devotionals, discipleship, reusable workflows, and appropriate communication tools based on actual demand.

**Gate:** demonstrable regular use beyond events and measurable reduction in leaders' administrative burden.

### Phase 4 — Commercial SaaS

Add self-service onboarding, tenant configuration, subscriptions/billing, usage limits, support, documentation, import/export, operating metrics, and an independent second-church pilot.

**Gate:** a second church with a different organization model can operate independently without a bespoke fork.

**Priority rule:** the camp deadline may require postponing or feature-flagging foundations that put the event at risk. There is no obligation to complete a phase in one release.

## 12. How success is evaluated

Measure operational and experience outcomes, with appropriate privacy protections:

- Completion and error rates for event registration, forms, and group join requests.
- Median time from join request to a human response; unresolved requests.
- Task completion rates for nontechnical members and leaders without help.
- Repeat, voluntary use of relevant church tools (not a "spiritual engagement score").
- Volunteer and event coordination time saved for leaders.
- Zero known cross-tenant or unauthorized cross-scope data exposure; tracked authorization test coverage.
- Camp regression test results and event operational stability.
- Adoption and support burden for a second, independently configured church.

Define exact baseline, targets, telemetry and retention in discovery; do not fabricate KPIs.

## 13. Audit questions that remain open

The repository audit and stakeholder discovery must answer:

1. What is the actual framework, infrastructure, hosting topology, and database schema?
2. How are users, camperos, tutores, coordinadores, pastores, and admins currently represented?
3. Which existing roles are global, hardcoded, or mixed into person identity?
4. Are resources scoped by event, sede, or tenant today? Which APIs enforce access on the server?
5. What are the existing camp schedules, registration, questionnaires, reports, realtime update flows, and presentation/export features?
6. What sensitive data exists? What consent, guardianship, retention and deletion behavior exists today?
7. Which existing components can be generalized with minimal risk?
8. What are the deployment and rollback constraints ahead of the camp?
9. Which capabilities currently work well, and what do church users struggle with?
10. What are current operating costs and usage patterns (storage, media, databases, email, messaging, compute)?
11. What church vocabulary and structure are required, and which elements vary across churches?
12. What is the simplest path to end-to-end camp-to-small-group continuity?

## 14. Economic and commercialization approach

**Product direction:** serve one church first; validate a repeatable product and subsequently test a second unrelated church. Potential future packaging: core community, growth/leadership, multi-sede network. Do not lock prices or plan boundaries yet.

**Estimate after auditing real usage and code:** hosting, database, backups, storage, notifications, email/SMS/WhatsApp, video distribution, maintenance, security, support, development, design, billing and customer acquisition. Separate fixed costs from per-tenant and per-active-user costs. Account for providers' commercial terms and data protection obligations.

**Avoid premature implementation:** no donations system, subscriptions, native apps, AI features, or broad messaging platform solely because commercial products offer them.

## 15. Definition of done for proposed development

A feature is not done merely because the UI renders. At minimum:

- Solves a specified user journey with Spanish UI copy and appropriate empty/error/loading states.
- Defines tenant ownership, exact authorization rules, privacy implications, and audit requirements.
- Has tests for positive paths, denied paths, and relevant cross-scope/cross-tenant cases.
- Does not regress camp workflows or compromise existing data.
- Includes migration/rollback and feature-flag strategy when needed.
- Is accessible and usable on mobile for the intended nontechnical user.
- Adds relevant documentation, monitoring, and supportability.

## 16. Decision register

| Decision | Status | Notes |
| --- | --- | --- |
| Church-first, SaaS-ready | **Agreed direction** | Initial church validates; second church tests repeatability |
| ChurchSuite as operational reference | **Agreed direction** | Inspiration for workflows, not a clone |
| People-first and roles scoped to context | **Proposed core invariant** | Confirm compatibility with current roles and data |
| Multi-sede model | **Proposed foundation** | Support cross-campus relationships where justified |
| Spanish-first, mobile-first | **Proposed UX principle** | Validate UX with actual nontechnical users |
| First milestone: camp → small group | **Proposed MVP** | Must not disrupt camp |
| Modular monolith / technical stack | **Provisional** | Decide after codebase audit |
| Billing tiers/prices and timeline | **Undecided** | Require real usage and cost analysis |

---

### Instructions for AI coding agents

When using this document as reference:

1. Treat it as the **desired product direction**, not a description of what currently exists.
2. Inspect the relevant source files, schemas, migrations, tests and deployment configuration before recommending change.
3. Always distinguish **observed fact**, **inference**, and **proposal**; cite repository paths/line numbers for codebase claims.
4. Preserve production and camp readiness; propose incremental changes, feature flags, migration safety, and rollback.
5. Ask for a decision only when it materially changes architecture, privacy, or implementation scope; otherwise state sensible assumptions.
6. Avoid overengineering, generic AI-looking UI, hidden permission changes, and scope expansion beyond the approved phase.
7. Use Spanish in end-user UI; technical documentation may remain in English unless requested otherwise.
8. Do not create or modify product code as part of a **read-only audit** unless explicitly authorized.
