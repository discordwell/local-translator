# Local Translator

Offline Japanese-English speech translation powered by Meta's SeamlessM4T model. Runs entirely on your local network with no cloud dependencies.

## Features

- **Japanese → English**: Speak Japanese, get English text
- **English → Japanese**: Speak English, hear Japanese audio with text display
- **Fully offline**: No internet required after initial model download
- **Two connection modes**: WiFi (with Bonjour discovery) or Bluetooth LE
- **GPU accelerated**: Uses Metal Performance Shaders on Apple Silicon

## Architecture

```
┌─────────────┐     WiFi/BLE      ┌─────────────────┐
│   iPhone    │ ←───────────────→ │   Mac Server    │
│   iOS App   │    Audio/Text     │  SeamlessM4T    │
└─────────────┘                   └─────────────────┘
```

The Mac runs a Python server with the SeamlessM4T v2 Large model. The iOS app records audio, sends it to the server, and displays/plays the translation results.

## Requirements

### Server (Mac)

- macOS 12+
- Python 3.9+
- Apple Silicon (M1/M2/M3) recommended for GPU acceleration
- 16GB+ RAM
- ~14GB disk space (model downloads on first run)

### iOS App

- iOS 16+
- Xcode 15+ (for building from source)

## Setup

### Server

```bash
cd server
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Start in WiFi mode (HTTP API with Bonjour discovery):
```bash
python main.py
```

Start in Bluetooth mode (no WiFi needed):
```bash
python main.py --bluetooth
```

The first run downloads the SeamlessM4T model (~14GB) which takes several minutes.

### iOS App

```bash
cd ios-app
open JapanTranslator.xcodeproj
```

Build and run on your iOS device from Xcode.

## Usage

1. Start the server on your Mac
2. Launch the iOS app
3. Wait for "Connected" status (green indicator)
4. **Japanese → English**: Hold the 日本語 button, speak Japanese, release
5. **English → Japanese**: Hold the English button, speak English, release
6. View translation results; use Replay button for audio playback

### Connection Modes

Toggle between WiFi and Bluetooth in the app's connection picker:

- **WiFi**: Uses Bonjour to auto-discover the server on your local network
- **Bluetooth**: Direct connection, no WiFi required

## Performance

| Hardware | Translation Time |
|----------|-----------------|
| Apple Silicon (M1/M2/M3) | ~2-3 seconds |
| Intel Mac | ~5-10 seconds |

Model loads once at startup (~30 seconds on Apple Silicon).

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Not connected" | Ensure server is running and on same network |
| Bluetooth won't connect | Start server with `--bluetooth` flag |
| Slow translations | Intel Macs use CPU; Apple Silicon uses GPU |
| No audio playback | Check iPhone volume and speaker settings |
| Model download fails | Ensure internet connection for first run |

## Technical Details

- **Model**: SeamlessM4T v2 Large (facebook/seamless-m4t-v2-large)
- **Audio format**: 16kHz mono PCM WAV
- **Bluetooth**: BLE with 182-byte chunked notifications
- **WiFi**: FastAPI server with multipart form upload

## License

MIT
