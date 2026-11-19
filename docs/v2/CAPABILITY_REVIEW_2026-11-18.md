# Capability and issue review — 2026-11-18

Review [#90](https://github.com/AdrianRomo/Emmaus/issues/90). [Matrix](FEATURE_MATRIX.md) covers source and comparator scope; the
[roadmap](../audits/EMMAUS_EVOLUTION_ROADMAP.md#current-priorities--2026-11-18) is the work queue.
No app behavior, production configuration, merge or deployment is part of this documentation change.

## Evidence and limits

Reviewed stable `main` at `75d42ddc9b9e5698cbd46b02dd54e7baf7fd9246`, v2/foundation at
`519e7389ab7d3b58b20447e80d1a22679a6d100b`, source routes/models/services/test definitions,
ADRs 0008–0016, all 15 open issues and their comments. No open PRs existed at the inventory point.
Source evidence is pinned in the matrix; historical test summaries in PROGRESS.md are not new runs.
Production/host/SMTP/backups and real visitor usability were not verified.

The exact referenced [REA project](https://github.com/morluto/rea) is **Reverse Engineer Anything**,
a research/developer tool, not a church-management competitor. Its identity is corroborated by
[existing reference research](REFERENCE_RESEARCH.md). ChurchSuite, Subsplash and Planning Center
are the bounded current functional references. Their official public descriptions establish feature
breadth; we did not trial authenticated products or verify every historically mentioned vendor.

## Dispositions for the 15 existing open issues

Keep issues open until their remaining acceptance evidence exists. Labels or a prototype do not
constitute policy approval. Priorities below apply to the remaining work, not already merged code.

| Issue | Priority / disposition | Remaining decision or evidence |
|---|---|---|
| [#13](https://github.com/AdrianRomo/Emmaus/issues/13) owner prerequisites | P0; retain as owner evidence tracker | Preserve checked history. CodeQL upload was reported resolved; do not reopen it from old comments. Current protection/check requirements and residual owner prerequisites need dated evidence; backups are [#45](https://github.com/AdrianRomo/Emmaus/issues/45) |
| [#32](https://github.com/AdrianRomo/Emmaus/issues/32) current church session | P0 before tenant two; accept scoped technical direction | Explicit validated selection; preserve single-church/camp behavior. Existing supervised authorization review is still required; this review does not implement it |
| [#37](https://github.com/AdrianRomo/Emmaus/issues/37) branding context | P0 before tenant two; accept after [#32](https://github.com/AdrianRomo/Emmaus/issues/32) | Resolve request-scoped church consistently; fallback only for supported legacy flow; two-church brand/tenant tests |
| [#39](https://github.com/AdrianRomo/Emmaus/issues/39) runtime configuration | P0; retain owner operation | Dated yes/no runbook; no .env, credential values, account identities or production data in public issues |
| [#45](https://github.com/AdrianRomo/Emmaus/issues/45) backups | P0 first operational priority | Off-host encrypted copy, successful scratch restore, matching aggregate counts and purge/retention evidence. Do not mark done from same-host dumps |
| [#52](https://github.com/AdrianRomo/Emmaus/issues/52) metrics and delivery alerts | P0 remaining host work; application half is implemented | Ship privacy-safe logs, alert on error/job failure and missing delivery configuration, validate SMTP and scheduler in a supervised environment |
| [#58](https://github.com/AdrianRomo/Emmaus/issues/58) map/workshops | P2 scoped proposal accepted; implementation awaits approval | Reuse programme places/slots; protected map and assigned-person-only whereabouts; capacity/guessed-id denial tests; owner and safeguarding review before minors' location data |
| [#59](https://github.com/AdrianRomo/Emmaus/issues/59) parents | P1 public notice links first; Phase B still gated | Phase A public info is implemented, anonymous subscriptions are not. Use explicitly published event notices with content-free email links; double opt-in, withdrawal/expiry before subscriptions. Phase B waits on [#75](https://github.com/AdrianRomo/Emmaus/issues/75) and verified/revocable relationship |
| [#60](https://github.com/AdrianRomo/Emmaus/issues/60) moments/photos | P2 staged scope retained | Staff text/curation first; image consent, trained moderation, protected storage, EXIF stripping and purge before uploads. External albums still need appropriate consent/access |
| [#75](https://github.com/AdrianRomo/Emmaus/issues/75) minor authorization | P0 affected-release gate remains open | Staff-verified age and pending unknown/self-declared age retained. Leadership/adviser approves scope/text/methods/retention and medical/emergency handling; no silent permission from competitor patterns |
| [#76](https://github.com/AdrianRomo/Emmaus/issues/76) attendance | P0 retention/privacy gate, not a request to build named access | Existing owner decision: no individual pastoral attendance. Aggregates may proceed with privacy floors; approved retention/backup/audit rules before scheduling pruning |
| [#80](https://github.com/AdrianRomo/Emmaus/issues/80) defaults | P1 partial technical decision, policy items stay open | Keep current restrictive/default behavior described below. Scheduler integration is built; runtime monitoring/delivery proof belongs to [#52](https://github.com/AdrianRomo/Emmaus/issues/52) |
| [#85](https://github.com/AdrianRomo/Emmaus/issues/85) child check-in | P2 after [#59](https://github.com/AdrianRomo/Emmaus/issues/59)/[#75](https://github.com/AdrianRomo/Emmaus/issues/75); discovery only | Workshop document exists; no custody-transfer implementation. Safeguarding team approves pickup/revocation/exception/offline and minimum retained data |
| [#86](https://github.com/AdrianRomo/Emmaus/issues/86) giving | P3 / last of ADR 0014 modules; discovery only | Administration/accounting/legal chooses go/no-go, supported country/provider, reconciliation/refunds/receipt responsibilities. No funds or provider provisioned by this review |
| [#87](https://github.com/AdrianRomo/Emmaus/issues/87) public home | P1 validation; prototype already implemented | Keep responsive web, explicit publication and noindex defaults during validation. Test two churches/anonymous phone visits, service time comprehension, unpublished material and editorial process before SEO/native expansion |

ADR 0008 Q-C1 (minimal camp history after purge) and Q-C3 (pastoral access to group rosters/requests)
remain unresolved, alongside Q-C4 member-directory visibility. They are distinct from attendance.
Keep current deletion/narrow-access defaults; [#91](https://github.com/AdrianRomo/Emmaus/issues/91) tracks identity/history/directory discovery.

## Decisions made within product/engineering scope

These preserve current safeguards and avoid expanding sensitive access. They are documentation and
backlog decisions, not replacements for the church approvals below.

- [#80](https://github.com/AdrianRomo/Emmaus/issues/80): retain complementary-cell suppression (both numerator and complement at least five), not a
  looser named/small-group disclosure. Exact existing behavior remains until privacy review.
- [#80](https://github.com/AdrianRomo/Emmaus/issues/80): retain explicit audited door override/reason; never override a minor's authorization rule.
  Keep staff verification of age; call/WhatsApp notes alone never confirm authorization.
- [#80](https://github.com/AdrianRomo/Emmaus/issues/80): keep current 48-hour offer with 12-hour platform floor, rejoin-at-end after expiry, and
  current freeze/publication rules as technical defaults. No automatic capacity policy relaxation.
- [#80](https://github.com/AdrianRomo/Emmaus/issues/80): keep per-event public pages opt-in and ended pages unavailable; urgent-only account email
  preference; safe team CSV restrictions, practice-data exclusion, and duplication without sensitive
  grants/approval. Scheduler code exists, but cadence/delivery guarantees require runtime evidence.
- [#59](https://github.com/AdrianRomo/Emmaus/issues/59): choose public event notices plus links for anonymous updates; do not email private notice
  bodies. Address verification proves an inbox, never guardianship or child access. Subscription
  lifespan/text remains a church decision; immediate public-page work can be separately scoped.
- [#58](https://github.com/AdrianRomo/Emmaus/issues/58): a protected site image and named places suffice; no live tracking/GIS. Do not start storing
  children's whereabouts until the existing owner/safeguarding review has approved the flow.
- [#87](https://github.com/AdrianRomo/Emmaus/issues/87): responsive web and external media links remain the baseline. Keep noindex for the prototype;
  SEO, native apps, push, streaming ownership and NFC need actual visitor/member evidence.
- Do not rebuild implemented basics. Treat new epics as discovery/slicing work; they do not carry
  `agent:ready`. Preserve accepted ADR 0014 module order and high-risk review requirements.

## Decisions reserved for church leadership

| Decision owner | Required decision / tracker |
|---|---|
| Leadership + safeguarding + privacy adviser | Minor consent scope, verified guardian identity/age boundary, revocation and own-child visibility [#75](https://github.com/AdrianRomo/Emmaus/issues/75)/[#59](https://github.com/AdrianRomo/Emmaus/issues/59); child pickup/custody/emergency/offline policy [#85](https://github.com/AdrianRomo/Emmaus/issues/85) |
| Leadership + privacy reviewer | Attendance/consent/audit/access retention, backup expiry/legal holds, privacy wording and directory visibility [#76](https://github.com/AdrianRomo/Emmaus/issues/76)/[#91](https://github.com/AdrianRomo/Emmaus/issues/91); current defaults are provisional periods, not legal conclusions |
| Leadership + safeguarding | Images of minors, moderator responsibilities, location/workshop access, medical/additional-needs handling [#60](https://github.com/AdrianRomo/Emmaus/issues/60)/[#58](https://github.com/AdrianRomo/Emmaus/issues/58)/[#85](https://github.com/AdrianRomo/Emmaus/issues/85) |
| Leadership + communication owner | Who publishes urgent notices, anonymous-event opt-in purpose/expiry and channel consent/moderation [#80](https://github.com/AdrianRomo/Emmaus/issues/80)/[#96](https://github.com/AdrianRomo/Emmaus/issues/96) |
| Pastoral/content leadership | Devotional/plan/media/music ownership, publication authority and rights [#99](https://github.com/AdrianRomo/Emmaus/issues/99)/[#95](https://github.com/AdrianRomo/Emmaus/issues/95); admin checkbox is not proof of pastoral sign-off; reading progress remains private |
| Pastoral leadership + safeguarding | Private prayer purpose, reader list, response expectations and emergency escalation [#97](https://github.com/AdrianRomo/Emmaus/issues/97); no automatic AI, diagnosis or scoring |
| Administration + legal/accounting | Giving country/provider, fees, reconciliation, receipts/tax treatment, refunds and financial access [#86](https://github.com/AdrianRomo/Emmaus/issues/86). Recheck official local requirements; do not copy Gift Aid/DBS |
| Product/operations owner | Backup/restore/runtime and tenant-two readiness [#45](https://github.com/AdrianRomo/Emmaus/issues/45)/[#39](https://github.com/AdrianRomo/Emmaus/issues/39)/[#52](https://github.com/AdrianRomo/Emmaus/issues/52)/[#32](https://github.com/AdrianRomo/Emmaus/issues/32)/[#37](https://github.com/AdrianRomo/Emmaus/issues/37); cut-over/rollback and any deployment require their existing reviewed process |

## Evidence required before closing work

Every implementation subissue names permitted roles, synthetic happy/denial/race cases, scope,
dependencies, rollout/rollback and applicable gates. An operational issue needs a dated runbook or
run output, not source code alone. A policy gate needs named approval, versioned wording and a
clear effective scope. No reviewer may infer either from this comparison or a checked setting.

The documentation PR records fresh checks separately. No new claim of application test success,
production readiness, competitor contractual suitability or Mexican legal compliance is made here.
