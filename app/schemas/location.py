# app/schemas/location.py

from pydantic import BaseModel


class CountryResponse(BaseModel):
    iso2: str
    iso3: str
    name: str
    latitude: float | None = None
    longitude: float | None = None
    phone_code: str | None = None
    currency: str | None = None
    capital: str | None = None
    region: str | None = None
    subregion: str | None = None
    native: str | None = None
    tld: str | None = None

    class Config:
        from_attributes = True


class ProvinceResponse(BaseModel):
    code: str
    iso_code: str
    name: str
    latitude: float | None = None
    longitude: float | None = None
    country_code: str | None = None
    type: str | None = None

    class Config:
        from_attributes = True


class CityResponse(BaseModel):
    name: str
    province: str | None = None  # This will be the state_code
    latitude: float | None = None
    longitude: float | None = None
    population: int | None = None

    class Config:
        from_attributes = True


class ReverseGeocodeResponse(BaseModel):
    country: str | None = None
    country_iso2: str | None = None
    province: str | None = None
    city: str | None = None


class LocationUpdateResponse(BaseModel):
    """Response for updating user location."""

    lat: float | None = None
    lng: float | None = None
    country: str | None = None
    province: str | None = None
    city: str | None = None
    location_manual: bool
