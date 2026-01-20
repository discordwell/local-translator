"""
SeamlessM4T wrapper for Japanese ↔ English translation.

Provides:
- translate_ja_to_en(audio) -> English text
- translate_en_to_ja(audio) -> Japanese audio bytes
"""

import io
import torch
import numpy as np
import soundfile as sf
from scipy import signal
from transformers import AutoProcessor, SeamlessM4Tv2Model


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
        self.model = SeamlessM4Tv2Model.from_pretrained(self.model_name)
        self.model = self.model.to(self.device)
        self.model.eval()

        self._loaded = True
        print("Model loaded successfully")

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def _load_audio(self, audio_bytes: bytes) -> tuple[np.ndarray, int]:
        """Load audio from bytes and return (waveform, sample_rate)."""
        audio_io = io.BytesIO(audio_bytes)
        waveform, sample_rate = sf.read(audio_io)

        # Convert stereo to mono if needed
        if len(waveform.shape) > 1:
            waveform = waveform.mean(axis=1)

        return waveform, sample_rate

    def _resample_audio(self, waveform: np.ndarray, orig_sr: int, target_sr: int = 16000) -> np.ndarray:
        """Resample audio to target sample rate."""
        if orig_sr == target_sr:
            return waveform

        # Calculate resampling ratio
        num_samples = int(len(waveform) * target_sr / orig_sr)
        resampled = signal.resample(waveform, num_samples)
        return resampled

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

        # Load and preprocess audio
        waveform, sample_rate = self._load_audio(audio_bytes)
        waveform = self._resample_audio(waveform, sample_rate, 16000)

        # Process audio input
        inputs = self.processor(
            audios=waveform,
            sampling_rate=16000,
            return_tensors="pt"
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        # Generate text translation (speech-to-text)
        with torch.no_grad():
            output_tokens = self.model.generate(
                **inputs,
                tgt_lang="eng",
                generate_speech=False,
            )

        # Decode tokens to text
        text = self.processor.decode(
            output_tokens[0].tolist()[0],
            skip_special_tokens=True
        )

        return text

    def translate_en_to_ja(self, audio_bytes: bytes) -> bytes:
        """
        Translate English speech to Japanese speech.

        Args:
            audio_bytes: WAV audio data containing English speech

        Returns:
            WAV audio data containing Japanese speech
        """
        if not self._loaded:
            raise RuntimeError("Model not loaded. Call load() first.")

        # Load and preprocess audio
        waveform, sample_rate = self._load_audio(audio_bytes)
        waveform = self._resample_audio(waveform, sample_rate, 16000)

        # Process audio input
        inputs = self.processor(
            audios=waveform,
            sampling_rate=16000,
            return_tensors="pt"
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        # Generate Japanese speech (speech-to-speech)
        with torch.no_grad():
            output = self.model.generate(
                **inputs,
                tgt_lang="jpn",
                generate_speech=True,
            )

        # Extract audio waveform from output
        # output[1] contains the audio waveform for speech generation
        audio_array = output[1].cpu().numpy().squeeze()

        # The model outputs at 16kHz sample rate
        output_sample_rate = 16000

        # Convert to WAV bytes
        audio_io = io.BytesIO()
        sf.write(audio_io, audio_array, output_sample_rate, format='WAV')
        audio_io.seek(0)

        return audio_io.read()


# Global translator instance
_translator = None


def get_translator() -> Translator:
    """Get the global translator instance."""
    global _translator
    if _translator is None:
        _translator = Translator()
    return _translator
