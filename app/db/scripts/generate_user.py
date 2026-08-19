"""
Generates app/db/seed_data/test_users_200.json — 200 test users:
  email:    test1@test.com ... test200@test.com
  phone:    +989100000001 ... +989100000200
  name:     test1 ... test200

100 male / 100 female (alternating) so they can match each other in a
heterosexual-only app. All other profile fields are randomized from value
pools mined directly from your existing dummy_users.json (1000-user seed),
so every value is guaranteed to match your real enums.

Run:  python generate_test_users.py
Output: test_users_200.json (copy into app/db/seed_data/, or point the
        insert script at wherever you save it)
"""
import json
import random
import uuid
from datetime import date, timedelta

random.seed(42)  # reproducible output — remove if you want fresh randomness each run

NUM_USERS = 200

BIOS = [
    "Foodie who's always looking for the next great meal.",
    "Coffee addict and book lover. Let's chat!",
    "Fitness enthusiast. Early morning gym sessions are my thing.",
    "Adventure seeker looking for a partner in crime.",
    "Work hard, play harder. That's my motto.",
    "Love exploring new places and meeting new people.",
    "Simple things, simple life.",
    "Dog parent and proud of it.",
    "Music is my love language.",
    "Weekend traveler, weekday dreamer.",
]

BODY_TYPES = ["athletic", "average", "curvy", "muscular", "overweight", "slim"]
RELATIONSHIP_STATUS = ["divorced", "separated", "single", "widowed"]
LIVING_SITUATION = ["alone", "with_family", "with_partner", "with_roommate"]
CHILDREN_STATUS = ["have_children", "want_children", "dont_want_children", "open_to_children"]
HERE_FOR = ["long_term_relationship", "casual_dating", "marriage", "new_friends", "not_sure_yet"]
PETS = ["dog", "cat", "both", "other_pet", "no_pets", "loves_pets"]
WORKOUT_FREQUENCY = ["never", "occasionally", "regularly", "daily"]
ZODIAC_SIGNS = ["aries", "taurus", "gemini", "cancer", "leo", "virgo",
                "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces"]
SMOKING = ["never", "occasionally", "regularly"]
DRINKING = ["never", "regularly", "socially"]
EDUCATION = ["bachelor", "high_school", "master", "phd"]
RELIGION = ["baha'i", "christianity", "islam", "none", "zoroastrian"]
ETHNICITY = ["arab", "baloch", "gilak", "kurdish", "lor", "mazani", "persian", "turk"]
POLITICAL = ["apolitical", "conservative", "liberal", "moderate"]
WORKPLACE = ["education", "freelancer", "government", "healthcare", "retail",
             "self-employed", "startup", "tech company"]

TEHRAN_LAT, TEHRAN_LNG = 35.7219, 51.3347  # kept for reference, no longer used for jitter
# Rough bounding box covering all of Tehran city (north mountains to south, west to east)
TEHRAN_LAT_MIN, TEHRAN_LAT_MAX = 35.56, 35.83
TEHRAN_LNG_MIN, TEHRAN_LNG_MAX = 51.05, 51.60

# Object keys these will be uploaded to in the MinIO public bucket by seed_test_users.py.
# Same 2 images reused for every user, per your request.
MALE_PHOTO_KEY = "seed/male_placeholder.jpg"
FEMALE_PHOTO_KEY = "seed/female_placeholder.jpg"


def random_birth_date():
    # Ages 18-45
    today = date.today()
    age_days = random.randint(18 * 365, 45 * 365)
    return (today - timedelta(days=age_days)).isoformat()


def build_user(i: int):
    gender = "male" if i % 2 == 1 else "female"
    user_id = str(uuid.uuid4())

    height = random.randint(170, 195) if gender == "male" else random.randint(155, 175)
    weight = random.randint(65, 100) if gender == "male" else random.randint(45, 70)

    user = {
        "id": user_id,
        "email": f"test{i}@test.com",
        "phone": f"+9891{i:08d}",
        "is_active": True,
        "phone_verified": True,
        "registration_status": "completed",
        "referral_code": None,
        "referred_by": None,
    }

    profile = {
        "user_id": user_id,
        "name": f"test{i}",
        "birth_date": random_birth_date(),
        "gender": gender,
        "sexual_orientation": "straight",
        "bio": random.choice(BIOS),
        "height": height,
        "weight": weight,
        "body_type": random.choice(BODY_TYPES),
        "relationship_status": random.choice(RELATIONSHIP_STATUS),
        "living_situation": random.choice(LIVING_SITUATION),
        "children_status": random.choice(CHILDREN_STATUS),
        "here_for": random.choice(HERE_FOR),
        "pets": random.choice(PETS),
        "workout_frequency": random.choice(WORKOUT_FREQUENCY),
        "zodiac_sign": random.choice(ZODIAC_SIGNS),
        "smoking": random.choice(SMOKING),
        "drinking": random.choice(DRINKING),
        "languages": ["Persian", "English"],
        "education": random.choice(EDUCATION),
        "workplace": random.choice(WORKPLACE),
        "religion": random.choice(RELIGION),
        "ethnicity": random.choice(ETHNICITY),
        "political_orientation": random.choice(POLITICAL),
        "lat": round(random.uniform(TEHRAN_LAT_MIN, TEHRAN_LAT_MAX), 6),
        "lng": round(random.uniform(TEHRAN_LNG_MIN, TEHRAN_LNG_MAX), 6),
        "country": "Iran",
        "province": "Tehran",
        "city": "Tehran",
        "location_manual": False,
        "is_verified": True,
        "premium_until": None,
    }

    settings = {
        "user_id": user_id,
        "hide_last_seen": False,
        "hide_online_status": False,
        "push_enabled": True,
        "like_notifications": True,
        "match_notifications": True,
        "message_notifications": True,
        "language": "fa",
        "dark_mode": False,
    }

    photo_key = MALE_PHOTO_KEY if gender == "male" else FEMALE_PHOTO_KEY
    photos = [
        {
            "user_id": user_id,
            "url": photo_key,
            "order": order,
            "is_main": order == 0,
            "status": "approved",
            "reject_reason": None,
            "face_verified": True,
        }
        for order in range(3)  # 3 photos per user, per your request
    ]

    # 8 interests per user — the insert script assigns these by randomly
    # picking 8 interest IDs from whatever's already in your `interests`
    # table, since interest IDs are DB-specific, not something we can bake
    # into this file.
    num_interests = 8

    return user, profile, settings, photos, num_interests


def main():
    users, profiles, settings_list, photos_list = [], [], [], []
    interests_per_user = {}

    for i in range(1, NUM_USERS + 1):
        user, profile, settings, photos, num_interests = build_user(i)
        users.append(user)
        profiles.append(profile)
        settings_list.append(settings)
        photos_list.extend(photos)
        interests_per_user[user["email"]] = num_interests

    data = {
        "users": users,
        "profiles": profiles,
        "settings": settings_list,
        "photos": photos_list,
        "interests_per_user": interests_per_user,
        "male_photo_key": MALE_PHOTO_KEY,
        "female_photo_key": FEMALE_PHOTO_KEY,
    }

    with open("test_users_200.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Wrote test_users_200.json — {len(users)} users "
          f"({sum(1 for u in profiles if u['gender']=='male')} male / "
          f"{sum(1 for u in profiles if u['gender']=='female')} female)")


if __name__ == "__main__":
    main()