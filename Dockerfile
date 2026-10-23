FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Build the hashed, compressed static manifest that WhiteNoise serves when DEBUG=false.
RUN DEBUG=false SECRET_KEY=build-only-not-secret python manage.py collectstatic --noinput

EXPOSE 8000

# Health check: ask with an allowed Host header and as if behind the HTTPS proxy, so it works
# with real ALLOWED_HOSTS and SECURE_SSL_REDIRECT=true (/health/ is also exempt from the redirect).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import http.client,os; h=(os.environ.get('ALLOWED_HOSTS') or 'localhost').split(',')[0].strip().lstrip('.'); h='localhost' if h in ('', '*') else h; c=http.client.HTTPConnection('127.0.0.1', 8000, timeout=4); c.request('GET', '/health/', headers={'Host': h, 'X-Forwarded-Proto': 'https'}); raise SystemExit(0 if c.getresponse().status == 200 else 1)"

CMD ["gunicorn", "emmaus.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--threads", "4", "--timeout", "60"]
