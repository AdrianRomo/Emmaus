"""Lists that do not fit one screen: pages of fifty, or a short list that says it is short.

A list never stops without saying so. Either it is paged (``paginate`` with ``partials/ui/pager.html``: "Anterior /
Siguiente", the page, which rows and how many there are, keeping the filters of the address), or it is a few rows on
purpose (the three next dates on a home, ten names under a search box) and then the screen says how many there are
and where the rest is (``first_rows`` gives the total for that sentence).
"""

from __future__ import annotations

from django.core.paginator import Page, Paginator

PAGE_SIZE = 50
PAGE_PARAM = "pagina"


def paginate(request, rows, *, param: str = PAGE_PARAM, per_page: int = PAGE_SIZE) -> Page:
    """The page of ``rows`` the address asks for in ``?pagina=`` (``param`` when a screen has two lists). A number
    that does not exist gives the nearest page, never an error: a list shrinks while somebody reads it."""
    return Paginator(rows, per_page).get_page(request.GET.get(param))


def first_rows(rows, limit: int) -> tuple[list, int]:
    """(the first ``limit`` rows, how many there are in all). One query when everything fits, two when it does not."""
    found = list(rows[: limit + 1])
    if len(found) <= limit:
        return found, len(found)
    return found[:limit], rows.count()
