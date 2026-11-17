# Emmaus v2 — Product blueprint (November 2026)

## North star

**Emmaus is a church's digital front door and a continuous, personal community journey.** It helps people move from first contact to meaningful participation, relationships, service, and discipleship without forcing them to understand the church's organizational structure or maintain separate identities for every event.

**First deployment:** one church in Mexico, leveraging existing sandbox and production environments. **Long-term direction:** original LATAM-first, Spanish-first, multi-tenant church platform, with potential commercial SaaS packaging. **Migration stance:** preserve the completed camp and its reports as a historical event; evolve, do not wipe/rebuild.

## Product inspirations and boundaries

| Reference | Borrow the product idea | Original Emmaus implementation |
| --- | --- | --- |
| Subsplash One | One home for members, one dashboard for staff, linked people/events/groups/media/giving/workflows | Single canonical Person, role-aware experiences, unified event-to-group journey |
| ChurchSuite | Practical directories, small groups, volunteer schedules, ministries, check-in | Operational tools understandable to volunteers and nontechnical church leaders |
| Emmaus v1 | Camp roles, questionnaires, assignment hierarchies, live event insights | General-purpose event engine with scope-specific camp features |

Use common product concepts and accessibility/UI conventions. **Do not copy branded visuals, identifiable screen arrangements, marketing copy, private APIs, or proprietary code.**

Source references: https://www.subsplash.com/product/one ; https://www.subsplash.com/products ; https://www.subsplash.com/product/workflows ; https://support.churchsuite.com/ ; https://www.subsplash.com/product/church-event-management

## Target personas and experiences

**Visitor/new attendee:** Can learn about church, register for a public event, provide minimal information, consent to updates, and see a simple next step. Authentication is not required for everything.

**Participant / member:** Has a personal home showing upcoming events, their groups, registration status, announcements, devotionals, and actionable personal preferences. Can request to join groups or volunteer.

**Campero:** Retains camp memories/feedback and an event-specific experience, then transitions to standard church participation without being trapped in a permanent camper role.

**Coordinator / Tutor (event-scoped):** Manages only assigned people and operational event information. Sees sessions, workshops, attendance, permitted feedback, and role-appropriate aggregates. Loses those permissions when the assignment ends.

**Group leader:** Handles group membership and meeting attendance, communication, and follow-up actions for their group; not a general church-wide people directory.

**Parent/guardian:** Sees only expressly permitted child/dependent event information, schedules, notices, pickups where applicable; never automatically all private responses or pastoral notes.

**Pastor / church admin:** Sees organization-wide dashboards and people records according to explicit organizational policies; can delegate, view aggregate engagement metrics, and supervise safe follow-up.

**Platform admin:** Operates tenants/configuration/infrastructure under audit; should not casually browse sensitive pastoral data.

## Foundational entities (proposed, adapt to repository)

- Organization > Campus/Sede
- Person, UserAccount, Profile, IdentityLink, Consent, Household, GuardianRelationship
- OrganizationMembership / PersonJourneyStage (configurable)
- RoleAssignment(role, scope_type, scope_id, effective_from, effective_until)
- Event(type, organization, campus, dates), EventSession, Registration, EventAssignment, Attendance, Workshop, EventForm, FormResponse, EventMedia
- Group(type, organization, campus), GroupMembership, GroupLeadership, GroupMeeting, GroupAttendance
- ContentItem, MediaResource, DevotionalPlan, DevotionalEntry, Reflection, Announcement
- WorkflowTemplate, WorkflowInstance, WorkflowTask, Assignment, ActivityLog
- CommunicationPreference, Notification, DeliveryAttempt, ProviderIntegration
- Future: Donation, PaymentIntent, LedgerEntry, Receipt, Subscription, AuditExport

Important: **UserAccount != Person**, **RoleAssignment != identity**, **EventRegistration != church membership**, **parent relationship != global access**, and **tenant != church campus**.

## Feature pillars and phased priorities

### P0: Secure people-first foundation

- Full repo + v1 data model audit and compatibility matrix.
- Canonical person records scoped to a tenant by default, non-destructive link to legacy camp participants; prohibit implicit cross-church data merging.
- Organizational and campus scopes; contextual role assignments.
- API permission model and tenant isolation tests.
- Backward-compatible migrations, staged releases, no unapproved production changes.
- Shared Spanish-first design primitives and navigation structure.

### P1: Church MVP

- Public church home and member home; onboarding guides and role-aware setup checklists.
- Church directory with permission/consent filtering and safe search.
- Unified events for camps, Sunday meetings, worship nights, workshops, retreats and other gatherings.
- Public registration and personal registrations; event capacity, calendar/schedule, attendance.
- Small-group directory, join request, leader approval, meetings and attendance.
- People-to-group follow-up and simple staff tasks.
- Role-based dashboards: participant, group leader, coordinator, tutor, pastor, admin.
- Notification center and opt-in email announcements.

### P2: Meaningful engagement and operations

- Sermon/video/audio library using providers rather than building streaming infrastructure immediately.
- Devotionals and Bible reading plans, bookmarks/reflections, scripture links with appropriate content licensing.
- Group communication and prayer requests with granular privacy.
- Forms and surveys for events, visitors and follow-up; reusable templates.
- Workflow Kanban for visitor follow-up, baptism interest, volunteer onboarding, ministry task ownership.
- Volunteer ministry assignment and scheduling.
- Photo uploads, moderation, best moments feed, feedback and FAQs.
- Emergency/important notices and role-specific event operations.
- Privacy-aware engagement and attendance analytics.

### P3: LATAM scaling and commerce

- Country-aware i18n/l10n, currency, phone numbers, time zones and translations.
- Optional WhatsApp Business Platform messaging with permissions and approved templates.
- Payment provider adapter; verify Mexico/other country support and fees before choosing Mercado Pago/others.
- Multi-campus and multi-tenant configuration, church branding, CSV import/export, subscription management.
- Advanced communications segmentation and reporting.

### P4: Only when usage justifies cost

- Drag-and-drop site builder, white-label native mobile/TV apps, first-party video/live-stream infrastructure, advanced giving ledger and fiscal reports, AI content features.
- Prioritize PWA + existing video embeds + integrations before building costly infrastructure.

## Emmaus-specific event requirements

A camp is just a **specialized event configuration**, not the entire database identity model. Preserve:

1. Assigned pastor/tutor/coordinator/participant hierarchy for the camp only.
2. Workshops attended and assignments.
3. Uploadable location maps/croquis with controlled access.
4. Activity program and time-sensitive announcements.
5. Real-time questionnaires, completion states, cohort comparisons, permission-scoped data.
6. Presentation view for leaders with aggregate insights and safety filtering.
7. Daily highlights and moderated photos from participants.
8. Post-camp reactions, comments, and feedback.
9. FAQs and common doubts raised by campers.
10. Parent/guardian restricted views for applicable participants.

## Canonical end-to-end user stories

**A. Camp-to-group continuity**

A youth participant attended the November 2026 camp. An admin creates a small group. The user enters Emmaus using the same linked Person identity, sees their camp history and an invitation to the new group, requests membership, receives group-leader approval and shows upcoming meetings. The person does not inherit previous camp coordinator privileges, even if they held them.

**B. Sunday visitor**

A visitor opens a QR code on a welcome card, sees the church and an upcoming Sunday event, registers with minimally required information and explicit channel opt-in, receives a useful confirmation, and optionally creates a user account later without generating a duplicate Person.

**C. Tutor oversight**

A tutor sees their assigned coordinators and activity completion statuses and may present aggregated questionnaire findings to leadership. Another tutor's participants, individual sensitive answers, and private pastoral notes must remain inaccessible.

**D. Volunteer pathway**

A church member expresses interest in a ministry. A follow-up task is assigned; church staff evaluates it, invites them to training, and grants a time-bounded ministry-scoped role rather than a global admin role.

**E. Guardian safety**

A verified guardian sees their dependent's session schedule and authorized attendance status, with no general access to private group conversations, sensitive questionnaire responses, or counseling information.

## Design principles

- Warm, human, calm, editorial, approachable; exceptionally polished without obvious AI-generated patterns.
- Spanish UI throughout; Latin American words should feel natural (Iglesia, Eventos, Grupos, Mi comunidad, Mis actividades, Avisos, Mi perfil, Servir).
- Make onboarding self-explanatory, especially for nontechnical pastors, volunteers, parents and campers.
- Design around **the next useful action**, not dashboard vanity charts.
- Dashboard home for participants differs from ministry operations dashboard.
- High-quality empty/error/loading states; forms with accessible labels, understandable validation and explicit next steps.
- Fluid, restrained animation with reduced-motion support.
- Mobile-first and low-bandwidth friendly; readable chart alternatives.
- Original branding and component designs; use references for patterns, not pixel cloning.

## Data protection and safety

- Enforce server-side organization/event/group scopes and object-level permissions.
- Religious participation, prayer requests, pastoral concerns, and minors' records demand extra protection and cautious retention.
- Verify guardian relationship and consent; support access logs and data-export/deletion workflows.
- Store minimum necessary data, protect uploads, never send private responses to third-party AI without explicit authorization.
- Use aggregate/anonymized outputs when presenting to a room; avoid small-group re-identification risks.
- Distinguish pastoral support from automated psychological/spiritual inference; do not assign “spiritual maturity” scores.
- Do country-specific legal assessment before claims of compliance or tax-valid donation receipts.

## Implementation strategy

1. **Audit:** discover what is working in v1; record feature matrix, architecture and risks.
2. **Bridge:** introduce a unified Person and scoped role model while leaving camp features operating.
3. **Useful first slice:** camper transitions to member home and small group.
4. **Church MVP:** events, memberships, registration, group attendance, onboarding, staff follow-up.
5. **Broaden:** discipleship/content/media, workflows, communication, ministry scheduling.
6. **Scale:** multi-tenant, payment/channel adapters, commercialization readiness.

Always prioritize tested, working end-to-end flows. Update progress docs with evidence after each increment. Sandbox first; production changes require a release checklist, backup and explicit approval.

## Reference endpoints (for research, not copying)

- Subsplash platform: https://www.subsplash.com/products
- Subsplash One: https://www.subsplash.com/product/one
- Subsplash church management: https://www.subsplash.com/product/church-management-software
- Subsplash workflows: https://www.subsplash.com/product/workflows
- Subsplash groups and messaging: https://www.subsplash.com/product/messaging-for-churches
- Subsplash events: https://www.subsplash.com/product/church-event-management
- ChurchSuite features/support: https://support.churchsuite.com/
- Mercado Pago developer documentation: https://www.mercadopago.com.mx/developers/es/docs/getting-started
- WhatsApp Business Platform: https://developers.facebook.com/documentation/business-messaging/whatsapp/overview
