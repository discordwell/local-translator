"""FastAPI endpoint tests using a fake translator (no model load).

These tests instantiate ``TestClient`` without the context-manager form on
purpose: the app's lifespan loads the real ~9 GB model, which we must not do
in a unit test. Plain instantiation skips lifespan startup/shutdown.
"""

import io
import urllib.parse

import pytest
from fastapi.testclient import TestClient

import main
from translator import AudioDecodeError


class FakeTranslator:
    def __init__(self, loaded=True, ja_en="hello",
                 en_ja=(b"RIFF-fake-wav-bytes", "こんにちは"), raises=None,
                 device_name="cpu", model_name="fake-model"):
        self.is_loaded = loaded
        self._ja_en = ja_en
        self._en_ja = en_ja
        self._raises = raises
        self.device_name = device_name
        self.model_name = model_name

    def load(self):  # no-op, so an accidental lifespan call stays cheap
        self.is_loaded = True

    def translate_ja_to_en(self, audio_bytes):
        if self._raises:
            raise self._raises
        return self._ja_en

    def translate_en_to_ja(self, audio_bytes):
        if self._raises:
            raise self._raises
        return self._en_ja


@pytest.fixture
def client():
    return TestClient(main.app)


def _use(monkeypatch, fake):
    monkeypatch.setattr(main, "get_translator", lambda: fake)


def _wav_upload():
    return {"audio": ("audio.wav", io.BytesIO(b"RIFFdummy"), "audio/wav")}


def _empty_upload():
    return {"audio": ("audio.wav", io.BytesIO(b""), "audio/wav")}


def test_health_ok(client, monkeypatch):
    _use(monkeypatch, FakeTranslator(loaded=True, device_name="mps", model_name="seamless"))
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "model_loaded": True,
        "device": "mps",
        "model": "seamless",
    }


def test_health_reports_unloaded_model(client, monkeypatch):
    # Before load() the device is unknown (None -> JSON null), but the model name
    # is still reported so a client can see what will be loaded.
    _use(monkeypatch, FakeTranslator(loaded=False, device_name=None, model_name="seamless"))
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["model_loaded"] is False
    assert body["device"] is None
    assert body["model"] == "seamless"


def test_ja_to_en_success(client, monkeypatch):
    _use(monkeypatch, FakeTranslator(ja_en="good morning"))
    r = client.post("/translate/ja-to-en", files=_wav_upload())
    assert r.status_code == 200
    assert r.json() == {"text": "good morning"}


def test_ja_to_en_503_when_model_not_loaded(client, monkeypatch):
    _use(monkeypatch, FakeTranslator(loaded=False))
    r = client.post("/translate/ja-to-en", files=_wav_upload())
    assert r.status_code == 503


def test_ja_to_en_500_on_translation_error(client, monkeypatch):
    _use(monkeypatch, FakeTranslator(raises=RuntimeError("boom")))
    r = client.post("/translate/ja-to-en", files=_wav_upload())
    assert r.status_code == 500
    assert "Translation failed" in r.json()["detail"]


def test_ja_to_en_400_on_empty_upload(client, monkeypatch):
    _use(monkeypatch, FakeTranslator(loaded=True))
    r = client.post("/translate/ja-to-en", files=_empty_upload())
    assert r.status_code == 400
    assert "Empty" in r.json()["detail"]


def test_ja_to_en_400_on_undecodable_audio(client, monkeypatch):
    # AudioDecodeError (bad input) is a client error, not a 500.
    _use(monkeypatch, FakeTranslator(raises=AudioDecodeError("bad wav")))
    r = client.post("/translate/ja-to-en", files=_wav_upload())
    assert r.status_code == 400
    assert r.json()["detail"] == "bad wav"


def test_en_to_ja_returns_audio_and_text_header(client, monkeypatch):
    """Regression test: en-to-ja must unpack (audio, text), not return the tuple."""
    fake = FakeTranslator(en_ja=(b"RIFF-fake-wav-bytes", "こんにちは"))
    _use(monkeypatch, fake)
    r = client.post("/translate/en-to-ja", files=_wav_upload())
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content == b"RIFF-fake-wav-bytes"
    # Japanese text rides along in a percent-encoded (latin-1 safe) header.
    assert urllib.parse.unquote(r.headers["x-translation-text"]) == "こんにちは"


def test_en_to_ja_header_round_trips_and_resists_injection(client, monkeypatch):
    """The X-Translation-Text contract the WiFi client decodes.

    Percent-encoding must (a) recover the exact UTF-8 text on the client side and
    (b) never emit a raw CR/LF, which would otherwise let translated text inject
    extra HTTP headers. The iOS client mirrors this with removingPercentEncoding.
    """
    # Includes a CRLF — the exact sequence an attacker would use to split the
    # header — plus a multibyte run and a literal percent and quote.
    text = "これはテスト\r\nLine 2: 50% \"done\""
    _use(monkeypatch, FakeTranslator(en_ja=(b"RIFF-wav", text)))
    r = client.post("/translate/en-to-ja", files=_wav_upload())
    assert r.status_code == 200

    raw = r.headers["x-translation-text"]
    # Neither the carriage return nor the newline leaks into the raw header value.
    assert "\n" not in raw and "\r" not in raw
    # And the client recovers the original text byte-for-byte.
    assert urllib.parse.unquote(raw) == text


def test_en_to_ja_empty_text_still_sends_header(client, monkeypatch):
    """When the model yields no intermediate text, the header is still present
    (empty), so the client's decode path has a well-defined, non-crashing input."""
    _use(monkeypatch, FakeTranslator(en_ja=(b"RIFF-wav", "")))
    r = client.post("/translate/en-to-ja", files=_wav_upload())
    assert r.status_code == 200
    assert r.headers["x-translation-text"] == ""
    assert urllib.parse.unquote(r.headers["x-translation-text"]) == ""


def test_en_to_ja_503_when_model_not_loaded(client, monkeypatch):
    _use(monkeypatch, FakeTranslator(loaded=False))
    r = client.post("/translate/en-to-ja", files=_wav_upload())
    assert r.status_code == 503


def test_en_to_ja_500_on_translation_error(client, monkeypatch):
    _use(monkeypatch, FakeTranslator(raises=ValueError("bad audio")))
    r = client.post("/translate/en-to-ja", files=_wav_upload())
    assert r.status_code == 500
    assert "Translation failed" in r.json()["detail"]


def test_en_to_ja_400_on_empty_upload(client, monkeypatch):
    _use(monkeypatch, FakeTranslator(loaded=True))
    r = client.post("/translate/en-to-ja", files=_empty_upload())
    assert r.status_code == 400
    assert "Empty" in r.json()["detail"]


def test_en_to_ja_400_on_undecodable_audio(client, monkeypatch):
    _use(monkeypatch, FakeTranslator(raises=AudioDecodeError("bad wav")))
    r = client.post("/translate/en-to-ja", files=_wav_upload())
    assert r.status_code == 400
    assert r.json()["detail"] == "bad wav"


def test_inference_is_serialized(monkeypatch):
    """Concurrent translations must not run two model calls at once.

    The endpoints offload inference to a threadpool (keeping the event loop free)
    but a lock serializes the calls, so the single shared model/GPU is never hit
    by two ``generate()`` calls simultaneously. A fake translator records the
    peak number of concurrent in-flight calls; with the lock it must be 1.
    """
    import asyncio
    import threading
    import time

    import httpx

    # Use a fresh inference lock bound to THIS test's event loop (and let
    # monkeypatch discard it afterward), so the module-global never leaks a
    # binding to a closed loop between tests.
    monkeypatch.setattr(main, "_inference_lock", None)

    state = {"active": 0, "max_active": 0}
    guard = threading.Lock()

    class SlowFake:
        is_loaded = True

        def load(self):
            pass

        def translate_ja_to_en(self, audio_bytes):
            with guard:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            time.sleep(0.02)  # long enough for overlap to occur if unserialized
            with guard:
                state["active"] -= 1
            return "ok"

    _use(monkeypatch, SlowFake())

    async def scenario():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await asyncio.gather(
                *(c.post("/translate/ja-to-en", files=_wav_upload()) for _ in range(4))
            )

    results = asyncio.run(scenario())
    assert all(r.status_code == 200 for r in results)
    assert state["max_active"] == 1
