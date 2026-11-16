# tests/test_location_service.py
"""Unit tests for app/services/location_service.py (offline reverse geocoder).

Pure-logic tests plus Redis-cache behaviour with a mocked redis client — the
service never touches the network by design.
"""

from unittest.mock import AsyncMock, MagicMock

from app.services import location_service as loc
from app.services.location_service import LocationService

TEHRAN = (35.6892, 51.3890)


class TestHaversine:
    def test_same_point_is_zero(self):
        assert loc._haversine_km(*TEHRAN, *TEHRAN) == 0.0

    def test_one_degree_longitude_on_equator(self):
        # 1 degree of longitude at the equator ≈ 111.19 km.
        km = loc._haversine_km(0.0, 0.0, 0.0, 1.0)
        assert 111.0 < km < 111.5

    def test_symmetry(self):
        assert loc._haversine_km(*TEHRAN, 25.27, 55.29) == loc._haversine_km(
            25.27, 55.29, *TEHRAN
        )


class TestCountryStateCityLookups:
    def test_get_countries_nonempty(self):
        countries = loc.get_countries()
        assert len(countries) > 100
        assert all("iso2" in c for c in countries)

    def test_get_country_by_iso2(self):
        iran = loc.get_country_by_iso2("IR")
        assert iran is not None
        assert iran["iso2"] == "IR"

    def test_get_country_unknown_returns_none(self):
        assert loc.get_country_by_iso2("XX") is None

    def test_states_of_country(self):
        states = loc.get_states("IR")
        assert states, "Iran must have states in the bundled dataset"
        assert all("code" in s and "name" in s for s in states)

    def test_get_state_by_code(self):
        state = loc.get_state_by_code("IR", "07")
        assert state is not None
        assert loc.get_state_by_code("IR", "ZZ") is None

    def test_get_state_by_name(self):
        state = loc.get_state_by_name("IR", "Tehran")
        assert state is not None
        assert loc.get_state_by_name("IR", "Atlantis") is None

    def test_get_cities_for_country(self):
        cities = loc.get_cities("IR")
        assert cities
        assert all("name" in c for c in cities)

    def test_get_cities_by_state(self):
        state_code = loc.get_state_by_name("IR", "Tehran")["code"]
        cities = loc.get_cities("IR", state_code)
        assert cities

    def test_get_cities_by_state_name(self):
        cities = loc.get_cities_by_state_name("IR", "Tehran")
        assert cities

    def test_search_cities_by_name(self):
        hits = loc.search_cities_by_name("IR", "Tehran")
        assert hits
        assert any(c["name"] == "Tehran" for c in hits)

    def test_get_city_centroid(self):
        centroid = loc.get_city_centroid("IR", "Tehran")
        assert centroid is not None
        assert centroid["latitude"] is not None
        assert loc.get_city_centroid("IR", "No Such City") is None


class TestNearestCountry:
    def test_tehran_is_iran(self):
        assert loc._nearest_country(*TEHRAN)["iso2"] == "IR"


class TestOfflineReverse:
    def test_tehran_coordinates(self):
        result = loc._offline_reverse_uncached(*TEHRAN)
        assert result is not None
        assert result["country_iso2"] == "IR"
        assert result["province"] is not None
        # Tehran the city may or may not win the nearest-centroid contest
        # depending on dataset granularity, but SOME city must be within 25km.
        assert result["city"] is not None

    def test_mid_ocean_has_no_city(self):
        # Southern Pacific point: nearest country centroid is thousands of km
        # away — far beyond GEO_REVERSE_MAX_DISTANCE_KM (25).
        result = loc._offline_reverse_uncached(-50.0, -140.0)
        if result is not None:
            assert result["city"] is None

    def test_cached_path_delegates(self):
        # _offline_reverse is the lru-cached entrypoint used in production.
        result = loc._offline_reverse(*TEHRAN)
        assert result is not None
        assert result["country_iso2"] == "IR"


class TestReverseGeocodeRedis:
    async def test_cache_roundtrip(self):
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock(return_value=True)

        first = await loc.reverse_geocode(*TEHRAN, redis_client=redis)
        assert first is not None
        redis.setex.assert_awaited_once()

        # Same rounded coordinates → identical cache key; the JSON stored by
        # the first write must round-trip through the read path unchanged.
        import json

        key, ttl, payload = redis.setex.await_args.args
        assert key == "geo:reverse:35.689:51.389"
        assert ttl == loc.REVERSE_GC_CACHE_TTL

        redis.get = AsyncMock(return_value=payload.encode("utf-8"))
        cached = await loc.reverse_geocode(
            TEHRAN[0] + 0.0001, TEHRAN[1] - 0.0001, redis_client=redis
        )
        assert cached == first
        assert json.loads(payload) == first

    async def test_cache_hit_skips_compute(self):
        stored = {
            "country": "X", "country_iso2": "XX", "province": "P", "city": "C"
        }
        redis = MagicMock()
        redis.get = AsyncMock(
            return_value=b'{"country": "X", "country_iso2": "XX", "province": "P", "city": "C"}'
        )
        result = await loc.reverse_geocode(*TEHRAN, redis_client=redis)
        assert result == stored

    async def test_redis_read_failure_still_geocodes(self):
        redis = MagicMock()
        redis.get = AsyncMock(side_effect=RuntimeError("redis down"))
        redis.setex = AsyncMock(return_value=True)
        result = await loc.reverse_geocode(*TEHRAN, redis_client=redis)
        assert result is not None
        assert result["country_iso2"] == "IR"

    async def test_redis_write_failure_still_returns(self):
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock(side_effect=RuntimeError("redis down"))
        result = await loc.reverse_geocode(*TEHRAN, redis_client=redis)
        assert result is not None
        assert result["country_iso2"] == "IR"

    async def test_no_redis_computes_directly(self):
        result = await loc.reverse_geocode(*TEHRAN, redis_client=None)
        assert result is not None


class TestServiceFacade:
    def test_static_delegation(self):
        assert LocationService.get_countries()
        assert LocationService.get_states("IR")
        assert LocationService.get_cities("IR")
        assert LocationService.get_cities_by_state_name("IR", "Tehran")
        assert LocationService.search_cities("IR", "Tehran")
        assert LocationService.get_city_centroid("IR", "Tehran")
        assert LocationService.get_state_by_code("IR", "07")
        assert LocationService.get_state_by_name("IR", "Tehran")

    def test_clear_cache(self):
        loc.clear_cache()  # must not raise
