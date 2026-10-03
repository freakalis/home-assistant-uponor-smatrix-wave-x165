"""L44 regression vectors from our CRC-valid I-167 captures; no RF transmit."""

from datetime import datetime, timezone
import unittest
from unittest.mock import Mock, patch

import numpy as np

from uponor_smatrix_wave_x165.crc import crc16_cms, crc16_modbus
from uponor_smatrix_wave_x165.interface import build_remote_setpoint, parse_remote_setpoint
from uponor_smatrix_wave_x165.live import LiveBurst
from uponor_smatrix_wave_x165.protocol import FrameError
from uponor_smatrix_wave_x165.receiver import present_decoded
from uponor_smatrix_wave_x165.state import DeviceRegistry


INTERFACE_ID = bytes.fromhex("14 FF 37 33")

# Reference frame from the original lab parser's remote-setpoint tests.
REFERENCE = bytes.fromhex(
    "AA AA AA AA D3 91 D3 91 21 14 FF 37 33 01 17 00 3D 00 0B 00 35 00 "
    "08 10 88 00 05 64 01 9A 03 B6 02 A8 03 14 02 C3 00 12 29 25 4E 9C"
)

# Lab capture bypass-kitchen-01-frames.jsonl (byte 25 = 01).
VARIABLE_BYTE_25 = bytes.fromhex(
    "AA AA AA AA D3 91 D3 91 21 14 FF 37 33 01 17 00 91 00 0B 00 89 00 "
    "08 10 88 01 05 64 01 9A 03 B6 02 A8 03 14 02 9F 00 12 09 6A A3 F9"
)

# SDR capture i167-kitchen-sdr-20261002-212810 (bytes 38-39 = 0036).
VARIABLE_FIELD_38_39 = bytes.fromhex(
    "AA AA AA AA D3 91 D3 91 21 14 FF 37 33 01 17 00 52 00 0B 00 4A 00 "
    "08 10 88 00 05 64 01 9A 03 B6 02 A8 03 14 02 A8 00 36 9D 5B 04 AB"
)


class RemoteSetpointRegressionTests(unittest.TestCase):
    def test_real_frames_with_variable_fields(self):
        for raw, room, byte_25, field_38_39 in (
            (REFERENCE, 0x3D, 0, 0x0012),
            (VARIABLE_BYTE_25, 0x91, 1, 0x0012),
            (VARIABLE_FIELD_38_39, 0x52, 0, 0x0036),
        ):
            with self.subTest(room=room):
                frame = parse_remote_setpoint(raw, interface_id=INTERFACE_ID)
                self.assertEqual(frame.room_primary, room)
                self.assertTrue(frame.remote_enabled)
                self.assertEqual(frame.unknown_byte_25, byte_25)
                self.assertEqual(frame.unknown_field_38_39, field_38_39)
                self.assertEqual(frame.min_setpoint_raw, 0x019A)
                self.assertEqual(frame.max_setpoint_raw, 0x03B6)

    def test_reference_builder_output_is_unchanged(self):
        self.assertEqual(
            build_remote_setpoint(
                interface_id=INTERFACE_ID, room_primary=0x3D,
                room_secondary=0x35, remote_enabled=True, raw_setpoint=0x02C3,
            ),
            REFERENCE,
        )

    def test_bad_outer_crc_still_rejected(self):
        bad = bytearray(VARIABLE_BYTE_25)
        bad[25] ^= 1
        with self.assertRaisesRegex(FrameError, "CRC mismatch"):
            parse_remote_setpoint(bytes(bad), interface_id=INTERFACE_ID)

    def test_bad_inner_crc_still_rejected(self):
        bad = bytearray(VARIABLE_BYTE_25)
        bad[40] ^= 1
        bad[-2:] = crc16_cms(bad[8:-2]).to_bytes(2, "big")
        with self.assertRaisesRegex(FrameError, "inner CRC mismatch"):
            parse_remote_setpoint(bytes(bad), interface_id=INTERFACE_ID)

    def test_wrong_interface_and_unchanged_structure_checks(self):
        with self.assertRaises(FrameError):
            parse_remote_setpoint(REFERENCE, interface_id=bytes(4))
        bad = bytearray(REFERENCE)
        bad[27] ^= 1  # A byte that remains fixed across our validated captures.
        bad[-4:-2] = crc16_modbus(bad[9:-4]).to_bytes(2, "little")
        bad[-2:] = crc16_cms(bad[8:-2]).to_bytes(2, "big")
        with self.assertRaisesRegex(FrameError, "unexpected remote-setpoint frame structure"):
            parse_remote_setpoint(bytes(bad), interface_id=INTERFACE_ID)

    def test_live_receiver_logs_valid_l44_without_publishing(self):
        observed_at = datetime(2026, 10, 2, 21, 28, 34, tzinfo=timezone.utc)
        burst = LiveBurst(250, 500, np.zeros(1), 1, 0)
        bridge = Mock()
        journal = Mock()
        registry = DeviceRegistry()
        with patch("builtins.print") as output:
            result = present_decoded(
                burst, ({"packet": VARIABLE_BYTE_25}, None, "unsupported thermostat frame length 44"),
                sample_rate=250_000, capture_start=observed_at, registry=registry,
                debug=False, mqtt_bridge=bridge, frame_journal=journal,
                interface_id=INTERFACE_ID,
            )
        self.assertEqual(result, (True, False))
        self.assertEqual(registry.devices, {})
        self.assertEqual(bridge.mock_calls, [])
        journal.observe.assert_called_once()
        line = output.call_args.args[0]
        self.assertIn("2026-10-02T21:28:34.001+00:00", line)
        self.assertIn("room_primary=0x91", line)
        self.assertIn("remote=on", line)
        self.assertIn("setpoint=19.5 C", line)
        self.assertIn("not published", line)

    def test_live_receiver_rejects_wrong_interface_and_bad_crc(self):
        burst = LiveBurst(0, 1, np.zeros(1), 1, 0)
        bridge = Mock()
        bad_crc = bytearray(VARIABLE_BYTE_25)
        bad_crc[25] ^= 1
        for raw, selected_id in (
            (VARIABLE_BYTE_25, bytes(4)),
            (bytes(bad_crc), INTERFACE_ID),
        ):
            with self.subTest(selected_id=selected_id, size=len(raw)), patch("builtins.print") as output:
                result = present_decoded(
                    burst, ({"packet": raw}, None, "unsupported thermostat frame"),
                    sample_rate=250_000, capture_start=datetime.now(timezone.utc),
                    registry=DeviceRegistry(), debug=False, mqtt_bridge=bridge,
                    interface_id=selected_id,
                )
                self.assertEqual(result, (True, False))
                output.assert_not_called()
        self.assertEqual(bridge.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
