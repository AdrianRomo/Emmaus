# Emmaus

Emmaus is a Django monolith for a church discipleship pilot. It helps pastors, mentors, admins, and participants keep discipleship journeys visible without replacing human shepherding.

## What Is Included

- Real first-church setup flow with no seed-user dependency
- Church-scoped roles: `church_admin`, `pastor`, `mentor`, `participant`
- Secure invitation links with expiration and single-use acceptance
- Participant onboarding with consent and emergency disclaimer acceptance
- Editable discipleship paths, lessons, reflection questions, enrollments, progress, and answers
- Participant questions, leader responses, pastoral escalations, and follow-up requests
- Pastoral notes hidden from participants and filtered by leader role
- Optional human-reviewed AI response drafts
- Synchronous email notifications with console email in local development
- Docker Compose for local PostgreSQL and first production deployment
- Healthcheck at `/health/`
- Tests for setup, invitations, access control, participant actions, leader responses, inactivity follow-ups, and health

## Local Development

Create an environment file:

```bash
cp .env.example .env
```

Run with Docker Compose:

```bash
make build
make up
```

In another terminal:

```bash
make migrate
make create-starter-path
```

Open `http://localhost:8000`.

## Real First-Church Setup

1. Visit `http://localhost:8000`.
2. If no church exists, Emmaus redirects to `/setup/`.
3. Enter the church name, slug, first admin name, email, and password.
4. After setup, create the starter path:

```bash
make create-starter-path
```

5. Use the admin dashboard to invite pastors, mentors, and participants.

The main flow does not require demo data.

## Optional Demo Data

For local testing only:

```bash
docker compose run --rm web python manage.py seed_dev_data
```

Demo users use `password12345`:

- `admin@example.org`
- `pastor@example.org`
- `mentor@example.org`
- `participant@example.org`

## Useful Commands

```bash
make up
make down
make build
make migrate
make createsuperuser
make create-starter-path
make test
make collectstatic
make logs
```

Without Docker, you can run:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

If `DATABASE_URL` is not set, Django uses local SQLite for convenience. Docker Compose uses PostgreSQL.

## Production Deployment Notes

Use `docker-compose.prod.yml` as the starting point:

```bash
cp .env.example .env
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml run --rm web python manage.py migrate
docker compose -f docker-compose.prod.yml run --rm web python manage.py collectstatic --noinput
docker compose -f docker-compose.prod.yml up -d
```

Set production values in `.env`:

- `SECRET_KEY`
- `DEBUG=false`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- `POSTGRES_PASSWORD`
- `DATABASE_URL`

Put Emmaus behind HTTPS before real participant use.

## Email Configuration

Local development defaults to console email. For SMTP, set:

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.example.org
EMAIL_PORT=587
EMAIL_HOST_USER=your-user
EMAIL_HOST_PASSWORD=your-password
EMAIL_USE_TLS=true
DEFAULT_FROM_EMAIL=Emmaus <no-reply@example.org>
```

If email sending fails, Emmaus logs the error and keeps the user flow moving. Invitation pages also show a manual copy link for early pilot operations.

## AI Draft Configuration

AI is disabled by default and is never shown directly to participants.

```env
LLM_ENABLED=true
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your-key
LLM_MODEL=gpt-4.1-mini
```

Leaders must review, edit, approve, or reject generated drafts before any response is sent.

## UI System

Emmaus uses Django templates with a self-contained local stylesheet, so the interface does not depend on CDN access:

- `static/css/emmaus.css` defines the warm visual palette, authenticated app shell, role-aware sidebar, mobile nav, buttons, cards, badges, responsive utility layer, and focus states.
- `templates/base.html` loads the local stylesheet with Django static files and does not include Tailwind CDN scripts.
- `templates/partials/` contains reusable pieces for page headers, messages, form fields, empty states, stat cards, progress bars, and status badges.
- Human-centered lists use card rows instead of dense tables where the record represents a person, question, or follow-up.
- Tables are reserved for invitation management and other scan-heavy administrative views.

The design language should stay calm, warm, minimal, and pastoral. Avoid generic SaaS patterns, loud colors, decorative religious imagery, and surveillance-style copy.

## Tests

```bash
make test
```

Or locally:

```bash
.venv/bin/python manage.py test
```

## Known Limitations

- Email is synchronous for MVP simplicity.
- No SMS or WhatsApp delivery is implemented.
- No billing, mobile app, or public social features are included.
- Multi-church support is model-scoped, but the pilot UI assumes a user works in one church at a time.
- Static files are collected into the app container; a production reverse proxy or object storage setup can be added later.

## Recommended Next Phase

- Add audit logs for sensitive pastoral records.
- Add richer lesson editing and reordering.
- Add notification preferences.
- Add backup/restore runbooks.
- Add production observability, uptime checks, and error reporting.
