from datetime import date
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# ============ Enums ============


class Gender(str, Enum):
    male = "male"
    female = "female"


class SexualOrientation(str, Enum):
    straight = "straight"
    gay = "gay"
    bisexual = "bisexual"
    pansexual = "pansexual"
    asexual = "asexual"


class BodyType(str, Enum):
    slim = "slim"
    average = "average"
    athletic = "athletic"
    curvy = "curvy"
    muscular = "muscular"
    overweight = "overweight"


class RelationshipStatus(str, Enum):
    single = "single"
    divorced = "divorced"
    widowed = "widowed"
    separated = "separated"


class LivingSituation(str, Enum):
    alone = "alone"
    with_family = "with_family"
    with_roommate = "with_roommate"
    with_partner = "with_partner"


class ChildrenStatus(str, Enum):
    have_children = "have_children"
    want_children = "want_children"
    dont_want_children = "dont_want_children"
    open_to_children = "open_to_children"


class HereFor(str, Enum):
    long_term_relationship = "long_term_relationship"
    casual_dating = "casual_dating"
    marriage = "marriage"
    new_friends = "new_friends"
    not_sure_yet = "not_sure_yet"


class Pets(str, Enum):
    dog = "dog"
    cat = "cat"
    both = "both"
    other_pet = "other_pet"
    no_pets = "no_pets"
    loves_pets = "loves_pets"


class WorkoutFrequency(str, Enum):
    never = "never"
    occasionally = "occasionally"
    regularly = "regularly"
    daily = "daily"


class ZodiacSign(str, Enum):
    aries = "aries"
    taurus = "taurus"
    gemini = "gemini"
    cancer = "cancer"
    leo = "leo"
    virgo = "virgo"
    libra = "libra"
    scorpio = "scorpio"
    sagittarius = "sagittarius"
    capricorn = "capricorn"
    aquarius = "aquarius"
    pisces = "pisces"


class SmokingStatus(str, Enum):
    never = "never"
    occasionally = "occasionally"
    regularly = "regularly"


class DrinkingStatus(str, Enum):
    never = "never"
    socially = "socially"
    regularly = "regularly"


class EducationLevel(str, Enum):
    high_school = "high_school"
    bachelor = "bachelor"
    master = "master"
    phd = "phd"


class PoliticalOrientation(str, Enum):
    liberal = "liberal"
    conservative = "conservative"
    moderate = "moderate"
    apolitical = "apolitical"


# ============ Phone validation helpers ============


def validate_e164_phone(value: str) -> str:
    """Normalize + validate an E.164 phone number (e.g. +989379191281)."""
    v = value.strip()
    if not v.startswith("+"):
        raise ValueError("Phone number must include country code, e.g. +989379191281")
    digits = v[1:]
    if not digits.isdigit():
        raise ValueError("Phone number may only contain digits after the country code")
    if not 8 <= len(digits) <= 15:
        raise ValueError("Phone number must be between 8 and 15 digits")
    return v


# ============ Step 1: Request Code (SMS OTP) ============


class RequestCodeRequest(BaseModel):
    phone: str = Field(..., description="E.164 phone number, e.g. +989379191281")
    language: str = Field(default="fa", description="Language code for SMS message (fa or en)")

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return validate_e164_phone(v)

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        if v not in ("fa", "en"):
            return "fa"
        return v


class RequestCodeResponse(BaseModel):
    message: str = "If this phone number is registered, a verification code has been sent."
    phone: str
    expires_in: int = 300
    resend_in: int = 60


# ============ Step 2: Verify Code ============


class VerifyCodeRequest(BaseModel):
    phone: str = Field(..., description="E.164 phone number, e.g. +989379191281")
    code: str = Field(..., min_length=6, max_length=6, description="6-digit verification code")
    referral_code: str | None = Field(
        None, min_length=8, max_length=8, description="Optional referral code"
    )

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return validate_e164_phone(v)


class VerifyCodeResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserProfileResponse"
    is_new_user: bool = False
    account_restored: bool = False


# ============ Step 3: Onboarding Complete ============


class UserPromptCreateRequest(BaseModel):
    prompt_id: UUID
    answer: str = Field(..., max_length=500)


class OnboardingCompleteRequest(BaseModel):
    """Complete user profile after phone verification."""

    # Identity
    name: str = Field(..., min_length=2, max_length=100)
    birth_date: date
    gender: Gender
    sexual_orientation: SexualOrientation | None = None
    bio: str | None = Field(None, max_length=500)

    # Appearance
    height: int | None = Field(None, ge=50, le=250, description="Height in cm")
    weight: int | None = Field(None, ge=30, le=300, description="Weight in kg")
    body_type: BodyType | None = None

    # Lifestyle
    relationship_status: RelationshipStatus | None = None
    living_situation: LivingSituation | None = None
    children_status: ChildrenStatus | None = None
    smoking: SmokingStatus | None = None
    drinking: DrinkingStatus | None = None
    here_for: HereFor | None = None
    pets: Pets | None = None
    workout_frequency: WorkoutFrequency | None = None
    zodiac_sign: ZodiacSign | None = None

    # Background
    education: EducationLevel | None = None
    workplace: str | None = Field(None, max_length=100)
    religion: str | None = Field(None, max_length=50)
    ethnicity: str | None = Field(None, max_length=50)
    political_orientation: PoliticalOrientation | None = None
    languages: list[str] | None = None

    # Location (required)
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    country: str | None = Field(None, max_length=100)
    province: str | None = Field(None, max_length=100)
    city: str | None = Field(None, max_length=100)

    # Optional extras
    interests: list[str] | None = Field(None, description="List of interest names")
    prompts: list[UserPromptCreateRequest] | None = None

    @field_validator("gender", mode="before")
    @classmethod
    def validate_gender(cls, v):
        if isinstance(v, Gender):
            return v.value
        return v


# ============ Authenticated Response ============


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserProfileResponse"


# ============ Refresh Token ============


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class RefreshTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


# ============ Logout ============


class LogoutRequest(BaseModel):
    refresh_token: str


# ============ Forward References ============

# Import here to avoid circular import
from app.schemas.user import UserProfileResponse

VerifyCodeResponse.model_rebuild()
AuthResponse.model_rebuild()
