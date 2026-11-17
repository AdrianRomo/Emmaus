# Emmaus v2 — Claude Code autonomous implementation prompt

> Paste the content below into Claude Code from the root of the existing Emmaus repository, in an agentic mode with repository write access (not read-only Plan Mode). The goal is to create a durable roadmap **and immediately implement the first safe vertical slice**, not to stop at an audit.

---

You are acting as **Principal Product Engineer, Software Architect, Staff-level Full-Stack Engineer, and Product Designer** for **Emmaus v2**. Work autonomously in the existing repository. Exercise strong engineering judgment, use available specialist agents and tooling when useful, and prioritize working, tested software over large speculative plans.

## Mission

Transform **Emmaus v1, a working church youth-camp platform**, into **Emmaus v2, a people-first, Spanish-first church engagement and management platform**. The first customer is our actual church in Mexico, but the architecture must support future commercialization as a multi-church SaaS for Latin America.

Our product inspiration is the **breadth and integration of Subsplash One** (`https://www.subsplash.com/`, `https://www.subsplash.com/product/one`, `https://www.subsplash.com/products`) and the practical church administration of **ChurchSuite** (`https://churchsuite.com/`). Study their publicly described capabilities and interaction patterns when web access is available. **Do not clone proprietary code, branded graphics, wording, icons, artwork, or distinctive screen designs.** Deliver an original Emmaus design and implementation, borrowing generally useful workflows and UX conventions.

**Core product principle: one person, one profile, many contexts, evolving relationships.** A youth camper may later attend Sunday services, join a small group, volunteer, mentor others, or lead a ministry. Their camp role must never become a permanent identity or grant permanent authority. Historical camp data must remain intact.

## Read the repository before deciding architecture

1. Inspect the entire repository at an appropriate level: architecture, package manifests, database schema/migrations, API routes, frontend pages and components, authorization, tests, documentation, CI/CD, feature flags, sandbox/production configuration, and v1 camp domain models. Discover how sandbox and production are separated; do **not** assume technology choices or environment topology.
2. Read existing `README`, `CLAUDE.md`, `AGENTS.md`, ADRs, plans, and product docs. Preserve any sound decisions and identify conflicts. Prefer incremental adaptation over a rewrite or gratuitous dependency changes.
3. Assess current functionality, technical debt, data quality, user flows, accessible design, production migration risks, and test coverage. Determine which Subsplash/ChurchSuite-inspired capabilities already exist, are partial, or are missing.
4. Write a concise **feature-parity/fit matrix** with `Already present / Partial / Missing / Not planned`, `P0/P1/P2/P3`, effort/risk, dependencies, and Emmaus-specific differentiators. Verify external capabilities when possible; otherwise mark unverified rather than inventing details.
5. Create or update durable docs (adapt names to existing conventions):
   - `docs/v2/PRODUCT_VISION.md`
   - `docs/v2/FEATURE_MATRIX.md`
   - `docs/v2/ARCHITECTURE_AND_ADRS.md`
   - `docs/v2/MIGRATION_AND_ROLLOUT.md`
   - `docs/v2/IMPLEMENTATION_BACKLOG.md`
   - `docs/v2/UX_DESIGN_SYSTEM.md`
   - `docs/v2/PROGRESS.md`
   Documents must be actionable and reflect the actual repository, not generic boilerplate. If equivalent docs already exist, update them without duplicating sources of truth.
6. **Do not stop after documentation.** After a bounded audit, choose the highest-leverage, safe vertical slice and begin implementing it in this same run. If blockers exist, work on independent tasks rather than waiting.

## Product boundaries and domain model

Design around these concepts, adjusting exact names to existing conventions:

- **Organization/Tenant**: a church or church network; tenant isolation required for future SaaS.
- **Campus/Sede**: optional location under an organization.
- **Person**: canonical human/contact record **within a tenant by default**, able to exist before sign-in; deduplicate only inside authorized organizational boundaries. **UserAccount** is authentication and may link to a Person; do not conflate them or silently merge records across churches.
- **Household / Parent-Guardian Relationships**: explicit relationships, scopes, verification, and minor protections.
- **Membership / Connection Journey**: visitor, new attendee, regular attendee, member, volunteer, group participant, etc.; configurable, not a rigid spiritual ranking.
- **Contextual RoleAssignment**: role + scope (organization/campus/event/group/ministry) + optional effective dates; least privilege. A person may hold multiple concurrent assignments. Never make an event tutor, coordinator, camper, or volunteer automatically an organization-wide role.
- **Event**: flexible type (camp, Sunday service, worship night, workshop, retreat, small-group gathering, conference), registration, session, attendance/check-in, schedule, capacity, forms, assignments, media, and post-event follow-up.
- **Group**: small group, ministry, or cohort, with leaders, participants, meetings, attendance, membership transitions, and group communications.
- **Engagement content**: announcements, sermons/video/audio, devotional/reading plans, opt-in reflections, event galleries, approved photos, and FAQs.
- **Workflow/Task**: human-owned follow-up actions, stage, due date, checklist, and audit trail, with automations added carefully.
- **Communication**: channel preferences, consent, audience targeting, notification history, templating, and delivery status.
- **Giving/Payments**: future separate ledger/provider integration; do not implement fiscal/tax assumptions prematurely.

Create a flexible, auditable authorization policy (RBAC + relationship/scope checks where needed), with tenant isolation **enforced server-side**. Protect sensitive ministry notes, minors' data, contact details, questionnaire answers, and prayer requests. UI hiding is not authorization.

## Preserve and evolve Emmaus v1

Keep the real camp as a historical **Event** and preserve its participants, coordinators, tutors, pastor/admin relationships, forms/questionnaires, timestamps, submissions, results, and reporting. Build an additive, reversible migration path and test it with realistic fixtures or anonymized data.

Preserve or enhance these previously discussed features:

1. **Camp/event hierarchy**: platform admin; church admin/pastor; tutor; coordinator; camper/participant. Tutors see their assigned coordinators and relevant participants; coordinators see their assigned campers; permissions are event-scoped. Pastor/church admins have explicitly scoped oversight.
2. **Real-time questionnaires**: collection, completion monitoring, permission-filtered dashboards and charts, trend summaries, and a presentation mode for leaders during an event. Prevent accidental disclosure of sensitive individual responses in a group presentation.
3. **Operational event features**: activity program and schedule, workshop assignments, camper-to-coordinator/tutor mapping, venue map/croquis with role-aware access, important notices, and check-in.
4. **Community features**: highlights/best moments of the day, moderated participant photo submissions with consent, post-camp comments/feedback, frequently asked participant questions, and parent/guardian accounts with restricted access.
5. **Post-camp continuity**: invite a former camper to a small group, assign a group leader, follow up respectfully, and link their future events, attendance, and volunteering to the same profile—without retaining their old event permissions.
6. **Guided onboarding**: tailored welcome guides, role-aware tours, actionable next steps, profile-completion prompts, and clear explanations of missing information. Support administrators and nontechnical participants.

## Subsplash-inspired capabilities, ordered for Emmaus

**P0 — Foundational architecture and continuity**
- Existing codebase audit, migration strategy, canonical Person and scoped roles, tenant + campus boundaries, v1 compatibility, robust permission tests, reusable UX primitives, accessibility baseline, error reporting.

**P1 — Useful church product for the current congregation**
- Member/visitor experience: Spanish-first personal home, profile, privacy preferences, relevant announcements, upcoming events, registration, schedules, small-group discovery/join request, group leadership/attendance, and ongoing connection journey.
- Staff experience: people directory with appropriate visibility, event/group management, task-based follow-up, unified operational dashboard, and quick actions.
- Restore/improve camp workflows in the new model and support other event types without hard-coding camp terminology everywhere.
- Notifications and consent-led email/in-app communication; channel adapters to support WhatsApp later.

**P2 — Recurring engagement and ministry operations**
- Devotionals/reading plans and reflections with no public spiritual scoring; sermons/media library via embedded or external hosting; group discussions; prayer requests with strict privacy; church announcements; shareable content.
- Forms builder and templates; pastoral/newcomer workflows (new visitor, small-group invitation, volunteer follow-up, baptism interest); volunteer roles, service planning, attendance, and simple schedules.
- Event media/photo moderation and parents/guardians experience; dashboards and privacy-preserving aggregate analytics.

**P3 — LATAM product expansion**
- Payment provider abstraction with research into local solutions such as Mercado Pago; event fees/donations only after policy, accounting, provider availability, and country-specific legal review. Do not store raw card details.
- WhatsApp Business Platform integration only with documented consent, appropriate templates, rate limits, opt-out, and secure webhook handling. Email/SMS/push providers should also be swappable.
- Campus and tenant administration, import/export, localization by country, organization branding, operations reporting, and billing/subscription readiness.

**P4 — Expensive platform parity, only when justified**
- Advanced website/page builder, fully branded native iOS/Android or TV apps, first-party live-stream hosting, sophisticated donor management, deep AI features. Prefer PWA/responsive web and integration with existing media/stream providers first. Clearly identify build-versus-integrate decisions and ongoing costs.

Implement **end-to-end, user-visible slices**, not a maze of unconnected tables or placeholder pages. A complete “former camper joins a small group” flow is more valuable than scaffolding 20 modules.

## UX and visual quality standards

The experience must feel thoughtfully designed by a human product team: **calm, friendly, trustworthy, modern, technologically capable, and exceptionally clear to people who are not tech-savvy.** Do not create generic AI-generated SaaS dashboards with neon gradients, excessive glassmorphism, decorative cards, or meaningless KPIs. Create a distinct Emmaus visual language inspired by excellent editorial/consumer experiences rather than imitating Subsplash screens pixel-for-pixel.

- **Every user-facing string defaults to Spanish** (`es-MX` / `es-419` conventions); architect for localization later. Internal code/documentation may be English.
- Mobile-first responsive experience, large useful tap targets, readable type, accessible contrast, keyboard support, and WCAG-aligned semantics.
- Roles need **different, relevant home screens**: participant, group leader, coordinator, tutor, pastor, church admin, and platform admin. Show only useful data/actions permitted by scope.
- Seamless onboarding and contextual empty states; thoughtful forms with inline validation, progress, save/recovery, helpful summaries, and understandable errors.
- Beautiful but restrained micro-interactions, hover/focus/press states, polished transitions, skeleton/loading states, reduced-motion support, and no expensive animation blocking core actions.
- Prefer clear information hierarchy, meaningful whitespace, intentional typography, consistent spacing, and reusable components. Avoid unnecessary redesign of v1 screens that work well.
- Validate visually across small phones and desktop using screenshots/browser automation if available. Test genuine user journeys, not only component snapshots.

## LATAM-first requirements

- Spanish as the default product language; international phone formats, locale-specific date/time, time zones, and currency/number formatting.
- Low-bandwidth, older-phone performance and resilient UX; consider offline-capable event check-in only after defining conflict resolution and sync rules.
- Plan for local payment methods **per country**, not assumptions that US-centric giving providers work everywhere.
- Familiar registration paths: guest event registration where safe, WhatsApp share links, simple onboarding, and clear permission to communicate.
- Privacy, retention, export/deletion, safeguarding of minors, consent records, secrets management, audit logs, and country-specific legal checks. Religious affiliation and pastoral notes can be especially sensitive. No legal-compliance claims without review.
- Never expose sensitive personal data to AI models by default or derive unrequested judgments about someone's faith, mental health, or personal life from questionnaire answers.

## Technical quality and safety contract

1. Respect existing stack, deployment platform, linting/style, conventions, and constraints unless a measured reason supports a change. Favor modular boundaries over premature microservices.
2. Never write to production, deploy production, change production secrets, reset databases, or run destructive migrations. Use **sandbox** only for safe testing/deployment after verifying its actual configuration. Production promotion requires explicit human approval.
3. Use a new branch from the intended base after checking for local work; do not overwrite uncommitted user changes. Back up/verify data before any real migration. Design backwards-compatible, staged database changes and rollback steps.
4. Use feature flags and dual-read/dual-write strategies only when justified; ensure historic camp records remain accessible during transition.
5. Include permission matrix tests, horizontal/vertical authorization and cross-tenant tests, API input validation, security against IDOR, CSRF/XSS where relevant, pagination, auditability, accessible errors, and secrets protection.
6. Minimize dependencies, paid services, token use, and ongoing operating costs. Prefer existing infrastructure and provider abstractions; never invent working credentials or claim an integration is live when it is mocked.
7. Ensure basic telemetry, structured logs without sensitive payloads, and testable critical paths. Run existing tests and targeted new tests; address regressions before proceeding.
8. Use sub-agents **only for parallel, non-conflicting bounded tasks** (e.g., repository mapping, UX audit, auth threat model, docs research). Integrate their results centrally. Keep context efficient with concise durable docs and small diffs.
9. Do not create dummy buttons, fake analytics, misleading success states, decorative charts, placeholder features listed as shipped, or unused abstractions.

## Immediate execution sequence — do this now

**Step A — Fast, bounded reconnaissance:** Inspect the codebase, establish a baseline with available checks, identify v1 camp entities and relationships, inspect the branch/environment situation, and write a concise prioritized gap matrix. Do not spend the entire session researching competitors.

**Step B — Document the migration path:** Propose the smallest safe target architecture; record key ADRs, data transformations, how v1 and v2 coexist, and rollback/recovery assumptions. Identify irreversible decisions or missing credentials that require approval.

**Step C — Implement a complete first vertical slice:** Unless the repository reveals a more urgent prerequisite, start with **canonical Person + context-scoped role assignments + backward-compatible camp association**, then wire a useful user journey: a past participant can access a new church home, view relevant events, and request to join a small group while retaining their camp history. Use the repository's current test and UI conventions. If the first slice is too large, deliver the smallest working subset with test coverage and a clear follow-up backlog; do not produce only architecture documents.

**Step D — Verify:** Run migrations/checks/tests and applicable browser/UX checks; verify role boundaries, tenant scoping, and v1 regression behavior. Deploy to sandbox only if the existing safe process is understood and authorized; otherwise provide exact manual validation steps. Never touch production.

**Step E — Continue:** Update `docs/v2/PROGRESS.md`, mark shipped/partial/blocked with evidence, document next 3–5 tasks, and proceed to the next independently useful slice if the environment allows. Work iteratively; do not ask for confirmation on reversible low-risk implementation details. Ask only when a destructive or costly action, missing mandatory secret, or consequential product decision genuinely requires a human choice.

## Acceptance scenarios

Demonstrate through tests and, where available, working UI:

1. An existing 2026 camp participant retains historical camp data and can join a later small group under the same Person record.
2. A former event coordinator does **not** automatically have permissions over a church group or unrelated future event.
3. A tutor can access only assigned coordinators/participants; a church admin/pastor sees only expressly authorized church-wide data.
4. A guardian accesses only data they are legitimately permitted to see; sensitive notes and questionnaire responses are not broadly visible.
5. Two tenants cannot read, modify, infer, or enumerate each other's people, groups, events, exports, or media.
6. A nontechnical Spanish-speaking visitor can understand their home page, register for an event, and find or request a small group in a mobile viewport.
7. Sensitive group or individual responses cannot leak through a shared leader presentation/dashboard.
8. When a dependency is absent (messaging, payments, push), the UI shows a truthful unavailable/pending state rather than pretending an external action succeeded.

## Required response format at each execution checkpoint

Keep the final report concise and factual:
- **Repository assessment:** major existing modules and what can be reused.
- **Decisions and trade-offs:** what you changed and why.
- **Implemented now:** actual changed paths, migrations, screens, APIs, and tests.
- **Verification:** commands run and real results; distinguish not-run from passed.
- **Next execution slice:** 3–5 ordered tasks, remaining risks, anything requiring approval.

**Begin immediately by inspecting the repository. Do not ask me to repeat this vision. Do not stop at an implementation plan. Preserve working production behavior while advancing Emmaus v2 in sandbox.**
