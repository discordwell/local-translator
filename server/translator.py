"""
SeamlessM4T wrapper for Japanese ↔ English translation.

Provides:
- translate_ja_to_en(audio) -> English text
- translate_en_to_ja(audio) -> Japanese audio bytes
"""

import io
from typing import Optional

import torch
import numpy as np
import soundfile as sf
from scipy import signal
from transformers import AutoProcessor, SeamlessM4Tv2Model


class AudioDecodeError(ValueError):
    """Raised when the supplied audio bytes can't be decoded into a waveform.

    This is a *client* error (bad/unsupported/empty audio), distinct from an
    internal failure during inference. Callers map it to an HTTP 400.
    """


class Translator:
    """Wrapper for SeamlessM4T model with MPS acceleration."""

    def __init__(self, model_name: str = "facebook/seamless-m4t-v2-large"):
        self.model_name = model_name
        self.model = None
        self.processor = None
        self.device = None
        self._loaded = False

    def load(self):
        """Load the model and processor. Call once at startup."""
        if self._loaded:
            return

        print(f"Loading SeamlessM4T model: {self.model_name}")

        # Determine device - prefer MPS (Metal) on Apple Silicon
        if torch.backends.mps.is_available():
            self.device = torch.device("mps")
            print("Using MPS (Metal) backend for M4 acceleration")
        elif torch.cuda.is_available():
            self.device = torch.device("cuda")
            print("Using CUDA backend")
        else:
            self.device = torch.device("cpu")
            print("Using CPU backend")

        # Load processor and model
        self.processor = AutoProcessor.from_pretrained(self.model_name)

        # Use float16 for faster inference on GPU (MPS/CUDA)
        dtype = torch.float16 if self.device.type in ("mps", "cuda") else torch.float32
        print(f"Loading model with dtype: {dtype}")

        self.model = SeamlessM4Tv2Model.from_pretrained(
            self.model_name,
            torch_dtype=dtype
        )
        self.model = self.model.to(self.device)
        self.model.eval()

        self._loaded = True
        print("Model loaded successfully")

        # Warmup: run dummy inference to pre-compile MPS/CUDA kernels
        print("Warming up model...")
        self._warmup()
        print("Warmup complete")

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def device_name(self) -> Optional[str]:
        """Active torch device type ('mps' / 'cuda' / 'cpu'), or None before load.

        Surfaced by ``/health`` so a client (or a curl) can confirm which backend
        is in use — the GPU-vs-CPU distinction is the main performance lever here.
        """
        return self.device.type if self.device is not None else None

    def _clear_cache(self):
        """Clear GPU memory cache to prevent accumulation."""
        if self.device.type == "mps":
            torch.mps.empty_cache()
        elif self.device.type == "cuda":
            torch.cuda.empty_cache()

    def _warmup(self):
        """Run dummy inference to pre-compile kernels."""
        dummy_audio = np.zeros(16000, dtype=np.float32)  # 1 second of silence
        inputs = self.processor(
            audio=dummy_audio,
            sampling_rate=16000,
            return_tensors="pt"
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with torch.inference_mode():
            _ = self.model.generate(**inputs, tgt_lang="eng", generate_speech=False)
        del inputs
        self._clear_cache()

    def _load_audio(self, audio_bytes: bytes) -> tuple[np.ndarray, int]:
        """Load audio from bytes and return (waveform, sample_rate).

        Raises ``AudioDecodeError`` if the bytes aren't a decodable audio file
        or contain no samples, so callers can distinguish bad input (HTTP 400)
        from an internal inference failure (HTTP 500).
        """
        try:
            waveform, sample_rate = sf.read(io.BytesIO(audio_bytes))
        except Exception as e:
            # soundfile raises LibsndfileError (a RuntimeError) whose message
            # leaks the BytesIO repr; surface a clean, client-safe message.
            raise AudioDecodeError(
                "Could not decode audio; expected a valid WAV file"
            ) from e

        # Convert stereo to mono if needed
        if waveform.ndim > 1:
            waveform = waveform.mean(axis=1)

        if waveform.size == 0:
            raise AudioDecodeError("Audio contains no samples")

        return waveform, sample_rate

    def _resample_audio(self, waveform: np.ndarray, orig_sr: int, target_sr: int = 16000) -> np.ndarray:
        """Resample audio to target sample rate."""
        if orig_sr == target_sr:
            return waveform

        # Calculate resampling ratio
        num_samples = int(len(waveform) * target_sr / orig_sr)
        resampled = signal.resample(waveform, num_samples)
        return resampled

    def _preprocess_audio(self, waveform: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        """Optional speech cleanup (peak-normalize + 80 Hz high-pass).

        Not currently applied in the inference path: SeamlessM4T's processor
        already normalizes its inputs, so this is kept as an opt-in helper for
        experimenting with noisy-audio handling rather than wired in by default.
        """
        # Normalize audio level (peak normalization to -1dB)
        max_val = np.abs(waveform).max()
        if max_val > 0:
            waveform = (waveform / max_val) * 0.89

        # High-pass filter to remove low-frequency noise (80 Hz cutoff)
        sos = signal.butter(N=5, Wn=80, btype='high', fs=sample_rate, output='sos')
        waveform = signal.sosfilt(sos, waveform)

        return waveform.astype(np.float32)

    def _encode_waveform_wav(self, waveform, sample_rate: int) -> bytes:
        """Encode a model-output waveform tensor to WAV bytes.

        ``output.waveform`` is a torch tensor of shape ``(batch, samples)``; we
        always generate with batch size 1, so squeezing yields the 1-D signal.
        ``np.atleast_1d`` guards the degenerate single-sample clip, where
        ``squeeze`` would otherwise collapse to a 0-d scalar that ``soundfile``
        cannot write. The tensor is cast to float32 first because ``soundfile``
        cannot consume the model's float16 GPU output; ``soundfile`` then writes
        its default 16-bit PCM WAV, which is what the iOS client plays.
        """
        audio_array = np.atleast_1d(waveform.cpu().float().numpy().squeeze())
        buf = io.BytesIO()
        sf.write(buf, audio_array, sample_rate, format="WAV")
        return buf.getvalue()

    def translate_ja_to_en(self, audio_bytes: bytes) -> str:
        """
        Translate Japanese speech to English text.

        Args:
            audio_bytes: WAV audio data containing Japanese speech

        Returns:
            English text translation
        """
        if not self._loaded:
            raise RuntimeError("Model not loaded. Call load() first.")

        # Load and resample audio
        waveform, sample_rate = self._load_audio(audio_bytes)
        waveform = self._resample_audio(waveform, sample_rate, 16000)

        # Process audio input
        inputs = self.processor(
            audio=waveform,
            sampling_rate=16000,
            return_tensors="pt"
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        try:
            # Generate text translation (speech-to-text)
            with torch.inference_mode():
                output_tokens = self.model.generate(
                    **inputs,
                    tgt_lang="eng",
                    generate_speech=False,
                )

            # With generate_speech=False the model returns a ModelOutput whose
            # first field is `sequences` (shape [batch, seq_len]); decode the
            # single batch row. (output_tokens[0][0] is equivalent but relies on
            # ModelOutput's positional indexing.)
            text = self.processor.decode(
                output_tokens.sequences[0],
                skip_special_tokens=True
            )

            return text
        finally:
            # Clean up tensors to prevent memory leak
            del inputs
            if 'output_tokens' in locals():
                del output_tokens
            self._clear_cache()

    def translate_en_to_ja(self, audio_bytes: bytes) -> tuple[bytes, str]:
        """
        Translate English speech to Japanese speech.

        Args:
            audio_bytes: WAV audio data containing English speech

        Returns:
            Tuple of (WAV audio data containing Japanese speech, Japanese text)
        """
        if not self._loaded:
            raise RuntimeError("Model not loaded. Call load() first.")

        # Load and resample audio
        waveform, sample_rate = self._load_audio(audio_bytes)
        waveform = self._resample_audio(waveform, sample_rate, 16000)

        # Process audio input
        inputs = self.processor(
            audio=waveform,
            sampling_rate=16000,
            return_tensors="pt"
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        try:
            # Generate Japanese speech
            with torch.inference_mode():
                output = self.model.generate(
                    **inputs,
                    tgt_lang="jpn",
                    generate_speech=True,
                    return_intermediate_token_ids=True,
                )

            # Extract the intermediate Japanese text if available
            japanese_text = ""
            if output.sequences is not None:
                try:
                    japanese_text = self.processor.decode(output.sequences[0], skip_special_tokens=True)
                except Exception:
                    pass

            # Convert the synthesized waveform (16 kHz) to WAV bytes.
            audio_data = self._encode_waveform_wav(
                output.waveform, self.model.config.sampling_rate
            )

            return audio_data, japanese_text
        finally:
            # Clean up tensors to prevent memory leak
            del inputs
            if 'output' in locals():
                del output
            self._clear_cache()


# Global translator instance
_translator = None


def get_translator() -> Translator:
    """Get the global translator instance."""
    global _translator
    if _translator is None:
        _translator = Translator()
    return _translator
