# Wave 9 — pastoral approval of content before it is published (issue #99)

Decision 0014 says pastoral and content leadership approves authorship and rights. Until this wave a church
administrator's checkbox published a reading plan, and devotionals and the public home were published by the
administration alone. This slice separates the two responsibilities.

## What was built

- **Two responsibilities.** *Preparing* content stays with the church's administration (`church_admin`).
  *Approving it for publication* belongs to a person with the church role **pastor** in that same church
  (`resources.approvals.can_approve`: the `UserChurchRole` table that already answers `core.authz.has_scope` and
  `overview.access`). A church role is one row per account and church, so no administrator is also a pastor:
  the administration can prepare and ask, never publish. An event role named "Pastor" grants nothing.
- **One record, `resources.ContentApproval`** (migration `resources/0003`): church, `kind` + `object_id`, state,
  who asked and when, the fingerprint (SHA-256) of the approved words, approver, `approved_at`, permitted use
  (`propia` obra propia de la iglesia / `permiso` con permiso del autor / `abierta` dominio público o licencia
  abierta), a short source note (200), and for a return: who, when and the reason (300). It has no reference to a
  reader, no count and nothing from the progress tables.
- **Kinds**: devotionals and reading plans (`resources/approvals.py`), and the public home (`sitio/approval.py`,
  registered in `SitioConfig.ready`). The registry (`approvals.register`) is where #95 would add songs.
- **Only `approvals.approve` publishes.** The administration's "publish" steps now ask: the devotional's box
  ("Pedir la aprobación pastoral para publicarlo"), the plan's page (`planes/<id>/publicar/`, same address) and
  the public home's review page (`pagina-publica/publicar/`). `plans.publish_plan` and `sitio.services.publish`
  no longer exist; the plan's old confirmation checkbox is gone.
- **The pastor's screens** (`resources/approval_views.py`): the list "Aprobación pastoral" (what waits, what was
  returned, and "Publicado antes de que existiera la aprobación": what is published with no approval on record), and one page
  per item with the whole text, "Aprobar y publicar" (permitted use, optional note) and "Devolver sin aprobar"
  (a reason). The administration opens the same pages read-only. The pastor has a link in "A tu cargo" with the
  number of pieces of content waiting (content, never people).
- **For who prepared it**: the state is on the administration's own lists and forms ("Por aprobar", "Devuelto"
  with the reason, "Fuera de publicación"). No e-mail is sent anywhere in this slice.
- **Seed**: `seed_community_demo` leaves its demo content published with the "published before" mark (it stands
  for a church that used Emmaus before this step); `--panorama` adds tomorrow's devotional waiting for the pastor.

## Product decisions taken (the owner delegated them)

1. **A material edit takes the item out of publication until a pastor approves it again.** Emmaus keeps no copy
   of the approved words, so the approved version cannot be served cheaply while another text waits; storing a
   copy of every approved body was judged more than this slice should add. Every editing screen says so *before*
   saving, and names what readers stop seeing. *Cost the owner should know*: correcting a typo in a published
   plan hides it (its readers keep their marks and find them again after the approval). On the public home the
   rule applies to the welcome only (decision 2, changed at integration).
2. **What is material**: devotional: title, passage, body, who signs (the day is not: moving it keeps the
   approval). Plan: title, description, author line and every day's position, title, reference, reflection and
   question (adding or removing a day too). Public home: **only the welcome** (owner's decision at integration, replacing the builder's
   wider set). The first publication is a pastor's approval of the whole page as it stands (the form's
   fingerprint covers everything listed); afterwards the times, the address, the map link and the phone are
   facts the administration keeps current: they change at once, never take the page offline and need no new
   approval. `ContentApproval.approved_digest` of a home is the fingerprint of its welcome
   (`sitio.services.authored_digest`). Saving the same words is not a change.
3. **The public home's events and resources are not part of the approval.** They have their own publication
   steps and keep appearing or leaving by themselves, as before. The pastor's preview lists them and the form's
   fingerprint covers them, so an approval of a list that changed meanwhile is refused.
4. **Media links need no approval of rights**: a resource is an address on a site the church does not host.
   `save_media` and "Mostrar en la página pública" are unchanged. The list says so.
5. **No active pastor account**: asking still works and waits; nothing new is published; the administration's
   screens say why and what to do. The links to "Invitar a alguien" and "Usuarios" are offered only when they
   open for this account in this church (those legacy screens act in the church of the account's first role;
   `resources/templatetags/approvals.py::team_screens_open` reuses `core.tenancy.Resolution.elsewhere`, the rule
   by which the shell hides "Configuración"); otherwise the notice says in words what to do. No fallback to the
   administration, in the view or in the service.
6. **Existing published content stays published.** Migrations `resources/0004` and `sitio/0002` mark it
   (`state="anterior"`, no approver, no date, reversible). Anything published with no row at all (a seed, a shell)
   is listed the same way with the words "no hay una aprobación registrada". A pastor approves item by item.
7. **A pastor cannot unpublish from the list**: for something already published the only action is to approve;
   taking it down stays with the administration (the page says so). Fewer powers, on purpose.
8. **Asking again is explicit** for plans and the public home (the item shows "cambió: necesita aprobación");
   for a devotional the box on the same form asks. A pastor is never handed something half edited by accident.
9. **Withdrawing**: unticking the devotional's box or retiring a plan withdraws an open request. Taking an
   approved item back to a draft keeps the record of its approval; publishing it again still goes through a pastor.
10. **Plans**: a plan that was ever published keeps its days even as a draft (deleting one would delete readers'
    marks), and a plan whose words changed cannot be retired until it is approved (retiring would show the new
    words to who had started it). `rights_confirmed_by/at` now hold the pastor who approved and when.
11. **The pastor reads a draft only through the preview**, and only when something was asked or decided about it:
    a draft nobody asked about is a 404 for the pastor.
12. **Audit**: one row per act (`contenido_aprobacion_pedida`, `contenido_aprobacion_retirada`,
    `contenido_aprobado`, `contenido_devuelto`, `contenido_fuera_de_publicacion`), with the object's type and id
    and the permitted use (a fixed word). Never a title, the reason or the note.

13. **Series on the public home** (integration): of a public weekly series the page, the review list and the
    fingerprint carry only the next date, with "Se repite cada …" (`agenda.series.collapse` in
    `sitio.services.public_events`); a date that was called off is not listed.

14. **The facts of the public home cannot carry a text** (review at integration). Because they change with
    no pastor, they are kept plain, in the service and in the form: the address is one line of up to 160
    characters (line breaks become spaces); a time's name up to 40 and its place up to 80, one line each; at
    most 12 times per church; the map link only `https://` to a map site (`maps.app.goo.gl`, `goo.gl/maps`,
    `google.com/maps`, `maps.google.com`, the `.com.mx` forms Google gives in Mexico, OpenStreetMap,
    `maps.apple.com`, Waze). No data migration: what was written before keeps showing and can be edited or
    removed; the limit holds from the next save and the error says it is new. 40 characters fits a bilingual
    name such as "Reunión general / Sunday service".
15. **What the screen says after asking** is what happens: the request waits in the pastors' list and nobody is
    sent anything; with no active pastor account it says that nobody can approve it (a warning).

## Known limit: who appoints the pastor

A church administrator can invite a person with the role pastor (`core/forms.py`, `InvitationForm`), and change
roles in Usuarios. So the separation of duties holds only while the administration does not appoint itself or
an account it controls: it is an organisational control, not a technical one. What the software gives is the
trail: the approval record names who approved, and the invitation and role rows show who granted the role.
Closing it (a second pastor, or the owner, confirming a new pastor) is a later decision, not built here.

## Routes and permissions

| Route (under `/mi-iglesia/recursos/`) | Who | Rule |
|---|---|---|
| `aprobaciones/` (`approval_queue`) | pastor or administration of the church | `approval_views.approvals_view` |
| `aprobaciones/<kind>/<id>/` (`approval_review`) | the same | `approvals_view`; 404 outside the church, for an unknown kind, or a draft nobody asked about (`_found`) |
| `aprobaciones/<kind>/<id>/aprobar/` (POST) | pastor of the church | `approval_views.pastor_view`, and again `approvals._for_decision` |
| `aprobaciones/<kind>/<id>/devolver/` (POST) | pastor of the church | the same |
| existing writing routes of devotionals, plans and `pagina-publica/` | administration | `church_admin_view`, unchanged |

All behind the `community` module (404 when off). Anybody else: 403; signed out: the sign-in.

## Rollout and rollback

Migrations are additive (one new table, two reversible data marks). Before deploying, each church needs at least
one account with the role pastor, or nothing new can be published. Rollback: revert the code and migrate
`resources` to `0002` and `sitio` to `0001`; published content was never touched by the migrations.

## Not built

Music and song libraries (#95); importing any Bible or third-party text; any change to who sees reading
progress; e-mail or notice to the pastor or the preparer; a history of past approvals beyond the audit log (the
row holds the last one); bulk approval; a stored copy of the approved version; approval of event pages, notices
or media links.

## Not verified

- PostgreSQL: the suite ran on SQLite only (the integrator runs `--postgres --e2e`). The locks are
  `select_for_update` on the object and then its row, in that order everywhere; the public home's uses `of=("self",)`.
- Real concurrency: races are tested with stale copies and repeated decisions, not with two connections.
- The browser gate `e2e/community-flow.mjs` was not run or edited. By reading it: it publishes nothing through a
  screen and uses none of the words that changed, and the seed still leaves the same content published.
- The screens were looked at with a scratch Playwright script (390 and 1440 px, axe, horizontal scroll) on the
  synthetic seed; see the builder's report for which ones were looked at by eye.
