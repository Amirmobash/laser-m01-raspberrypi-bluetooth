from __future__ import annotations

import unittest

from m01_bridge.protocol import (
    FrameParser,
    LASER_OFF,
    LASER_ON,
    QUICK_MEASURE,
    READ_RESULT,
    ProtocolError,
    build_measurement_frame,
    checksum_ok,
    parse_measurement_frame,
)


class ProtocolTests(unittest.TestCase):
    def test_reference_commands_have_valid_checksum(self) -> None:
        for command in (LASER_ON, LASER_OFF, QUICK_MEASURE, READ_RESULT):
            self.assertTrue(checksum_ok(command), command.hex(" "))

    def test_measurement_frame_decodes_bcd_millimeters(self) -> None:
        frame = build_measurement_frame(1234)
        measurement = parse_measurement_frame(frame)
        self.assertAlmostEqual(measurement.meters, 1.234)
        self.assertEqual(measurement.millimeters, 1234)
        self.assertEqual(measurement.function, 0x22)

    def test_checksum_error_is_rejected(self) -> None:
        frame = bytearray(build_measurement_frame(2500))
        frame[-1] ^= 0x01
        with self.assertRaises(ProtocolError):
            parse_measurement_frame(bytes(frame))

    def test_invalid_bcd_is_rejected(self) -> None:
        frame = bytearray(build_measurement_frame(2500))
        frame[6] = 0xFA
        frame[-1] = sum(frame[1:-1]) & 0xFF
        with self.assertRaises(ProtocolError):
            parse_measurement_frame(bytes(frame))

    def test_incremental_parser_handles_noise_and_split_frames(self) -> None:
        parser = FrameParser()
        frame = build_measurement_frame(9876)

        self.assertEqual(parser.feed(b"noise\x01\x02" + frame[:5]), [])
        result = parser.feed(frame[5:])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].millimeters, 9876)

    def test_parser_resynchronizes_after_corrupt_frame(self) -> None:
        parser = FrameParser()
        bad = bytearray(build_measurement_frame(1000))
        bad[-1] ^= 0xFF
        good = build_measurement_frame(4321)

        result = parser.feed(bytes(bad) + good)
        self.assertEqual([m.millimeters for m in result], [4321])


if __name__ == "__main__":
    unittest.main()
