from __future__ import annotations

import unittest

from m01_bridge.device import M01Device
from m01_bridge.protocol import QUICK_MEASURE, READ_RESULT, build_measurement_frame


class FakeSerial:
    def __init__(self, *, respond_to_quick: bool) -> None:
        self.respond_to_quick = respond_to_quick
        self.rx = bytearray()
        self.writes: list[bytes] = []
        self.closed = False

    @property
    def in_waiting(self) -> int:
        return len(self.rx)

    def write(self, data: bytes) -> int:
        payload = bytes(data)
        self.writes.append(payload)
        if payload == QUICK_MEASURE and self.respond_to_quick:
            self.rx.extend(build_measurement_frame(1500))
        elif payload == READ_RESULT:
            self.rx.extend(build_measurement_frame(1750))
        return len(payload)

    def flush(self) -> None:
        pass

    def read(self, size: int) -> bytes:
        if not self.rx:
            return b""
        chunk = bytes(self.rx[:size])
        del self.rx[:size]
        return chunk

    def reset_input_buffer(self) -> None:
        self.rx.clear()

    def close(self) -> None:
        self.closed = True


class DeviceTests(unittest.TestCase):
    def test_quick_measurement_path(self) -> None:
        fake = FakeSerial(respond_to_quick=True)
        device = M01Device(serial_factory=lambda **_: fake)
        try:
            measurement = device.measure_once(timeout=0.01, fallback_timeout=0.01)
        finally:
            device.close()

        self.assertEqual(measurement.millimeters, 1500)
        self.assertEqual(fake.writes, [QUICK_MEASURE])
        self.assertTrue(fake.closed)

    def test_read_result_fallback(self) -> None:
        fake = FakeSerial(respond_to_quick=False)
        device = M01Device(serial_factory=lambda **_: fake)
        try:
            measurement = device.measure_once(timeout=0.001, fallback_timeout=0.01)
        finally:
            device.close()

        self.assertEqual(measurement.millimeters, 1750)
        self.assertEqual(fake.writes, [QUICK_MEASURE, READ_RESULT])


if __name__ == "__main__":
    unittest.main()
