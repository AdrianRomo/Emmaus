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
- [x] B3. Adversarial review (three reviewers + home critic), ranked report `REVIEW_2026-11-18.md`, fixes
- [x] B4. Independent after-state critique; its ten defects fixed
- [ ] C. Every surface, by area, on the new system (wave 1 merged; wave 2 running)
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
| Camper administration (list, bulk assignment, home's urgent part, record's authorization) | `events/admin/participants.html`, `_home_live.html`, `participant_detail.html` | done | Reference screen; merged `ba97839` |
| Grupos (member, guide, administration), Mis datos | `groups/` | done | merged `0304f2e` | Copy work from the earlier pass is merged |
| Eventos for people, public event page | `agenda/` | done | merged `d2ef40e`; «Volver a Mi iglesia» `8239c12` | |
| Eventos administration (page, inscritos, programa, preguntas, menores) | `agenda/` | done | merged `d2ef40e`; consent wording untouched | Consent wording untouched |
| Avisos | `notices/` | done | merged `d2ef40e`; «Enterado» in place | «Enterado» in place |
| Devocional, recursos, planes de lectura | `resources/` | done | merged `d2ef40e`; «Marcar como leído» in place | «Marcar como leído» in place |
| Servir | `serving/` | done | merged `0304f2e`; one drawing of an answered date `ed7aacd` | |
| Acompañar | `care/` | done | merged `0304f2e`; privacy unchanged | |
| Personas | `people/` | done | merged `6303cd8` | |
| Panorama | `overview/` | done | merged `6303cd8`; thresholds untouched | Privacy thresholds untouched |
| Opiniones, políticas, página pública | `feedback/`, `policies/`, `sitio/` | done | merged `6303cd8` | |
| Cuenta, entrar, contraseñas, portada, unirme, errores, ayuda | `registration/`, `errors/`, `core/` | done | merged `71f3a36`; error pages unchanged on purpose | Shared with module-off |
| Configuración de la iglesia, usuarios | `core/` | done | merged `6303cd8` | Shared |
| Discipulado | `core/` | done | merged `71f3a36` | Shared |
| Evento: administración | `events/admin/` | done | merged `0ebcd48` | Shared |
| Evento: coordinador (rest), tutor, pastor, supervisión, reportes | `events/` | done | merged `c9e5857`, `0ebcd48` | Shared |
| Evento: entrevista y campero | `events/interview/`, `events/kiosk/` | wip | Privacy and consent text stays |

## Decisions taken (defaults; the owner may change them)

1. The redesign stays a layer under `<html data-ui="2">`; module-off output is byte-identical.
2. Consent, guardian authorization, privacy notices and irreversible-action wording are not shortened.
3. Members never see who signed up for an event (the list is the administration's); places left only
   where the event page already shows them. B's "Van Andrea, Diego y 10 más" is not built.
4. No event photos until issue #60 is decided: the arch shows the sky of the hour; the slot is ready.
5. Accounts that only work in events wear the shell of the event's church.
6. No push, no pull request, no deploy without the owner's word.

## Open decisions for the owner (found during the rollout; nothing below was changed)

1. Notices linked to the gathering they change (a notice «17:00 → 18:00» does not change the event): a new
   capability (review #24).
2. Decision 0010 (#75): an adult's typed birth date still waits for the team's confirmation; a pending
   sign-up offers «Cancelar mi inscripción» (existing behaviour, kept) — confirm both.
3. The sign-in error tells everyone «pide ayuda a la administración del evento», church members too. It is
   shared with the camp's sign-in (module-off output must not change), so it was left. Suggested: «Si no
   puedes entrar, pide ayuda a quien te dio acceso.»
4. The landing's promise («Platicar con cada joven, sin perder a nadie») speaks of camp to every visitor.
5. An interview step's «Continuar» with no answer chosen: whatever the interview area reports (what is
   required is the owner's rule).
6. «Nadie sirve el …» shows on the team page; counting it in the rail or on a coordinator's home needs a
   decision on who must see it first.
7. New words to confirm: «Hoy en la iglesia», «Ver cómo inscribirme», «Más de la iglesia», «Mis datos» in Mi
   cuenta, «Lo que preguntaste» / «Lo que escribiste», «También esperan tu respuesta» / «Con el pastor».
8. Event photos (#60): the arch shows the sky of the hour until the church decides.

