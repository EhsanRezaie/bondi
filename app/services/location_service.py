# app/services/location_service.py
"""
Global location service using countrystatecity-countries library.

Reverse geocoding is fully OFFLINE: GPS coordinates are resolved against the
bundled city/state/country centroids (haversine nearest-neighbour) so there
are no external API calls, API keys, rate limits or provider blocks.
"""
import json
import math
from functools import lru_cache
from typing import Optional, List, Dict, Any

from app.core.config import GEO_REVERSE_MAX_DISTANCE_KM
from app.core.logging import get_logger

logger = get_logger("location_service")

# Import countrystatecity-countries
from countrystatecity_countries import (
    get_countries as _get_countries,
    get_country_by_code,
    get_states_of_country,
    get_cities_of_state,
    get_cities_of_country,
    search_cities,
)

REVERSE_GC_CACHE_TTL = 60 * 60 * 24


# ============================================================================
# Helper: Convert library objects to dicts
# ============================================================================

def _country_to_dict(country) -> dict:
    """Convert Country object to dictionary."""
    return {
        "iso2": country.iso2,
        "iso3": country.iso3,
        "name": country.name,
        "latitude": country.latitude,
        "longitude": country.longitude,
        "phone_code": country.phone_code,
        "currency": country.currency,
        "capital": country.capital,
        "region": country.region,
        "subregion": country.subregion,
        "native": country.native,
        "tld": country.tld,
    }


def _state_to_dict(state) -> dict:
    """Convert State object to dictionary."""
    return {
        "code": state.state_code,
        "iso_code": f"{state.country_code}-{state.state_code}",
        "name": state.name,
        "latitude": float(state.latitude) if state.latitude is not None else None,
        "longitude": float(state.longitude) if state.longitude is not None else None,
        "country_code": state.country_code,
        "type": getattr(state, "type", None),
    }


def _city_to_dict(city) -> dict:
    """Convert City object to dictionary."""
    return {
        "name": city.name,
        "state_code": city.state_code,
        "country_code": city.country_code,
        "latitude": float(city.latitude) if city.latitude is not None else None,
        "longitude": float(city.longitude) if city.longitude is not None else None,
        "population": getattr(city, "population", None),
        "timezone": getattr(city, "timezone", None),
    }


# ============================================================================
# Country Functions
# ============================================================================

@lru_cache(maxsize=1)
def _countries_cached() -> list[dict]:
    """Get all countries with their details."""
    try:
        countries = _get_countries()
        return sorted(
            [_country_to_dict(c) for c in countries],
            key=lambda c: c["name"]
        )
    except Exception as e:
        logger.error("Error loading countries", error=str(e))
        return []


def get_countries() -> list[dict]:
    """Get all countries sorted by name."""
    return _countries_cached()


def get_country_by_iso2(iso2: str) -> Optional[dict]:
    """Get country by ISO2 code."""
    iso2 = iso2.upper()
    for c in get_countries():
        if c["iso2"] == iso2:
            return c
    return None


# ============================================================================
# State/Province Functions
# ============================================================================

@lru_cache(maxsize=256)
def _states_cached(country_iso2: str) -> list[dict]:
    """Get all states/provinces for a country."""
    try:
        states = get_states_of_country(country_iso2.upper())
        return sorted(
            [_state_to_dict(s) for s in states],
            key=lambda s: s["name"]
        )
    except Exception as e:
        logger.error("Error loading states", country=country_iso2, error=str(e))
        return []


def get_states(country_iso2: str) -> list[dict]:
    """Get all states/provinces for a country."""
    return _states_cached(country_iso2.upper())


def get_state_by_code(country_iso2: str, code: str) -> Optional[dict]:
    """Get state by code."""
    code = code.upper()
    for s in get_states(country_iso2):
        if s["code"].upper() == code or s["iso_code"].upper() == code:
            return s
    return None


def get_state_by_name(country_iso2: str, name: str) -> Optional[dict]:
    """Get state by name (case-insensitive)."""
    name_lower = name.lower().strip()
    for s in get_states(country_iso2):
        if s["name"].lower() == name_lower:
            return s
    return None


# ============================================================================
# City Functions
# ============================================================================

@lru_cache(maxsize=256)
def _cities_by_state_cached(country_iso2: str, state_code: str) -> list[dict]:
    """Get all cities for a specific state/province."""
    try:
        cities = get_cities_of_state(country_iso2.upper(), state_code.upper())
        
        if not cities:
            return []
        
        result = [_city_to_dict(c) for c in cities]
        result.sort(key=lambda c: (-(c["population"] or 0) if c["population"] else 0, c["name"]))
        return result
        
    except Exception as e:
        logger.error("Error loading cities for state", state_code=state_code, error=str(e))
        return []


@lru_cache(maxsize=256)
def _cities_by_country_cached(country_iso2: str) -> list[dict]:
    """Get all cities for a country (cached)."""
    try:
        cities = get_cities_of_country(country_iso2.upper())
        
        if not cities:
            return []
        
        result = [_city_to_dict(c) for c in cities]
        result.sort(key=lambda c: (-(c["population"] or 0) if c["population"] else 0, c["name"]))
        return result
        
    except Exception as e:
        logger.error("Error loading cities for country", country=country_iso2, error=str(e))
        return []


def get_cities(
    country_iso2: str,
    state_code: Optional[str] = None
) -> list[dict]:
    """
    Get cities for a country, optionally filtered by state/province.
    """
    country_iso2 = country_iso2.upper()
    
    if state_code:
        return _cities_by_state_cached(country_iso2, state_code.upper())
    else:
        return _cities_by_country_cached(country_iso2)


def get_cities_by_state_name(
    country_iso2: str,
    state_name: str
) -> list[dict]:
    """
    Get cities for a state/province by name.
    """
    country_iso2 = country_iso2.upper()
    state = get_state_by_name(country_iso2, state_name)
    if state:
        return get_cities(country_iso2, state["code"])
    return []


def search_cities_by_name(
    country_iso2: str,
    query: str,
    state_code: Optional[str] = None
) -> list[dict]:
    """
    Search cities by name.
    """
    try:
        results = search_cities(
            country_iso2.upper(),
            state_code.upper() if state_code else None,
            query
        )
        return [_city_to_dict(c) for c in results]
    except Exception as e:
        logger.error("Error searching cities", error=str(e))
        return []


def get_city_centroid(
    country_iso2: str,
    city_name: str,
    state_code: Optional[str] = None
) -> Optional[dict]:
    """
    Get centroid coordinates for a city.
    """
    results = search_cities_by_name(country_iso2, city_name, state_code)
    if results:
        return results[0]
    return None


def clear_cache() -> None:
    """Clear all cached data."""
    logger.info("Clearing all location caches")
    _countries_cached.cache_clear()
    _states_cached.cache_clear()
    _cities_by_state_cached.cache_clear()
    _cities_by_country_cached.cache_clear()


# ============================================================================
# Reverse Geocoding (OFFLINE — nearest city centroid)
# ============================================================================

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two coordinates in kilometres."""
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


@lru_cache(maxsize=512)
def _geocode_cache_key(lat: float, lng: float, radius: float) -> Optional[dict]:
    """In-process cached lookup; radius is only a cache-buster."""
    return _offline_reverse_uncached(lat, lng)


@lru_cache(maxsize=1)
def _country_centroids() -> List[Dict[str, Any]]:
    """Cached list of every country with its centroid (iso2, name, lat, lon)."""
    countries = _countries_cached()
    return [
        {
            "iso2": c["iso2"],
            "name": c["name"],
            "lat": float(c["latitude"]),
            "lon": float(c["longitude"]),
        }
        for c in countries
        if c.get("latitude") is not None and c.get("longitude") is not None
    ]


def _nearest_country(lat: float, lng: float) -> Optional[dict]:
    """Nearest country centroid to a coordinate (only ~250 entries — cheap)."""
    best = None
    best_km = float("inf")
    for row in _country_centroids():
        km = _haversine_km(lat, lng, row["lat"], row["lon"])
        if km < best_km:
            best_km = km
            best = row
    return best


def _offline_reverse_uncached(lat: float, lng: float) -> Optional[dict]:
    """
    Offline reverse geocode without building a global index.

    Strategy (all from the bundled dataset, no network):
      1. Find the nearest country by its centroid (~250 lookups, cached list).
      2. Load ONLY that country's states + cities and pick the nearest city
         (single-country index, built once per country and cached).
      3. Fall back to the state centroid, then the country centroid.
    """
    country = _nearest_country(lat, lng)
    if country is None:
        return None

    iso2 = country["iso2"]
    country_name = country["name"]

    states = {s["code"]: s["name"] for s in _states_cached(iso2)}
    cities = _cities_by_country_cached(iso2)

    best_city = None
    best_city_km = float("inf")

    for city in cities:
        clat = city.get("latitude")
        clon = city.get("longitude")
        if clat is None or clon is None:
            continue
        km = _haversine_km(lat, lng, float(clat), float(clon))
        if km < best_city_km:
            best_city_km = km
            best_city = city

    def as_result(city_name: Optional[str], province: Optional[str]) -> dict:
        return {
            "country": country_name,
            "country_iso2": iso2,
            "province": province,
            "city": city_name,
        }

    if best_city is None:
        return as_result(None, None)

    city_name = best_city["name"] if best_city_km <= GEO_REVERSE_MAX_DISTANCE_KM else None
    province = states.get(best_city.get("state_code") or "")
    return as_result(city_name, province)


def _offline_reverse(lat: float, lng: float) -> Optional[dict]:
    """Reverse geocode using the bundled offline dataset (long names)."""
    return _geocode_cache_key(round(lat, 4), round(lng, 4), GEO_REVERSE_MAX_DISTANCE_KM)


async def reverse_geocode(lat: float, lng: float, redis_client=None) -> Optional[dict]:
    """Convert GPS coordinates to location text using the offline dataset.

    The 24h Redis cache is still used for coordinated caching; there is no
    external service and therefore no rate limiter needed.
    """
    cache_key = f"geo:reverse:{round(lat, 3)}:{round(lng, 3)}"

    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception as e:
            logger.warning("Redis cache read failed", error=str(e))

    result = _offline_reverse(lat, lng)
    if result is None:
        return None

    if redis_client:
        try:
            await redis_client.setex(cache_key, REVERSE_GC_CACHE_TTL, json.dumps(result))
        except Exception as e:
            logger.warning("Redis cache write failed", error=str(e))

    return result


# =============================================================================
# LocationService Class
# =============================================================================

class LocationService:
    """Service class for location operations."""
    
    @staticmethod
    def get_countries() -> List[Dict[str, Any]]:
        return get_countries()
    
    @staticmethod
    def get_states(country_iso2: str) -> List[Dict[str, Any]]:
        return get_states(country_iso2)
    
    @staticmethod
    def get_cities(
        country_iso2: str,
        state_code: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return get_cities(country_iso2, state_code)
    
    @staticmethod
    def get_cities_by_state_name(
        country_iso2: str,
        state_name: str
    ) -> List[Dict[str, Any]]:
        return get_cities_by_state_name(country_iso2, state_name)
    
    @staticmethod
    def search_cities(
        country_iso2: str,
        query: str,
        state_code: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return search_cities_by_name(country_iso2, query, state_code)
    
    @staticmethod
    def get_city_centroid(
        country_iso2: str,
        city_name: str,
        state_code: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        return get_city_centroid(country_iso2, city_name, state_code)
    
    @staticmethod
    async def reverse_geocode(lat: float, lng: float) -> Optional[Dict[str, Any]]:
        from app.core.redis import redis_client
        return await reverse_geocode(lat, lng, redis_client=redis_client)
    
    @staticmethod
    def get_state_by_code(country_iso2: str, code: str) -> Optional[Dict[str, Any]]:
        return get_state_by_code(country_iso2, code)
    
    @staticmethod
    def get_state_by_name(country_iso2: str, name: str) -> Optional[Dict[str, Any]]:
        return get_state_by_name(country_iso2, name)