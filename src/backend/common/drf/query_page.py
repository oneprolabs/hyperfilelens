"""Exact count/page on one database snapshot, including out-of-range pages."""

from django.core.exceptions import EmptyResultSet
from django.db import connections


def snapshot_page(queryset, *, fields, page: int, page_size: int, metadata_queryset=None):
    """Use parameterized compiler SQL; fields/order are server-owned.

    Live authorization/filter predicates belong in queryset before this helper.
    No SELECT FOR UPDATE or stronger isolation is imposed on ordinary reads.
    Optional single-row values() metadata (e.g. a Group revision) shares this
    snapshot, even when the page is empty; that form returns a third value.
    """
    from rest_framework.exceptions import ValidationError

    if page < 1 or page_size < 1 or (page - 1) * page_size > 2**63 - 1:
        raise ValidationError("Invalid pagination range.")
    projected = queryset.values(*fields)
    try:
        sql, params = projected.query.sql_with_params()
    except EmptyResultSet:
        return ([], 0, None) if metadata_queryset is not None else ([], 0)
    # Preserve the queryset's explicit stable ordering through ordinal rows.
    ordering = queryset.query.order_by or queryset.model._meta.ordering
    if not ordering:
        ordering = ("pk",)
    from django.db.models import F, Window
    from django.db.models.functions import RowNumber

    expressions = [
        (F(field[1:]).desc() if field.startswith("-") else F(field).asc())
        if isinstance(field, str) else field
        for field in ordering
    ]
    projected = queryset.order_by().annotate(
        _page_ordinal=Window(expression=RowNumber(), order_by=expressions),
    ).values(*fields, "_page_ordinal")
    sql, params = projected.query.sql_with_params()
    metadata_cte = ""
    metadata_select = ""
    metadata_join = ""
    metadata_params = []
    if metadata_queryset is not None:
        metadata_sql, metadata_params = metadata_queryset.query.sql_with_params()
        metadata_cte = f", snapshot_metadata AS ({metadata_sql})"
        metadata_select = ", snapshot_metadata.*"
        metadata_join = " CROSS JOIN snapshot_metadata"
    statement = f"""
        WITH eligible AS ({sql}),
        amount AS (SELECT count(*) AS total FROM eligible),
        selected AS (SELECT * FROM eligible ORDER BY "_page_ordinal" LIMIT %s OFFSET %s)
        {metadata_cte}
        SELECT amount.total{metadata_select}, selected.*
        FROM amount{metadata_join} LEFT JOIN selected ON TRUE
        ORDER BY selected."_page_ordinal"
    """
    with connections[queryset.db].cursor() as cursor:
        cursor.execute(statement, [*params, page_size, (page - 1) * page_size, *metadata_params])
        columns = [column[0] for column in cursor.description][1:]
        records = cursor.fetchall()
    if not records:
        return ([], 0, None) if metadata_queryset is not None else ([], 0)
    metadata = None
    metadata_count = 0 if metadata_queryset is None else len(columns) - len(fields) - 1
    if metadata_queryset is not None:
        metadata = dict(zip(columns[:metadata_count], records[0][1:1 + metadata_count]))
    columns = columns[metadata_count:]
    rows = []
    for record in records:
        if record[-1] is not None:
            row = dict(zip(columns, record[1 + metadata_count:]))
            row.pop("_page_ordinal", None)
            rows.append(row)
    return (rows, records[0][0], metadata) if metadata_queryset is not None else (rows, records[0][0])
