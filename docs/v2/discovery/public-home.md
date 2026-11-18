# The church's public home — prototype and what to learn from it

**Issue:** #87. **Status (2026-11-17):** prototype on `v2/wave-8-inicio-publico`, synthetic data only, not
shown to a church yet. Nothing below about real use is evidence: it is what to look for.

## What the prototype is

`/iglesia/<slug>/`, read without an account, built for a phone at 390 px. App `sitio`, behind the
`community` module. Rules are the public event page's (decision 0010, phase A): one bare 404 for
everything that is not a published home, `no-store`, no cookie, no form, the same bytes for a
signed-in reader, `noindex`.

| It shows | Where it comes from |
|---|---|
| Name, logo or arch with initials, the church's own colours | `core.Church` |
| A welcome (280 characters), address, a link to a map on another site, a phone | `sitio.PublicHome` |
| The contact e-mail | `Church.contact_email` (already public on an event's page) |
| When the church meets, as a list by day | `sitio.ServiceTime` |
| Up to 4 coming events, each a link to its `/evento/<slug>/` | Events whose page is published AND public |
| Up to 4 resources as outbound links: title, speaker, series, date | Published in the library AND marked one by one (`sitio.PublicMedia`) |
| "¿Ya eres parte de esta iglesia? Entra a Emmaus" | The sign-in |

The administration writes it under Configuración, sees a preview, and publishes through a review
page that lists every item that will be public. Publishing, unpublishing, edits and each resource
made public or private leave a church audit row.

## What it deliberately does not do

- **No people.** No names (except the speaker of a resource the administration marked), no counts,
  no groups, no notices, no attendance, no "who is going". Groups stay behind the sign-in: a group's
  place is often a home.
- **No resource description, no devotional.** Descriptions were written for members; devotionals
  are signed and were not written for strangers. Each would need its own "public" decision.
- **No embeds, maps or players.** Links only: the CSP forbids embeds and nothing is fetched.
- **No search-engine indexing.** `noindex` stays until a church asks to be found by search; today
  the page is for someone who was sent the link.
- **No visitor form** ("I'm new", prayer request). It would collect data from people with no
  account and no privacy notice; it needs its own decision.
- **No per-change review after publishing.** Once live, a newly public event or a newly marked
  resource appears at once. The review page says so.
- **No link from an event's public page back to the home**, to leave that page's bytes untouched.

## Installability, push, live links, native apps

None is built. Each needs evidence from real use first.

| Capability | The need that would justify it | Evidence to collect before building | Today |
|---|---|---|---|
| **Installable (PWA)** | Members open Emmaus several times a week and lose the link or the tab | Share of members with 2+ sessions a week over a month; how they arrive (typed, WhatsApp link, bookmark); members asking "where is the app?" | A manifest exists. Missing: the church's own icon and name, and a decision that the installed app opens "Mi iglesia", not this page. A visitor has no reason to install |
| **Push notifications** | A notice that cannot wait for the person to open Emmaus or their e-mail (a cancelled meeting, a changed place) | How many urgent notices a church sends in a term; how long until half of the people press "Enterado"; whether leaders repeat them on WhatsApp anyway | Not justified. Needs a service worker, per-device consent and a provider; on iPhone it works only after installing. E-mail notices exist |
| **Live-stream link** | People who cannot come follow the meeting at its hour, and ask where | The church already streams every week; visitors or members ask for the link; how many open it during the hour | Cheapest of the four: one optional `https://` field shown beside the time, link only. Build it when one church streams weekly |
| **Native apps** | Something the browser cannot do and the church needs: offline check-in at a camp without signal, a scanner, background location | A concrete task that failed in the browser in real use, twice | Not justified. Store accounts, reviews and two codebases for no named task |

## What to test first with the church

1. **The visitor's task, on their own phone.** Send the link by WhatsApp to five people who do not
   attend. Ask: when is the next meeting, where is it, what is the next event. Watch where they
   scroll and what they tap first. Success: all three answered in under a minute without help.
2. **What the administration publishes.** Let the church administrator fill and publish it alone.
   Note what they try to add that does not fit (photos, a pastor's name, giving, a second site),
   whether the review page made them change anything, and whether the contact phone they wanted
   to write was a personal one.
3. **Whether it is shared.** After four weeks: did the church put the link anywhere (WhatsApp
   groups, a printed sign, a social profile)? Only an aggregate count of page views per week would
   answer more, and nothing counts them today: adding one is a privacy decision for the owner.
4. **Hours as 24 h.** The page writes "19:30". Ask whether people read it at once or expect
   "7:30 p. m.".

Decide installability and the live link after step 3; push and native only if a named need appears.
