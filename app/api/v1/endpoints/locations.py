# app/api/v1/endpoints/locations.py

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.cache import (
    TTL_LOCATIONS,
    cache_get,
    cache_set,
    invalidate_user_cache,
    key_cities,
    key_countries,
    key_provinces,
)
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.core.logging import get_logger
from app.core.redis import redis_client
from app.db.session import get_session
from app.models.user import User
from app.models.user_profile import UserProfile
from app.schemas.location import (
    CityResponse,
    CountryResponse,
    LocationUpdateResponse,
    ProvinceResponse,
    ReverseGeocodeResponse,
)
from app.services.location_service import (
    LocationService,
    get_country_by_iso2,
    reverse_geocode,
)

logger = get_logger("locations_endpoint")

router = APIRouter(prefix="/locations", tags=["locations"])


# ============================================================================
# Public reference endpoints (no auth)
# ============================================================================


@router.get("/countries", response_model=list[CountryResponse])
@limiter.limit("60/minute")
async def list_countries(request: Request, response: Response):
    """All countries sorted alphabetically."""
    response.headers["Cache-Control"] = "public, max-age=604800"
    cached = await cache_get(redis_client, key_countries())
    if cached:
        return cached
    # Library's first dataset load does blocking file IO — keep off the loop
    data = await run_in_threadpool(LocationService.get_countries)
    await cache_set(redis_client, key_countries(), data, TTL_LOCATIONS)
    return data


@router.get("/states", response_model=list[ProvinceResponse])
@limiter.limit("60/minute")
async def list_states(
    request: Request,
    country: str = Query(
        ..., min_length=2, max_length=2, description="ISO 3166-1 alpha-2 country code"
    ),
):
    """States / provinces for a country."""
    country = country.upper()
    if not await run_in_threadpool(get_country_by_iso2, country):
        raise HTTPException(status_code=404, detail=f"Country '{country}' not found")
    cache_key = key_provinces(country)
    cached = await cache_get(redis_client, cache_key)
    if cached:
        return cached
    data = await run_in_threadpool(LocationService.get_states, country)
    await cache_set(redis_client, cache_key, data, TTL_LOCATIONS)
    return data


@router.get("/cities", response_model=list[CityResponse])
@limiter.limit("60/minute")
async def list_cities(
    request: Request,
    country: str = Query(
        ..., min_length=2, max_length=2, description="ISO 3166-1 alpha-2 country code"
    ),
    state_code: str | None = Query(
        None, min_length=1, max_length=10, description="State code to filter cities"
    ),
    state_name: str | None = Query(
        None, min_length=1, max_length=100, description="State name to filter cities"
    ),
):
    """
    Cities for a country.
    Can filter by state_code OR state_name.

    Examples:
        GET /api/v1/locations/cities?country=IR
        GET /api/v1/locations/cities?country=IR&state_code=23
        GET /api/v1/locations/cities?country=IR&state_name=Tehran
    """
    country = country.upper()
    if not await run_in_threadpool(get_country_by_iso2, country):
        raise HTTPException(status_code=404, detail=f"Country '{country}' not found")

    # Build cache key from available params
    province_key = state_code or state_name or "_all"
    cache_key = key_cities(country, province_key)
    cached = await cache_get(redis_client, cache_key)
    if cached:
        return cached

    cities = []

    if state_code:
        logger.info("Getting cities by state_code", country=country, state_code=state_code)
        cities = await run_in_threadpool(LocationService.get_cities, country, state_code)
    elif state_name:
        logger.info("Getting cities by state_name", country=country, state_name=state_name)
        cities = await run_in_threadpool(
            LocationService.get_cities_by_state_name, country, state_name
        )
    else:
        logger.info("Getting all cities", country=country)
        cities = await run_in_threadpool(LocationService.get_cities, country)

    logger.info("Returning cities", country=country, count=len(cities))

    # If no cities, return empty list
    if not cities:
        return []

    # Convert to response model
    result = []
    for city in cities:
        result.append(
            CityResponse(
                name=city["name"],
                province=city.get("state_code"),
                latitude=city.get("latitude"),
                longitude=city.get("longitude"),
                population=city.get("population"),
            )
        )

    await cache_set(
        redis_client, cache_key, [r.model_dump(mode="json") for r in result], TTL_LOCATIONS
    )
    return result


@router.get("/cities/search", response_model=list[CityResponse])
@limiter.limit("60/minute")
async def search_cities_endpoint(
    request: Request,
    country: str = Query(
        ..., min_length=2, max_length=2, description="ISO 3166-1 alpha-2 country code"
    ),
    query: str = Query(..., min_length=2, max_length=100, description="City name to search"),
    state_code: str | None = Query(
        None, min_length=1, max_length=10, description="Optional state code filter"
    ),
):
    """Search cities by name (autocomplete)."""
    country = country.upper()
    if not await run_in_threadpool(get_country_by_iso2, country):
        raise HTTPException(status_code=404, detail=f"Country '{country}' not found")

    cities = await run_in_threadpool(LocationService.search_cities, country, query, state_code)

    return [
        CityResponse(
            name=city["name"],
            province=city.get("state_code"),
            latitude=city.get("latitude"),
            longitude=city.get("longitude"),
            population=city.get("population"),
        )
        for city in cities
    ]


@router.get("/reverse-geocode", response_model=ReverseGeocodeResponse)
@limiter.limit("30/minute")
async def reverse_geocode_endpoint(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
):
    """Convert GPS coordinates to country / state / city names."""
    from app.core.redis import redis_client

    result = await reverse_geocode(lat, lng, redis_client=redis_client)
    if result is None:
        raise HTTPException(
            status_code=503,
            detail="Reverse geocoding service unavailable. Please enter location manually.",
        )
    return ReverseGeocodeResponse(**result)


@router.get("/city-centroid", response_model=CityResponse)
@limiter.limit("60/minute")
async def city_centroid(
    request: Request,
    country: str = Query(..., min_length=2, max_length=2),
    city: str = Query(..., min_length=1, max_length=100),
    state_code: str | None = Query(None, min_length=1, max_length=10),
):
    """Get lat/lng for a manually selected city."""
    country = country.upper()
    # Dataset lookups read bundled files on first call — off the event loop
    centroid = await run_in_threadpool(LocationService.get_city_centroid, country, city, state_code)
    if centroid is None:
        raise HTTPException(
            status_code=404,
            detail=f"City '{city}' not found in country '{country}'",
        )
    return CityResponse(
        name=centroid["name"],
        province=centroid.get("state_code"),
        latitude=centroid.get("latitude"),
        longitude=centroid.get("longitude"),
        population=centroid.get("population"),
    )


# ============================================================================
# Authenticated: update user location
# ============================================================================


@router.patch("/me/location-gps", response_model=LocationUpdateResponse)
@limiter.limit("10/minute")
async def update_location_gps(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Update user location from GPS coordinates."""
    from sqlalchemy import select

    from app.core.redis import redis_client

    result = await reverse_geocode(lat, lng, redis_client=redis_client)

    profile_result = await session.execute(
        select(UserProfile).where(UserProfile.user_id == current_user.id)
    )
    profile = profile_result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    profile.lat = lat
    profile.lng = lng
    profile.location_manual = False

    if result:
        profile.country = result.get("country")
        profile.province = result.get("province")
        profile.city = result.get("city")

    await session.commit()
    await session.refresh(profile)
    await invalidate_user_cache(redis_client, current_user.id)

    return {
        "lat": profile.lat,
        "lng": profile.lng,
        "country": profile.country,
        "province": profile.province,
        "city": profile.city,
        "location_manual": profile.location_manual,
    }


@router.patch("/me/location-manual", response_model=LocationUpdateResponse)
@limiter.limit("10/minute")
async def update_location_manual(
    request: Request,
    country: str = Query(..., min_length=2, max_length=100),
    province: str = Query(..., min_length=1, max_length=100),
    city: str = Query(..., min_length=1, max_length=100),
    country_iso2: str = Query(..., min_length=2, max_length=2),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Update user location from manual selection."""
    from sqlalchemy import select

    profile_result = await session.execute(
        select(UserProfile).where(UserProfile.user_id == current_user.id)
    )
    profile = profile_result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    profile.country = country
    profile.province = province
    profile.city = city
    profile.location_manual = True

    # Get state code by name if needed (dataset file reads — off the loop)
    state = await run_in_threadpool(
        LocationService.get_state_by_name, country_iso2.upper(), province
    )
    state_code = state["code"] if state else None

    # Get centroid for the city
    centroid = await run_in_threadpool(
        LocationService.get_city_centroid, country_iso2.upper(), city, state_code
    )
    if centroid:
        profile.lat = centroid["latitude"]
        profile.lng = centroid["longitude"]

    await session.commit()
    await session.refresh(profile)
    await invalidate_user_cache(redis_client, current_user.id)

    return {
        "lat": profile.lat,
        "lng": profile.lng,
        "country": profile.country,
        "province": profile.province,
        "city": profile.city,
        "location_manual": profile.location_manual,
    }
