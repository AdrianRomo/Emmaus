# Wave 9: Espacios y equipo (issue #94, first slice)

Rooms and equipment a church lends to its own activities, who asked for each one and when. New app
`espacios`. The word "recursos" stays with the media library (`resources`); the UI says "Espacios",
"Salones y equipo" and "reserva".

## What was built

- `Space`: church, name (unique per church), kind (salón / equipo), optional capacity, notes for the
  administration, `active`, `needs_approval` (default on).
- `Booking`: space, title, `starts_at`, `ends_at`, `setup_minutes`, `teardown_minutes`,
  `blocked_from` / `blocked_until` (the use plus both buffers, written by `save()`), `requested_by`
  (a person), status (solicitada / aprobada / rechazada / cancelada), `decided_by`, `decided_at`,
  `decision_reason`, `overrode_clash`, optional `event` of the same church.
- Screens: "Espacios" (list, plus "Lo que pediste" for who may ask), one week of a space as a list by
  day, the request form, the administration's "Solicitudes de reserva" (por decidir / ya decididas),
  the clash page, the rejection form, "Administrar espacios y equipo" with its form, and "Uso de los
  espacios".
- Shell: "Espacios y equipo" under Administración with the number of requests to decide; "Espacios"
  under "A tu cargo" for who may ask, only once the church has an active space.
- `tenant_integrity`: `booking_requester_church` and `booking_event_church`.
- `seed_community_demo`: two spaces and three bookings, looked up by space and title.
- One migration, `espacios/0001_espacios_y_reservas` (CreateModel ×2, constraints, indexes). Nothing
  in `people`, `core` or `events` migrations.

## The clash rule

Two bookings of one space that are solicitada or aprobada never share a minute, counting setup and
teardown. Touching ends are allowed. A request holds its hours while it waits.

Enforced in `espacios/services.py` only: inside one `atomic()`, `select_for_update` on the **space**
row, then the overlap query, then the write. Lock order is always space, then booking.

**There is no database constraint.** PostgreSQL's exclusion constraint over (space, time range) needs
the `btree_gist` extension for the equality on the space; the brief forbids a new extension and
`RunSQL`. So the lock is the only guard: any future code that creates or moves a booking must go
through `_locked_space`. A hand-written `QuerySet.update()` or the Django admin could create an
overlap (the app registers no admin). The override also makes overlaps legitimate, so a constraint
would have needed a carve-out anyway.

Simultaneous requests are tested without a second connection: the rival's booking is committed at the
moment the lock is taken, and the request must then be refused; another test asserts the lock is
taken before the overlap is read in both writes that can take hours. Real concurrency on PostgreSQL
was not run.

## Decisions taken (the owner delegated them)

| Decision | Why |
|---|---|
| Every space is exclusive; there is no "shared" space | The brief speaks of exclusive spaces only; a flag can be added later |
| No sede on a space | `events.Sede` exists, but it would add a third tenant link and a filter nobody asked for; additive later |
| Who may ask: administration, church pastor, who guides an active group, who coordinates an active team | The brief's minimum. An event role, a mentor role or plain membership gives nothing |
| Plain members may read the list and the weeks but get no link in the navigation | The brief lets them see; the member side stays uncluttered. They reach it only by its address |
| Title, who asked and the linked event: only the administration and who asked. Everybody else reads "Ocupado" and the blocked hours | A title can be pastoral. Pastors are not "staff" here: conservative |
| "Por decidir" and "aprobada" look the same ("Ocupado") to other readers | Less shown |
| The reason of a rejection is read only by the administration, not by who asked | The brief says staff-visible; the screen tells the administrator to tell the person |
| The administration's own bookings are approved at once | It does not ask itself; `decided_by` records who |
| A space with `needs_approval` off approves at once for anybody who may ask | That is what the switch means; audited with `source: directa` |
| Override: only the administration, at booking or at approval, and only by sending the ids of the bookings it was shown; if the set changed, the list comes back | "Only after seeing the clash listed", enforced in the service, not by the template |
| Linking an event: only the administration, among the church's events in preparation or in progress | Other requesters would otherwise read unpublished events in a select |
| A booking is one day in the form and at most 24 hours in the service; it must start in the future | Keeps the phone form to one date and two hours |
| Buffers are a fixed list (0 to 120 minutes); the model allows up to 240 | A select reads better on a phone than a number |
| Spaces are switched off, never deleted; bookings keep `PROTECT` on the space | History and the usage report stay true |
| `requested_by` is `SET_NULL` | Removing a person must not be blocked by, or delete, a room's bookings |
| A booking that ended cannot be cancelled | The usage report counts it |
| Usage report: approved bookings only, hours cut at the period's edges, at most 366 days, no people | The brief; minimum reporting size does not apply (no count of people) |
| Somebody else's booking id answers 404 to a non-administrator, not 403 | Does not confirm it exists |
| Audit rows: fixed sentences, `space_id`, the booking as the object; never the title, the space's name or the reason | Invariant on free text |
| No e-mail, no notice: every screen says Emmaus tells nobody | Nothing is promised that the code does not do |
| Hours are the church's (`Church.time_zone`), computed in Python and rendered as plain times | A template cannot shift them to the server's zone |

## Routes (all under `/mi-iglesia/espacios/`, all signed-in, all 404 with the module off)

| Route | Name | Who |
|---|---|---|
| `` | `espacios:list` | Every person of the church |
| `<space>/` | `espacios:week` | Every person of the church (a space switched off: administration only) |
| `<space>/pedir/` | `espacios:request` | Who may ask (`espacios.access.can_request`), else 403 |
| `reservas/<id>/cancelar/` | `espacios:cancel` | Who asked, or the administration; else 404 |
| `solicitudes/` | `espacios:queue` | Administration |
| `reservas/<id>/aprobar/` (POST) | `espacios:approve` | Administration |
| `reservas/<id>/rechazar/` | `espacios:reject` | Administration |
| `administrar/`, `administrar/nuevo/`, `administrar/<space>/editar/` | `espacios:manage`, `space_create`, `space_edit` | Administration |
| `uso/` | `espacios:usage` | Administration |

Permission rules: `groups.views.community_view` and `church_admin_view` (module, person, church
`manage` through `core.authz.has_scope`); `espacios/access.py::can_request` and `can_read_details`;
`espacios/views.py::_shown` removes the title, the person and the event from the object for other
readers.

Never public: no anonymous route, nothing added to `sitio` or `agenda.public`, pages carry the
shell's `noindex`.

## Not built

- Recurring bookings (a later slice, with event series).
- Editing a booking's hours (cancel and ask again).
- Booking questions, shared (non-exclusive) spaces, sedes, a database exclusion constraint.
- Any notification to who asked or to the administration.
- A link for plain members; a booking shown on an event's page; the usage report by event.
- Django admin registration.

## Rollout and rollback

Additive: one new app with its own tables behind the existing `community` flag. A church with no
space sees one new link, for its administration only. Rollback: revert the commit; the two tables can
stay unused (or `migrate espacios zero`, which drops only them).

## Could not verify

- PostgreSQL: the suite ran on SQLite only (the integrator runs `--postgres --e2e`). The locks use
  `select_for_update()` on the space (no nullable join) and `of=("self",)` on the booking.
- Two real concurrent connections.
- Screen readers; only axe and the keyboard-neutral checks ran.
