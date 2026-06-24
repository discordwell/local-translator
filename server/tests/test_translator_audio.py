"""Tests for Translator audio helpers (no model load required)."""

import io

import numpy as np
import soundfile as sf
import pytest

from translator import Translator, get_translator, AudioDecodeError


@pytest.fixture
def tr():
    # The audio helpers don't touch the model, so an unloaded instance is fine.
    return Translator()


def _wav_bytes(waveform, sample_rate):
    buf = io.BytesIO()
    sf.write(buf, waveform, sample_rate, format="WAV")
    return buf.getvalue()


def test_load_audio_mono(tr):
    sr = 16000
    wave = np.sin(2 * np.pi * 440 * np.linspace(0, 1, sr, endpoint=False)).astype(np.float32)
    waveform, out_sr = tr._load_audio(_wav_bytes(wave, sr))
    assert out_sr == sr
    assert waveform.ndim == 1
    assert waveform.shape[0] == sr


def test_load_audio_downmixes_stereo(tr):
    sr = 16000
    n = 8000
    left = np.full(n, 0.5, dtype=np.float32)
    right = np.full(n, -0.5, dtype=np.float32)
    stereo = np.stack([left, right], axis=1)
    waveform, _ = tr._load_audio(_wav_bytes(stereo, sr))
    assert waveform.ndim == 1
    assert waveform.shape[0] == n
    # Average of +0.5 and -0.5 is ~0 (allowing for 16-bit quantization).
    assert np.allclose(waveform, 0.0, atol=1e-3)


def test_load_audio_rejects_empty_bytes(tr):
    with pytest.raises(AudioDecodeError):
        tr._load_audio(b"")


def test_load_audio_rejects_garbage(tr):
    with pytest.raises(AudioDecodeError):
        tr._load_audio(b"this is definitely not a wav file")


def test_load_audio_rejects_zero_sample_wav(tr):
    # A structurally valid WAV with no samples must not reach the model.
    empty_wav = _wav_bytes(np.zeros(0, dtype=np.float32), 16000)
    with pytest.raises(AudioDecodeError):
        tr._load_audio(empty_wav)


def test_resample_noop_when_rates_match(tr):
    wave = np.linspace(0, 1, 1000).astype(np.float32)
    out = tr._resample_audio(wave, 16000, 16000)
    assert out is wave


def test_resample_downsamples_length(tr):
    out = tr._resample_audio(np.zeros(48000, dtype=np.float32), 48000, 16000)
    assert len(out) == 16000


def test_resample_upsamples_length(tr):
    out = tr._resample_audio(np.zeros(8000, dtype=np.float32), 8000, 16000)
    assert len(out) == 16000


def test_preprocess_boosts_quiet_signal(tr):
    quiet = (np.sin(2 * np.pi * 200 * np.linspace(0, 1, 16000, endpoint=False)) * 0.1).astype(np.float32)
    out = tr._preprocess_audio(quiet, 16000)
    assert out.dtype == np.float32
    peak = float(np.abs(out).max())
    # Quiet 0.1-amplitude input is normalized up toward ~0.89.
    assert 0.5 < peak < 1.3


def test_preprocess_handles_silence(tr):
    out = tr._preprocess_audio(np.zeros(16000, dtype=np.float32), 16000)
    assert out.dtype == np.float32
    assert np.all(out == 0.0)


def test_encode_waveform_wav_round_trips(tr):
    """A (batch=1, N) model waveform encodes to a readable N-sample WAV."""
    import torch

    samples = 1600
    wav = torch.zeros((1, samples), dtype=torch.float16)
    data = tr._encode_waveform_wav(wav, 16000)
    arr, sr = sf.read(io.BytesIO(data))
    assert sr == 16000
    assert arr.shape[0] == samples


def test_encode_waveform_wav_preserves_signal(tr):
    """The encoded WAV recovers the input signal (within 16-bit PCM quantization)."""
    import torch

    signal_in = np.sin(
        2 * np.pi * 440 * np.linspace(0, 0.1, 1600, endpoint=False)
    ).astype(np.float32)
    wav = torch.from_numpy(signal_in).unsqueeze(0)  # shape (1, 1600)
    data = tr._encode_waveform_wav(wav, 16000)
    arr, _ = sf.read(io.BytesIO(data))
    assert arr.shape[0] == signal_in.shape[0]
    # Default WAV subtype is 16-bit PCM, so allow for quantization error.
    assert np.allclose(arr, signal_in, atol=1e-3)


def test_encode_waveform_wav_handles_single_sample(tr):
    """A 1-sample clip squeezes to a 0-d scalar; atleast_1d keeps it writable."""
    import torch

    wav = torch.tensor([[0.5]], dtype=torch.float32)  # (1, 1) -> squeeze -> scalar
    data = tr._encode_waveform_wav(wav, 16000)
    arr, sr = sf.read(io.BytesIO(data))
    assert sr == 16000
    assert np.atleast_1d(arr).shape[0] == 1


def test_get_translator_is_singleton():
    assert get_translator() is get_translator()
