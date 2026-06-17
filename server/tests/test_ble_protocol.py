"""Tests for the BLE wire protocol framing (pure, no CoreBluetooth needed)."""

import struct

import pytest

import ble_protocol as ble


def _payloads():
    return [
        b"",
        b"\x00",
        b"hello world",
        bytes(range(256)),
        bytes(range(256)) * 4,                  # 1024 bytes
        b"\xab" * ble.CHUNK_SIZE,               # exactly one full chunk
        b"\xcd" * (ble.CHUNK_SIZE + 1),         # one full chunk + 1 byte
        b"\x01" * (ble.CHUNK_SIZE * 3 + 7),     # several chunks + remainder
    ]


@pytest.mark.parametrize("payload", _payloads())
def test_round_trip(payload):
    """frame_audio followed by reassemble must recover the original bytes."""
    frames = list(ble.frame_audio(payload))
    assert ble.reassemble(frames) == payload


@pytest.mark.parametrize("payload", _payloads())
def test_frame_structure(payload):
    frames = list(ble.frame_audio(payload))

    # First frame is START with a big-endian length; last frame is END.
    assert frames[0] == struct.pack(">BI", ble.CMD_AUDIO_START, len(payload))
    assert frames[-1] == bytes([ble.CMD_AUDIO_END])

    chunks = frames[1:-1]
    assert len(chunks) == ble.chunk_count(len(payload))
    for chunk in chunks:
        assert chunk[0] == ble.CMD_AUDIO_CHUNK
        # 1 command byte + between 1 and CHUNK_SIZE payload bytes.
        assert 2 <= len(chunk) <= ble.CHUNK_SIZE + 1

    # Concatenated chunk payloads equal the original.
    assert b"".join(chunk[1:] for chunk in chunks) == payload


def test_chunk_count():
    assert ble.chunk_count(0) == 0
    assert ble.chunk_count(1) == 1
    assert ble.chunk_count(ble.CHUNK_SIZE) == 1
    assert ble.chunk_count(ble.CHUNK_SIZE + 1) == 2
    assert ble.chunk_count(ble.CHUNK_SIZE * 5) == 5


def test_encoders_exact_bytes():
    assert ble.encode_start(0x01020304) == bytes([ble.CMD_AUDIO_START, 1, 2, 3, 4])
    assert ble.encode_chunk(b"abc") == bytes([ble.CMD_AUDIO_CHUNK]) + b"abc"
    assert ble.encode_end() == bytes([ble.CMD_AUDIO_END])


def test_reassemble_ignores_empty_frames():
    frames = [ble.encode_start(3), b"", ble.encode_chunk(b"abc"), b"", ble.encode_end()]
    assert ble.reassemble(frames) == b"abc"


def test_reassemble_stops_at_end():
    frames = [
        ble.encode_start(3),
        ble.encode_chunk(b"abc"),
        ble.encode_end(),
        ble.encode_chunk(b"ignored after END"),
    ]
    assert ble.reassemble(frames) == b"abc"


def test_reassemble_rejects_short_start():
    with pytest.raises(ValueError):
        ble.reassemble([bytes([ble.CMD_AUDIO_START, 0, 0])])


def test_reassemble_rejects_length_mismatch():
    frames = [ble.encode_start(99), ble.encode_chunk(b"abc"), ble.encode_end()]
    with pytest.raises(ValueError):
        ble.reassemble(frames)


def test_reassemble_rejects_unknown_command():
    with pytest.raises(ValueError):
        ble.reassemble([bytes([0x77, 0x00])])


def test_frame_audio_rejects_bad_chunk_size():
    with pytest.raises(ValueError):
        list(ble.frame_audio(b"abc", chunk_size=0))
