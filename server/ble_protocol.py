"""
BLE wire protocol for Local Translator.

Pure-Python definitions and framing helpers shared by the Bluetooth peripheral
server. This module deliberately avoids importing CoreBluetooth/pyobjc so the
protocol can be unit-tested on any platform (and reasoned about in isolation).

GATT layout
-----------
A single primary service exposes four characteristics:

    AUDIO_INPUT         iPhone -> Mac   write    (recorded speech, chunked)
    TRANSLATION_OUTPUT  Mac -> iPhone   notify   (UTF-8 translated text)
    AUDIO_OUTPUT        Mac -> iPhone   notify   (synthesized speech, framed)
    COMMAND             iPhone -> Mac   write    (1-byte control commands)

Audio framing (AUDIO_OUTPUT, Mac -> iPhone)
-------------------------------------------
Audio is larger than a BLE notification, so it is sent as a sequence of frames,
each frame being one notification:

    START : >BI  -> CMD_AUDIO_START (1 byte) + total payload length (uint32 BE)
    CHUNK : B... -> CMD_AUDIO_CHUNK (1 byte) + up to CHUNK_SIZE payload bytes
    END   : B    -> CMD_AUDIO_END   (1 byte)

The iPhone reads the START length (informational), appends each CHUNK payload,
and finalizes on END. ``reassemble`` mirrors that decoder so the round-trip can
be exercised in tests.
"""

import struct
from typing import Iterable, Iterator

# Service / characteristic UUIDs (must match the iOS client).
SERVICE_UUID = "12345678-1234-1234-1234-123456789ABC"
# iPhone -> Mac: recorded speech for translation.
AUDIO_INPUT_UUID = "12345678-1234-1234-1234-123456789001"
# Mac -> iPhone: translated text (UTF-8).
TRANSLATION_OUTPUT_UUID = "12345678-1234-1234-1234-123456789002"
# Mac -> iPhone: synthesized speech (framed, see above).
AUDIO_OUTPUT_UUID = "12345678-1234-1234-1234-123456789003"
# iPhone -> Mac: control commands.
COMMAND_UUID = "12345678-1234-1234-1234-123456789004"

# Commands
CMD_JA_TO_EN = 0x01     # Japanese speech -> English text
CMD_EN_TO_JA = 0x02     # English speech -> Japanese speech
CMD_AUDIO_START = 0x10  # Start of audio data (followed by uint32 length)
CMD_AUDIO_CHUNK = 0x11  # Audio data chunk
CMD_AUDIO_END = 0x12    # End of audio data
CMD_TEST_AUDIO = 0xFF   # Test: send sample audio without needing voice input

# Payload bytes per CHUNK frame. Kept small to stay within the negotiated BLE
# notification MTU across devices (1 command byte + 182 payload = 183 bytes).
CHUNK_SIZE = 182


def encode_start(total_len: int) -> bytes:
    """Frame announcing an audio transfer of ``total_len`` payload bytes."""
    return struct.pack(">BI", CMD_AUDIO_START, total_len)


def encode_chunk(payload: bytes) -> bytes:
    """Frame carrying a single chunk of audio payload."""
    return bytes([CMD_AUDIO_CHUNK]) + bytes(payload)


def encode_end() -> bytes:
    """Frame marking the end of an audio transfer."""
    return bytes([CMD_AUDIO_END])


def chunk_count(total_len: int, chunk_size: int = CHUNK_SIZE) -> int:
    """Number of CHUNK frames needed for a payload of ``total_len`` bytes."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    return (total_len + chunk_size - 1) // chunk_size


def frame_audio(audio_data: bytes, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
    """Yield the BLE frames for ``audio_data`` in send order: START, CHUNK..., END."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    yield encode_start(len(audio_data))
    for i in range(0, len(audio_data), chunk_size):
        yield encode_chunk(audio_data[i:i + chunk_size])
    yield encode_end()


def reassemble(frames: Iterable[bytes]) -> bytes:
    """Rebuild the original payload from received frames (inverse of ``frame_audio``).

    Follows the same START/CHUNK/END logic as the iPhone-side decoder, but
    validates more strictly (the iPhone tolerates a short START frame and only
    logs a length mismatch) so tests catch framing regressions. Raises
    ``ValueError`` on a malformed frame or a START/payload length mismatch.
    """
    expected_len = None
    out = bytearray()
    for frame in frames:
        if not frame:
            continue
        cmd = frame[0]
        if cmd == CMD_AUDIO_START:
            if len(frame) != 5:
                raise ValueError("START frame must be exactly 5 bytes")
            (expected_len,) = struct.unpack(">I", frame[1:5])
            out = bytearray()
        elif cmd == CMD_AUDIO_CHUNK:
            out.extend(frame[1:])
        elif cmd == CMD_AUDIO_END:
            break
        else:
            raise ValueError(f"unknown frame command: {cmd:#04x}")
    if expected_len is not None and expected_len != len(out):
        raise ValueError(
            f"length mismatch: header says {expected_len}, reassembled {len(out)}"
        )
    return bytes(out)
