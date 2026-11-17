"""The one read path for module flags (decision 0004): ``enabled(church, "community")``."""

from django.conf import settings

from .models import ChurchSettings

MODULES = frozenset(
    field.name.removesuffix("_enabled") for field in ChurchSettings._meta.fields if field.name.endswith("_enabled")
)


def _check(module: str) -> None:
    if module not in MODULES:
        raise ValueError(f"Unknown module {module!r}; known modules: {', '.join(sorted(MODULES))}.")


def enabled(church, module: str) -> bool:
    """True when ``church`` uses ``module``. Off for no church, no row, or the kill switch
    (``settings.EMMAUS_V2_MODULES`` false). An unknown module raises, so a typo fails a test."""
    _check(module)
    if not settings.EMMAUS_V2_MODULES or church is None:
        return False
    return bool(
        ChurchSettings.objects.filter(church=church).values_list(f"{module}_enabled", flat=True).first()
    )


def set_enabled(church, module: str, value: bool) -> bool:
    """Switch ``module`` for ``church``; creates the row on the first write. True when it changed."""
    _check(module)
    row, _ = ChurchSettings.objects.get_or_create(church=church)
    field = f"{module}_enabled"
    if getattr(row, field) == value:
        return False
    setattr(row, field, value)
    row.save(update_fields=[field, "updated_at"])
    return True
