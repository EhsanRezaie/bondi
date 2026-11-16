# alembic/versions/e7f8a9b0c1d2_postgis_location_geography.py
"""PostGIS geography location column + GiST index.

- Enables the postgis extension (ships in the postgis/postgis image).
- Adds user_profiles.location as a STORED generated column derived from
  lat/lng, so it can never drift from the source floats.
- GiST index for index-assisted ST_DWithin/ST_Distance (discover refill,
  search) replacing per-row haversine trig.
"""

from alembic import op

revision = "e7f8a9b0c1d2"
down_revision = "bfb4c45e6582"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute(
        "ALTER TABLE user_profiles ADD COLUMN location geography(Point,4326) "
        "GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography) STORED"
    )
    op.execute("CREATE INDEX idx_profiles_location_gist ON user_profiles USING gist (location)")


def downgrade() -> None:
    # Extension intentionally kept on downgrade — dropping it could break
    # other objects; it is inert if unused.
    op.drop_index("idx_profiles_location_gist", table_name="user_profiles")
    op.drop_column("user_profiles", "location")
