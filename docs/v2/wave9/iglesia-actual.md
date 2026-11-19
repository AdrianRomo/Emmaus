# Wave 9 — the church of the request (issues #32 and #37)

Branch `v2/wave-9-iglesia-actual`. High risk: authorization. No migration. Synthetic data only.

## What was built

**#32. An account that belongs to more than one church chooses the one it works in.**

- `POST /cuenta/iglesia/` (`core.views.switch_church`, name `switch_church`): signed in, POST only,
  CSRF. Field `church` is a church id. It goes through `core.authz.switch_church`, which accepts
  only a church of `core.authz.churches_of(user)`; anything else (another church's id, an invented
  one, text) is the same 404 and changes nothing. Optional `next`: followed only when it is a path
  of this site (`core.views._local_path`: starts with one `/`, no `//`, no backslash, no scheme or
  host); otherwise the chosen church's "Mi iglesia", or the usual home when that church does not
  use the community module.
- The session keeps one integer under `current_church`. `core.authz.church_choices(request)` (and
  `current_church`, which is its first element) reads it on every request and looks for it among the
  churches the account belongs to at that moment: a church role, or a person that was not merged
  away, in a church that is active. If it is not there the key is removed and the default answers:
  the first church role by id, else the first person by id, which is what the code did before. A
  value that is not an integer is removed too, and so is a value in the session of an account
  with no church. Being a superuser adds no church.
- Signing in removes the key (`core.authz.forget_choice` on `user_logged_in`); signing out already
  empties the session. A choice is not remembered from one sign-in to the next.
- One audit row per change, in the chosen church: `people.audit` action `iglesia_elegida`, the
  account as actor, a fixed sentence, no metadata. Choosing the church one is already in writes
  nothing.
- The switcher: one quiet button per other church, "Cambiar a «nombre»", beside "Ayuda" and "Cerrar
  sesión" in the rail and in the "Más" panel (`templates/partials/ui/shell.html`,
  `shell_end.html`), and a short list in "Mi cuenta" (`templates/core/account.html`), which is the
  only way out of a church that has no shell (module off). It is rendered only when
  `church_choices` is in the context, which `core.context_processors` sets only for an account of
  two or more churches. It is not shown on event screens.
- `core/tenancy.py`: `resolve(request)` gives the church of the request, the choices, and the
  church the legacy discipleship screens follow; `elsewhere` is true when those two differ.

**#37. One answer for the landing page, the logo and the colours.**

- `core.tenancy.current_church(request)`: signed in, the church of the request (above), else the
  church of the first church role (the legacy fallback: a role in a church that was closed); signed
  out or with no church at all, the first active church by `created_at`, as before. A session
  value without an account means nothing.
- `landing` and `core.context_processors.current_role` (`brand_church`, `theme_church`,
  `current_church`, the role shown in the shell) use it.
- Pages about a church keep that church whoever reads: `agenda/public.py` and `sitio/public.py`
  were already passing their own `brand_church`/`theme_church` and are untouched (now tested with
  readers of another church). The two event shells now pass `brand_church=event.church` to the
  Santuario shell, as the old bar always did (`brand=event.church`): before this, an event page in
  the new shell showed the logo of the reader's church.

## Decisions taken (the conservative option each time)

1. **`core/access.py` and `events/access.py` are not edited** (decision 0003, rule 7). The
   discipleship screens (`role_required` in `core/views.py`, 27 views) still act in the church of
   the account's first role. Instead of showing them under another church's name:
   while the request acts in another church (`elsewhere`), `role_required` answers 404, the shell
   does not list the discipleship links nor "Configuración", the context carries the role held in
   the church of the request (not the first role's), and `dashboard` leads to "Mi iglesia". Going
   back with the switcher restores everything. This only ever denies. **Consequence, not built:**
   an account whose first role is in church A cannot open church B's `church_settings`, users,
   paths or invitations, even as B's administrator (the criterion of #32 that decision 0003 moved
   to a later issue). "Políticas de la iglesia" and "Página pública" do follow the request, so in
   that state the shell links them directly and their "Volver a Configuración" link is not drawn.
2. **The id travels in the POST body, not in the address** (#32 suggested `/cuenta/iglesia/<slug>/`):
   one address, nothing to guess at in logs, and the same 404 for every wrong value.
3. **The audit row holds no "previous church"**: the administration of one church has no reason to
   learn which other church a person belongs to.
4. **No `next` is sent by the switcher.** The page one is on is usually an object of the church
   being left, which would answer 404 in the other one. The view supports `next` for later use.
5. **No switcher on event screens.** An event is its own place; the general links of the rail there
   are the chosen church's, as on every page.
6. **"Elsewhere" also covers an account whose first role is in a closed church** and that belongs
   to another active one: it works in the active church, and the closed church's discipleship
   screens answer 404 for it. An account whose only church is closed is unchanged.
7. The confirmation ("Ahora estás en «nombre».") is a normal message; no e-mail, no notification.

## Routes and permissions

| Route | Who | Rule |
|---|---|---|
| `POST /cuenta/iglesia/` | any signed-in account, for a church it belongs to | `core.views.switch_church` → `core.authz.switch_church` / `churches_of` |

Every other screen is unchanged in who may use it: the community views read `request.church` from
`people.decorators.module_required` → `core.authz.current_church`, and ask `has_scope` about that
church.

## Where the church does not come from the request (left as is)

- `core/access.py::get_user_role` and everything built on it: the first church role by id. Used by
  `core/views.py` (`role_required`, `dashboard`, `account`, the discipleship views) and by
  `events/views/home.py:43` (which welcome to show on the event list). Guarded as in decision 1;
  the welcome on the event list and the line "En «iglesia»: «rol»" of "Mi cuenta" still describe the
  first role.
- `templates/layout/skeleton.html` colours on event pages: `theme_church` is the reader's church
  (their chosen one), not `event.church`. The logo and the title are the event's. An account with no
  church gets no church colours on an event page. Changing it would change camp screens for event-only
  staff, so it was left and is reported.
- The general links of the Santuario rail on an event page are those of the reader's church.
- `events/access.py::administered_churches` and `accessible_events`: every church where the account
  administers, every event it may open, whatever was chosen (event scope; superuser sees all). The
  event list is therefore not filtered by the chosen church.
- `people/persons.py::persons_for(user)`: people of every church where the user holds a role, and
  all of them for a superuser. No view calls it today (only tests); a view that did would ignore the
  request's church.
- `templates/core/ayuda.html` reads `current_church.discipleship_enabled`: in the "elsewhere" state
  it describes the chosen church's module, whose screens that account cannot open there.
- `core/views.py::church_logo` (by slug, public) and the public pages (by slug): by design.

## Not built

Remembering the choice across sign-ins; a church picker at sign-in; B's settings for an account
whose first role is elsewhere; cross-church search; onboarding a second church; anything in #98.

## Not verified

- PostgreSQL and the browser gate (`--postgres --e2e`): left to the integrator.
- axe was not run on the changed screens. Looked at in a real browser (Playwright, synthetic seed
  plus a second synthetic church, 390px and 1440px): "Mi iglesia" before and after switching, the
  "Más" panel with the switcher (390px), the rail with the switcher (1440px), "Mi cuenta" after
  switching (390px; 1440px only for overflow), "Políticas" in the elsewhere state (overflow only).
  No horizontal scroll on any. Not looked at: event screens for a two-church account, the legacy bar.
- The logo on event shells for an account of another church was checked by test, not by eye.

## After review: a form opened in one church must not write into another

A form of the community modules names no church; it writes into the church of the request that
receives it. Opened in A and sent after the session moved to B, it used to write into B. Fixed in
one place, `core.authz._church_changed`, not form by form:

- The session also keeps `church_seen`, the church its last request acted in. It decides nothing.
- When the resolved church differs from it (the person switched; a role or a person ended; a church
  was closed; the default itself moved), the CSRF secret is rotated
  (`django.middleware.csrf.rotate_token`; the secret lives in the `csrftoken` cookie, shared by
  every tab), so Django refuses every form drawn before, from the next request on.
- The request that *discovers* the change has already passed the CSRF check. If it is a write, the
  resolver raises `core.authz.StaleChurch` before the view runs; `core.views.permission_denied`
  answers it with the page of an expired form ("Esta pantalla ya no está al día. Regresa,
  recárgala y repite el paso.", 403). Reading is never refused.
- The switch view resolves with `refuse_stale=False`, so somebody whose church just changed can
  still choose. Choosing the church one is already in rotates nothing.
- An account of one church never triggers any of this (tested: same cookie, old form accepted).

Left as is: one audit row per switch has no cap. `events.services.throttle` blocks for a time once
a limit is passed, and a blocked switcher (or a switch that silently is not audited) is worse than
rows that hold two ids; a member can only toggle among their own churches.
