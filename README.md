# Emmaus

Emmaus is a Spanish-language (es-MX) Django platform that helps a church care for people at its events.

- **Emmaus Campamentos** runs a camp's interview operation: 350–400 camperos without accounts, ten shared interview computers, volunteer coordinadores, tutores who supervise them, pastors who need an honest overview, and leadership meetings that need a privacy-safe projector view.
- **Discipulado**, the original module, is still supported: discipleship paths, lessons, reflections, participant questions and pastoral follow-up for participants who have accounts. It is shown only to churches that turn it on.

The interface is Mexican Spanish throughout and every screen uses one design system, "Atrio" ([`docs/design-system.md`](docs/design-system.md)), which replaced "Rótulo" on 2026-10-26: white pages, one action colour (petróleo `#0B6270`), Figtree with sentences as headings, no icons, shadows or yellow, the church's own logo as the only ornament, and at most one tinted panel per screen that asks the next question and holds its one button. The stylesheet keeps its name, `static/css/rotulo.css`. Why the direction changed: `docs/design-direction.md`.

## What a camp gets

| Role | Main screen | Highlights |
|---|---|---|
| Coordinador | **Mi inicio**: one tinted panel with the thing to do now, asked as a question ("¿A quién vas a entrevistar?") | Register camperos, search, assisted interview or supervised capture on a shared computer, resume interrupted interviews, decide follow-up, print a paper backup list |
| Campero | No account, no navigation | One question per screen, "Prefiero no responder" everywhere, answers saved at every step, screen locks when idle, returns the computer to the coordinador |
| Tutor | **Mi equipo** | Progress per assigned coordinador, interrupted interviews, follow-ups, filters, team cutoffs |
| Pastor | **Resumen general** | Five questions answered in order, team progress, privacy-suppressed answer totals, deterministic next steps, **Modo presentación** |
| Administración | **Inicio** | Events, team and roles, tutor assignments, CSV import with duplicate review, a campero table sorted by any column, questionnaires (several per event, answered in a fixed or any order, each with its versions), who reads confidential answers (the pastor can set it too), cutoffs, audit log, open sessions |

Cutoffs ("cortes de seguimiento") are immutable snapshots generated every two hours (configurable). Each one can be viewed, compared with the previous one, presented full screen, or downloaded as a PDF.

## Quick start (local)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export DATABASE_URL=postgres://emmaus:emmaus@localhost:5432/emmaus   # or omit for SQLite
export DEBUG=true   # DEBUG is off unless you turn it on; production needs SECRET_KEY instead
.venv/bin/python manage.py migrate
DEBUG=true .venv/bin/python manage.py seed_camp_demo --password "una-contraseña-de-prueba"
DEBUG=true .venv/bin/python manage.py runserver
```

`seed_camp_demo` builds a synthetic camp: 400 fictitious camperos, an admin, a pastor, a care-team member, 4 tutors, 12 coordinators, a simulated day of interviews and its cutoffs. It refuses to run unless `DEBUG=true`. Without `--password` (or `EMMAUS_DEMO_PASSWORD`) it prints a random password once. The demo accounts use the reserved `@demo.invalid` domain. Turn them off with `manage.py deactivate_demo_accounts`; `check --deploy` warns while they are active.

With Docker: `make build && make up`, then `make migrate` and `make seed-demo`.

For a real deployment, start at `/setup/` (first church and administrator), then use **Crear campamento**. See `docs/deployment.md`.
A server that has shown the demo goes back to an empty database with `manage.py purge_demo_data` (dry run; `--yes` deletes): see "Empezar de cero para un evento real" in `docs/deployment.md`.

## Tests

```bash
DATABASE_URL=postgres://… .venv/bin/python manage.py test          # 1279 tests, use PostgreSQL (6 skip on SQLite)
python scripts/simulate_camp_day.py --base http://127.0.0.1:8000 --event carga-2026 --password …   # 10-station load test
cd e2e && npm install && npx playwright install chromium && EMMAUS_PASSWORD=… npm test              # browser check
```

`TEST_DATABASE_NAME` lets two test runs share one Postgres server. The threaded tests in `events/tests/test_concurrency.py` need PostgreSQL row locks.

## Documentation

| Document | Contents |
|---|---|
| `docs/ux-redesign-plan.md` | Audit of the original app, information architecture, data-model changes, migration strategy, phases |
| `docs/design-research.md` | Real screens from Planning Center, Tally, Airtable, GOV.UK, Mobbin and Refero, then (v3) 32 church, camp, discipleship and calm-product tools: what to adapt, what not to copy, what we could not access, and the three directions that led to "Atrio" |
| `docs/design-direction.md` | The two retired directions ("Cuaderno de campo", "Rótulo") and why each was retired; "Atrio": why it won, its patterns and principles, what it kept from Rótulo, the landing page copy and the church's logo |
| `docs/design-system.md` | "Atrio" tokens, components, chart rules, screens, language, accessibility, motion and how to make a new screen |
| `docs/ux-review.md` | Sixteen screenshot review rounds at three widths (round 16 is Atrio), axe results and what is still open |
| `docs/architecture.md` | Apps, models, concurrency model, live updates, PDF, static files |
| `docs/roles-and-permissions.md` | Role resolution, permission matrix, field-level visibility |
| `docs/interview-station-workflow.md` | Assisted and supervised flows on shared computers, locking, recovery, load test |
| `docs/reporting.md` | Metric definitions, cutoffs, presentation privacy, PDF |
| `docs/privacy-and-safeguarding.md` | Data inventory, consent, retention, escalation, checklist before use with minors |
| `docs/deployment.md` | Environment, Docker, scheduler, HTTPS, backups, rollback |
| `docs/user-guide-es.md` | Guía para coordinadores, tutores, pastores y administración |
| `docs/EMMAUS_PRODUCT_VISION.md` | **Next version.** Product north star: Emmaus as a connected home for church life (church-first, multi-sede, SaaS-ready later), principles, personas, the four experiences, target domain model, phased delivery, decision register |
| `docs/EMMAUS_AUDIT_PROMPT.md` | **Next version.** The read-only audit brief to run first: current-state inventory, gap analysis against the vision, evolution roadmap, open questions |

## Next version (this branch)

`main` runs the camp in production and only takes the small fixes the camp needs. This branch, `v2/foundation`, is the
foundation for the next version of Emmaus. It starts from the camp code and adds the two documents above; nothing in the
application changes here until the audit has been done and reviewed.

Order of work: read `docs/EMMAUS_PRODUCT_VISION.md`; run the audit in `docs/EMMAUS_AUDIT_PROMPT.md` (it writes only under
`docs/audits/`); agree the first implementation slice; then build it in small, reversible steps that keep every camp flow
working (vision §0 and §2.6). Changes that the camp needs keep going to `main` and get merged into this branch.

## Principles that are not negotiable

- Camperos never have accounts, and a supervised capture never exposes coordinator privileges: the coordinador's login ends before the campero touches the keyboard.
- Spiritual and personal answers are the participant's own words. Emmaus never judges them, never scores them, never diagnoses, and never sends them to an AI service or by email.
- Pastoral or administrative status does not unlock confidential answers. That takes an explicit, audited "acceso confidencial" per event, unless the event's administración or pastoral opens them to more of the team ("Respuestas confidenciales", audited), which reaches only the interviews that start afterwards.
- Reports never widen access: a saved cutoff is shown only to people whose **current** scope includes it.
- Before real minors are interviewed, the church must define and approve its consent, safeguarding and escalation procedures. Emmaus blocks interviews until an administrator confirms this.
