from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

FRAME_START = 0xAA
MEASUREMENT_FRAME_SIZE = 13
MEASUREMENT_FUNCTIONS = frozenset({0x20, 0x21, 0x22})

LASER_ON = bytes.fromhex("AA 00 01 BE 00 01 00 01 C1")
LASER_OFF = bytes.fromhex("AA 00 01 BE 00 01 00 00 C0")
QUICK_MEASURE = bytes.fromhex("AA 00 00 22 00 01 00 00 23")
READ_RESULT = bytes.fromhex("AA 80 00 22 A2")


class ProtocolError(ValueError):
    """Raised when a frame is structurally invalid."""


@dataclass(frozen=True, slots=True)
class Measurement:
    meters: float
    function: int
    raw: bytes

    @property
    def millimeters(self) -> int:
        return round(self.meters * 1000)


def checksum_ok(frame: bytes) -> bool:
    if len(frame) < 3:
        return False
    return (sum(frame[1:-1]) & 0xFF) == frame[-1]


def _decode_bcd(data: bytes) -> int:
    value = 0
    for byte in data:
        high = (byte >> 4) & 0x0F
        low = byte & 0x0F
        if high > 9 or low > 9:
            raise ProtocolError(f"invalid BCD byte 0x{byte:02X}")
        value = value * 100 + high * 10 + low
    return value


def parse_measurement_frame(frame: bytes) -> Measurement:
    if len(frame) != MEASUREMENT_FRAME_SIZE:
        raise ProtocolError(
            f"measurement frame must be {MEASUREMENT_FRAME_SIZE} bytes, got {len(frame)}"
        )
    if frame[0] != FRAME_START:
        raise ProtocolError("measurement frame does not start with 0xAA")
    if not checksum_ok(frame):
        raise ProtocolError("measurement frame checksum mismatch")
    if frame[3] not in MEASUREMENT_FUNCTIONS:
        raise ProtocolError(f"unexpected measurement function 0x{frame[3]:02X}")
    if frame[4] != 0x00 or frame[5] != 0x04:
        raise ProtocolError("unexpected measurement payload header")

    raw_distance = _decode_bcd(frame[6:10])
    return Measurement(
        meters=raw_distance / 1000.0,
        function=frame[3],
        raw=bytes(frame),
    )


class FrameParser:
    """Incremental parser that resynchronizes after noise or corrupt frames."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, data: bytes | bytearray | memoryview) -> list[Measurement]:
        self._buffer.extend(data)
        measurements: list[Measurement] = []

        while True:
            try:
                start = self._buffer.index(FRAME_START)
            except ValueError:
                self._buffer.clear()
                break

            if start:
                del self._buffer[:start]

            if len(self._buffer) < MEASUREMENT_FRAME_SIZE:
                break

            candidate = bytes(self._buffer[:MEASUREMENT_FRAME_SIZE])
            try:
                measurement = parse_measurement_frame(candidate)
            except ProtocolError:
                # Drop one byte and search for the next 0xAA. This is slower than
                # discarding the whole candidate but recovers if a valid frame
                # starts inside a corrupt one.
                del self._buffer[0]
                continue

            measurements.append(measurement)
            del self._buffer[:MEASUREMENT_FRAME_SIZE]

        return measurements


def build_measurement_frame(
    millimeters: int,
    *,
    function: int = 0x22,
    byte1: int = 0x80,
    byte2: int = 0x00,
    trailer: Iterable[int] = (0x00, 0x00),
) -> bytes:
    """Build a valid frame for tests and diagnostics.

    The real module emits BCD distance bytes. This helper intentionally lives in
    the protocol module because it is also useful when replaying captures.
    """

    if not 0 <= millimeters <= 99_999_999:
        raise ValueError("millimeters must fit in eight BCD digits")
    if function not in MEASUREMENT_FUNCTIONS:
        raise ValueError("unsupported measurement function")

    digits = f"{millimeters:08d}"
    bcd = bytes(int(digits[i : i + 2], 16) for i in range(0, 8, 2))
    tail = bytes(trailer)
    if len(tail) != 2:
        raise ValueError("trailer must contain exactly two bytes")

    frame = bytearray(
        [FRAME_START, byte1 & 0xFF, byte2 & 0xFF, function, 0x00, 0x04]
    )
    frame.extend(bcd)
    frame.extend(tail)
    frame.append(sum(frame[1:]) & 0xFF)
    return bytes(frame)
