"""Brute-force protection backed by the database (works across gunicorn workers)."""

from __future__ import annotations

import math
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import AuthThrottle


def consume(key: str, *, limit: int, window_seconds: int, block_seconds: int) -> int:
    """Count one attempt. Returns 0 if allowed, else the seconds to wait."""
    now = timezone.now()
    for _ in range(2):
        try:
            with transaction.atomic():
                row, _ = AuthThrottle.objects.select_for_update().get_or_create(key=key)
                if row.blocked_until and row.blocked_until > now:
                    return math.ceil((row.blocked_until - now).total_seconds())
                if now - row.window_started_at > timedelta(seconds=window_seconds) or row.blocked_until:
                    row.attempts = 0
                    row.window_started_at = now
                    row.blocked_until = None
                row.attempts += 1
                if row.attempts > limit:
                    row.blocked_until = now + timedelta(seconds=block_seconds)
                    row.save(update_fields=["attempts", "window_started_at", "blocked_until"])
                    return block_seconds
                row.save(update_fields=["attempts", "window_started_at", "blocked_until"])
                return 0
        except IntegrityError:  # two first attempts raced to create the row; retry once
            continue
    return 0


def reset(key: str) -> None:
    AuthThrottle.objects.filter(key=key).delete()


def wait_message(seconds: int) -> str:
    if seconds >= 90:
        return f"Demasiados intentos. Espera {math.ceil(seconds / 60)} minutos e inténtalo otra vez."
    return f"Demasiados intentos. Espera {seconds} segundos e inténtalo otra vez."
