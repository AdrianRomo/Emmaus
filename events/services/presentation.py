"""Privacy-safe serializer for "Modo presentación" and report PDFs meant for projection.

The template for presentations receives *only* what this module returns. It never sees
participant names, coordinator or tutor names, answers, free text, safety reports, or
spiritual/support aggregates. Counts about people who asked for help are suppressed
below the event's minimum group size and rounded above it.
"""

from __future__ import annotations

from datetime import datetime

from .metrics import STALE_MINUTES, eta_minutes, format_duration, suppress


def _team_rows(teams: list[dict]) -> list[dict]:
    # Stable numbering by tutor id, so "Equipo 2" means the same team in every cutoff.
    ids = sorted(int(t["key"].split(":")[1]) for t in teams if t["key"].startswith("equipo:"))
    number_for = {f"equipo:{tutor_id}": index for index, tutor_id in enumerate(ids, start=1)}
    rows = []
    for team in teams:
        if team["key"] == "sin_equipo":
            label = "Sin equipo asignado"
        else:
            label = team.get("team_name_public") or f"Equipo {number_for.get(team['key'], '')}".strip()
        rows.append(
            {
                "label": label,
                "eligible": team["eligible"],
                "completed": team["completed"],
                "in_progress": team["in_progress"],
                "pending": team["pending"],
                "percent": team["percent"],
            }
        )
    return rows


def build_deck(
    payload: dict,
    *,
    event_name: str,
    kind_label: str,
    k: int,
    team_names: dict[str, str] | None = None,
    released_follow_ups: dict | None = None,
    released_at=None,
) -> dict:
    """``released_follow_ups`` comes from the latest saved cutoff (see reports.release_cells):
    follow-up counts on a projector never change because of a single person."""
    team_names = team_names or {}
    progress = payload["progress"]
    teams = [{**team, "team_name_public": team_names.get(team["key"], "")} for team in payload.get("teams", [])]
    team_rows = _team_rows(teams)
    fu = released_follow_ups or {}
    has = released_follow_ups is not None
    follow = {
        "requested": suppress(fu.get("requested_total") if has else None, k),
        "done": suppress(fu.get("done") if has else None, k),
        "open": suppress(fu.get("open_total") if has else None, k),
        "to_evaluate": suppress(fu.get("to_evaluate") if has else None, k),
        "as_of": released_at,
    }
    rate = payload["throughput"]["per_hour"]
    eta = eta_minutes(progress["pending"], rate)

    steps = []
    if progress["eligible"] and progress["pending"]:
        text = "Completar 1 entrevista pendiente." if progress["pending"] == 1 else f"Completar {progress['pending']} entrevistas pendientes."
        if eta:
            text += f" Al ritmo actual, unas {format_duration(eta)}."
        steps.append(text)
    overall = progress["percent"]
    if overall is not None:
        for row in team_rows:
            if row["label"] != "Sin equipo asignado" and row["percent"] is not None and row["percent"] <= overall - 15 and row["pending"] >= 5:
                steps.append(f"{row['label']} necesita apoyo: lleva {row['percent']} % y le faltan {row['pending']} entrevistas.")
    stale = payload["attention"]["stale_interviews"]
    if stale:
        steps.append(f"Retomar {stale} {'entrevista detenida' if stale == 1 else 'entrevistas detenidas'} hace más de {STALE_MINUTES} minutos.")
    unassigned = payload["attention"]["unassigned_participants"]
    if unassigned:
        steps.append(f"Asignar coordinador a {unassigned} {'campero' if unassigned == 1 else 'camperos'}.")
    if has and follow["to_evaluate"]["display"] != "0":
        steps.append(f"Decidir el seguimiento de {follow['to_evaluate']['display'].lower()} entrevistas por evaluar.")
    if has and follow["open"]["display"] != "0":
        steps.append(f"Asignar líderes a {follow['open']['display'].lower()} seguimientos abiertos.")
    if not steps and progress["eligible"]:
        steps.append("Todas las entrevistas están completas. Gracias, equipo.")

    as_of = payload["data_as_of"]
    return {
        "event_name": event_name,
        "kind_label": kind_label,
        "scope_label": payload["scope"]["label"] if payload["scope"]["key"] == "evento" else "Equipo",
        "as_of": datetime.fromisoformat(as_of) if isinstance(as_of, str) else as_of,
        "progress": {
            "eligible": progress["eligible"],
            "completed": progress["completed"],
            "in_progress": progress["in_progress"],
            "pending": progress["pending"],
            "not_started": progress["not_started"],
            "percent": progress["percent"],
        },
        "rate": rate,
        "eta": format_duration(eta) if eta else "",
        "timeline": [{"label": p["label"], "day": p.get("day", ""), "count": p["count"], "cumulative": p["cumulative"]} for p in payload["timeline"]["points"]],
        "teams": team_rows,
        "follow_ups": follow,
        "next_steps": steps[:5],
        "k": k,
    }
