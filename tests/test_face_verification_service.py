from unittest.mock import MagicMock, patch
import zipfile

import cv2
import numpy as np

from app.core.config import settings
from app.services.face_verification_service import (
    ALIGN_TEMPLATE,
    FaceVerificationService,
    INPUT_SIZE,
)


def _encoded_frame():
    ok, encoded = cv2.imencode(".jpg", np.zeros((80, 100, 3), dtype=np.uint8))
    assert ok
    return encoded.tobytes()


def _service():
    service = FaceVerificationService()
    service._face_detector = None
    service._rec = None
    return service


class TestFaceServiceModelFreeBranches:
    def test_model_paths_download_and_recognition_archive_reuse(self, tmp_path, monkeypatch):
        service = _service()
        monkeypatch.setattr(settings, "FACE_MODELS_DIR", str(tmp_path))
        assert service._model_dir() == tmp_path

        existing = tmp_path / "existing.onnx"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_bytes(b"model")
        assert service._download("https://not-called.test/model", existing) == existing

        archive = tmp_path / "buffalo_sc.zip"
        with zipfile.ZipFile(archive, "w") as model_archive:
            model_archive.writestr("w600k_mbf.onnx", b"fake-model")
        assert service._ensure_recognition_model(tmp_path) == tmp_path / "w600k_mbf.onnx"
        assert (tmp_path / "w600k_mbf.onnx").read_bytes() == b"fake-model"

    def test_ensure_model_downloads_and_initializes_detector_and_recognizer(self, tmp_path, monkeypatch):
        service = _service()
        monkeypatch.setattr(settings, "FACE_MODELS_DIR", str(tmp_path))
        monkeypatch.setattr(settings, "FACE_MODEL_THREADS", 2)
        monkeypatch.setattr(settings, "FACE_DET_SCORE_THRESHOLD", 0.42)
        monkeypatch.setattr(service, "_download", lambda _url, destination: destination)
        monkeypatch.setattr(service, "_ensure_recognition_model", lambda _directory: tmp_path / "rec.onnx")
        detector = MagicMock()
        session = MagicMock()
        with (
            patch("app.services.face_verification_service.cv2.FaceDetectorYN.create", return_value=detector) as create_detector,
            patch("app.services.face_verification_service.ort.SessionOptions") as session_options,
            patch("app.services.face_verification_service.ort.InferenceSession", return_value=session) as inference,
        ):
            service._ensure_model()

        assert service._face_detector is detector
        assert service._rec is session
        assert session_options.return_value.intra_op_num_threads == 2
        assert create_detector.call_args.args[2] == (320, 320)
        assert create_detector.call_args.kwargs["score_threshold"] == 0.42
        assert inference.call_args.kwargs["providers"] == ["CPUExecutionProvider"]

    def test_ensure_model_returns_when_both_models_loaded(self, monkeypatch):
        service = _service()
        service._face_detector = object()
        service._rec = object()
        download = MagicMock(side_effect=AssertionError("unexpected model load"))
        monkeypatch.setattr(service, "_download", download)

        service._ensure_model()

        download.assert_not_called()

    def test_detect_faces_sets_dimensions_and_handles_none_or_faces(self):
        service = _service()
        detector = MagicMock()
        service._face_detector = detector
        frame = np.zeros((120, 240, 3), dtype=np.uint8)
        detector.detect.return_value = (None, None)
        assert service._detect_faces(frame) == []
        detector.setInputSize.assert_called_with((240, 120))

        faces = np.array([[1, 2, 3]])
        detector.detect.return_value = (None, faces)
        detected = service._detect_faces(frame)
        assert len(detected) == 1
        np.testing.assert_array_equal(detected[0], faces[0])

    def test_alignment_and_embedding_success_and_zero_norm(self):
        service = _service()
        frame = np.zeros((160, 160, 3), dtype=np.uint8)
        landmarks = ALIGN_TEMPLATE + 10
        detection = np.concatenate(([10, 10, 100, 100], landmarks.flatten(), [0.99]))
        aligned = service._align(frame, detection)
        assert aligned.shape == (INPUT_SIZE, INPUT_SIZE, 3)
        with patch("app.services.face_verification_service.cv2.estimateAffinePartial2D", return_value=(None, None)):
            assert service._align(frame, detection) is None

        service._rec = MagicMock()
        service._rec.get_inputs.return_value = [MagicMock(name="input")]
        service._rec.get_inputs.return_value[0].name = "input"
        service._rec.run.return_value = [np.array([[3.0, 4.0]], dtype=np.float32)]
        embedding = service._embed(aligned)
        assert np.allclose(embedding, np.array([0.6, 0.8]))
        service._rec.run.return_value = [np.zeros((1, 2), dtype=np.float32)]
        assert service._embed(aligned) is None

    def test_extract_image_sync_failure_reasons_without_model_loading(self, monkeypatch):
        service = _service()
        ensure = MagicMock()
        monkeypatch.setattr(service, "_ensure_model", ensure)
        assert service._extract_image_embedding_sync(b"not an image") == (
            None,
            "Could not decode the image",
        )
        ensure.assert_not_called()

        monkeypatch.setattr(service, "_ensure_model", lambda: None)
        monkeypatch.setattr(service, "_detect_faces", lambda _frame: [])
        assert service._extract_image_embedding_sync(_encoded_frame()) == (
            None,
            "No face detected in the image",
        )

        monkeypatch.setattr(service, "_detect_faces", lambda _frame: [1, 2])
        assert service._extract_image_embedding_sync(_encoded_frame()) == (
            None,
            "Multiple faces detected. Please upload a photo of only yourself.",
        )

        monkeypatch.setattr(service, "_detect_faces", lambda _frame: [1])
        monkeypatch.setattr(service, "_align", lambda _frame, _face: None)
        assert service._extract_image_embedding_sync(_encoded_frame()) == (
            None,
            "Could not align the face",
        )

        monkeypatch.setattr(service, "_align", lambda _frame, _face: np.zeros((1, 1, 3)))
        monkeypatch.setattr(service, "_embed", lambda _aligned: None)
        assert service._extract_image_embedding_sync(_encoded_frame()) == (
            None,
            "Could not extract the face embedding",
        )

        expected = np.array([0.1, 0.2], dtype=np.float32)
        monkeypatch.setattr(service, "_embed", lambda _aligned: expected)
        actual, error = service._extract_image_embedding_sync(_encoded_frame())
        assert error == ""
        assert actual is expected

    def test_single_photo_sync_failure_reasons(self, monkeypatch):
        service = _service()
        monkeypatch.setattr(service, "_ensure_model", lambda: None)
        assert service._extract_single_photo_embedding_sync(b"bad") == (
            None,
            "Could not decode the photo",
        )
        monkeypatch.setattr(service, "_detect_faces", lambda _frame: [])
        assert service._extract_single_photo_embedding_sync(_encoded_frame()) == (
            None,
            "No face detected in the photo",
        )
        monkeypatch.setattr(service, "_detect_faces", lambda _frame: [1, 2])
        assert service._extract_single_photo_embedding_sync(_encoded_frame()) == (
            None,
            "Multiple faces detected. Only photos of yourself are allowed.",
        )
        monkeypatch.setattr(service, "_detect_faces", lambda _frame: [1])
        monkeypatch.setattr(service, "_align", lambda _frame, _face: None)
        assert service._extract_single_photo_embedding_sync(_encoded_frame()) == (
            None,
            "Could not align the face",
        )
        monkeypatch.setattr(service, "_align", lambda _frame, _face: np.zeros((1, 1, 3)))
        monkeypatch.setattr(service, "_embed", lambda _aligned: None)
        assert service._extract_single_photo_embedding_sync(_encoded_frame()) == (
            None,
            "Could not extract the face embedding",
        )
        expected = np.array([0.4, 0.3], dtype=np.float32)
        monkeypatch.setattr(service, "_embed", lambda _aligned: expected)
        actual, error = service._extract_single_photo_embedding_sync(_encoded_frame())
        assert actual is expected
        assert error == ""

    def test_profile_photo_batch_skips_bad_items_and_normalizes_mean(self, monkeypatch):
        service = _service()
        monkeypatch.setattr(service, "_ensure_model", lambda: None)
        valid = _encoded_frame()
        monkeypatch.setattr(service, "_detect_faces", lambda _frame: [1])
        monkeypatch.setattr(service, "_align", lambda _frame, _face: np.zeros((1, 1, 3)))
        embeddings = iter([np.array([1.0, 0.0]), np.array([0.0, 1.0])])
        monkeypatch.setattr(service, "_embed", lambda _aligned: next(embeddings))

        averaged, error = service._extract_photo_embeddings_sync([b"bad", valid, valid])
        assert error == ""
        assert np.allclose(averaged, np.array([2**-0.5, 2**-0.5]))

        monkeypatch.setattr(service, "_detect_faces", lambda _frame: [])
        absent, error = service._extract_photo_embeddings_sync([valid])
        assert absent is None
        assert error == "No faces detected in profile photos"

    def test_compare_embeddings_none_shape_empty_and_threshold(self, monkeypatch):
        service = _service()
        assert service.compare_embeddings(None, np.ones(2)) == (False, 0.0)
        assert service.compare_embeddings(np.ones((1, 2)), np.ones(2)) == (False, 0.0)
        assert service.compare_embeddings(np.ones(2), np.ones(3)) == (False, 0.0)
        assert service.compare_embeddings(np.array([]), np.array([])) == (False, 0.0)
        assert service.compare_embeddings(np.zeros(2), np.ones(2)) == (False, 0.0)

        monkeypatch.setattr(settings, "FACE_MATCH_THRESHOLD", 0.8)
        assert service.compare_embeddings(np.array([1.0, 0.0]), np.array([0.8, 0.6])) == (
            True,
            0.8,
        )
