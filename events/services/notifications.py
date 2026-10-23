"""E-mail notices. They never include answers, names of camperos, or report details."""

from __future__ import annotations

from django.contrib.auth.models import User

from core.models import Role
from core.services import send_email_safely

from ..models import EventMembership, FollowUp, MembershipRole


def _care_team_emails(event) -> list[str]:
    sensitive = EventMembership.objects.filter(
        event=event, is_active=True, can_view_sensitive=True, user__is_active=True
    ).values_list("user__email", flat=True)
    admins = EventMembership.objects.filter(
        event=event, is_active=True, role=MembershipRole.ADMIN, user__is_active=True
    ).values_list("user__email", flat=True)
    church_admins = User.objects.filter(
        church_roles__church=event.church, church_roles__role=Role.CHURCH_ADMIN, is_active=True
    ).values_list("email", flat=True)
    return sorted({email for email in [*sensitive, *admins, *church_admins] if email})


def notify_safety_report(follow_up: FollowUp) -> bool:
    recipients = _care_team_emails(follow_up.event)
    if not recipients:
        return False
    return send_email_safely(
        f"Emmaus: reporte urgente en {follow_up.event.name}",
        (
            "Se registró un reporte de una posible situación de riesgo.\n\n"
            "Por privacidad, este correo no incluye nombres ni detalles. "
            "Ingresa a Emmaus con tu cuenta para atenderlo y sigue el procedimiento de protección de tu iglesia."
        ),
        recipients,
    )
