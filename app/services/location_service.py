# app/services/location_service.py
"""
Global location service using countrystatecity-countries library.
"""
import json
import httpx
from functools import lru_cache
from typing import Optional, List, Dict, Any

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
# Nominatim usage policy: valid contact info + ~1 req/sec. Keep the app name
# fixed but use a real admin contact so OSM reach out to us (not IP-ban) on abuse.
NOMINATIM_USER_AGENT = "Bondi (prod@example.com)"
NOMINATIM_GLOBAL_RATE_SEC = 1  # global token bucket, 1 req/sec


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
# Reverse Geocoding
# ============================================================================

async def reverse_geocode(lat: float, lng: float, redis_client=None) -> Optional[dict]:
    """Convert GPS coordinates to location text using Nominatim API.

    The 24h cache is checked first; an un-cached miss consumes a global
    Nominatim rate-limit token (1 req/sec) so the free OSM API doesn't ban us.
    """
    cache_key = f"geo:reverse:{round(lat, 3)}:{round(lng, 3)}"

    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception as e:
            logger.warning("Redis cache read failed", error=str(e))

        # Global token-bucket limiter: one Nominatim call per second.
        try:
            allowed = await redis_client.set(
                "rl:nominatim:global", "1", nx=True, ex=NOMINATIM_GLOBAL_RATE_SEC
            )
        except Exception as e:
            logger.warning("Nominatim limiter check failed", error=str(e))
            allowed = True
        if not allowed:
            return None

    result = await _nominatim_reverse(lat, lng)
    if result is None:
        return None

    if redis_client:
        try:
            await redis_client.setex(cache_key, REVERSE_GC_CACHE_TTL, json.dumps(result))
        except Exception as e:
            logger.warning("Redis cache write failed", error=str(e))

    return result


async def _nominatim_reverse(lat: float, lng: float) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={
                    "lat": lat,
                    "lon": lng,
                    "format": "json",
                    "addressdetails": 1,
                    "accept-language": "en",
                },
                headers={"User-Agent": NOMINATIM_USER_AGENT},
            )
            response.raise_for_status()
            data = response.json()

        address = data.get("address", {})
        
        return {
            "country": address.get("country"),
            "country_iso2": address.get("country_code", "").upper() or None,
            "province": (
                address.get("state")
                or address.get("province")
                or address.get("region")
                or address.get("county")
            ),
            "city": (
                address.get("city")
                or address.get("town")
                or address.get("municipality")
                or address.get("village")
                or address.get("hamlet")
            ),
        }

    except httpx.TimeoutException:
        logger.error("Nominatim timed out", lat=lat, lng=lng)
        return None
    except Exception as e:
        logger.error("Reverse geocoding failed", lat=lat, lng=lng, error=str(e))
        return None


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