"""
Local Translator Server - FastAPI application.

Provides HTTP endpoints for Japanese ↔ English translation using SeamlessM4T.
Supports both WiFi (Bonjour) and Bluetooth LE connections.
"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from translator import get_translator, AudioDecodeError


# Server configuration
PORT = int(os.environ.get("PORT", 8000))
USE_BLUETOOTH = os.environ.get("USE_BLUETOOTH", "0") == "1"


# Serializes model inference so concurrent requests never run two generate()
# calls against the single shared model/GPU at once. Created lazily on first use
# (inside the running event loop) rather than at import time, so it binds to the
# server's loop — important on Python 3.9 where asyncio.Lock binds at creation.
_inference_lock = None


def _get_inference_lock() -> asyncio.Lock:
    global _inference_lock
    if _inference_lock is None:
        _inference_lock = asyncio.Lock()
    return _inference_lock


async def _run_inference(call):
    """Run a blocking translator call off the event loop, serialized.

    ``run_in_threadpool`` keeps the event loop responsive (e.g. /health) while
    the CPU/GPU-bound model runs; the lock ensures only one inference at a time.
    ``AudioDecodeError`` (bad input) maps to HTTP 400; anything else to 500.
    """
    try:
        async with _get_inference_lock():
            return await run_in_threadpool(call)
    except AudioDecodeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translation failed: {str(e)}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle - load model and start Bonjour on startup."""
    # Startup
    print("Starting Local Translator Server...")

    # Load the translation model
    translator = get_translator()
    translator.load()

    # Start Bonjour advertisement. Imported here (not at module load) so the app
    # — and the endpoint unit tests — don't require zeroconf just to import.
    from bonjour import get_bonjour_service

    bonjour = get_bonjour_service(PORT)
    bonjour.start()

    print(f"Server ready on port {PORT}")

    yield

    # Shutdown
    print("Shutting down...")
    bonjour.stop()


app = FastAPI(
    title="Local Translator Server",
    description="Japanese ↔ English translation API using SeamlessM4T",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Health check endpoint.

    ``device`` and ``model`` are advisory (older clients ignore unknown fields);
    they make it easy to confirm the server is on the GPU and which model it runs.
    """
    translator = get_translator()
    return {
        "status": "ok",
        "model_loaded": translator.is_loaded,
        "device": translator.device_name,
        "model": translator.model_name,
    }


@app.post("/translate/ja-to-en")
async def translate_japanese_to_english(audio: UploadFile = File(...)):
    """
    Translate Japanese speech to English text.

    Args:
        audio: WAV audio file containing Japanese speech

    Returns:
        JSON with English text translation
    """
    translator = get_translator()

    if not translator.is_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio upload")

    english_text = await _run_inference(
        lambda: translator.translate_ja_to_en(audio_bytes)
    )
    return {"text": english_text}


@app.post("/translate/en-to-ja")
async def translate_english_to_japanese(audio: UploadFile = File(...)):
    """
    Translate English speech to Japanese speech.

    Args:
        audio: WAV audio file containing English speech

    Returns:
        WAV audio file containing Japanese speech
    """
    translator = get_translator()

    if not translator.is_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio upload")

    # translate_en_to_ja returns both the synthesized audio and the
    # intermediate Japanese text.
    japanese_audio, japanese_text = await _run_inference(
        lambda: translator.translate_en_to_ja(audio_bytes)
    )

    return Response(
        content=japanese_audio,
        media_type="audio/wav",
        headers={
            "Content-Disposition": "attachment; filename=translation.wav",
            # HTTP headers must be latin-1 safe, so percent-encode the
            # UTF-8 Japanese text. Clients can opt in to display it.
            "X-Translation-Text": quote(japanese_text or ""),
        },
    )


def run_bluetooth_server():
    """Run the Bluetooth-only server (no WiFi needed)."""
    from bluetooth_server import get_bluetooth_server, CMD_JA_TO_EN, CMD_EN_TO_JA
    from Foundation import NSRunLoop, NSDate

    print("Starting Bluetooth-only mode...")
    print("Make sure Bluetooth is enabled on this Mac.")

    # Load translator
    translator = get_translator()
    translator.load()

    def on_audio_received(audio_data: bytes, command: int):
        """Handle received audio from iPhone."""
        print(f"Received {len(audio_data)} bytes, command: {command}")

        try:
            if command == CMD_JA_TO_EN:
                # Japanese -> English text
                text = translator.translate_ja_to_en(audio_data)
                print(f"Translation: {text}")
                bt_server.sendTextResponse_(text)
            elif command == CMD_EN_TO_JA:
                # English -> Japanese audio + text
                audio, japanese_text = translator.translate_en_to_ja(audio_data)
                print(f"Japanese text: {japanese_text}")
                print(f"Generated {len(audio)} bytes of Japanese audio")

                # Debug: save audio to file (commented out)
                # debug_path = "/tmp/translation_audio.wav"
                # with open(debug_path, "wb") as f:
                #     f.write(audio)
                # print(f"Saved audio to {debug_path}")

                # Send text first
                bt_server.sendTextResponse_(japanese_text)

                # Schedule audio send via timer - lets runloop process BLE events properly
                bt_server.scheduleAudioSend_(audio)
        except Exception as e:
            print(f"Translation error: {e}")
            bt_server.sendTextResponse_(f"Error: {str(e)}")

    # Start Bluetooth server
    bt_server = get_bluetooth_server(on_audio_received)
    bt_server.start()

    print("\nBluetooth server running. Press Ctrl+C to stop.")
    print("On your iPhone, open the Local Translator app.")
    print("It will connect automatically via Bluetooth.\n")

    # Run the macOS event loop (required for CoreBluetooth)
    try:
        while True:
            NSRunLoop.currentRunLoop().runUntilDate_(
                NSDate.dateWithTimeIntervalSinceNow_(0.1)
            )
    except KeyboardInterrupt:
        print("\nStopping...")
        bt_server.stop()


if __name__ == "__main__":
    if USE_BLUETOOTH or "--bluetooth" in sys.argv:
        run_bluetooth_server()
    else:
        import uvicorn

        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=PORT,
            reload=False,  # Disable reload for production
        )
