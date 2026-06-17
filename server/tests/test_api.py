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


class FakeTranslator:
    def __init__(self, loaded=True, ja_en="hello",
                 en_ja=(b"RIFF-fake-wav-bytes", "こんにちは"), raises=None):
        self.is_loaded = loaded
        self._ja_en = ja_en
        self._en_ja = en_ja
        self._raises = raises

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


def test_health_ok(client, monkeypatch):
    _use(monkeypatch, FakeTranslator(loaded=True))
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "model_loaded": True}


def test_health_reports_unloaded_model(client, monkeypatch):
    _use(monkeypatch, FakeTranslator(loaded=False))
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["model_loaded"] is False


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


def test_en_to_ja_503_when_model_not_loaded(client, monkeypatch):
    _use(monkeypatch, FakeTranslator(loaded=False))
    r = client.post("/translate/en-to-ja", files=_wav_upload())
    assert r.status_code == 503


def test_en_to_ja_500_on_translation_error(client, monkeypatch):
    _use(monkeypatch, FakeTranslator(raises=ValueError("bad audio")))
    r = client.post("/translate/en-to-ja", files=_wav_upload())
    assert r.status_code == 500
    assert "Translation failed" in r.json()["detail"]
