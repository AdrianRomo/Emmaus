# Deployment

Emmaus runs as three containers in production: `web` (gunicorn), `scheduler` (cutoffs and session housekeeping) and `db` (PostgreSQL 16). Everything below comes from `Dockerfile`, `docker-compose.yml`, `docker-compose.prod.yml`, `.env.example`, `Makefile` and `emmaus/settings.py`.

## Local development

`emmaus/settings.py` reads only real environment variables. It does **not** load `.env` by itself; Docker Compose does that through `env_file`.

### With a virtualenv

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export DEBUG=true
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

* `DEBUG` defaults to `false`. Turn it on explicitly in development: with `DEBUG=true`, `SECRET_KEY` falls back to a development key; with `DEBUG=false` and no `SECRET_KEY`, every `manage.py` command stops with "SECRET_KEY must be set when DEBUG=false." Test runs (`manage.py test`, `pytest`) get a test key automatically.
* Without `DATABASE_URL` the project uses SQLite (`db.sqlite3`). Row locks are no-ops on SQLite, so use PostgreSQL to try concurrent stations: `export DATABASE_URL=postgres://user:password@localhost:5432/emmaus`.

### With Docker Compose

```bash
cp .env.example .env
make build        # docker compose build
make up           # runserver on :8000 with the source mounted
make migrate      # in another terminal
```

`docker-compose.yml` starts `web` (runserver, source mounted at `/app`) and `db` (`postgres:16`, user, password and database `emmaus`). The compose file sets `DATABASE_URL` and `DEBUG: "true"`.

Other targets: `make createsuperuser`, `make test`, `make logs`, `make cutoffs` (one cutoff pass), `make seed-demo`, `make create-starter-path` (discipleship module), `make down`.

### Demo data

`python manage.py seed_camp_demo` (or `make seed-demo`, which adds `--reset`) builds "Campamento de Jóvenes 2026" with 400 synthetic camperos, 19 staff accounts, 4 teams, 10 computers, simulated interviews and cutoffs.

* It refuses to run unless `DEBUG=true`.
* All demo accounts end in `@demo.invalid` and share one password: `--password`, else `EMMAUS_DEMO_PASSWORD`, else a random one printed at the end with the account list.
* Options: `--participants 400`, `--completed 0.62`, `--hours 9`, `--seed 2026`, `--slug jovenes-2026`, `--reset`.
* It uses its own church (identifier `iglesia-demo`, "Iglesia de demostración", created if missing) and makes the demo administrator a church admin of that church only. It still creates accounts with a shared password, so do not run it on a server with real data.

`seed_dev_data` (discipleship module) is also DEBUG-only and uses the same password rules.

## Production with `docker-compose.prod.yml`

| Service | Command | Notes |
|---|---|---|
| `web` | `gunicorn emmaus.wsgi:application --bind 0.0.0.0:8000 --workers 3 --threads 4 --timeout 60` | `DEBUG=false`; `DATABASE_URL` built from `POSTGRES_PASSWORD`; publishes `127.0.0.1:8000:8000` (only a proxy on the same host can reach it) |
| `scheduler` | `python manage.py run_report_cutoffs --loop 300` | Same image; image healthcheck disabled |
| `db` | `postgres:16` | Volume `postgres_data`; `pg_isready` healthcheck |

The compose file builds the image from the working copy (`build: .`); there is no `image:` tag.

```bash
cp .env.example .env                 # then edit it (table below)
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml run --rm web python manage.py migrate
docker compose -f docker-compose.prod.yml run --rm web python manage.py check --deploy
docker compose -f docker-compose.prod.yml up -d
```

The containers do not run migrations on start. Run `migrate` before `up -d` on every upgrade.

### Environment variables

| Variable | Default in code | Production value |
|---|---|---|
| `SECRET_KEY` | none; startup fails if missing with `DEBUG=false` | Long random string |
| `DEBUG` | `false` if unset | `false` (the prod compose file also forces it; `.env.example` has `true` for development) |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Your domain, **first** in the list: the container healthcheck sends the first entry as its `Host` header (`*` makes it use `localhost`) |
| `CSRF_TRUSTED_ORIGINS` | empty | `https://your-domain` |
| `POSTGRES_PASSWORD` | none | Strong password; used by `db` and to build `DATABASE_URL` |
| `DATABASE_URL` | SQLite | Set by the prod compose file (overrides `.env`) |
| `DB_CONN_MAX_AGE` | `60` | Seconds to reuse a connection |
| `TIME_ZONE` | `America/Mexico_City` | Server time zone (each event also has its own) |
| `EMMAUS_IDLE_LOGOUT_SECONDS` | `1800` | Staff idle logout |
| `SECURE_SSL_REDIRECT` | `false` | See HTTPS below |
| `SECURE_HSTS_SECONDS` | `0` | See HTTPS below |
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | `true` when `DEBUG=false` | Leave `true` |
| `EMAIL_BACKEND` | console | `django.core.mail.backends.smtp.EmailBackend` |
| `EMAIL_HOST`, `EMAIL_PORT` (587), `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` (`true`) | | Your SMTP |
| `DEFAULT_FROM_EMAIL` | `Emmaus <no-reply@example.org>` | A real sender |
| `LOG_LEVEL` | `INFO` | |
| `LLM_ENABLED`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | disabled | Leave disabled unless the discipleship module needs AI drafts; the camp module never uses them |
| `EMMAUS_DEMO_PASSWORD` | empty | Development only |
| `TEST_DATABASE_NAME` | Django default | Tests only |

Without SMTP, safety alerts, invitations and "¿Olvidaste tu contraseña?" e-mails are only printed to the container log. The new-member screen still shows the link to copy, but administrators can generate a password link only for accounts that never set a password, so anyone who forgets an existing password depends on SMTP (or on a superuser using `manage.py changepassword`).

## First-time setup

1. Run `migrate`.
2. Open the site. With no church in the database, `/` redirects to `/setup/`: church name and identifier, first administrator's name, e-mail and password. That user becomes the church admin (Administrador of every event of that church). `createsuperuser` also works, but a superuser cannot create an event until a church exists, so `/setup/` (or the Django admin) is still needed.
3. "Campamentos y eventos" → "Crear campamento": name, kind, identifier for the URL, place, dates, status. This also attaches the church's newest published questionnaire, or creates and publishes the default "Conversación espiritual del campamento" if the church has none, and creates "Computadora 1" to "Computadora 10".
4. "Configuración": adapt the time zone, the privacy notice, the safeguarding instructions, adult age, guardian authorization, minimum group size, retention and the cutoff schedule. When the church has approved its procedures, tick the box under "Procedimientos de la iglesia" and press "Confirmar". Interviews stay blocked until then.
5. "Equipo" → "Agregar persona": coordinadores, tutores (with a team name), pastores, administración; check "Puede leer información confidencial" only for the care team. New people get an e-mail or a link to copy to create their password.
6. "Asignar tutores": one tutor per coordinador.
7. "Participantes" → "Importar CSV" (with duplicate review) or "Registrar campero".
8. Label each computer with its number.

The event's time zone ("Zona horaria", default `America/Mexico_City`) is chosen in "Configuración" from the Mexican zones; it decides when cutoffs fall and how times are shown.

## HTTPS and the reverse proxy

Put a TLS-terminating reverse proxy (Caddy, nginx, Traefik) in front of `web:8000`.

* `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` is always on. The proxy must set (and overwrite) `X-Forwarded-Proto`. Do not expose gunicorn directly: the prod compose file binds it to `127.0.0.1:8000`, so the proxy must run on the same host (change the binding only if the proxy runs elsewhere, and then firewall the port).
* `CSRF_TRUSTED_ORIGINS` must list the public `https://` origin, otherwise every form fails CSRF checks.
* **HTTP → HTTPS redirect:** do it in the proxy, in Django (`SECURE_SSL_REDIRECT=true`), or both. `/health/` is exempt from Django's redirect (`SECURE_REDIRECT_EXEMPT`), and the image's healthcheck sends `X-Forwarded-Proto: https` and the first `ALLOWED_HOSTS` entry as `Host`, so it keeps working.
* **HSTS:** `SECURE_HSTS_SECONDS` sets the header. Start low (for example `3600`), then raise to `31536000` once HTTPS is stable. There are no settings for `includeSubDomains` or preload.
* Attempt limits live in the `AuthThrottle` table and are per typed e-mail, not per IP address: login allows 8 attempts, then blocks of 5 minutes that double up to 60; "¿Olvidaste tu contraseña?" sends at most 3 e-mails per address per hour. Consider rate limiting `/cuenta/entrar/` and `/cuenta/olvide-mi-contrasena/` per IP in the proxy.
* Remember that search terms (campero names) appear in URLs and therefore in proxy access logs.

## Static files

* WhiteNoise serves `/static/` from the image. The Dockerfile runs `collectstatic` with `DEBUG=false` at build time to create the hashed, compressed manifest.
* Do not mount a volume on `staticfiles/`: it would hide the new manifest after an upgrade. The README's `collectstatic` step for production is not needed with this image.
* Outside Docker with `DEBUG=false`, run `python manage.py collectstatic --noinput` before starting, otherwise pages fail with a missing-manifest error.

## Database connection budget

Each gunicorn thread keeps one connection for `DB_CONN_MAX_AGE` seconds (`CONN_HEALTH_CHECKS` on). The maximum is 3 workers x 4 threads = 12 from `web`, 1 from `scheduler`, plus 1 per one-off `manage.py` command. That stays under a free-tier limit of about 20. If you raise workers or threads, recompute; with a tight limit lower `DB_CONN_MAX_AGE` or set it to `0`.

## Scheduled jobs

| Job | How |
|---|---|
| Cutoffs, expired claims, idle capture sessions, expired Django sessions | `scheduler` service, every 300 s. Or cron every 5 minutes: `python manage.py run_report_cutoffs` |
| Retention purge | Not scheduled. `python manage.py purge_expired_event_data` (dry run), then `--apply` |

With compose: `docker compose -f docker-compose.prod.yml run --rm web python manage.py <command>`.

## Backups (suggested, not part of the repository)

The database holds everything, including answers. A daily custom-format dump:

```bash
docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U emmaus -d emmaus -Fc > emmaus-$(date +%F-%H%M).dump
```

Restore into the running `db` after stopping the app:

```bash
docker compose -f docker-compose.prod.yml stop web scheduler
docker compose -f docker-compose.prod.yml exec -T db \
  pg_restore -U emmaus -d emmaus --clean --if-exists < emmaus-YYYY-MM-DD-HHMM.dump
docker compose -f docker-compose.prod.yml start web scheduler
```

Encrypt dumps, keep them off the server, test a restore before the camp, and apply the same retention as the event data (purging the database does not touch old dumps).

## Deploy checks

```bash
docker compose -f docker-compose.prod.yml run --rm web python manage.py check --deploy
```

* Django's own warnings appear while `SECURE_SSL_REDIRECT=false` or `SECURE_HSTS_SECONDS=0`; they are expected if the proxy handles the redirect, and should disappear for HSTS once you set it.
* `emmaus.W001` (from `events/checks.py`, only when `DEBUG=false`) lists active accounts ending in `@example.org` or `@demo.invalid`. Fix with `python manage.py deactivate_demo_accounts` (`--dry-run` lists them first). Migration `core/0004` already deactivates `participant@example.org`.

## Running the tests

```bash
DATABASE_URL=postgres://user:password@localhost:5432/emmaus python manage.py test
```

* 175 tests: `core/tests.py` and `events/tests/` (access, interviews, questionnaire, reporting, UI, concurrency and `test_security_review.py`, the regression tests for the adversarial security review).
* `make test` runs the same inside the dev compose stack, against its PostgreSQL.
* The four threaded tests in `events/tests/test_concurrency.py` run only on PostgreSQL; on SQLite they are skipped.
* Django creates a test database; the database user needs permission to create it. `TEST_DATABASE_NAME` sets its name.
* `pytest` also works (`pytest.ini` points at `emmaus.settings`).

Browser check of the station flow (Playwright, synthetic data, server running with the demo seed):

```bash
cd e2e && npm install && npx playwright install chromium
EMMAUS_URL=http://127.0.0.1:8000 EMMAUS_USER=jorge.pineda@demo.invalid EMMAUS_PASSWORD=<demo password> npm test
```

`SHOTS_DIR=/some/dir` also saves a screenshot of every step.

## Load test

`scripts/simulate_camp_day.py` drives ten stations through the real HTTP forms while two pastor pollers fetch the live dashboard, then checks the database for duplicate interviews. It reads the database directly, so it needs the same `DATABASE_URL` as the server, and the seed needs `DEBUG=true`:

```bash
export DEBUG=true DATABASE_URL=postgres://user:password@localhost:5432/emmaus
python manage.py migrate
python manage.py seed_camp_demo --slug carga-2026 --completed 0 --reset --password Carga-2026
gunicorn emmaus.wsgi:application --bind 127.0.0.1:8000 --workers 3 --threads 4   # second terminal, same env
python scripts/simulate_camp_day.py --base http://127.0.0.1:8000 --event carga-2026 --password Carga-2026
```

If you run the server with `DEBUG=false` over plain HTTP, also set `SESSION_COOKIE_SECURE=false` and `CSRF_COOKIE_SECURE=false` (the script cannot send Secure cookies over HTTP) and run `collectstatic` first. The script exits with code 1 on any HTTP error or duplicate. The 2026-10-23 result is in `interview-station-workflow.md`.

## Upgrades and rollback

Before every upgrade: take a database dump and keep the current image.

```bash
docker compose -f docker-compose.prod.yml images        # note the web image name
docker tag <web image>:latest <web image>:before-YYYYMMDD
```

To roll back the code, point the services at the saved image (or check out the previous commit and rebuild) and start them again.

About the database:

* The camp redesign's migrations are additive: `core/0003` adds two `Church` columns, `core/0004` is a data migration with a no-op reverse, `core/0005` only changes model options, `events/0001` adds new tables, `events/0002` adds `AuthThrottle` and `FollowUp.confidential`, `events/0003` gives that column a database default, `events/0004` adds `AuthThrottle.strikes` and Spanish field labels, and `events/0005` gives `strikes` a database default. Older code keeps reading and writing the database. The one exception is a new non-null column without a database default: creating a church (for example through `/setup/`) fails with code older than `core/0003`. Code from before `events/0002` can still insert follow-ups (`events/0003`) and throttle rows (`events/0005`); and the throttle fails closed: if its counter cannot be written, the password is not checked.
* Reversing `events/0002` restores the old one-care-follow-up-per-interview constraint, which fails as soon as an interview has both an ordinary and a confidential referral.
* Do **not** reverse with `python manage.py migrate events zero`: it drops every camp table (camperos, interviews, answers, follow-ups, cutoffs, activity log). Reversing `core` to `0002` needs that first and drops the two church columns.
* To really go back, stop `web` and `scheduler`, restore the dump taken before the upgrade, and start the previous image.
