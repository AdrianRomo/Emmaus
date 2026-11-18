# Redesign checklist: "se entiende sin leer"

Branch `v2/redesign-claridad` (from `v2/foundation` at 519e738), worktree
`../Emmaus.worktrees/redesign-claridad`. Started 2026-11-18. The directive: a product-wide UI/UX
redesign whose first rule is that unnecessary explanatory copy is a defect. Design system:
`DESIGN_SYSTEM.md` (Santuario, second edition). This file is the working checklist; keep it current.

Baseline measured before any change (synthetic seed, text inside `<main>`):
145 page heads, 120 of them with a lede sentence; 94 `aside-note` paragraphs; 162 `empty-line`
sentences; 60 `help_text` in forms; "Mi iglesia" 377 words; Panorama 628; an event's page form 396.

## How to run

```bash
P=/home/adrian/projects/Emmaus/.venv/bin/python
export DEBUG=true DATABASE_URL=sqlite:////<scratch>/dev.sqlite3
$P manage.py migrate && $P manage.py seed_camp_demo --password DemoCamp-2026 \
  && $P manage.py seed_dev_data --password DemoCamp-2026 \
  && $P manage.py seed_community_demo --password DemoCamp-2026 --panorama --eventos
$P manage.py runserver 127.0.0.1:8791
```

## Direction (decided 2026-11-18, after two rejected passes)

1. Card dashboard on cream (first pass): rejected by the owner as generic.
2. "Paper, not cards" (bare rows): rejected as a flat productivity list.
3. Three directions built by separate designers (A typographic poster, B «Ventanas», C picture book),
   compared by an independent critic against the build: **B chosen**, refined twice with the owner's
   notes and a second critic, then built into the product. Prototypes and captures:
   scratchpad `explore/` (not in the repository).

## Phases

- [x] A. Discovery
- [x] B. Foundation: «Ventanas» tokens, type, shell, components, motion; CRAFT.md; actions in place
- [x] B2. Representative screens: Mi iglesia (member, guide) and the coordinator's workspace, with the
  serving interaction verified in a browser (busy, offline failure, confirmed, changed)
- [ ] C. Every surface, by area, on the new system
- [ ] D. Visual review at 360 / 390 / 820 / 1440, fixes
- [ ] E. Consistency pass
- [ ] F. Gates: ruff, Django checks, pytest, e2e (axe), deploy check

## Areas

Status: `todo`, `wip`, `done` (implemented on «Ventanas», tests green, looked at in the browser).

| Area | Templates | Status | Notes |
|---|---|---|---|
| Foundation (CSS, JS, shell, partials) | `static/`, `partials/ui`, `core/shell.py` | done | |
| Mi iglesia | `groups/home.html` | done | Reference screen |
| Coordinator home | `events/coordinator/home.html`, `_home_live.html` | done | Reference screen |
| Grupos (member, guide, administration), Mis datos | `groups/` | todo | Copy work from the earlier pass is merged |
| Eventos for people, public event page | `agenda/` | todo | |
| Eventos administration (page, inscritos, programa, preguntas, menores) | `agenda/` | todo | Consent wording untouched |
| Avisos | `notices/` | todo | «Enterado» in place |
| Devocional, recursos, planes de lectura | `resources/` | todo | «Marcar como leído» in place |
| Servir | `serving/` | todo | |
| Acompañar | `care/` | todo | |
| Personas | `people/` | todo | |
| Panorama | `overview/` | todo | Privacy thresholds untouched |
| Opiniones, políticas, página pública | `feedback/`, `policies/`, `sitio/` | todo | |
| Cuenta, entrar, contraseñas, portada, unirme, errores, ayuda | `registration/`, `errors/`, `core/` | todo | Shared with module-off |
| Configuración de la iglesia, usuarios | `core/` | todo | Shared |
| Discipulado | `core/` | todo | Shared |
| Evento: administración | `events/admin/` | todo | Shared |
| Evento: coordinador (rest), tutor, pastor, supervisión, reportes | `events/` | todo | Shared |
| Evento: entrevista y campero | `events/interview/`, `events/kiosk/` | todo | Privacy and consent text stays |

## Decisions taken (defaults; the owner may change them)

1. The redesign stays a layer under `<html data-ui="2">`; module-off output is byte-identical.
2. Consent, guardian authorization, privacy notices and irreversible-action wording are not shortened.
3. Members never see who signed up for an event (the list is the administration's); places left only
   where the event page already shows them. B's "Van Andrea, Diego y 10 más" is not built.
4. No event photos until issue #60 is decided: the arch shows the sky of the hour; the slot is ready.
5. Accounts that only work in events wear the shell of the event's church.
6. No push, no pull request, no deploy without the owner's word.
