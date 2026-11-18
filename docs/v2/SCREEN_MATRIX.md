# Screen migration matrix

**Date:** 2026-11-17. One row per screen for the community modules, one per family for the camp
(its screens are listed one by one, per role, in `e2e/all_urls.py` and described in
`docs/design-system.md` §4). Counts are template files that are pages (partials excluded).

**Status words.** **Redesigned**: rebuilt on Santuario's components and in the shell.
**In the shell**: wears the shell and the tokens; its content is still Atrio's layout.
**Tokens only**: keeps Atrio's bar; colours and the title voice are Santuario's.
**Unchanged**: served as before (module off, or not reached by `data-ui`).

**Coverage words.** *Unit*: Django tests. *e2e-C*: `community-flow.mjs`, modules on, axe + overflow +
one `h1` + 44 px targets at 390/768/1440. *e2e-R*: `review-all.mjs`, modules **off**, the same checks
at three widths. "Visual" names what was looked at as a rendered screenshot in this round.

## Community and people (module on; these screens do not exist with it off)

| Screen | Route | For | Status | Components | Unit | Browser | Visual |
|---|---|---|---|---|---|---|---|
| Mi iglesia | `/mi-iglesia/` | Every person of the church | Redesigned; shows notices, the devotional, published events with sign-up, resources | hero, sign--lead, dates, mono, camino, quiet, cols, meeting | `test_journey`, `test_shell` | e2e-C | 390, 768, 1440 |
| Mis datos | `/mi-iglesia/mis-datos/` | Every person | New | Atrio form | `test_shell::MyDataTests` | e2e-C | — (gate only) |
| Grupos | `/mi-iglesia/grupos/` | Every person | Redesigned | filters, row--lead, mono | `test_views`, `test_shell::GroupListTests` | e2e-C | 390, 1440 |
| Un grupo | `/mi-iglesia/grupos/<id>/` | Every person | Redesigned | group-head, sign--lead, meeting, summary-list | `test_views`, `test_journey` | e2e-C | 390, 1440 |
| Personas del grupo | `…/<id>/personas/` | Guide, administration | Redesigned | work head, filters, row--attention, row__contact | `test_views`, `test_shell` | e2e-C | 390, 768, 1440 |
| Reuniones del grupo | `…/<id>/reuniones/` | Guide, administration | New | work head, sign, dates | `test_meetings` | e2e-C | 390, 1440 |
| Pasar lista | `…/<id>/reuniones/<día>/` | Guide, administration | New | choices (multi) | `test_meetings` | e2e-C | 390 |
| Salir / quitar / archivar | `…/salir/`, `…/quitar/`, `…/archivar/` | Member, guide, administration | In the shell | Atrio | `test_views` | — | — |
| Administrar los grupos | `/mi-iglesia/grupos/administrar/` | Administration | In the shell | Atrio rows | `test_views` | e2e-C | 390, 1440 |
| Crear / editar un grupo | `…/nuevo/`, `…/editar/` | Administration | In the shell | Atrio form | `test_views` | e2e-C (crear) | — |
| Avisos (lista, un aviso) | `/mi-iglesia/avisos/…` | Every person | New | rows, status, prose | `notices/tests`, `test_shell::HomeModulesTests` | e2e-C | 390, 1440 |
| Publicar, editar, retirar un aviso | `…/avisos/nuevo/`, `…/editar/`, `…/retirar/` | Administration; a group's guide for their group | New | Atrio form | `notices/tests` | e2e-C (nuevo) | 1440 (nuevo) |
| Eventos (lista, un evento) | `/mi-iglesia/eventos/…` | Every person | New | dates, sign--lead, summary-list | `agenda/tests`, `test_shell::HomeModulesTests` | e2e-C | 390, 1440 |
| Eventos de la iglesia, página del evento, inscritos y asistencia | `…/eventos/administrar/`, `…/pagina/`, `…/inscritos/` | Administration | New | rows, Atrio form, choices | `agenda/tests` | e2e-C | 390 (página), 1440 |
| Devocional (de hoy, anteriores, uno) | `/mi-iglesia/recursos/devocional/…` | Every person | New | prose, dates | `resources/tests`, `test_shell` | e2e-C | 390 |
| Recursos | `/mi-iglesia/recursos/` | Every person | New | rows, filters | `resources/tests` | e2e-C | 1440 |
| Devocionales y recursos (administrar, escribir, agregar, retirar) | `…/devocional/administrar/`, `…/recursos/administrar/`… | Administration | New | rows, Atrio form | `resources/tests` | e2e-C | 1440 (devocionales) |
| Planes de lectura (lista, un plan, un día, dejar) | `/mi-iglesia/recursos/planes/…` | Every person; a retired plan only who had started it | New (issue #84) | rows, status, prose, pager | `resources/tests/test_plans.py` | e2e-C (empty list) | 390, 1440 |
| Planes de lectura (administrar, escribir, días, publicar con confirmación, retirar) | `…/planes/administrar/`, `…/planes/<id>/dias/`, `…/publicar/` | Administration | New (issue #84) | rows, Atrio form, notice--quiet | `resources/tests/test_plans.py` | e2e-C (lista, nuevo) | 390, 1440 |
| Servir (mis fechas, mis equipos, días que no puedo) | `/mi-iglesia/servir/` | Who is in a team | New | dates, row--attention, mono | `serving/tests` | e2e-C | 390, 1440 |
| Un equipo, una fecha, agregar una fecha | `…/servir/equipos/<id>/`, `…/fechas/…` | Who coordinates it, administration | New | work head, dates, choices | `serving/tests` | e2e-C (equipo) | 390, 1440 (equipo) |
| Equipos de la iglesia (crear, editar, archivar, personas) | `…/servir/equipos/…` | Administration | New | rows, Atrio form | `serving/tests` | e2e-C | 1440 |
| Personas que acompañas, un seguimiento | `/mi-iglesia/acompanar/…` | The follow-up's owner | New | mono, summary-list, choices | `care/tests` | e2e-C | 390, 1440 |
| Seguimiento de personas (todos, nuevo, editar, cancelar) | `…/acompanar/todos/`, `…/nuevo/`… | Administration | New | filters, rows, choices | `care/tests` | e2e-C | 390 (nuevo), 1440 (todos) |
| Panorama | `/mi-iglesia/panorama/` | Church pastor, administration | New (wave 4) | figures, table--stack | `overview/tests` | e2e-C | 390, 1440 |
| Tu asistencia (en la página del grupo) | `/mi-iglesia/grupos/<id>/` | The member, about themselves | New (wave 4) | rows | `groups/tests/test_own_attendance` | — (agent: axe at 390, 768, 1440) | 768 (agent) |
| Crear un evento, datos del evento | `/mi-iglesia/eventos/nuevo/`, `…/<slug>/datos/` | Administration | New (wave 4) | Atrio form | `agenda/tests/test_simple` | e2e-C | 390, 1440 (list) |
| Preguntas frecuentes, programa (administrar) | `…/<slug>/preguntas/`, `…/programa/` | Administration | New (wave 4) | rows, Atrio form | `agenda/tests/test_event_extras` | e2e-C | agent: 390, 1440 |
| Registrar a alguien que llegó, autorización, agregar a personas | `…/<slug>/inscritos/llego/`, `…/autorizacion/`… | Administration | New (wave 4) | choices, Atrio form | `agenda/tests/test_event_extras` | e2e-C (llegó) | 1440 (inscritos); agent: 390 |
| Opinar de un evento, gracias | `/mi-iglesia/opiniones/<slug>/` | Who went | New (wave 4) | Atrio form | `feedback/tests` | — | — (loaded at 390, 1440, not looked at) |
| Opiniones del evento | `…/opiniones/<slug>/resultados/` | Administration | New (wave 4) | summary-list, rows | `feedback/tests` | e2e-C | — (loaded, not looked at) |
| Aviso con nivel, «Enterado», compartir por WhatsApp | `/mi-iglesia/avisos/<id>/` | Every person; counts for who published | Changed (wave 4) | notice, status | `notices/tests/test_levels_and_sharing` | — (agent: axe at 390, 1440) | 390 (home, by me); agent: detail |
| Avisos por correo (preferencias); correos enviados en el detalle de un aviso | `/mi-iglesia/avisos/correo/`, `/mi-iglesia/avisos/<id>/` | Every person (their own choice); counts for who published | New (wave 6) | Atrio form, notice | `notices/tests/test_email` | — (agent: axe at 390, 1440) | agent: 390, 1440 |
| Personas (lista, ficha, alta, editar) | `/mi-iglesia/personas/…` | Administration | In the shell | Atrio | `test_people_views` | e2e-C | 390, 1440 (lista, ficha) |
| Traer personas de un evento | `…/desde-un-evento/…` | Administration | In the shell | Atrio | `test_people_views` | e2e-C | — |
| Dar acceso, enlace | `…/acceso/…` | Administration | In the shell | Atrio | `test_people_views` | — | — |

## Church pages on `base.html` (30 discipleship and account templates, 6 sign-in, 5 errors)

| Family | Routes | For | Module on | Module off | Unit | Browser | Visual |
|---|---|---|---|---|---|---|---|
| Mi cuenta, contraseña | `/cuenta/…` | Everyone signed in | In the shell | Unchanged | `core/test_account.py` | e2e-C (cuenta), e2e-R | 1440 |
| Ayuda | `/ayuda/` | Everyone signed in | In the shell; community tasks rewritten for it | Unchanged | `core/` | e2e-R | — |
| Campamentos y eventos | `/eventos/` | Event staff | In the shell ("Eventos") | Unchanged | `events/tests` | e2e-R | 1440 |
| Discipulado (panorama, participantes, rutas, preguntas, seguimientos, notas, invitaciones, mentor, participante, lecciones) | `/church/…`, `/mentor/…`, `/participant/…` | Pastor, administration, mentor, participant | In the shell, under "Discipulado" | Unchanged | `core/test_*` | e2e-R (module off only) | **Not looked at with the module on** |
| Configuración de la iglesia, usuarios | `/church/settings/`, `/church/users/` | Administration | In the shell | Unchanged | `core/` | e2e-R | — |
| Entrar, contraseñas, portada, `/unirme/`, errores | public | Anyone | Tokens only (no shell when signed out) | Unchanged | `core/test_landing.py` | e2e-R | 1440 (entrar) |
| Página pública de la iglesia (prototype, #87) | `/iglesia/<slug>/` | Anyone | Redesigned, no shell and no bar by design | New | `sitio/tests/test_public_home.py` | By hand with axe (not in e2e-C yet) | 390, 768, 1440, with and without theme and logo |
| Página pública: editar, horario, revisar, vista previa | `/mi-iglesia/pagina-publica/…` | Administration | In the shell (the preview has none) | New | `sitio/tests/test_public_home.py` | By hand with axe (not in e2e-C yet) | 390, 1440 |

## The camp (55 templates under `events/`)

"Module on" is for an account with a church role in a church that uses the module; an account
with only an event role, and every church without the module, get the "module off" column.

| Family | Shell | For | Module on | Module off | Coverage |
|---|---|---|---|---|---|
| Administración del evento (19) | `shell_leader` | Administrator | In the shell: the event is a place in the rail | Unchanged | Unit (`test_shell`); e2e-R off and on; visual 390, 1024, 1440 of the home |
| Pastor (2), supervisión (7), reportes (2) | `shell_leader` | Pastor, leaders | In the shell | Unchanged | Unit; e2e-R off and on; not looked at one by one |
| Coordinador (4), tutor (1) | `shell_coordinator`, `shell_leader` | Event roles | In the shell when the account is also a person of the church; else the bar | Unchanged | Unit; e2e-R off and on (in the seed these accounts are not people of the church, so "on" shows them the bar); visual, by hand with a person added: the coordinator's home at 390, 1024, 1440, "Mis camperos" and the tutor's home at 390 and 1440 |
| Entrevista (10), campero (6) | `shell_focus`, `shell_kiosk` | Coordinator, campero | Tokens only: no navigation by design | Unchanged | Unit; `station-flow.mjs`; not looked at with the module on |
| Presentación (1) | skeleton | Leaders, on a projector | Tokens only; the deck keeps Figtree | Unchanged | Unit; e2e-R |
| Unirse con código (3) | `base.html` | Anyone | Tokens only | Unchanged | Unit; e2e-R |

"e2e-R on" is the second pass of `review-all.mjs` that `scripts/agents/e2e-gate.sh` now runs after the
community seed has turned the modules on: the same 243 page/width checks (axe, overflow, one `h1`,
stylesheet, JavaScript errors), with the redesigned shell on every screen of the church-role accounts.

## Waves

1. **Done.** Tokens, shell, Mi iglesia, groups, the guide's work, meetings.
2. **Done.** The camp inside the shell; "Eventos" as the word; a modules-on review of every screen.
   Left from it: look at the interview and the campero's screens with the module on, and add a
   coordinator who is also a person of the church to the seed so the automated review sees it too.
3. **Done in a first version.** Avisos, events for people (sign-up, attendance), the devotional of
   the day, resources, serving (teams and dates), accompanying (follow-ups of people).
3b. **Then, with each module.** Atrio's tables, figures, charts and reports get their Santuario
   treatment when the pastor's view of groups and events is built (issue #66).
4. **Last.** When every church is on the new modules, fold `santuario.css` into `rotulo.css` and
   retire the bar. Until then the split is by flag, not by screen: a church sees one system.
