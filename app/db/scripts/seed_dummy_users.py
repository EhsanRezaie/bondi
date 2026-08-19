"""
Seed 1000 dummy users from app/db/seed_data/dummy_users.json.

Usage:
    python -m app.db.scripts.seed_dummy_users

Re-run safe — existing test%@test.com users are deleted first.
Phone numbers are generated deterministically (+9891...) for each user.
"""

import asyncio
import io
import json
import logging
import random
import uuid as uuid_lib
from pathlib import Path

import aioboto3
from PIL import Image, ImageDraw
from datetime import date
from sqlalchemy import delete, select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.interest import Interest
from app.models.photo import Photo
from app.models.prompt import Prompt
from app.models.user import User
from app.models.user_interest import UserInterest
from app.models.user_profile import UserProfile
from app.models.user_prompt import UserPrompt
from app.models.user_settings import UserSettings

SAMPLE_ANSWERS = [
    "I love this!", "Absolutely yes", "It's complicated",
    "Always and forever", "Not my thing", "Let's talk about it",
    "Best experience ever", "Would do it again", "No comment",
    "Life is beautiful", "Living my best life", "Ask me later",
    "That's a great question", "I'm passionate about it",
    "It depends on the day", "Why not?", "Honestly, I don't know",
    "More than anything", "It makes me happy", "We'll see",
]

logger = logging.getLogger(__name__)

SEED_FILE = Path(__file__).parent.parent / "seed_data" / "dummy_users.json"


async def _upload_placeholder_images(photos_data: list) -> None:
    """Generate and upload placeholder images to the MinIO public bucket."""
    s3_session = aioboto3.Session()
    user_colors: dict[str, tuple[int, int, int]] = {}
    uploaded = 0

    async with s3_session.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
    ) as s3:
        for ph in photos_data:
            key = ph["url"]
            user_id = ph["user_id"]

            # Skip if already exists
            try:
                await s3.head_object(Bucket=settings.S3_PUBLIC_BUCKET, Key=key)
                continue
            except Exception:
                pass

            # Pick a stable pastel color per user
            if user_id not in user_colors:
                user_colors[user_id] = (
                    random.randint(100, 230),
                    random.randint(100, 230),
                    random.randint(100, 230),
                )

            img = Image.new("RGB", (400, 400), user_colors[user_id])
            draw = ImageDraw.Draw(img)
            initials = user_id[:2].upper()
            draw.text((200, 200), initials, fill="white", anchor="mm")

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


def load_seed_data() -> dict:
    if not SEED_FILE.exists():
        raise FileNotFoundError(f"Seed file not found: {SEED_FILE}")
    with open(SEED_FILE, encoding="utf-8") as f:
        data = json.load(f)
    for key in ("users", "profiles", "settings", "photos"):
        if key not in data:
            raise ValueError(f"Missing required key '{key}' in {SEED_FILE}")
    return data


PHOTOS_PER_USER = 3


async def seed_dummy_users() -> None:
    data = load_seed_data()
    users_data = data["users"]
    profiles_data = data["profiles"]
    settings_data = data["settings"]

    print(f"📦  Loaded {len(users_data)} dummy users from seed file\n")

    async with AsyncSessionLocal() as session:
        existing_ids = (
            await session.execute(
                select(User.id).where(User.email.like("%@test.com"))
            )
        ).scalars().all()
        if existing_ids:
            print(f"🧹  Removing {len(existing_ids)} existing test@test.com users …")
            await session.execute(delete(Photo).where(Photo.user_id.in_(existing_ids)))
            await session.execute(delete(UserInterest).where(UserInterest.user_id.in_(existing_ids)))
            await session.execute(delete(UserPrompt).where(UserPrompt.user_id.in_(existing_ids)))
            await session.execute(delete(UserSettings).where(UserSettings.user_id.in_(existing_ids)))
            await session.execute(delete(UserProfile).where(UserProfile.user_id.in_(existing_ids)))
            await session.execute(delete(User).where(User.id.in_(existing_ids)))
            await session.commit()

        batch_size = 100

        for i, u in enumerate(users_data, start=1):
            user = User(
                id=uuid_lib.UUID(u["id"]),
                email=u["email"],
                phone=f"+9891{i:08d}",
                phone_verified=True,
                is_active=True,
                registration_status="onboarding_complete",
            )
            session.add(user)
            if (i + 1) % batch_size == 0:
                await session.commit()

        await session.commit()
        print(f"   ✅  {len(users_data)} users inserted")

        for p in profiles_data:
            profile = UserProfile(
                user_id=uuid_lib.UUID(p["user_id"]),
                name=p["name"],
                birth_date=date.fromisoformat(p["birth_date"]),
                gender=p["gender"],
                sexual_orientation=p["sexual_orientation"],
                bio=p["bio"],
                height=p["height"],
                weight=p["weight"],
                body_type=p["body_type"],
                relationship_status=p["relationship_status"],
                living_situation=p["living_situation"],
                children_status=p["children_status"],
                smoking=p["smoking"],
                drinking=p["drinking"],
                languages=p["languages"],
                education=p["education"],
                workplace=p["workplace"],
                religion=p["religion"],
                ethnicity=p["ethnicity"],
                political_orientation=p["political_orientation"],
                lat=p["lat"],
                lng=p["lng"],
                country=p["country"],
                province=p["province"],
                city=p["city"],
                is_verified=p["is_verified"],
                premium_until=p["premium_until"],
            )
            session.add(profile)

        await session.commit()
        print(f"   ✅  {len(profiles_data)} profiles inserted")

        for s in settings_data:
            settings = UserSettings(
                user_id=uuid_lib.UUID(s["user_id"]),
                hide_last_seen=s["hide_last_seen"],
                hide_online_status=s["hide_online_status"],
                push_enabled=s["push_enabled"],
                like_notifications=s["like_notifications"],
                match_notifications=s["match_notifications"],
                message_notifications=s["message_notifications"],
                language=s["language"],
                dark_mode=s["dark_mode"],
            )
            session.add(settings)

        await session.commit()
        print(f"   ✅  {len(settings_data)} settings inserted")

        # Generate 3 photos per user (app requires >= 3)
        photos_to_insert = []
        photos_for_minio = []
        for u in users_data:
            uid = uuid_lib.UUID(u["id"])
            for idx in range(PHOTOS_PER_USER):
                key = f"dummy/{u['id']}/photo{idx + 1}.jpg"
                photos_to_insert.append(Photo(
                    user_id=uid,
                    url=key,
                    order=idx,
                    is_main=(idx == 0),
                    status="approved",
                    reject_reason=None,
                    face_verified=True,
                    crop=None,
                ))
                photos_for_minio.append({"user_id": u["id"], "url": key})

        session.add_all(photos_to_insert)
        await session.commit()
        print(f"   ✅  {len(photos_to_insert)} photos inserted")

    # Upload placeholder images to MinIO so photos are actually viewable
    await _upload_placeholder_images(photos_for_minio)

    # Assign random interests and prompts to each user
    async with AsyncSessionLocal() as session:
        all_interests = (await session.execute(select(Interest.id))).scalars().all()
        all_prompts = (await session.execute(select(Prompt.id))).scalars().all()

        user_ids = [uuid_lib.UUID(u["id"]) for u in users_data]
        user_interest_rows = []
        user_prompt_rows = []

        for uid in user_ids:
            for iid in random.sample(all_interests, min(8, len(all_interests))):
                user_interest_rows.append(UserInterest(user_id=uid, interest_id=iid))
            for pid in random.sample(all_prompts, min(3, len(all_prompts))):
                user_prompt_rows.append(UserPrompt(
                    user_id=uid, prompt_id=pid,
                    answer=random.choice(SAMPLE_ANSWERS),
                ))

        session.add_all(user_interest_rows)
        await session.commit()
        print(f"   ✅  {len(user_interest_rows)} user_interest rows inserted")

        session.add_all(user_prompt_rows)
        await session.commit()
        print(f"   ✅  {len(user_prompt_rows)} user_prompt rows inserted")

    print(f"\n🎉  Done! {len(users_data)} dummy users in the database.")
    print(f"    Email range:  test1@test.com … test{len(users_data)}@test.com")
    print(f"    Phone range:  +989100000001 … +9891{len(users_data):08d}")
    print(f"    Photos:       {PHOTOS_PER_USER} per user")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(seed_dummy_users())


if __name__ == "__main__":
    main()
