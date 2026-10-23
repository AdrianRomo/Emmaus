"""Brute-force protection backed by the database (works across gunicorn workers).

Attempts are counted *before* the password is checked, under a row lock. After ``limit``
attempts the key is blocked; each further block doubles (up to ``max_block_seconds``) and
the strike count is forgotten only after a full quiet window. So a patient attacker gets
a handful of guesses per hour, not a fresh allowance every minute.
"""

from __future__ import annotations

import math
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import AuthThrottle


def consume(key: str, *, limit: int, window_seconds: int, block_seconds: int, max_block_seconds: int | None = None) -> int:
    """Count one attempt. Returns 0 if allowed, else the seconds to wait."""
    max_block_seconds = max_block_seconds or block_seconds * 16
    now = timezone.now()
    for _ in range(2):
        try:
            with transaction.atomic():
                row, _ = AuthThrottle.objects.select_for_update().get_or_create(key=key)
                if row.blocked_until and row.blocked_until > now:
                    return math.ceil((row.blocked_until - now).total_seconds())
                if now - row.window_started_at > timedelta(seconds=window_seconds):
                    row.attempts = 0
                    row.strikes = 0
                    row.window_started_at = now
                if row.blocked_until:  # a block just ended: a smaller allowance, same window
                    row.blocked_until = None
                    row.attempts = max(row.attempts - limit // 2, 0)
                row.attempts += 1
                if row.attempts > limit:
                    row.strikes += 1
                    seconds = min(block_seconds * 2 ** (row.strikes - 1), max_block_seconds)
                    row.blocked_until = now + timedelta(seconds=seconds)
                    row.window_started_at = now
                    row.save()
                    return seconds
                row.save()
                return 0
        except IntegrityError:  # two first attempts raced to create the row; retry once
            continue
    return block_seconds  # fail closed: if the counter cannot be written, do not check the password


def reset(key: str) -> None:
    AuthThrottle.objects.filter(key=key).delete()


def wait_message(seconds: int) -> str:
    if seconds >= 90:
        return f"Demasiados intentos. Espera {math.ceil(seconds / 60)} minutos e inténtalo otra vez."
    return f"Demasiados intentos. Espera {seconds} segundos e inténtalo otra vez."
