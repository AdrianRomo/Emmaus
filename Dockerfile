FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged runtime user with a fixed uid/gid. It owns nothing in /app.
RUN groupadd --system --gid 10001 emmaus \
    && useradd --system --uid 10001 --gid emmaus --no-create-home --home-dir /nonexistent \
       --shell /usr/sbin/nologin emmaus

COPY requirements.txt .
RUN pip install -r requirements.txt

# .dockerignore keeps .env, .git, .venv, local databases, staticfiles/ and caches out of this copy.
COPY . .

# Build the hashed, compressed static manifest that WhiteNoise serves when DEBUG=false (as root).
# Then make /app readable but not writable by the app user, whatever umask the checkout had: the app
# writes only to /app/media (the church logo; a volume in docker-compose.prod.yml), the one directory it owns.
# Other uploads stay in memory or the system temp dir.
RUN DEBUG=false SECRET_KEY=build-only-not-secret python manage.py collectstatic --noinput \
    && chmod -R a+rX,go-w /app \
    && mkdir /app/media && chown 10001:10001 /app/media

USER emmaus:emmaus

EXPOSE 8000

# Health check: ask with an allowed Host header and as if behind the HTTPS proxy, so it works
# with real ALLOWED_HOSTS and SECURE_SSL_REDIRECT=true (/health/ is also exempt from the redirect).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import http.client,os; h=(os.environ.get('ALLOWED_HOSTS') or 'localhost').split(',')[0].strip().lstrip('.'); h='localhost' if h in ('', '*') else h; c=http.client.HTTPConnection('127.0.0.1', 8000, timeout=4); c.request('GET', '/health/', headers={'Host': h, 'X-Forwarded-Proto': 'https'}); raise SystemExit(0 if c.getresponse().status == 200 else 1)"

CMD ["gunicorn", "emmaus.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--threads", "4", "--timeout", "60"]
