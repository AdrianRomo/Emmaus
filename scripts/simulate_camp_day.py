"""Load test: 10 interview stations working at once against a running Emmaus server.

Synthetic data only. Usage (from the repo root, same DATABASE_URL as the server):

    python manage.py seed_camp_demo --slug carga-2026 --completed 0 --reset --password Carga-2026
    python scripts/simulate_camp_day.py --base http://127.0.0.1:8000 --event carga-2026 --password Carga-2026

Each station logs in as a different coordinador, chooses its computer, and completes the
assisted interview of every pending campero assigned to it through the real HTTP forms.
Two "pastor" pollers fetch the live dashboard fragments every 2 seconds meanwhile.
At the end the script checks database integrity and prints latency percentiles.
"""

import argparse
import http.cookiejar
import os
import random
import re
import statistics
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "emmaus.settings")

import django  # noqa: E402

django.setup()

from events.models import Event, EventMembership, EventParticipant, Interview, MembershipRole  # noqa: E402

LATENCIES = defaultdict(list)
ERRORS = []
LOCK = threading.Lock()


class Browser:
    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def csrf(self):
        return next((c.value for c in self.jar if c.name == "csrftoken"), "")

    def request(self, label, path, data=None, headers=None):
        url = path if path.startswith("http") else self.base + path
        body = None
        hdrs = {"Referer": self.base + "/", **(headers or {})}
        if data is not None:
            data = {**data, "csrfmiddlewaretoken": self.csrf()}
            body = urllib.parse.urlencode(data, doseq=True).encode()
        start = time.perf_counter()
        try:
            with self.opener.open(urllib.request.Request(url, data=body, headers=hdrs), timeout=30) as response:
                html = response.read().decode("utf-8", "replace")
                final = response.geturl()
                status = response.status
        except urllib.error.HTTPError as exc:
            html, final, status = exc.read().decode("utf-8", "replace"), url, exc.code
        elapsed = (time.perf_counter() - start) * 1000
        with LOCK:
            LATENCIES[label].append(elapsed)
            if status >= 400:
                ERRORS.append(f"{label} {status} {url}")
        return status, final, html


def station(index, base, slug, membership, password, campers, done):
    b = Browser(base)
    b.request("login_form", "/cuenta/entrar/")
    b.request("login", "/cuenta/entrar/", {"username": membership.user.username, "password": password})
    b.request("station_form", f"/eventos/{slug}/computadora/")
    station_id = Event.objects.get(slug=slug).stations.get(number=index + 1).id
    b.request("station", f"/eventos/{slug}/computadora/", {"station": station_id, "siguiente": f"/eventos/{slug}/mi-inicio/"})
    rng = random.Random(index)
    for camper in campers:
        b.request("start_form", f"/eventos/{slug}/camperos/{camper.public_id}/entrevista/")
        status, url, html = b.request(
            "start", f"/eventos/{slug}/camperos/{camper.public_id}/entrevista/",
            {"mode": "asistida", "consent_assent": "on", "consent_consent": "on", "consent_guardian": "on"},
        )
        for _ in range(12):
            if "/revision/" in url or "/completada/" in url:
                break
            revision = re.search(r'name="revision" value="(\d+)"', html)
            fields = re.findall(r'name="(q_\d+)" value="([\w-]+)"', html)
            text_field = re.search(r'<textarea[^>]*name="(q_\d+)"', html)
            if not revision or not (fields or text_field):
                ERRORS.append(f"step parse failed for {url}")
                break
            if fields:
                first_field = fields[0][0]
                answer = {first_field: rng.choice([v for f, v in fields if f == first_field])}
            else:
                answer = {text_field.group(1): rng.choice(["", "Texto sintético de prueba."])}
            status, url, html = b.request("step", url, {"revision": revision.group(1), "direction": "next", **answer})
        revision = re.search(r'name="revision" value="(\d+)"', html)
        if revision:
            status, url, html = b.request("finalize", url, {"revision": revision.group(1), "follow_up_decision": rng.choice(["no_requerido", "requerido", "por_evaluar"])})
        with LOCK:
            done.append(camper.id)
        b.request("next_search", f"/eventos/{slug}/entrevistas/iniciar/")


def poller(base, slug, user, password, stop):
    b = Browser(base)
    b.request("login_form", "/cuenta/entrar/")
    b.request("login", "/cuenta/entrar/", {"username": user.username, "password": password})
    while not stop.is_set():
        b.request("live_pastor", f"/eventos/{slug}/resumen/", headers={"X-Requested-With": "fetch"})
        time.sleep(2)


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p))] if values else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--event", default="carga-2026")
    parser.add_argument("--password", required=True)
    parser.add_argument("--stations", type=int, default=10)
    args = parser.parse_args()

    event = Event.objects.get(slug=args.event)
    coordinators = list(EventMembership.objects.filter(event=event, role=MembershipRole.COORDINATOR).select_related("user").order_by("id"))[: args.stations]
    plan = {m.id: list(EventParticipant.objects.filter(event=event, coordinator=m, interview_status="pendiente", is_active=True)) for m in coordinators}
    total = sum(len(v) for v in plan.values())
    pastor = EventMembership.objects.filter(event=event, role=MembershipRole.PASTOR).select_related("user").first().user
    print(f"{len(coordinators)} estaciones, {total} entrevistas planeadas")

    done, stop = [], threading.Event()
    pollers = [threading.Thread(target=poller, args=(args.base, args.event, pastor, args.password, stop)) for _ in range(2)]
    stations = [threading.Thread(target=station, args=(i, args.base, args.event, m, args.password, plan[m.id], done)) for i, m in enumerate(coordinators)]
    started = time.perf_counter()
    for t in pollers + stations:
        t.start()
    for t in stations:
        t.join()
    stop.set()
    for t in pollers:
        t.join()
    elapsed = time.perf_counter() - started

    completed = Interview.objects.filter(event=event, status="completada").count()
    duplicates = (
        Interview.objects.filter(event=event).exclude(status="descartada").values("participant").order_by().annotate(n=django.db.models.Count("id")).filter(n__gt=1).count()
    )
    print(f"Tiempo total: {elapsed:.1f} s · entrevistas recorridas: {len(done)} · completadas en BD: {completed} · duplicadas: {duplicates}")
    print(f"{'acción':<14}{'n':>6}{'p50 ms':>10}{'p95 ms':>10}{'max ms':>10}")
    for label, values in sorted(LATENCIES.items()):
        print(f"{label:<14}{len(values):>6}{statistics.median(values):>10.0f}{pct(values, 0.95):>10.0f}{max(values):>10.0f}")
    print(f"Errores HTTP: {len(ERRORS)}")
    for line in ERRORS[:10]:
        print("  ", line)
    sys.exit(1 if ERRORS or duplicates else 0)


if __name__ == "__main__":
    main()
