# Emmaus

Emmaus is a Spanish-language (es-MX) Django platform that helps a church care for people at its events.

- **Emmaus Campamentos** runs a camp's interview operation: 350–400 camperos without accounts, ten shared interview computers, volunteer coordinadores, tutores who supervise them, pastors who need an honest overview, and leadership meetings that need a privacy-safe projector view.
- **Discipulado**, the original module, is still supported: discipleship paths, lessons, reflections, participant questions and pastoral follow-up for participants who have accounts. It is shown only to churches that turn it on.

The interface is Mexican Spanish throughout and uses its own design system, "Cuaderno de campo" (`docs/design-system.md`).

## What a camp gets

| Role | Main screen | Highlights |
|---|---|---|
| Coordinador | **Mi inicio**: "¿Qué tengo que hacer ahora?" | Register camperos, search, assisted interview or supervised capture on a shared computer, resume interrupted interviews, decide follow-up, print a paper backup list |
| Campero | No account, no navigation | One question per screen, "Prefiero no responder" everywhere, answers saved at every step, screen locks when idle, returns the computer to the coordinador |
| Tutor | **Mi equipo** | Progress per assigned coordinador, interrupted interviews, follow-ups, filters, team cutoffs |
| Pastor | **Resumen general** | Five questions answered in order, team progress, privacy-suppressed answer totals, deterministic next steps, **Modo presentación** |
| Administración | **Inicio** | Events, team and roles, tutor assignments, CSV import with duplicate review, questionnaire versions, cutoffs, audit log, open sessions |

Cutoffs ("cortes de seguimiento") are immutable snapshots generated every two hours (configurable). Each one can be viewed, compared with the previous one, presented full screen, or downloaded as a PDF.

## Quick start (local)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export DATABASE_URL=postgres://emmaus:emmaus@localhost:5432/emmaus   # or omit for SQLite
.venv/bin/python manage.py migrate
DEBUG=true .venv/bin/python manage.py seed_camp_demo --password "una-contraseña-de-prueba"
DEBUG=true .venv/bin/python manage.py runserver
```

`seed_camp_demo` builds a synthetic camp: 400 fictitious camperos, an admin, a pastor, a care-team member, 4 tutors, 12 coordinators, a simulated day of interviews and its cutoffs. It refuses to run unless `DEBUG=true`. Without `--password` (or `EMMAUS_DEMO_PASSWORD`) it prints a random password once. The demo accounts use the reserved `@demo.invalid` domain. Turn them off with `manage.py deactivate_demo_accounts`; `check --deploy` warns while they are active.

With Docker: `make build && make up`, then `make migrate` and `make seed-demo`.

For a real deployment, start at `/setup/` (first church and administrator), then use **Crear campamento**. See `docs/deployment.md`.

## Tests

```bash
DATABASE_URL=postgres://… .venv/bin/python manage.py test          # 130+ tests, use PostgreSQL
python scripts/simulate_camp_day.py --base http://127.0.0.1:8000 --event carga-2026 --password …   # 10-station load test
cd e2e && npm install && npx playwright install chromium && EMMAUS_PASSWORD=… npm test              # browser check
```

`TEST_DATABASE_NAME` lets two test runs share one Postgres server. The threaded tests in `events/tests/test_concurrency.py` need PostgreSQL row locks.

## Documentation

| Document | Contents |
|---|---|
| `docs/ux-redesign-plan.md` | Audit of the original app, information architecture, data-model changes, migration strategy, phases |
| `docs/design-research.md` | What we learned from Planning Center, GOV.UK, Tally, kiosk survey tools and dashboard guidance (and what we could not access) |
| `docs/design-direction.md` | Three visual concepts and why "Cuaderno de campo" won |
| `docs/design-system.md` | Tokens, components, patterns, language and accessibility rules |
| `docs/ux-review.md` | Screenshot-based review rounds, critique and what changed |
| `docs/architecture.md` | Apps, models, concurrency model, live updates, PDF, static files |
| `docs/roles-and-permissions.md` | Role resolution, permission matrix, field-level visibility |
| `docs/interview-station-workflow.md` | Assisted and supervised flows on shared computers, locking, recovery, load test |
| `docs/reporting.md` | Metric definitions, cutoffs, presentation privacy, PDF |
| `docs/privacy-and-safeguarding.md` | Data inventory, consent, retention, escalation, checklist before use with minors |
| `docs/deployment.md` | Environment, Docker, scheduler, HTTPS, backups, rollback |
| `docs/user-guide-es.md` | Guía para coordinadores, tutores, pastores y administración |

## Principles that are not negotiable

- Camperos never have accounts, and a supervised capture never exposes coordinator privileges: the coordinador's login ends before the campero touches the keyboard.
- Spiritual and personal answers are the participant's own words. Emmaus never judges them, never scores them, never diagnoses, and never sends them to an AI service or by email.
- Pastoral or administrative status does not unlock confidential answers. That takes an explicit, audited "acceso confidencial" per event.
- Reports never widen access: a saved cutoff is shown only to people whose **current** scope includes it.
- Before real minors are interviewed, the church must define and approve its consent, safeguarding and escalation procedures. Emmaus blocks interviews until an administrator confirms this.
