# Architecture

Local Translator is a two-part system: an **iOS app** (client) that records
speech, and a **Mac server** that runs Meta's SeamlessM4T v2 model and returns
the translation. Everything stays on the local network — there is no cloud
component after the one-time model download.

```
┌──────────────┐   WiFi (HTTP/Bonjour)  ┌──────────────────────────┐
│   iPhone     │ ◀────────── or ───────▶ │   Mac server (Python)    │
│   iOS app    │   Bluetooth LE (GATT)   │   SeamlessM4T v2 Large   │
└──────────────┘                         └──────────────────────────┘
```

The client can talk to the server over **either** transport; both expose the
same two translation operations.

## Translation operations

| Direction | Input            | Output                                   |
|-----------|------------------|------------------------------------------|
| JA → EN   | Japanese speech  | English **text**                         |
| EN → JA   | English speech   | Japanese **speech** + Japanese **text**  |

Audio is 16 kHz mono PCM WAV. The server resamples any other input rate.

## Server modules (`server/`)

| File                  | Responsibility |
|-----------------------|----------------|
| `main.py`             | Entry point. WiFi mode runs a FastAPI/uvicorn app; `--bluetooth` runs the BLE event loop. Routes requests to the translator. |
| `translator.py`       | `Translator` wraps SeamlessM4T v2: device selection (MPS/CUDA/CPU), load + warmup, audio decode/resample, and the two translate methods. Exposed as a process-wide singleton via `get_translator()`. |
| `bonjour.py`          | Advertises the WiFi server over mDNS as `_jptranslate._tcp.local.` so the app can auto-discover it. |
| `bluetooth_server.py` | CoreBluetooth peripheral. Implements the GATT service, receives audio writes, and pushes text/audio notifications back. macOS-only (pyobjc). |
| `ble_protocol.py`     | Pure-Python BLE wire contract: UUIDs, command bytes, and the audio framing/reassembly helpers. No CoreBluetooth import, so it is unit-testable anywhere. |

`main.py` imports `bluetooth_server` lazily (only in `--bluetooth` mode), so the
WiFi path never needs CoreBluetooth. Likewise `bonjour` is imported inside the
startup lifespan rather than at module load, so importing the app (e.g. for the
endpoint tests) doesn't require `zeroconf`.

## WiFi transport (HTTP)

FastAPI app served by uvicorn on port `8000` (override with `PORT`).

| Method & path             | Request                       | Response |
|---------------------------|-------------------------------|----------|
| `GET /health`             | —                             | `{"status": "ok", "model_loaded": <bool>, "device": <str\|null>, "model": <str>}` |
| `POST /translate/ja-to-en`| multipart `audio` (WAV)       | `{"text": "<english>"}` |
| `POST /translate/en-to-ja`| multipart `audio` (WAV)       | WAV body (`audio/wav`) + `X-Translation-Text` header |

`/health`'s `device` (`"mps"`/`"cuda"`/`"cpu"`, or `null` before the model
loads) and `model` fields are advisory — they make it easy to confirm the
server is on the GPU. Older clients ignore unknown JSON fields, so adding them
is backward-compatible.

The `X-Translation-Text` header carries the intermediate Japanese text. HTTP
headers are latin-1 only, so the UTF-8 text is **percent-encoded**; clients
percent-decode it. (`en-to-ja` returns both audio and text; the header is how
the WiFi transport delivers the text alongside the audio body.) The iOS WiFi
client (`TranslationService.translateEnglishToJapanese`) decodes this header and
displays the Japanese text, matching the Bluetooth path. Percent-encoding also
keeps any CR/LF in the translated text out of the raw header, so translated
content can't inject extra HTTP headers.

**Error responses** distinguish the caller's fault from the server's:

| Status | When |
|--------|------|
| `400`  | Empty upload, or audio that can't be decoded (`AudioDecodeError`) |
| `503`  | Model not loaded yet |
| `500`  | Anything else that fails during inference |

**Concurrency.** The two translate endpoints are `async def`, but inference is
CPU/GPU-bound and blocking, so each call is offloaded with
`run_in_threadpool` — this keeps the event loop responsive (e.g. `/health`
answers while a translation runs). A process-wide `asyncio.Lock` then
serializes inference so the single shared model/GPU is never hit by two
`generate()` calls at once. The lock is created lazily inside the running loop
(not at import) so it binds to the server's event loop.

Discovery: the app browses for `_jptranslate._tcp` via `NWBrowser`, resolves the
first instance, and uses its address/port. The service *type* is part of the
contract and must not change; the human-readable instance name is cosmetic.

## Bluetooth LE transport (GATT)

The Mac is the BLE **peripheral**; the iPhone is the **central**. One primary
service (`SERVICE_UUID`) exposes four characteristics:

| Characteristic       | Direction      | Properties | Purpose |
|----------------------|----------------|------------|---------|
| `AUDIO_INPUT`        | iPhone → Mac   | write      | Recorded speech (chunked writes) |
| `COMMAND`            | iPhone → Mac   | write      | 1-byte control commands |
| `TRANSLATION_OUTPUT` | Mac → iPhone   | notify     | UTF-8 translated text |
| `AUDIO_OUTPUT`       | Mac → iPhone   | notify     | Synthesized speech (framed) |

**Commands** (`COMMAND` characteristic): `CMD_JA_TO_EN` (0x01) and
`CMD_EN_TO_JA` (0x02) select the mode and reset the receive buffer;
`CMD_AUDIO_END` (0x12) signals that the uploaded clip is complete and triggers
translation.

**Audio framing** (`AUDIO_OUTPUT`, Mac → iPhone) — defined in `ble_protocol.py`,
since a single notification can't hold a whole clip:

```
START : CMD_AUDIO_START (0x10) + uint32 big-endian total length     (5 bytes)
CHUNK : CMD_AUDIO_CHUNK (0x11) + up to CHUNK_SIZE (182) payload bytes
  ...  (repeated)
END   : CMD_AUDIO_END   (0x12)                                      (1 byte)
```

The receiver reads the START length (informational), appends each CHUNK
payload, and finalizes on END. `frame_audio()` produces this sequence and
`reassemble()` inverts it; the two are exercised against each other in the
tests. BLE notifications are flow-controlled, so the server paces sends and
retries via `peripheralManagerIsReadyToUpdateSubscribers_`.

## Model & inference

- **Model:** `facebook/seamless-m4t-v2-large` (downloaded once, ~9 GB).
- **Device:** MPS on Apple Silicon, else CUDA, else CPU. GPU paths load the
  model in `float16`; CPU uses `float32`.
- **Warmup:** a dummy inference runs at startup to pre-compile kernels so the
  first real request isn't slow.
- GPU memory cache is cleared after each request to avoid accumulation.

`Translator._preprocess_audio` (peak-normalize + 80 Hz high-pass) exists as an
opt-in helper but is **not** wired into the inference path — SeamlessM4T's
processor already normalizes its inputs.

## Testing

Server tests live in `server/tests/` and run without loading the model:

- `test_ble_protocol.py` — framing/reassembly round-trips and edge cases.
- `test_translator_audio.py` — audio decode/resample/preprocess helpers,
  including `AudioDecodeError` on empty/garbage/zero-sample input.
- `test_api.py` — FastAPI endpoints with a fake translator (the `TestClient` is
  created without its context manager so the model-loading lifespan never runs):
  success paths, the 400/503/500 error taxonomy, the `/health` device/model
  fields, the `X-Translation-Text` round-trip / header-injection-safety contract,
  and a concurrency test (via `httpx.AsyncClient`) asserting inference is
  serialized to one call at a time.

```bash
cd server
pip install -r requirements.txt -r requirements-dev.txt
pytest
```
