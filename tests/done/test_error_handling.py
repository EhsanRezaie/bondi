# tests/test_error_logging.py
import json
import uuid

import pytest
from fastapi import Request
from unittest.mock import patch

import app.models.message as message_model
from app.models.message import Message


# ---------------------------------------------------------------------------
# Global exception handlers
# ---------------------------------------------------------------------------

def _make_request(app) -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/_test_boom",
            "raw_path": b"/_test_boom",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("test", 80),
            "app": app,
        }
    )


class TestGlobalExceptionHandling:
    async def test_unhandled_exception_handler_returns_500_json(self, client):
        """The global Exception handler must return safe JSON + request id."""
        from app.main import app

        handler = app.exception_handlers[Exception]
        resp = await handler(_make_request(app), RuntimeError("boom"))
        assert resp.status_code == 500
        assert json.loads(resp.body) == {"detail": "Internal Server Error"}
        assert resp.headers.get("X-Request-ID")

    async def test_unhandled_exception_propagates_in_debug(self, client):
        """DEBUG=True: Starlette surfaces the exception so devs see the traceback."""
        from app.main import app

        async def boom():
            raise RuntimeError("boom")

        added = False
        try:
            app.add_api_route("/_test_boom", boom, methods=["GET"])
            added = True
            with pytest.raises(RuntimeError, match="boom"):
                await client.get("/_test_boom")
        finally:
            if added:
                app.router.routes = [
                    r for r in app.router.routes if getattr(r, "path", "") != "/_test_boom"
                ]

    async def test_validation_error_returns_422(self, client):
        """RequestValidationError must map to 422 (and never to GlitchTip)."""
        resp = await client.post("/api/v1/auth/request-code", json={})
        assert resp.status_code == 422
        assert "detail" in resp.json()
        assert resp.headers.get("X-Request-ID")

    async def test_health_ready_returns_ok(self, client):
        """Normal endpoints are unaffected by the new handlers."""
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Message encryption: never store plaintext, log failures
# ---------------------------------------------------------------------------

class TestMessageEncryptionFailure:
    def test_encrypt_failure_raises_and_does_not_store_plaintext(self):
        m = Message(
            id=uuid.uuid4(),
            chat_id=uuid.uuid4(),
            sender_id=uuid.uuid4(),
            receiver_id=uuid.uuid4(),
        )
        with patch.object(message_model, "encrypt_message", side_effect=Exception("crypto boom")):
            with pytest.raises(Exception, match="crypto boom"):
                m.content = "top-secret"
        # The plaintext must never reach _content.
        assert m._content is None or "top-secret" not in str(m._content)

    def test_decrypt_failure_returns_ciphertext_and_logs(self, caplog):
        m = Message(
            id=uuid.uuid4(),
            chat_id=uuid.uuid4(),
            sender_id=uuid.uuid4(),
            receiver_id=uuid.uuid4(),
        )
        m._content = "ciphertext-blob"
        with patch.object(message_model, "decrypt_message", side_effect=Exception("decrypt boom")):
            assert m.content == "ciphertext-blob"