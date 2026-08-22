"""
Face Verification Service (lightweight mobile models)

Uses two tiny ONNX models from opencv_zoo, both mobile-grade and run on CPU
via onnxruntime:
  - YuNet  (~340 KB)  face detection
  - SFace  (~9 MB)    face recognition / 128-d embeddings

No landmarks, head-pose or eye-openness heuristics. Flow:
  - Detect faces. The image must contain exactly one face.
  - Align the face to a canonical 112x112 crop.
  - Extract its SFace embedding.
  - Compare embeddings (cosine similarity) against the profile photos.

All heavy computation runs in a thread executor to avoid blocking the loop.
"""

import asyncio
import threading
import urllib.request
from pathlib import Path
from typing import ClassVar, Optional, Tuple

import cv2
import numpy as np
import onnxruntime as ort

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("face_verification_service")


# opencv_zoo model URLs (small, mobile-friendly)
YU_NET_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
S_FACE_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_recognition_sface/face_recognition_sface_2021dec.onnx"
)

# SFace expects 112x112 aligned crops.
INPUT_SIZE = 112

# 5-point template (left eye, right eye, nose, left mouth, right mouth)
# used for the similarity-transform alignment, matching SFace training.
ALIGN_TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


class FaceVerificationService:
    """Singleton service for face detection and embedding comparison."""

    _instance: ClassVar[Optional["FaceVerificationService"]] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()
    _face_detector: Optional[object] = None
    _rec: Optional[ort.InferenceSession] = None

    def __new__(cls) -> "FaceVerificationService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    # ------------------------------------------------------------------
    # Model loading (downloaded once, cached on disk)
    # ------------------------------------------------------------------
    def _model_dir(self) -> Path:
        return Path(settings.FACE_MODELS_DIR)

    def _download(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            return dest
        logger.info("face_model_download_start", url=url)
        urllib.request.urlretrieve(url, dest)
        logger.info("face_model_downloaded", path=str(dest))
        return dest

    def _ensure_model(self) -> None:
        if self._face_detector is not None and self._rec is not None:
            return
        with self._lock:
            if self._face_detector is not None and self._rec is not None:
                return
            d = self._model_dir()
            det_path = self._download(YU_NET_URL, d / "face_detection_yunet.onnx")
            rec_path = self._download(S_FACE_URL, d / "face_recognition_sface.onnx")

            # YuNet via OpenCV's built-in FaceDetectorYN (handles resize,
            # normalization, box decoding and NMS correctly).
            self._face_detector = cv2.FaceDetectorYN.create(
                str(det_path),
                "",
                (320, 320),
                score_threshold=settings.FACE_DET_SCORE_THRESHOLD,
                nms_threshold=0.3,
                top_k=5000,
            )

            so = ort.SessionOptions()
            so.intra_op_num_threads = settings.FACE_MODEL_THREADS
            self._rec = ort.InferenceSession(
                str(rec_path), sess_options=so, providers=["CPUExecutionProvider"]
            )
            logger.info("face_model_loaded", models="yunet+sface")

    # ------------------------------------------------------------------
    # Detection / alignment / embedding
    # ------------------------------------------------------------------
    def _detect_faces(self, frame: np.ndarray) -> list:
        """Return YuNet detections; each is [x, y, w, h, score, *5 landmarks]."""
        h, w = frame.shape[:2]
        self._face_detector.setInputSize((w, h))
        _, faces = self._face_detector.detect(frame)
        if faces is None:
            return []
        return list(faces)

    def _align(self, frame: np.ndarray, det) -> Optional[np.ndarray]:
        """Warp the detected face to the canonical 112x112 SFace crop."""
        # FaceDetectorYN row: [x, y, w, h, l1..l5 (2 each), score]. The
        # landmark order already matches the template:
        #   left eye, right eye, nose, left mouth, right mouth.
        src = np.array(
            [det[4:6], det[6:8], det[8:10], det[10:12], det[12:14]],
            dtype=np.float32,
        ).reshape(5, 2)

        tform = cv2.estimateAffinePartial2D(src, ALIGN_TEMPLATE, method=cv2.LMEDS)
        if tform[0] is None:
            return None
        return cv2.warpAffine(frame, tform[0], (INPUT_SIZE, INPUT_SIZE), borderValue=0.0)

    def _embed(self, aligned: np.ndarray) -> Optional[np.ndarray]:
        """Return an L2-normalized SFace embedding, or None."""
        blob = cv2.dnn.blobFromImage(
            aligned, 1.0 / 128.0, (INPUT_SIZE, INPUT_SIZE),
            (127.5, 127.5, 127.5), swapRB=True, crop=False,
        )
        out = self._rec.run(None, {self._rec.get_inputs()[0].name: blob})[0]
        emb = out[0].astype(np.float32)
        norm = np.linalg.norm(emb)
        if norm == 0:
            return None
        return emb / norm

    # ------------------------------------------------------------------
    # Public API (called by verify / photos endpoints)
    # ------------------------------------------------------------------
    async def extract_image_embedding(
        self, image_bytes: bytes
    ) -> Tuple[Optional[np.ndarray], str]:
        """Decode an image and return a face embedding (exactly one face)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._extract_image_embedding_sync, image_bytes
        )

    def _extract_image_embedding_sync(self, image_bytes: bytes):
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            return None, "Could not decode the image"

        self._ensure_model()
        faces = self._detect_faces(frame)
        if len(faces) == 0:
            return None, "No face detected in the image"
        if len(faces) > 1:
            return None, "Multiple faces detected. Please upload a photo of only yourself."

        aligned = self._align(frame, faces[0])
        if aligned is None:
            return None, "Could not align the face"
        embedding = self._embed(aligned)
        if embedding is None:
            return None, "Could not extract the face embedding"
        return embedding, ""

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
        faces = self._detect_faces(frame)
        if len(faces) == 0:
            return None, "No face detected in the photo"
        if len(faces) > 1:
            return None, "Multiple faces detected. Only photos of yourself are allowed."

        aligned = self._align(frame, faces[0])
        if aligned is None:
            return None, "Could not align the face"
        embedding = self._embed(aligned)
        if embedding is None:
            return None, "Could not extract the face embedding"
        return embedding, ""

    async def extract_photo_embeddings(
        self, photo_bytes_list: list
    ) -> Tuple[Optional[np.ndarray], str]:
        """Average face embeddings across photos (for face-reference matching)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._extract_photo_embeddings_sync, photo_bytes_list
        )

    def _extract_photo_embeddings_sync(self, photo_bytes_list: list):
        embeddings = []
        self._ensure_model()
        for photo_bytes in photo_bytes_list:
            nparr = np.frombuffer(photo_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is None:
                continue
            faces = self._detect_faces(frame)
            if len(faces) != 1:
                continue
            aligned = self._align(frame, faces[0])
            if aligned is None:
                continue
            emb = self._embed(aligned)
            if emb is not None:
                embeddings.append(emb)

        if not embeddings:
            return None, "No faces detected in profile photos"

        avg_embedding = np.mean(embeddings, axis=0)
        norm = np.linalg.norm(avg_embedding)
        if norm > 0:
            avg_embedding = avg_embedding / norm
        return avg_embedding, ""

    def compare_embeddings(
        self,
        embedding_a: np.ndarray,
        embedding_b: np.ndarray,
    ) -> Tuple[bool, float]:
        """Cosine similarity comparison against FACE_MATCH_THRESHOLD."""
        if embedding_a is None or embedding_b is None:
            return False, 0.0
        a = np.asarray(embedding_a, dtype=np.float64)
        b = np.asarray(embedding_b, dtype=np.float64)
        # Guard against stale references with a different embedding size
        # (e.g. 512-d from the old model) so they never crash or misfire.
        if a.ndim != 1 or b.ndim != 1 or a.shape != b.shape or a.size == 0:
            return False, 0.0

        dot_product = np.dot(a, b)
        norm_product = np.linalg.norm(a) * np.linalg.norm(b)

        if norm_product == 0:
            return False, 0.0

        similarity = float(dot_product / norm_product)
        matched = similarity >= settings.FACE_MATCH_THRESHOLD
        return matched, similarity


# Singleton
face_verification_service = FaceVerificationService()
