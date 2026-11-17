"""Church policies: the rules a church may one day set for itself, in one place (decision 0015).

Today every value is the product default. The functions take the church so that a screen under
"Configuración de la iglesia" can later store a church's own choice without touching the callers.
Some values have a floor the platform enforces whatever a church chooses: a safeguard or a privacy
minimum is not a preference.
"""

# Fewer people than this and a total says who: no attendance breakdown, no count of answers,
# no results of a survey. Platform floor; a church may raise it, never lower it.
MIN_REPORTING_SIZE_FLOOR = 5
# Days after an event's last day during which who went can give an opinion.
FEEDBACK_OPEN_DAYS = 14
# Months a guardian's authorization is kept after the event (initial policy, to be approved: issue #75).
CONSENT_RETENTION_MONTHS = 24


def min_reporting_size(church=None) -> int:
    return MIN_REPORTING_SIZE_FLOOR


def feedback_open_days(church=None) -> int:
    return FEEDBACK_OPEN_DAYS


def urgent_notice_roles(church=None) -> frozenset[str]:
    """Who may publish an urgent notice: church roles, by their value in ``core.models.Role``."""
    return frozenset({"church_admin"})
