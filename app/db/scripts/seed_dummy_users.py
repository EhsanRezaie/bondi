"""
Seed dummy users generated from the CURRENT models & schema enums (no stale JSON).

Creates `--count` (default 1000) test users: test1@test.com … testN@test.com,
phone +989100000001 … +989100000N, password n/a (dummy rows only). Data is
generated in-memory from the app's own enum classes so every value matches the
onboarding/discover validators.

Besides users/profiles/settings/photos it also seeds related tables so the
discover, likes, matches and chat flows have realistic content:
  - user_interests / user_prompts
  - swipes (mutual likes), matches
  - chats + a few encrypted messages on the newest matches
  - daily_limits (today, Tehran date) for free users
  - subscriptions for the premium subset

Usage:
    python -m app.db.scripts.seed_dummy_users [--count 1000] [--no-seed]

Re-run safe — existing test%@test.com users are deleted first (FK cascades
remove their swipes/matches/chats/etc.). User ids are deterministic per index,
so the same dummy/* photo objects are simply overwritten on each run.
"""

import argparse
import asyncio
import io
import logging
import random
import string
import uuid as uuid_lib
from datetime import date, datetime, timedelta, timezone

import aioboto3
from PIL import Image, ImageDraw
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.chat import Chat
from app.models.daily_limit import DailyLimit
from app.models.interest import Interest
from app.models.match import Match
from app.models.message import Message
from app.models.photo import Photo
from app.models.prompt import Prompt
from app.models.subscription import Subscription
from app.models.swipe import Swipe
from app.models.user import User
from app.models.user_interest import UserInterest
from app.models.user_profile import UserProfile
from app.models.user_prompt import UserPrompt
from app.models.user_settings import UserSettings
from app.schemas.auth import (
    Gender,
    SexualOrientation,
    BodyType,
    RelationshipStatus,
    LivingSituation,
    ChildrenStatus,
    HereFor,
    Pets,
    WorkoutFrequency,
    ZodiacSign,
    SmokingStatus,
    DrinkingStatus,
    EducationLevel,
    PoliticalOrientation,
)

logger = get_logger("scripts.seed_dummy_users")

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))  # Tehran = UTC+3:30

PHOTOS_PER_USER = 3

# Deterministic namespace so default runs reuse the same user ids / photo keys.
# Re-running then overwrites the same MinIO objects instead of piling up.
_DUMMY_NS = uuid_lib.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def _make_uid(index: int, deterministic: bool) -> uuid_lib.UUID:
    if deterministic:
        return uuid_lib.uuid5(_DUMMY_NS, f"dummy-user-{index}")
    return uuid_lib.uuid4()

# ---------------------------------------------------------------------------
# Value pools (schemas enums are authoritative; the rest are free-text pools)
# ---------------------------------------------------------------------------
GENDERS = [g.value for g in Gender]
ORIENTATIONS = [o.value for o in SexualOrientation]
BODY_TYPES = [b.value for b in BodyType]
RELATIONSHIP_STATUS = [r.value for r in RelationshipStatus]
LIVING_SITUATIONS = [l.value for l in LivingSituation]
CHILDREN_STATUS = [c.value for c in ChildrenStatus]
HERE_FOR = [h.value for h in HereFor]
PETS = [p.value for p in Pets]
WORKOUT_FREQUENCY = [w.value for w in WorkoutFrequency]
ZODIAC_SIGNS = [z.value for z in ZodiacSign]
SMOKING = [s.value for s in SmokingStatus]
DRINKING = [d.value for d in DrinkingStatus]
EDUCATION = [e.value for e in EducationLevel]
POLITICAL = [p.value for p in PoliticalOrientation]

BIOS = [
    "عاشق سفر و کشف جاهای جدید.", "قهوه‌ای‌های همیشگی — بیا گپ بزنیم!",
    "به دنبال یه ارتباط واقعی و صادقانه.", "ورزش صبحگاهی، رمز روز خوبم.",
    "موسیقی زبان عشقه.", "آدم ساده‌ای هستم، زندگی ساده.", "کتاب‌خوان حرفه‌ای، همیشه دنبال پیشنهاد کتابم.",
    "آخر هفته‌ها کوه یا دریا، بستگی به حال‌وهوا داره.", "در جستجوی همراهی برای خنده‌های بی‌دلیل.",
    "هوای تازه و قهوه تلخ، بهترین شروع روزه.",
]
RELIGIONS = ["islam", "christianity", "zoroastrian", "none", "baha'i"]
ETHNICITIES = ["persian", "turk", "kurdish", "lor", "mazani", "gilak", "arab", "baloch"]
WORKPLACES = ["education", "healthcare", "tech company", "startup", "freelancer",
              "retail", "government", "self-employed"]
# (city, province, lat, lng) — jittered a bit per user.
CITIES = [
    ("Tehran", "Tehran", 35.6892, 51.3890),
    ("Shiraz", "Fars", 29.5918, 52.5837),
    ("Isfahan", "Isfahan", 32.6546, 51.6680),
    ("Mashhad", "Razavi Khorasan", 36.2605, 59.6168),
    ("Karaj", "Alborz", 35.8400, 50.9391),
    ("Tabriz", "East Azerbaijan", 38.0800, 46.2919),
    ("Qom", "Qom", 34.6401, 50.8764),
]
CHAT_SEEDS = [
    "سلام! پروفایلت خیلی جذاب بود 👋",
    "Hey! Love your photos 😊",
    "What kind of music are you into?",
    "راستی اون سفر مشهد که گفتی چطور بود؟",
    "Haha that prompt answer cracked me up 🤣",
    "سلام، این آخر هفته کجا می‌ری؟",
    "Your bio is so relatable — same here!",
]
REFERRAL_ALPHABET = string.ascii_uppercase + string.digits

# ---------------------------------------------------------------------------
# MinIO helpers
# ---------------------------------------------------------------------------

def _rand_dt(days_back: float, days_fwd: float = 0) -> datetime:
    seconds = random.uniform(0, (days_back + days_fwd) * 86400)
    return datetime.now(timezone.utc) + timedelta(seconds=seconds - days_back * 86400)


async def _upload_placeholder_images(photos: list[tuple[str, str]]) -> None:
    """Upload one pastel image per user (reused for all 3 photo slots).

    Photo keys embed the (deterministic) user id, so re-runs overwrite the same
    objects — no purge/cleanup of MinIO is required.
    """
    s3_session = aioboto3.Session()
    uploaded = 0
    # stable color + initials per user, re-encoded into each key
    user_colors: dict[str, tuple[int, int, int]] = {}

    async with s3_session.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
    ) as s3:
        for user_id, key in photos:
            try:
                await s3.head_object(Bucket=settings.S3_PUBLIC_BUCKET, Key=key)
                continue
            except Exception:
                pass

            if user_id not in user_colors:
                user_colors[user_id] = (
                    random.randint(100, 230),
                    random.randint(100, 230),
                    random.randint(100, 230),
                )
            img = Image.new("RGB", (400, 400), user_colors[user_id])
            draw = ImageDraw.Draw(img)
            draw.text((200, 200), user_id[:2].upper(), fill="white", anchor="mm")

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            buf.seek(0)
            await s3.put_object(
                Bucket=settings.S3_PUBLIC_BUCKET,
                Key=key,
                Body=buf.getvalue(),
                ContentType="image/jpeg",
            )
            uploaded += 1

    print(f"   ✅  {uploaded} placeholder images uploaded to MinIO")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def build_person(index: int, deterministic: bool):
    """Return (user, profile, settings, photo_keys, premium_days) for one person."""
    uid = _make_uid(index, deterministic)
    gender = GENDERS[index % 2]

    # Location (city + jitter)
    city, province, clat, clng = random.choice(CITIES)
    lat = round(clat + random.uniform(-0.12, 0.12), 6)
    lng = round(clng + random.uniform(-0.12, 0.12), 6)

    if gender == "male":
        height, weight = random.randint(168, 195), random.randint(62, 100)
    else:
        height, weight = random.randint(152, 178), random.randint(44, 72)

    is_verified = random.random() < 0.92
    premium_days = random.randint(7, 60) if random.random() < 0.12 else 0
    premium_until = (
        datetime.now(timezone.utc) + timedelta(days=premium_days) if premium_days else None
    )

    user = User(
        id=uid,
        email=f"test{index}@test.com",
        phone=f"+9891{index:08d}",
        phone_verified=True,
        is_active=True,
        registration_status="onboarding_complete",
        referral_code=_unique_referral(uid),
        last_seen_at=_rand_dt(5) if random.random() < 0.75 else None,
    )

    profile = UserProfile(
        user_id=uid,
        name=f"test{index}",
        birth_date=date.today() - timedelta(days=random.randint(18 * 365, 45 * 365)),
        gender=gender,
        sexual_orientation=random.choice(ORIENTATIONS),
        bio=random.choice(BIOS),
        height=height,
        weight=weight,
        body_type=random.choice(BODY_TYPES),
        relationship_status=random.choice(RELATIONSHIP_STATUS),
        living_situation=random.choice(LIVING_SITUATIONS),
        children_status=random.choice(CHILDREN_STATUS),
        smoking=random.choice(SMOKING),
        drinking=random.choice(DRINKING),
        here_for=random.choice(HERE_FOR),
        pets=random.choice(PETS),
        workout_frequency=random.choice(WORKOUT_FREQUENCY),
        zodiac_sign=random.choice(ZODIAC_SIGNS),
        languages=random.choice([["Persian"], ["Persian", "English"], ["English"]]),
        education=random.choice(EDUCATION),
        workplace=random.choice(WORKPLACES),
        religion=random.choice(RELIGIONS),
        ethnicity=random.choice(ETHNICITIES),
        political_orientation=random.choice(POLITICAL),
        lat=lat,
        lng=lng,
        country="Iran",
        province=province,
        city=city,
        location_manual=False,
        is_verified=is_verified,
        verified_at=datetime.now(timezone.utc) - timedelta(days=random.randint(1, 200)) if is_verified else None,
        premium_until=premium_until,
    )

    settings_row = UserSettings(
        user_id=uid,
        hide_last_seen=random.random() < 0.15,
        hide_online_status=random.random() < 0.1,
        push_enabled=True,
        like_notifications=True,
        match_notifications=True,
        message_notifications=True,
        language=random.choice(["fa", "fa", "en"]),
        dark_mode=random.random() < 0.3,
    )

    photo_keys = [f"dummy/{uid}/photo{i + 1}.jpg" for i in range(PHOTOS_PER_USER)]

    return user, profile, settings_row, photo_keys, premium_days


_REFERRALS: set[str] = set()


def _unique_referral(uid: uuid_lib.UUID) -> str:
    """Deterministic 8-char referral code derived from the (unique) user id."""
    raw = uid.hex.upper()
    while True:
        code = "".join(REFERRAL_ALPHABET[ord(c) % len(REFERRAL_ALPHABET)] for c in raw[:8])
        if code not in _REFERRALS:
            _REFERRALS.add(code)
            return code


def _pairs_with_mutual_likes(persons: list[tuple[uuid_lib.UUID, str]], count: int):
    """Deterministically pair users of opposite gender so matches always exist."""
    men = [uid for uid, g in persons if g == "male"]
    women = [uid for uid, g in persons if g == "female"]
    pairs = list(zip(men, women))[:count]
    random.shuffle(pairs)
    return pairs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def seed_dummy_users(count: int = 1000, deterministic: bool = True) -> None:
    if deterministic:
        random.seed(20260907)

    async with AsyncSessionLocal() as session:
        existing_ids = (
            await session.execute(select(User.id).where(User.email.like("%@test.com")))
        ).scalars().all()
        if existing_ids:
            print(f"🧹  Removing {len(existing_ids)} existing test@test.com users …")
            await session.execute(delete(User).where(User.id.in_(existing_ids)))
            await session.commit()

        print(f"📦  Generating {count} dummy users from current models\n")

        # ---- users / profiles / settings / photos ----
        user_ids: list[uuid_lib.UUID] = []
        persons: list[tuple[uuid_lib.UUID, str]] = []
        users: list[User] = []
        profiles: list[UserProfile] = []
        settings_rows: list[UserSettings] = []
        photos: list[Photo] = []
        photo_keys: list[tuple[str, str]] = []
        premium_user_ids: set[uuid_lib.UUID] = set()

        for i in range(1, count + 1):
            user, profile, srow, keys, premium_days = build_person(i, deterministic)
            uid = user.id
            user_ids.append(uid)
            persons.append((uid, profile.gender))
            users.append(user)
            profiles.append(profile)
            settings_rows.append(srow)
            if premium_days:
                premium_user_ids.add(uid)
            for idx, key in enumerate(keys):
                photos.append(Photo(
                    user_id=uid,
                    url=key,
                    order=idx,
                    is_main=idx == 0,
                    status="approved",
                    reject_reason=None,
                    face_verified=True,
                    crop=None,
                ))
                photo_keys.append((str(uid), key))

        session.add_all(users)
        session.add_all(profiles)
        session.add_all(settings_rows)
        session.add_all(photos)
        await session.commit()
        print(f"   ✅  {count} users, profiles, settings + {len(photos)} photos inserted")

    # Placeholder images (outside the DB session to keep commits small)
    await _upload_placeholder_images(photo_keys)

    # ---- interests / prompts ----
    async with AsyncSessionLocal() as session:
        all_interests = (await session.execute(select(Interest.id))).scalars().all()
        all_prompts = (await session.execute(select(Prompt.id))).scalars().all()

        uinterest_rows = []
        uprompt_rows = []
        for uid in user_ids:
            for iid in random.sample(list(all_interests), min(8, len(all_interests))):
                uinterest_rows.append(UserInterest(user_id=uid, interest_id=iid))
            for pid in random.sample(list(all_prompts), min(3, len(all_prompts))):
                uprompt_rows.append(UserPrompt(
                    user_id=uid, prompt_id=pid, answer=random.choice(BIOS + CHAT_SEEDS),
                ))

        session.add_all(uinterest_rows)
        session.add_all(uprompt_rows)
        await session.commit()
        print(f"   ✅  {len(uinterest_rows)} user_interest + {len(uprompt_rows)} user_prompt rows")

    # ---- swipes / matches ----
    liked: dict[uuid_lib.UUID, set[uuid_lib.UUID]] = {}
    for uid in user_ids:
        liked[uid] = set()

    men = [uid for uid, g in persons if g == "male"]
    women = [uid for uid, g in persons if g == "female"]

    def _like(frm: uuid_lib.UUID, to: uuid_lib.UUID):
        liked[frm].add(to)

    # random likes across genders
    for uid in user_ids:
        pool = women if uid in men else men
        if not pool:
            continue
        for to in random.sample(pool, min(len(pool), random.randint(2, 4))):
            _like(uid, to)

    # guaranteed reciprocal likes so matches always exist (fresh pairs each run)
    for a, b in _pairs_with_mutual_likes(persons, min(count // 2, 300)):
        _like(a, b)
        _like(b, a)

    matched: set[tuple[uuid_lib.UUID, uuid_lib.UUID]] = set()
    for a in user_ids:
        for b in liked[a]:
            if a in liked.get(b, set()):
                matched.add(tuple(sorted([a, b])))

    async with AsyncSessionLocal() as session:
        swipes = [
            Swipe(from_user=a, to_user=b, direction="like")
            for a in user_ids
            for b in liked[a]
        ]
        session.add_all(swipes)
        await session.commit()
        print(f"   ✅  {len(swipes)} like-swipes inserted")

        matches = [Match(user1_id=a, user2_id=b) for a, b in matched]
        session.add_all(matches)
        await session.commit()
        print(f"   ✅  {len(matches)} matches inserted")

    # ---- chats + encrypted messages (newest matches only) ----
    async with AsyncSessionLocal() as session:
        ordered_matches = sorted(matched, key=lambda p: p[0].int + p[1].int)[-120:]
        chats = []
        messages = []
        for a, b in ordered_matches:
            chat_id = uuid_lib.uuid4()
            chats.append(Chat(
                id=chat_id, initiator_id=a, recipient_id=b, status="accepted", is_active=True,
            ))
            for k, text in enumerate(random.sample(CHAT_SEEDS, k=random.randint(1, 3))):
                sender, receiver = (a, b) if k % 2 == 0 else (b, a)
                msg = Message(
                    chat_id=chat_id,
                    sender_id=sender,
                    receiver_id=receiver,
                    is_delivered=True,
                    is_read=k == 0,
                )
                msg.content = text  # encrypts using chat_id
                messages.append(msg)
        session.add_all(chats)
        session.add_all(messages)
        await session.commit()
        print(f"   ✅  {len(chats)} chats + {len(messages)} encrypted messages inserted")

    # ---- daily_limits (today, Tehran) + subscriptions for premium ----
    tehran_today = (datetime.now(timezone.utc) + timedelta(hours=3, minutes=30)).date()
    async with AsyncSessionLocal() as session:
        limits = []
        subs = []
        for uid in user_ids:
            is_premium = uid in premium_user_ids
            if not is_premium:
                limits.append(DailyLimit(
                    user_id=uid,
                    date=tehran_today,
                    likes_used=random.randint(0, 15),
                    chats_used=random.randint(0, 5),
                ))
            else:
                started = _rand_dt(10, 0)
                expires = datetime.now(timezone.utc) + timedelta(days=random.randint(7, 60))
                subs.append(Subscription(
                    user_id=uid,
                    plan="monthly",
                    status="active",
                    started_at=started,
                    expires_at=expires,
                    source="seed",
                ))
        session.add_all(limits)
        session.add_all(subs)
        await session.commit()
        print(f"   ✅  {len(limits)} daily_limits + {len(subs)} subscriptions inserted")

    print(f"\n🎉  Done! {count} dummy users in the database.")
    print(f"    Email range:        test1@test.com … test{count}@test.com")
    print(f"    Phone range:        +989100000001 … +9891{count:08d}")
    print(f"    Photos:             {PHOTOS_PER_USER} per user")
    print(f"    Swipes / matches:   {sum(len(v) for v in liked.values())} likes / {len(matched)} matches")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed dummy users from current models")
    parser.add_argument("--count", type=int, default=1000, help="number of users (default 1000)")
    parser.add_argument("--no-seed", action="store_true", help="use fresh randomness instead of the reproducible seed")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(seed_dummy_users(count=args.count, deterministic=not args.no_seed))


if __name__ == "__main__":
    main()
