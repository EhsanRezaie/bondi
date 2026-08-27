from typing import Optional, List
from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import datetime, timezone, date
from uuid import UUID


# ============ Request Schemas ============

class UserUpdateRequest(BaseModel):
    """Update user profile - all fields optional."""
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    bio: Optional[str] = Field(None, max_length=500)
    gender: Optional[str] = None
    sexual_orientation: Optional[str] = None
    birth_date: Optional[date] = None
    height: Optional[int] = None
    weight: Optional[int] = None
    body_type: Optional[str] = None
    relationship_status: Optional[str] = None
    living_situation: Optional[str] = None
    children_status: Optional[str] = None
    smoking: Optional[str] = None
    drinking: Optional[str] = None
    here_for: Optional[str] = None
    pets: Optional[str] = None
    workout_frequency: Optional[str] = None
    zodiac_sign: Optional[str] = None
    education: Optional[str] = None
    workplace: Optional[str] = None
    religion: Optional[str] = None
    ethnicity: Optional[str] = None
    political_orientation: Optional[str] = None
    languages: Optional[List[str]] = None

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, v: str) -> str:
        if v is not None and v not in ["male", "female"]:
            raise ValueError("Gender must be 'male' or 'female'")
        return v

    @field_validator("sexual_orientation")
    @classmethod
    def validate_sexual_orientation(cls, v: str) -> str:
        if v is not None and v not in ["straight", "gay", "bisexual", "pansexual", "asexual"]:
            raise ValueError("Invalid sexual orientation")
        return v

    @field_validator("body_type")
    @classmethod
    def validate_body_type(cls, v: str) -> str:
        if v is not None and v not in ["slim", "average", "athletic", "curvy", "muscular", "overweight"]:
            raise ValueError("Invalid body type")
        return v

    @field_validator("relationship_status")
    @classmethod
    def validate_relationship_status(cls, v: str) -> str:
        if v is not None and v not in ["single", "divorced", "widowed", "separated"]:
            raise ValueError("Invalid relationship status")
        return v

    @field_validator("living_situation")
    @classmethod
    def validate_living_situation(cls, v: str) -> str:
        if v is not None and v not in ["alone", "with_family", "with_roommate", "with_partner"]:
            raise ValueError("Invalid living situation")
        return v

    @field_validator("children_status")
    @classmethod
    def validate_children_status(cls, v: str) -> str:
        if v is not None and v not in ["have_children", "want_children", "dont_want_children", "open_to_children"]:
            raise ValueError("Invalid children status")
        return v

    @field_validator("here_for")
    @classmethod
    def validate_here_for(cls, v: str) -> str:
        if v is not None and v not in ["long_term_relationship", "casual_dating", "marriage", "new_friends", "not_sure_yet"]:
            raise ValueError("Invalid here_for value")
        return v

    @field_validator("pets")
    @classmethod
    def validate_pets(cls, v: str) -> str:
        if v is not None and v not in ["dog", "cat", "both", "other_pet", "no_pets", "loves_pets"]:
            raise ValueError("Invalid pets value")
        return v

    @field_validator("workout_frequency")
    @classmethod
    def validate_workout_frequency(cls, v: str) -> str:
        if v is not None and v not in ["never", "occasionally", "regularly", "daily"]:
            raise ValueError("Invalid workout frequency")
        return v

    @field_validator("zodiac_sign")
    @classmethod
    def validate_zodiac_sign(cls, v: str) -> str:
        if v is not None and v not in ["aries", "taurus", "gemini", "cancer", "leo", "virgo",
                                       "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces"]:
            raise ValueError("Invalid zodiac sign")
        return v

    @field_validator("smoking")
    @classmethod
    def validate_smoking(cls, v: str) -> str:
        if v is not None and v not in ["never", "occasionally", "regularly"]:
            raise ValueError("Invalid smoking status")
        return v

    @field_validator("drinking")
    @classmethod
    def validate_drinking(cls, v: str) -> str:
        if v is not None and v not in ["never", "socially", "regularly"]:
            raise ValueError("Invalid drinking status")
        return v

    @field_validator("education")
    @classmethod
    def validate_education(cls, v: str) -> str:
        if v is not None and v not in ["high_school", "bachelor", "master", "phd"]:
            raise ValueError("Invalid education level")
        return v

    @field_validator("height")
    @classmethod
    def validate_height(cls, v: int) -> int:
        if v is not None and (v < 50 or v > 250):
            raise ValueError("Height must be between 50cm and 250cm")
        return v

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, v: int) -> int:
        if v is not None and (v < 30 or v > 300):
            raise ValueError("Weight must be between 30kg and 300kg")
        return v

    @field_validator("birth_date", mode="before")
    @classmethod
    def validate_birth_date(cls, v):
        """Convert string to date object."""
        if v is None:
            return None
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            try:
                return datetime.strptime(v, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError("Invalid birth_date format. Use YYYY-MM-DD")
        raise ValueError("birth_date must be a string or date")


class LocationTextUpdateRequest(BaseModel):
    """Update user location with text fields."""
    country: Optional[str] = Field(None, max_length=100)
    province: Optional[str] = Field(None, max_length=100)
    city: Optional[str] = Field(None, max_length=100)


class LocationUpdateRequest(BaseModel):
    """Update user location with GPS coordinates."""
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


# ============ Response Schemas ============

class UserSettingsResponse(BaseModel):
    """Response schema for user settings."""
    hide_last_seen: bool = False
    hide_online_status: bool = False
    push_enabled: bool = True
    like_notifications: bool = True
    match_notifications: bool = True
    message_notifications: bool = True
    language: str = "fa"
    dark_mode: bool = False

    class Config:
        from_attributes = True


class UserProfileResponse(BaseModel):
    """Response schema for user profile (excludes sensitive data)."""
    id: UUID
    phone: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    sexual_orientation: Optional[str] = None
    bio: Optional[str] = None
    height: Optional[int] = None
    weight: Optional[int] = None
    body_type: Optional[str] = None
    relationship_status: Optional[str] = None
    living_situation: Optional[str] = None
    children_status: Optional[str] = None
    smoking: Optional[str] = None
    drinking: Optional[str] = None
    here_for: Optional[str] = None
    pets: Optional[str] = None
    workout_frequency: Optional[str] = None
    zodiac_sign: Optional[str] = None
    education: Optional[str] = None
    workplace: Optional[str] = None
    religion: Optional[str] = None
    ethnicity: Optional[str] = None
    political_orientation: Optional[str] = None
    languages: Optional[List[str]] = None
    country: Optional[str] = None
    province: Optional[str] = None
    city: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    location_manual: bool = False
    is_premium: bool = False
    premium_until: Optional[datetime] = None
    is_verified: bool = False
    is_active: bool
    is_profile_complete: bool = False
    profile_completion: int = 0
    created_at: datetime
    last_seen_at: Optional[datetime] = None
    settings: Optional[UserSettingsResponse] = None
    main_photo_url: Optional[str] = None
    birth_date: Optional[str] = None
    interests: Optional[List[str]] = None
    prompts: Optional[List[dict]] = None

    class Config:
        from_attributes = True

    @model_validator(mode='before')
    @classmethod
    def extract_profile_fields(cls, values):
        """
        Extract all profile fields from user.profile.
        This runs BEFORE validation so Pydantic sees these fields as present.
        """
        if hasattr(values, 'profile'):
            profile = values.profile
            if profile:
                values.name = profile.name
                values.age = profile.age
                values.gender = profile.gender
                values.sexual_orientation = profile.sexual_orientation
                values.bio = profile.bio
                values.height = profile.height
                values.weight = profile.weight
                values.body_type = profile.body_type
                values.relationship_status = profile.relationship_status
                values.living_situation = profile.living_situation
                values.children_status = profile.children_status
                values.smoking = profile.smoking
                values.drinking = profile.drinking
                values.here_for = profile.here_for
                values.pets = profile.pets
                values.workout_frequency = profile.workout_frequency
                values.zodiac_sign = profile.zodiac_sign
                values.education = profile.education
                values.workplace = profile.workplace
                values.religion = profile.religion
                values.ethnicity = profile.ethnicity
                values.political_orientation = profile.political_orientation
                values.languages = profile.languages
                values.country = profile.country
                values.province = profile.province
                values.city = profile.city
                values.lat = profile.lat
                values.lng = profile.lng
                values.location_manual = profile.location_manual
                values.is_premium = profile.is_premium
                values.premium_until = profile.premium_until
                values.is_verified = profile.is_verified
                values.is_profile_complete = profile.is_profile_complete
                values.profile_completion = cls._compute_profile_completion(profile, values)
                values.birth_date = profile.birth_date.isoformat() if profile.birth_date else None

                # interests: safe to assign directly because `interests` is NOT
                # a relationship on the User ORM model — no descriptor to trip over.
                values.interests = (
                    [ui.interest.name for ui in values.user_interests]
                    if values.user_interests else []
                )

                # prompts: IS a relationship on User, so assigning directly triggers
                # SQLAlchemy's collection descriptor and crashes with dicts.
                # Fix: snapshot the ORM collection first, then bypass the descriptor
                # by writing to __dict__ directly.
                raw_prompts = list(values.prompts) if values.prompts else []
                values.__dict__['prompts'] = [
                    {"prompt_id": str(up.prompt_id), "answer": up.answer}
                    for up in raw_prompts
                ]

        return values

    @classmethod
    def _compute_profile_completion(cls, profile, user) -> int:
        """Weighted profile completion percent (0-100)."""
        checks = [
            profile.name,
            profile.birth_date,
            profile.gender,
            profile.sexual_orientation,
            profile.bio,
            profile.height,
            profile.weight,
            profile.languages,
            profile.education,
            profile.workplace,
            profile.city,
            profile.here_for,
            profile.pets,
            profile.workout_frequency,
            profile.zodiac_sign,
        ]
        photo_count = len([p for p in (user.photos or []) if p.status == "approved"])
        if photo_count > 0:
            checks.append(True)
        interests = getattr(user, "user_interests", None)
        if interests:
            checks.append(True)
        prompts = getattr(user, "prompts", None)
        if prompts:
            checks.append(True)
        filled = sum(1 for c in checks if c not in (None, "", []))
        return round((filled / len(checks)) * 100)


class PublicUserResponse(BaseModel):
    """Public profile response for other users (respects privacy settings)."""
    id: UUID
    name: str
    age: int
    gender: str
    sexual_orientation: Optional[str] = None
    bio: Optional[str] = None
    height: Optional[int] = None
    weight: Optional[int] = None
    body_type: Optional[str] = None
    relationship_status: Optional[str] = None
    education: Optional[str] = None
    workplace: Optional[str] = None
    country: Optional[str] = None
    province: Optional[str] = None
    city: Optional[str] = None
    main_photo_url: Optional[str] = None
    is_premium: bool
    is_verified: bool = False
    last_seen_at: Optional[datetime] = None
    is_online: Optional[bool] = None
    interests: Optional[List[str]] = None

    class Config:
        from_attributes = True

    @classmethod
    def from_user_with_privacy(cls, user, settings, current_user_id: UUID = None):
        """Create public response respecting privacy settings."""
        is_self = current_user_id and user.id == current_user_id

        if is_self:
            return cls(
                id=user.id,
                name=user.profile.name,
                age=user.profile.age,
                gender=user.profile.gender,
                sexual_orientation=user.profile.sexual_orientation,
                bio=user.profile.bio,
                height=user.profile.height,
                weight=user.profile.weight,
                body_type=user.profile.body_type,
                relationship_status=user.profile.relationship_status,
                education=user.profile.education,
                workplace=user.profile.workplace,
                country=user.profile.country,
                province=user.profile.province,
                city=user.profile.city,
                main_photo_url=None,
                is_premium=user.profile.is_premium,
                is_verified=user.profile.is_verified,
                last_seen_at=user.last_seen_at,
                is_online=user.last_seen_at and (datetime.now(timezone.utc) - user.last_seen_at).seconds < 300,
                interests=None
            )

        if settings.hide_last_seen:
            return cls(
                id=user.id,
                name=user.profile.name,
                age=user.profile.age,
                gender=user.profile.gender,
                sexual_orientation=user.profile.sexual_orientation,
                bio=user.profile.bio,
                height=user.profile.height,
                weight=user.profile.weight,
                body_type=user.profile.body_type,
                relationship_status=user.profile.relationship_status,
                education=user.profile.education,
                workplace=user.profile.workplace,
                country=user.profile.country,
                province=user.profile.province,
                city=user.profile.city,
                main_photo_url=None,
                is_premium=user.profile.is_premium,
                is_verified=user.profile.is_verified,
                last_seen_at=None,
                is_online=None,
                interests=None
            )
        else:
            return cls(
                id=user.id,
                name=user.profile.name,
                age=user.profile.age,
                gender=user.profile.gender,
                sexual_orientation=user.profile.sexual_orientation,
                bio=user.profile.bio,
                height=user.profile.height,
                weight=user.profile.weight,
                body_type=user.profile.body_type,
                relationship_status=user.profile.relationship_status,
                education=user.profile.education,
                workplace=user.profile.workplace,
                country=user.profile.country,
                province=user.profile.province,
                city=user.profile.city,
                main_photo_url=None,
                is_premium=user.profile.is_premium,
                is_verified=user.profile.is_verified,
                last_seen_at=user.last_seen_at,
                is_online=user.last_seen_at and (datetime.now(timezone.utc) - user.last_seen_at).seconds < 300,
                interests=None
            )


class LocationTextUpdateResponse(BaseModel):
    """Response for PATCH /users/me/location-text"""
    country: Optional[str] = None
    province: Optional[str] = None
    city: Optional[str] = None
    location_manual: bool


class DeleteAccountResponse(BaseModel):
    """Response for account deletion."""
    message: str = "Account deleted successfully"


class UserInterestResponse(BaseModel):
    """Response schema for user interests."""
    id: UUID
    name: str
    category: Optional[str] = None
    icon: Optional[str] = None

    class Config:
        from_attributes = True


class UserPromptResponse(BaseModel):
    """Response schema for user prompts."""
    id: UUID
    prompt_id: UUID
    question: str
    answer: str

    class Config:
        from_attributes = True


class InterestUpdateRequest(BaseModel):
    """Update user interests."""
    interests: List[str] = Field(..., description="List of interest names")


class PromptUpdateRequest(BaseModel):
    """Update user prompts."""
    prompts: List[dict[str, str]] = Field(..., description="List of prompts with prompt_id and answer")


# ============ Alias for backward compatibility ============
UserResponse = UserProfileResponse