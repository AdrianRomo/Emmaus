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

## Phases

- [x] A. Discovery: skills, repository, references, screenshots of every screen (before)
- [ ] B. Foundation: tokens, type, shell, components, motion, icons, feedback (toasts, progress)
- [ ] C. Every surface, by area (table below)
- [ ] D. Visual review at 390 / 820 / 1440, fixes
- [ ] E. Consistency pass: words, components, leftovers
- [ ] F. Gates: ruff, Django checks, pytest, e2e (axe), deploy check

## Areas

Status: `todo`, `wip`, `done` (implemented, tests green, looked at in the browser at two widths).

| Area | Templates | Status | Notes |
|---|---|---|---|
| Foundation (CSS, JS, shell, partials) | `static/`, `partials/ui`, `core/shell.py` | wip | |
| Mi iglesia (home), Mis datos, welcome | `groups/home.html`, `profile.html`, `partials/ui/welcome.html` | todo | |
| Grupos (member, guide, administration) | `groups/` | todo | |
| Eventos for people, public event page | `agenda/` (list, detail, public) | todo | |
| Eventos administration (page, inscritos, programa, preguntas, menores) | `agenda/` | todo | Consent wording untouched |
| Avisos | `notices/` | todo | |
| Devocional, recursos, planes de lectura | `resources/` | todo | |
| Servir | `serving/` | todo | |
| Acompañar | `care/` | todo | |
| Personas | `people/` | todo | |
| Panorama | `overview/` | todo | Privacy thresholds untouched |
| Opiniones, políticas, página pública | `feedback/`, `policies/`, `sitio/` | todo | |
| Cuenta, entrar, contraseñas, portada, unirme, errores, ayuda | `registration/`, `errors/`, `core/` | todo | |
| Configuración de la iglesia, usuarios | `core/church_settings.html`, `user_list.html` | todo | |
| Discipulado (pastor, administración, mentor, participante) | `core/` | todo | |
| Evento: administración | `events/admin/` | todo | |
| Evento: coordinador, tutor, pastor, supervisión, reportes | `events/` | todo | |
| Evento: entrevista y campero (kiosk) | `events/interview/`, `events/kiosk/` | todo | Privacy and consent text stays |
| Presentación (projector) | `events/presentation/` | todo | Keeps its own fitted type |

## Decisions taken (defaults; change them here if the owner says otherwise)

1. The redesign stays a layer under `<html data-ui="2">`; `rotulo.css` is not edited, so a church
   with the module off keeps Atrio. Template copy is shared, so shorter copy reaches both.
2. Consent, guardian authorization, privacy notices and the wording of irreversible actions are
   not shortened. Policy text is not changed anywhere.
3. No push, no pull request, no deploy without the owner's word.
