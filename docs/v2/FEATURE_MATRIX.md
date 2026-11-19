# Feature fit matrix — reviewed 2026-11-18

This replaces the 2026-11-17 snapshot. **Implemented means source exists**, not deployed or newly
tested here. Stable `main` at `75d42ddc9b9e5698cbd46b02dd54e7baf7fd9246` contains camp operations and
optional discipleship. The table describes **v2/foundation at
`519e7389ab7d3b58b20447e80d1a22679a6d100b`**. Production, SMTP and host state were not inspected.
What changed after that commit is in the last section, "State on v2/redesign-claridad (d59e1ee), 2026-11-18";
the tables are left as reviewed.

Priorities are sequencing, not authorization: P0 release prerequisite; P1 next scoped work;
P2 later depth; P3 optional/platform work. Existing accepted ADRs and safety approvals still govern.
Relative effort: S one session, M several, L many, XL substantial. Epics require bounded child issues.
See [review and issue decisions](CAPABILITY_REVIEW_2026-11-18.md) and the
[current roadmap](../audits/EMMAUS_EVOLUTION_ROADMAP.md#current-priorities--2026-11-18).

## ChurchSuite module coverage

Official public documentation was read on 2026-11-18; these are vendor claims, not a signed-in trial.
Every ChurchSuite module is covered below; similar names do not establish equivalent scope.

| Reference module | Emmaus v2 status and source | Remaining capability / tracking | Priority; effort / risk |
|---|---|---|---|
| [Address Book](https://churchsuite.com/tour/address-book/) | **Partial**: [Person, campuses, account and explicit camp links][people]; staff directory/search and own contact edits | Reviewed person import/export/merge, fields/tags/key dates, membership lifecycle, opt-in member directory [#91](https://github.com/AdrianRomo/Emmaus/issues/91); households/verified guardians [#59](https://github.com/AdrianRomo/Emmaus/issues/59) | P1; L / high |
| [Children](https://churchsuite.com/tour/children/) | **Partial safeguards**: [age verification and event-specific authorization][agenda]; no child ministry/household domain | Guardian verification/revocation, child logistics, age groups/promotion and restricted emergency/medical needs [#59](https://github.com/AdrianRomo/Emmaus/issues/59)/[#75](https://github.com/AdrianRomo/Emmaus/issues/75); pickup [#85](https://github.com/AdrianRomo/Emmaus/issues/85); photos [#60](https://github.com/AdrianRomo/Emmaus/issues/60) | P1 gated; L / high |
| [Bookings](https://churchsuite.com/tour/bookings/) | **Missing**: media `resources` and agenda places are not room/equipment reservations | Rooms/assets, clashes, setup buffers, approval, booking questions and reports [#94](https://github.com/AdrianRomo/Emmaus/issues/94) | P2; L / medium |
| [Small Groups](https://churchsuite.com/tour/small-groups/) | **Implemented basics**: [discovery, joining/approval, guides, meetings, attendance, rhythms][groups] | Capacity/advanced directory/embeds [#91](https://github.com/AdrianRomo/Emmaus/issues/91); messaging [#96](https://github.com/AdrianRomo/Emmaus/issues/96); retention approval [#76](https://github.com/AdrianRomo/Emmaus/issues/76) | P1–P2; M / medium |
| [Rotas](https://churchsuite.com/tour/rotas/) | **Partial**: [serving teams/dates, assignments, accept/decline, unavailability and clash checks][serving] | Repeating team rotations, role slots, swaps, reminders, eligibility/training records [#95](https://github.com/AdrianRomo/Emmaus/issues/95) | P2; L / medium |
| [Calendar](https://churchsuite.com/tour/calendar/) | **Partial**: [simple events, public information, programme/FAQ, member signup, attendance, waiting lists, walkups][agenda] | Series/recurrence, feeds/embeds, anonymous visitor signup, ticket types/questions [#93](https://github.com/AdrianRomo/Emmaus/issues/93); families [#59](https://github.com/AdrianRomo/Emmaus/issues/59); payments [#86](https://github.com/AdrianRomo/Emmaus/issues/86); staff leave [#93](https://github.com/AdrianRomo/Emmaus/issues/93) | P2; L / high |
| [Giving](https://churchsuite.com/tour/giving/) | **Missing**: [Mexico discovery draft][giving], no donation collection | Hosted payment go/no-go first; funds/campaigns, recurring gifts/pledges, reconciliation, refunds, statements and restricted finance access only if approved [#86](https://github.com/AdrianRomo/Emmaus/issues/86) | P3 / last of ADR 0014 modules; XL / high |
| [Planning](https://churchsuite.com/tour/planning/) | **Partial adjacent tools**: event programme and serving dates; no service-order/music domain | Timed service order, responsibility, reusable items, song/chord/file library, usage and rights records [#95](https://github.com/AdrianRomo/Emmaus/issues/95) | P2; L / medium |
| [Attendance](https://churchsuite.com/tour/attendance/) | **Partial**: [group meetings/attendance][groups], event attendance, camp cutoffs; [aggregate pastoral Panorama][overview] | Gathering/service headcounts and trends [#93](https://github.com/AdrianRomo/Emmaus/issues/93); approved retention and subtraction-safe suppression [#76](https://github.com/AdrianRomo/Emmaus/issues/76)/[#80](https://github.com/AdrianRomo/Emmaus/issues/80). No named pastoral attendance | P0 policy, P2 depth; M / high |
| [Network](https://churchsuite.com/tour/network/) | **Missing** organisation/network directory; Church and Sede are not this domain | Organisation records/roles/maps and explicitly consented network communication [#98](https://github.com/AdrianRomo/Emmaus/issues/98); separate from SaaS tenancy | P3 conditional; XL / high |

## Cross-module and Subsplash / Planning Center coverage

| Capability | Emmaus v2 status / practical limit | Remaining work / issue |
|---|---|---|
| Church/event/group/ministry scope | **Implemented** [authorization projection][authz]; session church switching incomplete | P0 before church two: [#32](https://github.com/AdrianRomo/Emmaus/issues/32) then [#37](https://github.com/AdrianRomo/Emmaus/issues/37); tenant tests do not prove usable multi-church switching |
| Member home, account and data | **Implemented basics** [Mi iglesia, profile, group requests][groups] | Directory/household privacy [#91](https://github.com/AdrianRomo/Emmaus/issues/91)/[#59](https://github.com/AdrianRomo/Emmaus/issues/59) |
| Church module flags and audit | **Implemented** [flags][flags], [audit helper][audit], policy settings; audit viewing/portability not fully established | Retention [#76](https://github.com/AdrianRomo/Emmaus/issues/76); permission review/support/export [#98](https://github.com/AdrianRomo/Emmaus/issues/98) |
| Camp operations | **Implemented on main and v2** [interviews, hierarchy, confidential access, immutable reports/PDF and campero import][camp]; practice, team CSV enhancements and templates are v2 additions | Resilience [#45](https://github.com/AdrianRomo/Emmaus/issues/45), runtime [#39](https://github.com/AdrianRomo/Emmaus/issues/39), location/workshops [#58](https://github.com/AdrianRomo/Emmaus/issues/58); camp-history survival after purge remains Q-C1, tracked [#91](https://github.com/AdrianRomo/Emmaus/issues/91) |
| Discipleship | **Implemented legacy module** [paths, lessons, questions and mentor follow-up][core] | Person-based integration and permitted work queue [#92](https://github.com/AdrianRomo/Emmaus/issues/92); never score spirituality |
| General forms | **Partial foundation**: camp questionnaire versions/conditions are not general church forms | Public/invited/anonymous forms, conditional actions and consent/versioning [#92](https://github.com/AdrianRomo/Emmaus/issues/92); no automatic person creation/merge by default |
| Visitor/pastoral staff tasks | **Partial** [person owner/due/status/fixed steps][care]; camp and discipleship lists remain distinct | Permission-filtered common queue, stages/templates/history, explicit automation [#92](https://github.com/AdrianRomo/Emmaus/issues/92) |
| Private prayer requests | **Missing**; fixed-reason care tasks are not member prayer intake | Leadership-approved private scope, response/escalation and retention [#97](https://github.com/AdrianRomo/Emmaus/issues/97); no public wall by default |
| Announcements | **Implemented** [church/group notices, urgency, versioned acknowledgement, email preferences][notices] | Host delivery/alerts [#52](https://github.com/AdrianRomo/Emmaus/issues/52); public event audience and channel controls [#96](https://github.com/AdrianRomo/Emmaus/issues/96) |
| Scheduled email | **Source implemented** [due sends, isolated jobs and heartbeats][scheduler]; real SMTP/configuration unknown | [#52](https://github.com/AdrianRomo/Emmaus/issues/52)/[#80](https://github.com/AdrianRomo/Emmaus/issues/80); do not promise delivery from source tests alone |
| Anonymous parents' event updates | **Missing**; account preferences and a public FAQ do not subscribe visitors | Public per-event notice links first, then verified opt-in/unsubscribe/expiry [#96](https://github.com/AdrianRomo/Emmaus/issues/96) and [#59](https://github.com/AdrianRomo/Emmaus/issues/59); no child access |
| Media, devotionals and reading plans | **Implemented** [linked media search/series/speaker, daily devotionals and private multi-day progress][resources] | Pastoral/content approval differs from admin rights checkbox [#99](https://github.com/AdrianRomo/Emmaus/issues/99); no Bible text ingestion or progress reporting |
| Event feedback | **Implemented** [post-window/minimum-size feedback][feedback]; not every accountless camp survey use case | Keep optional contact separate; general forms [#92](https://github.com/AdrianRomo/Emmaus/issues/92) |
| Public church home/mobile web | **Prototype implemented** [service times, address, public events/media, publication review][sitio] | Real two-church/visitor validation, SEO/editorial policy, optional live link [#87](https://github.com/AdrianRomo/Emmaus/issues/87) |
| Household signup/guardian portal | **Missing**; authorization records do not establish verified relationships | [#59](https://github.com/AdrianRomo/Emmaus/issues/59), gated by [#75](https://github.com/AdrianRomo/Emmaus/issues/75); own-child logistics only, never answers/care/safety reports |
| Children's check-in/out | **Missing**; camp kiosk and attendance are not custody handoff | [#85](https://github.com/AdrianRomo/Emmaus/issues/85) after [#59](https://github.com/AdrianRomo/Emmaus/issues/59)/[#75](https://github.com/AdrianRomo/Emmaus/issues/75); pickup authority/revocation, denial/offline procedure, badges/labels and live registers only after approved flow |
| Moments and photos | **Missing** native gallery/moderation | [#60](https://github.com/AdrianRomo/Emmaus/issues/60); distinct image consent, reviewer, protected storage/EXIF/purge before uploads; links do not replace consent |
| Paid registration | **Missing** | [#93](https://github.com/AdrianRomo/Emmaus/issues/93)/[#86](https://github.com/AdrianRomo/Emmaus/issues/86); capacity signup is not financial settlement |
| API/webhooks/embeds and portability | **Missing general integration surface**; camp CSV is limited | [#98](https://github.com/AdrianRomo/Emmaus/issues/98); scopes, revocation, replay/failure, rights-aware export/import |
| Provisioning and support | **Partial** setup/branding/flags, one church in practice | [#98](https://github.com/AdrianRomo/Emmaus/issues/98) after [#32](https://github.com/AdrianRomo/Emmaus/issues/32)/[#37](https://github.com/AdrianRomo/Emmaus/issues/37) and [#45](https://github.com/AdrianRomo/Emmaus/issues/45); time-limited audited support, tenant-two onboarding |
| SMS, WhatsApp Business, push, group chat | **Deferred**; no automated external channels implied by email | [#96](https://github.com/AdrianRomo/Emmaus/issues/96); consent, cost, moderation/safeguarding and provider discovery first; manual sharing may be scoped separately |
| Native mobile/TV/car apps, website builder, NFC | **Deferred** beyond responsive web | [#87](https://github.com/AdrianRomo/Emmaus/issues/87) discovers demonstrated visitor/member needs; do not confuse deferral with parity |
| Owned media hosting, podcasts, streaming/casting | **Deferred**; external media links exist | [#87](https://github.com/AdrianRomo/Emmaus/issues/87); validate need, cost, rights and operations before infrastructure |
| AI data queries, sermon repurposing/personalisation | **Deferred** comparator capabilities, not accepted parity goals | [#87](https://github.com/AdrianRomo/Emmaus/issues/87) captures product demand only; any later proposal requires separate content/privacy review. Sensitive answers stay out of AI; no spiritual scoring |

Sources: [ChurchSuite tour](https://churchsuite.com/tour/),
[forms](https://support.churchsuite.com/article/589-forms),
[flows](https://support.churchsuite.com/article/154-flows),
[member access](https://support.churchsuite.com/article/385-my-churchsuite-user-guide),
[API](https://churchsuite.com/api-and-json/),
[Planning Center overview](https://help.planningcenter.com/en/179938-what-is-planning-center-.html),
[Subsplash catalog](https://www.subsplash.com/products) and
[workflows](https://www.subsplash.com/product/workflows).
UK Gift Aid/DBS features do not establish Mexican tax/safeguarding requirements; [#86](https://github.com/AdrianRomo/Emmaus/issues/86)/[#75](https://github.com/AdrianRomo/Emmaus/issues/75) require local review.

## State on v2/redesign-claridad (d59e1ee), 2026-11-18

Same rule: **implemented means source exists** on that branch, not merged, deployed or released. Since
`519e738`, `v2/foundation` merged public notices of an event (#101, `6169e42`); `v2/redesign-claridad`
merged that in (`1b3caa3`), rebuilt every screen of a church with the `community` module on «Ventanas»
(`<html data-ui="2">`; with the module off the screens are byte for byte what they were; see
[SCREEN_MATRIX.md](SCREEN_MATRIX.md) and [RUNBOOK_REDESIGN.md](RUNBOOK_REDESIGN.md)), and built the owner's
decisions of 2026-11-18 and those taken under his delegation ([REDESIGN_CHECKLIST.md](REDESIGN_CHECKLIST.md)).
No new module; no priority above changes. The rows above that this moves:

| Row above | Now, on `v2/redesign-claridad` at `d59e1ee` | Source |
|---|---|---|
| Anonymous parents' event updates | **Partial** (was Missing): an event's public notices, read without an account by anybody with the event's public address; written, edited, retired and deleted by the church's administration; no subscription and no e-mail (#101). Verified opt-in, unsubscribe and expiry stay in [#96](https://github.com/AdrianRomo/Emmaus/issues/96)/[#59](https://github.com/AdrianRomo/Emmaus/issues/59) | [`agenda/models.py`](../../agenda/models.py) (`EventNotice`), `agenda/tests/test_public_notices.py` |
| Announcements | Besides church and group notices, an event carries public notices (#101); one published in the last 7 days is a linked line under the event on «Mi iglesia» and in Eventos (`d59e1ee`). A church notice is never tied to an event | [`agenda/services.py`](../../agenda/services.py) (`recent_notices`), `agenda/tests/test_recent_notice.py` |
| Calendar | «Eventos de la iglesia» lists an event underway under «En curso»; a camp without a page for the church shows its camperos instead of sign-ups (`9dfc09a`) | [`agenda/views.py`](../../agenda/views.py) (`admin_list`) |
| Rotas | A coming date where everyone asked said no («Nadie sirve el …») is raised on the team coordinator's «Mi iglesia», not only on the team page (`24d4970`) | [`serving/services.py`](../../serving/services.py) (`uncovered_dates`) |
| Camp operations | No interview question is required; «Prefiero no responder» on every question, stored apart from a blank (`events/0017`); the identity confirmed on «Antes de empezar» and checked again by anyone else who continues the interview; a follow-up decision can be changed with a reason, its task marked «Decisión cambiada» with «Revisar», reopened («Reabierto el …») or kept with «Continuar seguimiento» (`events/0016`, `0018`, `0019`); a camp administrator's sign-in opens the camp underway; one sign-in error; bulk coordinator assignment and the minors-without-authorization filter on Camperos | [`events/services/interviews.py`](../../events/services/interviews.py), [`events/services/follow_up_decision.py`](../../events/services/follow_up_decision.py), [`events/services/landing.py`](../../events/services/landing.py) |
| Public church home/mobile web | The landing of a church on the community module promises «Tu iglesia, entre domingo y domingo.»; the camp's door keeps its own (`970d340`) | [`templates/core/landing.html`](../../templates/core/landing.html) |

[people]: https://github.com/AdrianRomo/Emmaus/blob/519e7389ab7d3b58b20447e80d1a22679a6d100b/people/models.py
[agenda]: https://github.com/AdrianRomo/Emmaus/blob/519e7389ab7d3b58b20447e80d1a22679a6d100b/agenda/models.py
[groups]: https://github.com/AdrianRomo/Emmaus/blob/519e7389ab7d3b58b20447e80d1a22679a6d100b/groups/models.py
[overview]: https://github.com/AdrianRomo/Emmaus/blob/519e7389ab7d3b58b20447e80d1a22679a6d100b/overview/services.py
[serving]: https://github.com/AdrianRomo/Emmaus/blob/519e7389ab7d3b58b20447e80d1a22679a6d100b/serving/services.py
[giving]: discovery/giving-mexico.md
[authz]: https://github.com/AdrianRomo/Emmaus/blob/519e7389ab7d3b58b20447e80d1a22679a6d100b/core/authz.py
[flags]: https://github.com/AdrianRomo/Emmaus/blob/519e7389ab7d3b58b20447e80d1a22679a6d100b/people/flags.py
[audit]: https://github.com/AdrianRomo/Emmaus/blob/519e7389ab7d3b58b20447e80d1a22679a6d100b/people/audit.py
[camp]: https://github.com/AdrianRomo/Emmaus/tree/519e7389ab7d3b58b20447e80d1a22679a6d100b/events
[core]: https://github.com/AdrianRomo/Emmaus/blob/519e7389ab7d3b58b20447e80d1a22679a6d100b/core/models.py
[care]: https://github.com/AdrianRomo/Emmaus/blob/519e7389ab7d3b58b20447e80d1a22679a6d100b/care/models.py
[notices]: https://github.com/AdrianRomo/Emmaus/blob/519e7389ab7d3b58b20447e80d1a22679a6d100b/notices/services.py
[scheduler]: https://github.com/AdrianRomo/Emmaus/blob/519e7389ab7d3b58b20447e80d1a22679a6d100b/core/scheduler.py
[resources]: https://github.com/AdrianRomo/Emmaus/blob/519e7389ab7d3b58b20447e80d1a22679a6d100b/resources/models.py
[feedback]: https://github.com/AdrianRomo/Emmaus/blob/519e7389ab7d3b58b20447e80d1a22679a6d100b/feedback/models.py
[sitio]: https://github.com/AdrianRomo/Emmaus/blob/519e7389ab7d3b58b20447e80d1a22679a6d100b/sitio/models.py
