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

The image runs as the unprivileged user `emmaus` (uid 10001), which can neither write the bind-mounted checkout (`make collectstatic` fails with `PermissionError`) nor, in a checkout made with umask 077, even read `manage.py`. The dev `web` service therefore sets `user: root`; files it creates in the checkout (`staticfiles/`, migrations from `makemigrations`) belong to root. Production (`docker-compose.prod.yml`) has no such override and stays non-root.

Other targets: `make createsuperuser`, `make test`, `make logs`, `make cutoffs` (one cutoff pass), `make seed-demo`, `make create-starter-path` (discipleship module), `make down`.

### Demo data

`python manage.py seed_camp_demo` (or `make seed-demo`, which adds `--reset`) builds "Campamento de Jóvenes 2026" with 400 synthetic camperos, 19 staff accounts, 4 teams, 10 computers, simulated interviews and cutoffs.

* It refuses to run unless `DEBUG=true`.
* All demo accounts end in `@demo.invalid` and share one password: `--password`, else `EMMAUS_DEMO_PASSWORD`, else a random one printed at the end with the account list.
* Options: `--participants 400`, `--completed 0.62`, `--hours 9`, `--seed 2026`, `--slug jovenes-2026`, `--reset`.
* It uses its own church (identifier `iglesia-demo`, "Iglesia de demostración", created if missing) and makes the demo administrator a church admin of that church only. It still creates accounts with a shared password, so do not run it on a server with real data.

`seed_dev_data` (discipleship module) is also DEBUG-only and uses the same password rules.

To take a server that has shown the demo back to an empty database, see [Empezar de cero para un evento real](#empezar-de-cero-para-un-evento-real).

## Production with `docker-compose.prod.yml`

| Service | Command | Notes |
|---|---|---|
| `web` | `gunicorn emmaus.wsgi:application --bind 0.0.0.0:8000 --workers 3 --threads 4 --timeout 60` | `DEBUG=false`; `DATABASE_URL` built from `POSTGRES_PASSWORD`; publishes `127.0.0.1:8000:8000` (only a proxy on the same host can reach it) |
| `scheduler` | `python manage.py run_report_cutoffs --loop 300` | Same image; image healthcheck disabled |
| `db` | `postgres:16` | Volume `postgres_data`; `pg_isready` healthcheck |

The compose file builds the image from the working copy (`build: .`); there is no `image:` tag.

* `.dockerignore` keeps `.env`, `.git`, `.venv`, local SQLite files, the host's `staticfiles/`, caches, `e2e/node_modules` and `docs/img` out of the image.
* The containers run as the unprivileged user `emmaus` (uid/gid 10001), including `docker compose run --rm web python manage.py …`. Everything under `/app` is owned by root and read-only for that user; nothing there is written at runtime.
* `requirements.txt` pins exact versions; upgrade one package at a time and re-pin after the test suite passes.

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
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | The real hostname (for example `campamento.tu-iglesia.mx`), **first** in the list: the container healthcheck sends the first entry as its `Host` header (`*` makes it use `localhost`) |
| `CSRF_TRUSTED_ORIGINS` | empty | `https://` plus the same hostname (`https://campamento.tu-iglesia.mx`) |
| `POSTGRES_PASSWORD` | none | Strong password; used by `db` and to build `DATABASE_URL` |
| `DATABASE_URL` | SQLite | Set by the prod compose file (overrides `.env`) |
| `DB_CONN_MAX_AGE` | `60` | Seconds to reuse a connection |
| `TIME_ZONE` | `America/Mexico_City` | Server time zone (each event also has its own) |
| `EMMAUS_IDLE_LOGOUT_SECONDS` | `1800` | Staff idle logout |
| `SECURE_SSL_REDIRECT` | `false` | `true` behind Traefik/HTTPS (see HTTPS below) |
| `SECURE_HSTS_SECONDS` | `0` | `3600` to start (no preload); see HTTPS below |
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | `true` when `DEBUG=false` | Leave unset (so `true`) |
| `EMAIL_BACKEND` | console | `django.core.mail.backends.smtp.EmailBackend` (see SMTP below) |
| `EMAIL_HOST`, `EMAIL_PORT` (587), `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` (`true`) | | Your SMTP provider |
| `DEFAULT_FROM_EMAIL` | `Emmaus <no-reply@example.org>` | A real sender |
| `LOG_LEVEL` | `INFO` | |
| `LLM_ENABLED`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | disabled | Leave disabled unless the discipleship module needs AI drafts; the camp module never uses them |
| `EMMAUS_DEMO_PASSWORD` | empty | Development only |
| `TEST_DATABASE_NAME` | Django default | Tests only |

### Recommended production values behind Traefik (HTTPS)

Traefik (or any TLS proxy) terminates HTTPS and forwards to `web:8000` with `X-Forwarded-Proto: https`. In `.env`:

```bash
DEBUG=false
SECRET_KEY=<long random string>
ALLOWED_HOSTS=campamento.tu-iglesia.mx
CSRF_TRUSTED_ORIGINS=https://campamento.tu-iglesia.mx
SECURE_SSL_REDIRECT=true
SECURE_HSTS_SECONDS=3600
# SESSION_COOKIE_SECURE and CSRF_COOKIE_SECURE: leave them out; with DEBUG=false they are true.
```

* `SECURE_HSTS_SECONDS=3600` is one hour and has no preload: a mistake undoes itself quickly. Raise it to `31536000` after the camp runs cleanly on HTTPS.
* With these values `check --deploy` still shows `security.W005` (`SECURE_HSTS_INCLUDE_SUBDOMAINS`) and `security.W021` (`SECURE_HSTS_PRELOAD`). Both are expected: `settings.py` has no switch for either, and both stay off on purpose, because a preloaded or subdomain-wide HSTS policy binds every subdomain of the church's domain and cannot be withdrawn quickly.
* Open the app **only** at `https://<hostname>`. Over plain `http://<LAN-IP>:8000` sign-in fails, in one of three ways: "400 Bad Request" on every page when the LAN IP is not in `ALLOWED_HOSTS` (the usual case); a redirect to `https://<LAN-IP>:8000`, where nothing speaks TLS, when it is and `SECURE_SSL_REDIRECT=true`; otherwise the login page loads but submitting it shows Django's 403 page "CSRF verification failed. Request aborted.", because with `DEBUG=false` the CSRF and session cookies are `Secure` and the browser does not keep them on an `http://` page. (The prod compose file publishes gunicorn on `127.0.0.1:8000` only, so the LAN cannot reach it anyway.) For a throwaway test over HTTP, set `SESSION_COOKIE_SECURE=false` and `CSRF_COOKIE_SECURE=false`; never on the camp server.

### SMTP

Without SMTP (`EMAIL_BACKEND` left at the console backend), invitations, safety alerts and "¿Olvidaste tu contraseña?" e-mails are only printed to the container log (`docker compose -f docker-compose.prod.yml logs web`); nobody receives them. The new-member screen still shows the link to copy, but administrators can generate a password link only for accounts that never set a password, so anyone who forgets an existing password depends on SMTP (or on a superuser using `manage.py changepassword`).

Any provider that offers SMTP submission with STARTTLS on port 587 works: the church's Google Workspace or Microsoft 365 account (with an app password), or a transactional service such as Brevo, Mailgun, SendGrid or Amazon SES.

```bash
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.your-provider.example
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=<SMTP user>
EMAIL_HOST_PASSWORD=<SMTP password or app password>
DEFAULT_FROM_EMAIL=Campamento <campamento@tu-iglesia.mx>
```

* `DEFAULT_FROM_EMAIL` must be an address the provider lets you send from (verified sender or domain with SPF/DKIM), otherwise mail is rejected or lands in spam.
* Only STARTTLS (`EMAIL_USE_TLS`) is read from the environment; port 465 with implicit TLS has no setting.
* Check it before the camp: `docker compose -f docker-compose.prod.yml run --rm web python manage.py sendtestemail you@your-domain`.

## Empezar de cero para un evento real

A server that has shown the demo still holds it: churches, 400 synthetic camperos, interviews, cutoffs and demo logins. The first real camp must start from an empty database. `purge_demo_data` removes exactly the demo:

* the churches with identifier `iglesia-demo` (`seed_camp_demo`, whatever event `--slug` it used: `jovenes-2026`, `carga-2026`, …) and `emmaus-demo` (`seed_dev_data`), with everything that hangs from them: events, camperos, interviews, answers, cutoffs, follow-ups, questionnaires, discipleship paths and their lessons and enrollments;
* the users whose e-mail ends in `@demo.invalid` or `@example.org` (the same markers as `emmaus.W001` and `deactivate_demo_accounts`), **except superusers**: an account made with `createsuperuser` and a placeholder address such as `soporte@example.org` is listed as skipped and never deleted;
* if no church is left afterwards, every session (`django_session`) and every attempt counter (`AuthThrottle`): nobody real is signed in on a demo database, and the first real camp starts without leftovers.

The command works with `DEBUG=false` (production is where the demo lives) and deletes nothing without `--yes`. It refuses, exits 1 and deletes nothing, in the dry run as well, when the demo is tangled with real data:

* any row that does not go with the demo churches points at a demo user, through any foreign key of any app: a role, team membership or notes in another church, an event or interview it created, approved or conducted, a consent it recorded, an invitation it sent… Deleting the user would delete those rows or erase their author. The message lists each user with `app.Model.field: rows`. The one exception is the Django admin history (`admin.LogEntry`) of a demo account: it records what the demo did, so it is deleted with the account and counted as "Historial del admin de Django" (a superuser's history stays, like the superuser);
* an event, interview or answer of another church uses a demo questionnaire or question. The message names the events.

Remove that link (or only turn the accounts off with `deactivate_demo_accounts`) and run it again.

The church identifier cannot be changed from "Configuración", but its name can: a church created by the demo seeds and then renamed and used for real still has identifier `emmaus-demo` or `iglesia-demo`, and the purge would wipe it. The dry run prints each church it would delete by **name** (with its identifier) at the top, followed by the counts: read that list before running `--yes`.

The sequence, on the server, from the checkout (the backup user needs a directory it owns):

```bash
sudo install -d -m 700 -o "$USER" /var/backups/emmaus

# 1. A dump first, so the purge can be undone (see Backups).
scripts/backup_db.sh /var/backups/emmaus

# 2. Dry run: the demo churches by name, skipped superusers, demo users, then what would be
#    deleted per kind and per table. Read the church and user lists before going on.
docker compose -f docker-compose.prod.yml run --rm web python manage.py purge_demo_data

# 3. Stop the app so nothing writes into the demo event meanwhile, then purge (one transaction).
docker compose -f docker-compose.prod.yml stop web scheduler
docker compose -f docker-compose.prod.yml run --rm web python manage.py purge_demo_data --yes
docker compose -f docker-compose.prod.yml start web scheduler
```

4. Open `https://<hostname>/`. With no church left, it redirects to `/setup/`.
5. Create the real church and its first administrator there (see First-time setup), with a real e-mail address: an `@example.org` address would be flagged by `emmaus.W001` and removed by the next purge.
6. `docker compose -f docker-compose.prod.yml run --rm web python manage.py check --deploy` must no longer show `emmaus.W001`.

To undo, restore the dump from step 1 (see Backups).

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
| Retention purge | Same `scheduler` service, once a day: see below. By hand: `python manage.py purge_expired_event_data` (dry run), then `--apply` |
| "Cortes automáticos" row on the administration home | Shown only while the event is **En curso** with automatic cutoffs on. Shows the newest automatic cutoff. It turns to "Revisar" ("El programador de cortes no ha corrido desde las 12:00...") when the first slot after the latest of the newest automatic cutoff, the start of the day, the event's last save and the confirmation of its procedures passed more than 15 minutes ago without an automatic cutoff. That is how an administrator learns the `scheduler` service stopped; opening the camp at 10:40 or turning cutoffs back on does not count the slots before (the settings form saves the event; a change made with `QuerySet.update()` in a shell does not move `updated_at` and can raise a false alarm) |

With compose: `docker compose -f docker-compose.prod.yml run --rm web python manage.py <command>`.

**Retention.** `run_report_cutoffs` runs the `purge_expired_event_data --apply` logic itself (`purge_event` in that command's module), so the period an event's settings promise ("Días que se conservan las respuestas después del evento", `retention_days`) is enforced without a separate job. It only looks at events that are **Terminado** or **Archivado** and have an end date (`ends_on`); an event left **En curso** or **En preparación** is never purged automatically, whatever its dates. Each such event is checked on the first pass after 03:00 in the event's own time zone, once per day; when `ends_on` plus `retention_days` has passed, and no safety report or confidential referral of the event is still open (pendiente or en proceso), every campero of the event is deleted (interviews, answers, consents, follow-ups and capture sessions go with them), `data_purged_at` is set and the activity log gets a "Sistema" row. Cutoffs, the team, the questionnaire and the activity log stay. A pass that checked something prints one line: `2026-11-05 03:04 · eventos terminados revisados: 2 · eventos con datos vencidos borrados: 1 · camperos borrados: 312`.

* An event with an open safety report or confidential referral keeps everything and prints `… · conservación: <slug> conserva sus datos: tiene reportes de riesgo o seguimientos confidenciales abiertos`; it is checked again the next day and purged once they are closed (atendido or cancelado).
* The event row is locked and read again before anything is deleted (`select_for_update`, still unpurged, still Terminado or Archivado, nothing open), so two overlapping runs delete and audit once; the other prints `conserva sus datos: ya se habían borrado sus datos`.
* "Once per day" is held by the `--loop` process. If you replace the service with cron, each run after 03:00 checks again; that repeats nothing, since a purged event is marked and never selected again.
* The purge does not touch database backups: a dump taken before the purge still holds the data until it is rotated away (see Backups).
* To purge an event that is still marked **En curso**, or one with open reports, change its status or close the reports, or run `purge_expired_event_data --apply` by hand, which checks neither.

## Backups

The database holds everything, including answers. `scripts/backup_db.sh [OUT_DIR] [KEEP]` writes a custom-format dump (`pg_dump -Fc`) from the running `db` container to `OUT_DIR/emmaus-db-<UTC time>.dump`, restores it into `/dev/null` with `pg_restore` inside the container (every data block is read, so a truncated or corrupt dump is rejected), keeps the newest `KEEP` (default 48) and prints the file and its size. It exits non-zero, and keeps no partial file, if anything fails; a partial file left by a killed run is removed by the next one.

* Only names the script writes (`emmaus-db-20261026T050000Z.dump`) count against `KEEP`. A dump you name by hand in the same directory (`emmaus-db-before-upgrade.dump`) is never rotated away.

* It holds no password: `pg_dump` runs inside the container as the container's own `$POSTGRES_USER` on `$POSTGRES_DB`.
* It finds the container by compose project and service (`db`). The project defaults to the checkout's directory name, normalised as compose does (lowercase, only `a-z0-9_-`, no leading `-` or `_`); if the stack was started with `-p <name>` or a `COMPOSE_PROJECT_NAME` in `.env`, pass `COMPOSE_PROJECT_NAME=<name>`, or name the container directly with `EMMAUS_DB_CONTAINER=<container>`.
* Dumps are created mode `600` (they contain every answer). The user running it needs access to Docker.

Hourly during the camp weekend (48 dumps = two days), in the crontab of a user in the `docker` group or root (`crontab -e`; adjust the checkout path). Create the directory first, owned by that user, or cron cannot even open the log: `sudo install -d -m 700 -o <user> /var/backups/emmaus`.

```cron
0 * * * * cd /srv/emmaus && scripts/backup_db.sh /var/backups/emmaus 48 >> /var/backups/emmaus/backup_db.log 2>&1
```

Cron's `PATH` is `/usr/bin:/bin`; that finds `docker` on Ubuntu. Look at the log (or the newest file's time) after the first hour: a job that is scheduled is not a job that ran.

Restore into the running `db` after stopping the app:

```bash
docker compose -f docker-compose.prod.yml stop web scheduler
docker compose -f docker-compose.prod.yml exec -T db \
  sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' \
  < /var/backups/emmaus/emmaus-db-YYYYMMDDTHHMMSSZ.dump
docker compose -f docker-compose.prod.yml start web scheduler
```

Encrypt dumps, keep copies off the server, test a restore before the camp, and apply the same retention as the event data (purging the database does not touch old dumps; delete the camp's dumps when its retention ends).

## Deploy checks

```bash
docker compose -f docker-compose.prod.yml run --rm web python manage.py check --deploy
```

* Django's own warnings: `security.W008` while `SECURE_SSL_REDIRECT=false` (expected if the proxy handles the redirect) and `security.W004` while `SECURE_HSTS_SECONDS=0`. Once HSTS is set, W004 gives way to `security.W005` and `security.W021` (no `includeSubDomains`, no preload); those two are expected, see "Recommended production values behind Traefik".
* `emmaus.W001` (from `events/checks.py`, only when `DEBUG=false`) lists active accounts ending in `@example.org` or `@demo.invalid`. Fix with `python manage.py deactivate_demo_accounts` (`--dry-run` lists them first), or remove the whole demo with `purge_demo_data` (see [Empezar de cero para un evento real](#empezar-de-cero-para-un-evento-real)). Migration `core/0004` already deactivates `participant@example.org`.

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
