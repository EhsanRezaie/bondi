"""
Face Verification Service

Singleton service using InsightFace + OpenCV for liveness detection
and face embedding comparison. No paid APIs, no cloud services.

Architecture:
  - Lazy-loaded InsightFace FaceAnalysis (buffalo_l model, CPU)
  - Challenge-response liveness via facial landmarks
  - Cosine similarity face matching against existing profile photos
  - All heavy computation runs in thread executor to avoid blocking the event loop
"""

import asyncio
import io
import os
import threading
import time
import uuid
from typing import ClassVar, Optional, Tuple

import cv2
import numpy as np

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("face_verification_service")

# Challenge types and their user-friendly instructions
CHALLENGE_TYPES = {
    "blink": "Look at the camera and blink twice naturally",
    "turn_left": "Look at the camera, then slowly turn your head to the left",
    "turn_right": "Look at the camera, then slowly turn your head to the right",
    "smile": "Look at the camera and give a natural smile",
    "nod": "Look at the camera and nod your head up and down",
}

def _safe_remove_file(path: str, retries: int = 3, delay: float = 0.1) -> None:
    """Delete a temp file, retrying briefly to work around Windows file locks
    (OpenCV video backends can keep the handle open for a moment)."""
    for attempt in range(retries):
        try:
            os.unlink(path)
            return
        except (OSError, PermissionError):
            if attempt < retries - 1:
                time.sleep(delay)
    # Last attempt: best-effort, ignore failure so processing can still return.
    try:
        os.unlink(path)
    except OSError:
        pass


# 3D model points for head pose estimation (approximate face landmarks)
MODEL_POINTS_3D = np.array([
    [0.0, 0.0, 0.0],       # Nose tip
    [0.0, -330.0, -65.0],   # Chin
    [-225.0, 170.0, -135.0], # Left eye left corner
    [225.0, 170.0, -135.0],  # Right eye right corner
    [-150.0, -150.0, -125.0], # Left mouth corner
    [150.0, -150.0, -125.0],  # Right mouth corner
], dtype=np.float64)

# 2D landmark indices for head pose (from 68-point model)
POSE_LANDMARK_INDICES = [30, 8, 36, 45, 48, 54]

# Eye landmark indices (68-point model)
LEFT_EYE_INDICES = [36, 37, 38, 39, 40, 41]
RIGHT_EYE_INDICES = [42, 43, 44, 45, 46, 47]

# Mouth landmark indices
MOUTH_OUTER_INDICES = [48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59]


class FaceVerificationService:
    """Singleton service for face detection, liveness, and verification."""

    _instance: ClassVar[Optional["FaceVerificationService"]] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()
    _face_analyzer: Optional[object] = None

    def __new__(cls) -> "FaceVerificationService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def _ensure_model(self) -> None:
        """Lazy-load InsightFace model on first use (thread-safe)."""
        if self._face_analyzer is None:
            with self._lock:
                if self._face_analyzer is None:
                    from insightface.app import FaceAnalysis
                    analyzer = FaceAnalysis(
                        name=settings.FACE_VERIFICATION_MODEL,
                        providers=["CPUExecutionProvider"],
                    )
                    analyzer.prepare(ctx_id=0, det_size=(640, 640))
                    self._face_analyzer = analyzer
                    logger.info("face_model_loaded", model=settings.FACE_VERIFICATION_MODEL)

    def _get_face(self, frame: np.ndarray) -> Optional[object]:
        """Detect single face in frame. Returns None if 0 or >1 faces."""
        self._ensure_model()
        faces = self._face_analyzer.get(frame)
        if len(faces) != 1:
            return None
        return faces[0]

    def _get_embedding(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Get 512-d face embedding from frame."""
        face = self._get_face(frame)
        if face is None:
            return None
        return face.normed_embedding

    def _compute_ear(self, landmarks: np.ndarray) -> float:
        """Compute Eye Aspect Ratio from 68 landmarks."""
        # Vertical distances
        v1 = np.linalg.norm(landmarks[37] - landmarks[35])
        v2 = np.linalg.norm(landmarks[38] - landmarks[36])
        v3 = np.linalg.norm(landmarks[44] - landmarks[42])
        v4 = np.linalg.norm(landmarks[43] - landmarks[45])

        # Horizontal distance
        h1 = np.linalg.norm(landmarks[36] - landmarks[39])
        h2 = np.linalg.norm(landmarks[42] - landmarks[45])

        # Avoid division by zero
        if h1 == 0 or h2 == 0:
            return 0.0

        ear_left = (v1 + v2) / (2.0 * h1)
        ear_right = (v3 + v4) / (2.0 * h2)

        return (ear_left + ear_right) / 2.0

    def _compute_yaw_pitch_roll(self, landmarks: np.ndarray) -> Tuple[float, float, float]:
        """Estimate head pose from 68 landmarks using PnP."""
        h, w = 640, 640  # Assuming standardized frame size

        # Camera internals (approximate)
        focal_length = w
        center = (w / 2, h / 2)
        camera_matrix = np.array([
            [focal_length, 0, center[0]],
            [0, focal_length, center[1]],
            [0, 0, 1],
        ], dtype=np.float64)

        dist_coeffs = np.zeros((4, 1), dtype=np.float64)

        # 2D image points from landmarks
        image_points = np.array([
            landmarks[30],  # Nose tip
            landmarks[8],   # Chin
            landmarks[36],  # Left eye left corner
            landmarks[45],  # Right eye right corner
            landmarks[48],  # Left mouth corner
            landmarks[54],  # Right mouth corner
        ], dtype=np.float64)

        success, rotation_vector, translation_vector = cv2.solvePnP(
            MODEL_POINTS_3D,
            image_points,
            camera_matrix,
            dist_coeffs,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )

        if not success:
            return 0.0, 0.0, 0.0

        # Convert rotation vector to euler angles
        rotation_matrix, _ = cv2.Rodrigues(rotation_vector)
        angles, _, _, _, _, _ = cv2.RQDecomp3x3(rotation_matrix)

        return float(angles[1]), float(angles[0]), float(angles[2])  # yaw, pitch, roll

    def _validate_blink(self, landmarks_list: list) -> Tuple[bool, str]:
        """Detect blink via Eye Aspect Ratio (EAR) drop."""
        blink_count = 0
        below_threshold = False
        threshold = settings.FACE_VERIFICATION_BLINK_THRESHOLD

        for landmarks in landmarks_list:
            ear = self._compute_ear(landmarks)
            if ear < threshold:
                if not below_threshold:
                    blink_count += 1
                    below_threshold = True
            else:
                below_threshold = False

        if blink_count >= 2:
            return True, f"Detected {blink_count} blinks"
        return False, f"Only detected {blink_count} blink(s), need at least 2"

    def _validate_turn(self, landmarks_list: list, direction: str) -> Tuple[bool, str]:
        """Detect head turn via face yaw angle."""
        threshold = settings.FACE_VERIFICATION_TURN_THRESHOLD
        max_yaw = 0.0

        for landmarks in landmarks_list:
            yaw, _, _ = self._compute_yaw_pitch_roll(landmarks)
            if direction == "turn_left":
                max_yaw = max(max_yaw, yaw)
            else:
                max_yaw = min(max_yaw, yaw)

        if direction == "turn_left" and max_yaw > threshold:
            return True, f"Head turned left {max_yaw:.1f} degrees"
        elif direction == "turn_right" and max_yaw < -threshold:
            return True, f"Head turned right {abs(max_yaw):.1f} degrees"

        return False, f"Head turn angle {max_yaw:.1f} degrees, need {threshold} degrees"

    def _validate_smile(self, landmarks_list: list) -> Tuple[bool, str]:
        """Detect smile via mouth corner distance relative to lip width."""
        threshold = settings.FACE_VERIFICATION_SMILE_THRESHOLD
        ratios = []

        for landmarks in landmarks_list:
            # Mouth width (left corner to right corner)
            mouth_width = np.linalg.norm(landmarks[48] - landmarks[54])
            if mouth_width == 0:
                continue

            # Vertical distance between upper and lower lip at center
            lip_height = np.linalg.norm(landmarks[51] - landmarks[57])
            ratio = lip_height / mouth_width
            ratios.append(ratio)

        if len(ratios) < 2:
            return False, "Insufficient frames for smile detection"

        # Check if ratio increased significantly (smile)
        early_avg = np.mean(ratios[:len(ratios)//2])
        late_avg = np.mean(ratios[len(ratios)//2:])
        increase = (late_avg - early_avg) / early_avg if early_avg > 0 else 0

        if increase > threshold:
            return True, f"Mouth ratio increased by {increase:.1%}"

        return False, f"Mouth ratio increase {increase:.1:.1%}, need {threshold:.0%}"

    def _validate_nod(self, landmarks_list: list) -> Tuple[bool, str]:
        """Detect nod via face pitch angle."""
        threshold = settings.FACE_VERIFICATION_NOD_THRESHOLD
        max_pitch = 0.0

        for landmarks in landmarks_list:
            _, pitch, _ = self._compute_yaw_pitch_roll(landmarks)
            max_pitch = max(max_pitch, pitch)

        if max_pitch > threshold:
            return True, f"Head nodded {max_pitch:.1f} degrees"

        return False, f"Head pitch angle {max_pitch:.1f} degrees, need {threshold} degrees"

    async def validate_challenge(
        self,
        video_frames: list,
        challenge_type: str,
    ) -> Tuple[bool, str]:
        """Validate that the user performed the requested liveness challenge."""
        if challenge_type not in CHALLENGE_TYPES:
            return False, f"Unknown challenge type: {challenge_type}"

        # Extract landmarks from all frames
        landmarks_list = []
        for frame in video_frames:
            face = self._get_face(frame)
            if face is not None and hasattr(face, 'landmark_3d_68'):
                landmarks_list.append(face.landmark_3d_68)

        if len(landmarks_list) < 2:
            return False, "Insufficient frames with detectable face landmarks"

        # Run challenge-specific validation
        if challenge_type == "blink":
            return self._validate_blink(landmarks_list)
        elif challenge_type in ("turn_left", "turn_right"):
            return self._validate_turn(landmarks_list, challenge_type)
        elif challenge_type == "smile":
            return self._validate_smile(landmarks_list)
        elif challenge_type == "nod":
            return self._validate_nod(landmarks_list)

        return False, "Challenge validation not implemented"

    async def extract_video_embeddings(
        self, video_frames: list
    ) -> Tuple[Optional[np.ndarray], str]:
        """Extract and average face embeddings from video frames."""
        embeddings = []

        for frame in video_frames:
            embedding = self._get_embedding(frame)
            if embedding is not None:
                embeddings.append(embedding)

        if not embeddings:
            return None, "No faces detected in video"

        if len(embeddings) < settings.FACE_VERIFICATION_MIN_FRAMES:
            return None, f"Only {len(embeddings)} valid frames, need at least {settings.FACE_VERIFICATION_MIN_FRAMES}"

        # Average embeddings
        avg_embedding = np.mean(embeddings, axis=0)
        # Normalize
        norm = np.linalg.norm(avg_embedding)
        if norm > 0:
            avg_embedding = avg_embedding / norm

        return avg_embedding, ""

    async def extract_photo_embeddings(
        self, photo_bytes_list: list
    ) -> Tuple[Optional[np.ndarray], str]:
        """Extract and average face embeddings from photo bytes."""
        embeddings = []

        for photo_bytes in photo_bytes_list:
            # Decode image
            nparr = np.frombuffer(photo_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is None:
                continue

            embedding = self._get_embedding(frame)
            if embedding is not None:
                embeddings.append(embedding)

        if not embeddings:
            return None, "No faces detected in profile photos"

        # Average embeddings
        avg_embedding = np.mean(embeddings, axis=0)
        # Normalize
        norm = np.linalg.norm(avg_embedding)
        if norm > 0:
            avg_embedding = avg_embedding / norm

        return avg_embedding, ""

    async def process_video(self, video_bytes: bytes) -> Tuple[Optional[list], str]:
        """Decode video, extract frames at configured sample rate."""
        try:
            import tempfile

            # Write bytes to a temp file so OpenCV's video backend can open it.
            with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
                tmp.write(video_bytes)
                tmp_path = tmp.name

            try:
                cap = cv2.VideoCapture(tmp_path)
                if not cap.isOpened():
                    return None, "Could not open video"

                # Get video properties
                fps = cap.get(cv2.CAP_PROP_FPS)
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                duration = total_frames / fps if fps > 0 else 0

                # Validate duration
                if duration < settings.FACE_VERIFICATION_VIDEO_MIN_SECONDS:
                    return None, f"Video too short: {duration:.1f}s, minimum {settings.FACE_VERIFICATION_VIDEO_MIN_SECONDS}s"
                if duration > settings.FACE_VERIFICATION_VIDEO_MAX_SECONDS:
                    return None, f"Video too long: {duration:.1f}s, maximum {settings.FACE_VERIFICATION_VIDEO_MAX_SECONDS}s"

                # Calculate frame sampling interval
                target_fps = settings.FACE_VERIFICATION_FRAME_RATE
                sample_interval = max(1, int(fps / target_fps))

                frames = []
                frame_count = 0

                while True:
                    ret, frame = cap.read()
                    if not ret:
                        break

                    if frame_count % sample_interval == 0:
                        # Resize for consistent processing
                        frame = cv2.resize(frame, (640, 640))
                        frames.append(frame)

                        if len(frames) >= settings.FACE_VERIFICATION_MAX_FRAMES:
                            break

                    frame_count += 1

                cap.release()

                if len(frames) < settings.FACE_VERIFICATION_MIN_FRAMES:
                    return None, f"Only extracted {len(frames)} frames, need at least {settings.FACE_VERIFICATION_MIN_FRAMES}"

                return frames, ""

            finally:
                _safe_remove_file(tmp_path)

        except Exception as e:
            logger.error("video_processing_error", error=str(e))
            return None, f"Failed to process video: {str(e)}"

    def compare_embeddings(
        self,
        video_embedding: np.ndarray,
        photo_embedding: np.ndarray,
    ) -> Tuple[bool, float]:
        """Cosine similarity comparison."""
        # Compute cosine similarity
        dot_product = np.dot(video_embedding, photo_embedding)
        norm_product = np.linalg.norm(video_embedding) * np.linalg.norm(photo_embedding)

        if norm_product == 0:
            return False, 0.0

        similarity = dot_product / norm_product
        matched = bool(similarity >= settings.FACE_MATCH_THRESHOLD)

        return matched, float(similarity)


# Singleton
face_verification_service = FaceVerificationService()
