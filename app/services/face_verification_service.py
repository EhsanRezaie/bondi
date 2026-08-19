"""
Face Verification Service (image-based)

Lighter alternative to the previous video/liveness flow. Uses InsightFace
(CPU) for face detection + 512-d embeddings and OpenCV for single-image
heuristics. No video processing, no multi-frame liveness challenges.

Flow:
  - User uploads a clear selfie (image).
  - We detect exactly one face, run light single-image checks
    (frontal head pose, eyes open, face not too small), extract the
    embedding, and compare it against the user's profile photos.
  - If the selfie matches the profile photos, the account is verified and
    the selfie embedding becomes the canonical face reference.

All heavy computation runs in a thread executor to avoid blocking the loop.
"""

import asyncio
import io
import threading
from typing import ClassVar, Optional, Tuple

import cv2
import numpy as np

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("face_verification_service")


# 3D model points for head pose estimation (approximate face landmarks)
MODEL_POINTS_3D = np.array([
    [0.0, 0.0, 0.0],        # Nose tip
    [0.0, -330.0, -65.0],   # Chin
    [-225.0, 170.0, -135.0],  # Left eye left corner
    [225.0, 170.0, -135.0],   # Right eye right corner
    [-150.0, -150.0, -125.0], # Left mouth corner
    [150.0, -150.0, -125.0],  # Right mouth corner
], dtype=np.float64)

# 2D landmark indices for head pose (from 68-point model)
POSE_LANDMARK_INDICES = [30, 8, 36, 45, 48, 54]

# Eye landmark indices (68-point model)
LEFT_EYE_INDICES = [36, 37, 38, 39, 40, 41]
RIGHT_EYE_INDICES = [42, 43, 44, 45, 46, 47]

# Frontal pose limits (degrees)
MAX_YAW = 25.0
MAX_PITCH = 25.0

# Minimum EAR to consider eyes open
MIN_EAR = 0.18

# Minimum fraction of image width/height the face should occupy
MIN_FACE_RATIO = 0.15


class FaceVerificationService:
    """Singleton service for face detection, embedding and selfie checks."""

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
        """Detect a single face in a frame. Returns None if 0 or >1 faces."""
        self._ensure_model()
        faces = self._face_analyzer.get(frame)
        if len(faces) != 1:
            return None
        return faces[0]

    def _get_embedding(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Get 512-d face embedding from a frame."""
        face = self._get_face(frame)
        if face is None:
            return None
        return face.normed_embedding

    def _compute_ear(self, landmarks: np.ndarray) -> float:
        """Compute Eye Aspect Ratio from 68 landmarks (1 = open, 0 = closed)."""
        v1 = np.linalg.norm(landmarks[37] - landmarks[35])
        v2 = np.linalg.norm(landmarks[38] - landmarks[36])
        v3 = np.linalg.norm(landmarks[44] - landmarks[42])
        v4 = np.linalg.norm(landmarks[43] - landmarks[45])

        h1 = np.linalg.norm(landmarks[36] - landmarks[39])
        h2 = np.linalg.norm(landmarks[42] - landmarks[45])

        if h1 == 0 or h2 == 0:
            return 0.0

        ear_left = (v1 + v2) / (2.0 * h1)
        ear_right = (v3 + v4) / (2.0 * h2)

        return float((ear_left + ear_right) / 2.0)

    def _compute_yaw_pitch(self, landmarks: np.ndarray) -> Tuple[float, float]:
        """Estimate head yaw/pitch (degrees) from 68 landmarks via PnP."""
        h, w = 640, 640  # standardized processing size
        focal_length = w
        center = (w / 2, h / 2)
        camera_matrix = np.array([
            [focal_length, 0, center[0]],
            [0, focal_length, center[1]],
            [0, 0, 1],
        ], dtype=np.float64)
        dist_coeffs = np.zeros((4, 1), dtype=np.float64)

        image_points = np.array([
            landmarks[30],  # Nose tip
            landmarks[8],   # Chin
            landmarks[36],  # Left eye left corner
            landmarks[45],  # Right eye right corner
            landmarks[48],  # Left mouth corner
            landmarks[54],  # Right mouth corner
        ], dtype=np.float64)

        success, rotation_vector, _ = cv2.solvePnP(
            MODEL_POINTS_3D,
            image_points,
            camera_matrix,
            dist_coeffs,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not success:
            return 0.0, 0.0

        rotation_matrix, _ = cv2.Rodrigues(rotation_vector)
        angles, _, _, _, _, _ = cv2.RQDecomp3x3(rotation_matrix)

        return float(angles[1]), float(angles[0])  # yaw, pitch

    def validate_selfie_image(self, frame: np.ndarray) -> Tuple[bool, str]:
        """
        Light single-image selfie checks (no video / multi-frame liveness):
          - exactly one face
          - frontal (|yaw|, |pitch| within limits)
          - eyes open (EAR above threshold)
          - face not too small
        """
        self._ensure_model()
        faces = self._face_analyzer.get(frame)
        if len(faces) == 0:
            return False, "No face detected in the image"
        if len(faces) > 1:
            return False, "Multiple faces detected. Please upload a photo of only yourself."

        face = faces[0]

        # Face size check — the bounding box should occupy a reasonable portion.
        h, w = frame.shape[:2]
        bbox = face.bbox
        fw = bbox[2] - bbox[0]
        fh = bbox[3] - bbox[1]
        ratio = max(fw / w, fh / h)
        if ratio < MIN_FACE_RATIO:
            return False, "Face is too small. Please take a closer, clearer selfie."

        landmarks = getattr(face, "landmark_3d_68", None)
        if landmarks is None:
            # No landmarks available — accept the frontal/size checks only.
            return True, ""

        yaw, pitch = self._compute_yaw_pitch(landmarks)
        if abs(yaw) > MAX_YAW or abs(pitch) > MAX_PITCH:
            return False, "Please look directly at the camera in good lighting."

        ear = self._compute_ear(landmarks)
        if ear < MIN_EAR:
            return False, "Please keep your eyes open for the selfie."

        return True, ""

    async def extract_image_embedding(
        self, image_bytes: bytes
    ) -> Tuple[Optional[np.ndarray], str]:
        """Decode an image and return its face embedding (exactly one face)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._extract_image_embedding_sync, image_bytes
        )

    def _extract_image_embedding_sync(self, image_bytes: bytes):
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            return None, "Could not decode the image"

        frame = cv2.resize(frame, (640, 640))

        ok, reason = self.validate_selfie_image(frame)
        if not ok:
            return None, reason

        embedding = self._get_embedding(frame)
        if embedding is None:
            return None, "No face detected in the image"

        return embedding, ""

    async def extract_photo_embeddings(
        self, photo_bytes_list: list
    ) -> Tuple[Optional[np.ndarray], str]:
        """
        Extract and average face embeddings from photo bytes.

        Returns the average embedding of all photos that contain exactly one
        detectable face, or None if no usable face is found.
        """
        embeddings = []

        for photo_bytes in photo_bytes_list:
            nparr = np.frombuffer(photo_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is None:
                continue

            embedding = self._get_embedding(frame)
            if embedding is not None:
                embeddings.append(embedding)

        if not embeddings:
            return None, "No faces detected in profile photos"

        avg_embedding = np.mean(embeddings, axis=0)
        norm = np.linalg.norm(avg_embedding)
        if norm > 0:
            avg_embedding = avg_embedding / norm

        return avg_embedding, ""

    async def extract_single_photo_embedding(
        self, photo_bytes: bytes
    ) -> Tuple[Optional[np.ndarray], str]:
        """Extract a face embedding from a single image (no selfie heuristics)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._extract_single_photo_embedding_sync, photo_bytes
        )

    def _extract_single_photo_embedding_sync(self, photo_bytes: bytes):
        nparr = np.frombuffer(photo_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            return None, "Could not decode the photo"

        self._ensure_model()
        faces = self._face_analyzer.get(frame)
        if len(faces) == 0:
            return None, "No face detected in the photo"
        if len(faces) > 1:
            return None, "Multiple faces detected. Only photos of yourself are allowed."

        return faces[0].normed_embedding, ""

    def compare_embeddings(
        self,
        embedding_a: np.ndarray,
        embedding_b: np.ndarray,
    ) -> Tuple[bool, float]:
        """Cosine similarity comparison against FACE_MATCH_THRESHOLD."""
        dot_product = np.dot(embedding_a, embedding_b)
        norm_product = np.linalg.norm(embedding_a) * np.linalg.norm(embedding_b)

        if norm_product == 0:
            return False, 0.0

        similarity = float(dot_product / norm_product)
        matched = similarity >= settings.FACE_MATCH_THRESHOLD

        return matched, similarity


# Singleton
face_verification_service = FaceVerificationService()
