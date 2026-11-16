import random

# ±500m = ±0.5 km — enough to prevent triangulation while keeping
# the displayed distance useful for the user.
FUZZ_RADIUS_KM = 0.5

# Writes to /me/location below this movement are no-ops for coordinates —
# app-open pings from the same spot must not churn the GiST location index
# (non-HOT row updates → bloat/vacuum pressure). Haversine helper for the
# threshold check lives in app.services.profile_service.haversine_km.
LOCATION_WRITE_THRESHOLD_KM = 0.1


def fuzz_distance(distance_km: float | None) -> float | None:
    """
    Add random noise to a distance value before returning it in an API response.
    Prevents attackers from triangulating a user's exact location via multiple
    distance readings from different positions.

    The stored DB coordinates remain exact — only the displayed distance is fuzzed.
    """
    if distance_km is None:
        return None
    noise = random.uniform(-FUZZ_RADIUS_KM, FUZZ_RADIUS_KM)
    fuzzed = distance_km + noise
    return round(max(0.0, fuzzed), 1)
