from django.conf import settings
from django.core.checks import Tags, Warning, register


@register(Tags.security, deploy=True)
def demo_accounts_inactive(app_configs, **kwargs):
    """Warn when example.org demo logins are active on a production deployment."""
    from django.contrib.auth.models import User
    from django.db.utils import DatabaseError

    if settings.DEBUG:
        return []
    try:
        from django.db.models import Q

        demo = Q(email__iendswith="@example.org") | Q(email__iendswith="@demo.invalid")
        active = list(User.objects.filter(demo, is_active=True).values_list("email", flat=True)[:5])
    except DatabaseError:
        return []
    if not active:
        return []
    return [
        Warning(
            "Hay cuentas de demostración activas: " + ", ".join(active),
            hint="Desactívalas con: python manage.py deactivate_demo_accounts",
            id="emmaus.W001",
        )
    ]
