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
| Mi iglesia | `/mi-iglesia/` | Every person of the church | Redesigned | hero, sign--lead, dates, mono, camino, quiet, cols | `test_journey`, `test_shell` | e2e-C | 390, 768, 1440 |
| Mis datos | `/mi-iglesia/mis-datos/` | Every person | New | Atrio form | `test_shell::MyDataTests` | e2e-C | — (gate only) |
| Grupos | `/mi-iglesia/grupos/` | Every person | Redesigned | filters, row--lead, mono | `test_views`, `test_shell::GroupListTests` | e2e-C | 390, 1440 |
| Un grupo | `/mi-iglesia/grupos/<id>/` | Every person | Redesigned | group-head, sign--lead, meeting, summary-list | `test_views`, `test_journey` | e2e-C | 390, 1440 |
| Personas del grupo | `…/<id>/personas/` | Guide, administration | Redesigned | work head, filters, row--attention, row__contact | `test_views`, `test_shell` | e2e-C | 390, 768, 1440 |
| Reuniones del grupo | `…/<id>/reuniones/` | Guide, administration | New | work head, sign, dates | `test_meetings` | e2e-C | 390, 1440 |
| Pasar lista | `…/<id>/reuniones/<día>/` | Guide, administration | New | choices (multi) | `test_meetings` | e2e-C | 390 |
| Salir / quitar / archivar | `…/salir/`, `…/quitar/`, `…/archivar/` | Member, guide, administration | In the shell | Atrio | `test_views` | — | — |
| Administrar los grupos | `/mi-iglesia/grupos/administrar/` | Administration | In the shell | Atrio rows | `test_views` | e2e-C | 390, 1440 |
| Crear / editar un grupo | `…/nuevo/`, `…/editar/` | Administration | In the shell | Atrio form | `test_views` | e2e-C (crear) | — |
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

## The camp (55 templates under `events/`)

| Family | Shell | For | Module on | Module off | Coverage |
|---|---|---|---|---|---|
| Administración del evento (19) | `shell_leader` | Administrator | Tokens only | Unchanged | Unit; e2e-R; visual 390 and 1440 of the home with the module on |
| Coordinador (4), tutor (1), pastor (2), supervisión (7) | `shell_coordinator`, `shell_leader` | Event roles | Tokens only | Unchanged | Unit; e2e-R; **not looked at with the module on** |
| Entrevista (10), campero (6) | `shell_focus`, `shell_kiosk` | Coordinator, campero | Tokens only | Unchanged | Unit; `station-flow.mjs`; **not looked at with the module on** |
| Reportes (2) | `shell_leader` | Leaders | Tokens only | Unchanged | Unit; e2e-R |
| Presentación (1) | skeleton | Leaders, on a projector | Tokens only; the deck keeps Figtree | Unchanged | Unit; e2e-R |
| Unirse con código (3) | `base.html` | Anyone | Tokens only | Unchanged | Unit; e2e-R |

## Waves

1. **Done.** Tokens, shell, Mi iglesia, groups, the guide's work, meetings.
2. **Next.** The camp inside the shell: an event as a place in the rail with its own sub-navigation
   (the role's links that the bar holds today), so entering an event no longer changes the frame
   (finding R13). The bar's byte-level tests stay for the module-off path. Then look at every camp
   and discipleship screen with the module on and add them to a modules-on run of `review-all.mjs`.
3. **Then, with each module.** Atrio's tables, figures, charts and reports get their Santuario
   treatment when events for people (P3-8) and the pastor's view of groups are built.
4. **Last.** When every church is on the new modules, fold `santuario.css` into `rotulo.css` and
   retire the bar. Until then the split is by flag, not by screen: a church sees one system.
