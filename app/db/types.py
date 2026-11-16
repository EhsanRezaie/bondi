# app/db/types.py
"""Custom SQLAlchemy types for Postgres-only columns."""

from sqlalchemy.types import UserDefinedType


class Geography(UserDefinedType):
    """Minimal PostGIS geography column type — no geoalchemy2 dependency.

    DDL-only by design: the `location` column is a stored generated column
    (derived from lat/lng via the table-level Computed expression), so app
    code never writes it directly — drift between lat/lng and geography is
    impossible. Cast to this type in queries: `.cast(Geography)`.
    """

    def get_col_spec(self, **kw) -> str:
        return "geography(Point,4326)"
