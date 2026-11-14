import random

# ±500m = ±0.5 km — enough to prevent triangulation while keeping
# the displayed distance useful for the user.
FUZZ_RADIUS_KM = 0.5


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
