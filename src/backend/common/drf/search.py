"""Bounded, additive aliases for localized text search before pagination.

The optional search_aliases query parameter is a JSON array of at most 64
nonempty strings, each at most 200 characters. Aliases are ORed with the
original search term within the caller's existing queryset and field allowlist.
They have no effect without a nonempty original search term.

Alert record requests may opt into search_context=global to also search the
record UUID, status, and severity, preserving the global search UI's fields
without widening the default alert-list search behavior.
"""

import json

from django.db.models import Q
from rest_framework.exceptions import ValidationError


def parse_search_aliases(params) -> tuple[str, ...]:
    raw = params.get("search_aliases", "")
    if not raw:
        return ()
    if len(raw) > 20000:
        raise ValidationError({"search_aliases": "Search aliases are too large."})
    try:
        aliases = json.loads(raw)
    except (ValueError, TypeError, RecursionError) as error:
        raise ValidationError({"search_aliases": "Expected a JSON array of strings."}) from error
    if not isinstance(aliases, list) or len(aliases) > 64 or any(
        not isinstance(alias, str) or not alias.strip() or len(alias) > 200
        for alias in aliases
    ):
        raise ValidationError({"search_aliases": "Expected at most 64 nonempty strings of at most 200 characters."})
    return tuple(dict.fromkeys(alias.strip() for alias in aliases))


def text_search_query(search: str, fields: tuple[str, ...], aliases: tuple[str, ...] = ()) -> Q:
    query = Q()
    if search:
        for term in dict.fromkeys((search, *aliases)):
            for field in fields:
                query |= Q(**{f"{field}__icontains": term})
    return query
