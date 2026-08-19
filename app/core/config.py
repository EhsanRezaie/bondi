# app/core/config.py
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # ============================================
    # App Settings
    # ============================================
    APP_NAME: str = "Bondi"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"
    TESTING: bool = False

    # ============================================
    # Database & Redis
    # ============================================
    DATABASE_URL: str
    REDIS_URL: str

    # ============================================
    # Security
    # ============================================
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15

    # ============================================
    # SMS (Kavenegar via OAuth2 gateway)
    # ============================================
    SMS_ENABLED: bool = False
    SMS_TOKEN_URL: str = "https://apim.ahuryx.com/oauth2/token"
    SMS_BASE_URL: str = "https://10.10.40.41:8243/smsapi/0.1.0"
    SMS_CLIENT_ID: str = ""
    SMS_CLIENT_SECRET: str = ""
    SMS_SENDER_LINE: str = ""

    # ============================================
    # Admin
    # ============================================
    ADMIN_SECRET_KEY: str = ""
    ADMIN_USERNAME: str = ""
    ADMIN_PASSWORD_HASH: str = ""

    # ============================================
    # Redis Settings
    # ============================================
    REDIS_SOCKET_TIMEOUT: int = 5
    REDIS_SOCKET_CONNECT_TIMEOUT: int = 5
    REDIS_RETRY_ON_TIMEOUT: bool = True
    REDIS_MAX_RETRIES: int = 3

    # ============================================
    # Daily Limits & Rewards
    # ============================================
    FREE_USER_DAILY_LIKES: int = 20
    FREE_USER_DAILY_CHATS: int = 10
    AD_REWARD_LIKES_BONUS: int = 5
    AD_REWARD_CHATS_BONUS: int = 3
    MAX_AD_REWARDS_PER_DAY: int = 2
    WELCOME_BONUS_DAYS: int = 7
    REFERRAL_INVITER_DAYS: int = 3
    REFERRAL_INVITED_DAYS: int = 3
    SUBSCRIPTION_MONTHLY_DAYS: int = 30
    SUBSCRIPTION_QUARTERLY_DAYS: int = 90
    SUBSCRIPTION_YEARLY_DAYS: int = 365
    SUBSCRIPTION_QUARTERLY_DISCOUNT: int = 15
    SUBSCRIPTION_YEARLY_DISCOUNT: int = 30

    # ============================================
    # Payment
    # ============================================
    ZARINPAL_MERCHANT_ID: str = ""
    ZARINPAL_SANDBOX: bool = True
    ZARINPAL_CALLBACK_URL: str = ""

    # ============================================
    # File Uploads
    # ============================================
    MAX_PHOTO_SIZE_MB: int = 10
    MAX_PHOTOS_PER_USER: int = 9
    
    # ============================================
    # Chat Media Settings
    # ============================================
    MAX_CHAT_PHOTO_SIZE_MB: int = 5
    MAX_CHAT_VOICE_SIZE_MB: int = 2
    MAX_CHAT_VOICE_DURATION: int = 120
    ALLOWED_CHAT_IMAGE_FORMATS: str = "JPEG,PNG,WEBP,JPG"
    
    # ============================================
    # MinIO / S3 Settings (NO DEFAULTS - MUST BE IN .env)
    # ============================================
    S3_ENDPOINT_URL: str
    S3_ACCESS_KEY: str
    S3_SECRET_KEY: str
    S3_REGION: str
    S3_PUBLIC_BUCKET: str
    S3_PRIVATE_BUCKET: str
    S3_PUBLIC_BASE_URL: str
    S3_SIGNED_URL_EXPIRE_SECONDS: int
    
    # ===========================================
    # Encryption
    # ===========================================
    ENCRYPTION_SECRET: str = "your-super-secret-32-byte-key-here-change-in-production"
    
    # ============================================
    # App Version
    # ============================================
    APP_VERSION: str = "1.0.0"
    MIN_ANDROID_VERSION: str = "1.0.0"
    MIN_IOS_VERSION: str = "1.0.0"
    
    # ============================================
    # App Store Links
    # ============================================
    PLAY_STORE_URL: str = "https://play.google.com/store/apps/details?id=your.app.id"
    APP_STORE_URL: str = "https://apps.apple.com/app/your-app-id"

    # ============================================
    # Force Update
    # ============================================
    FORCE_UPDATE_ENABLED: bool = False
    FORCE_UPDATE_MESSAGE: str = "A critical update is available. Please update to continue using the app."

    # ============================================
    # Error Tracking (GlitchTip)
    # ============================================
    GLITCHTIP_DSN: str = ""

    # ============================================
    # FCM Push Notifications
    # ============================================
    FCM_SERVICE_ACCOUNT_PATH: str = ""

    # ============================================
    # Celery
    # When True, notification/push work is enqueued to Redis and processed by
    # the celery-worker service (durable, retryable). When False, it runs
    # inline/in-process (used by tests and minimal dev setups).
    # ============================================
    CELERY_ENABLED: bool = False

    # ============================================
    # NSFW Detection
    # ============================================
    NSFW_ENABLED: bool = True
    NSFW_THRESHOLD: float = 0.8

    # ============================================
    # Face Verification (image-based selfie)
    # ============================================
    FACE_VERIFICATION_MODEL: str = "buffalo_l"
    FACE_MATCH_THRESHOLD: float = 0.45
    FACE_VERIFICATION_MAX_SIZE_MB: int = 10
    FACE_VERIFICATION_COOLDOWN_TTL: int = 86400
    FACE_VERIFICATION_MAX_ATTEMPTS_PER_DAY: int = 3
    FACE_VERIFICATION_MIN_PHOTOS: int = 1
    FACE_REFERENCE_CACHE_TTL: int = 604800  # 7 days

    # ============================================
    # CORS
    # ============================================
    CORS_ORIGINS: str = ""

    # ============================================
    # Pydantic Config
    # ============================================
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"


settings = Settings()