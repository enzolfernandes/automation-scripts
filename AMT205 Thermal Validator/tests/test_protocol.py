from __future__ import annotations

import struct
import unittest

from amt205_validator.protocol import (
    CURRENT_SENSORS,
    DEVICE_IP,
    INTERNAL_SENSORS,
    VOLTAGE_SENSORS,
    PacketRecord,
    decode_fan_mode,
    decode_status,
    decode_vector_command,
)


def packet(payload: bytes, *, src: str = DEVICE_IP, src_port: int = 1001, dst: str = "192.168.1.69", dst_port: int = 58299) -> PacketRecord:
    return PacketRecord(1, 1_777_000_000.0, 3.5, src, src_port, dst, dst_port, payload)


class ProtocolTests(unittest.TestCase):
    def test_temperature_sensors_are_split_by_channel_type(self) -> None:
        self.assertEqual(VOLTAGE_SENSORS, ("V1 Board 1", "V2 Board 1", "V1 Board 2", "V2 Board 2"))
        self.assertEqual(CURRENT_SENSORS, ("I1 Board 1", "I2 Board 1", "I1 Board 2", "I2 Board 2"))
        self.assertEqual(INTERNAL_SENSORS, ("Main Board 1", "Main Board 2"))
        self.assertTrue(set(VOLTAGE_SENSORS).isdisjoint(CURRENT_SENSORS))

    def test_decodes_temperatures_execution_and_thermal_progress(self) -> None:
        payload = bytearray(1101)
        payload[:4] = b"\x04\x00\x00\x00"
        struct.pack_into("<II", payload, 4, 1, 6)
        offsets = (197, 201, 205, 209, 213, 217, 221, 225, 233, 241)
        for offset, value in zip(offsets, (297, 294, 338, 336, 328, 331, 311, 294, 372, 359)):
            struct.pack_into("<i", payload, offset, value)
        for offset in range(329, 366, 4):
            struct.pack_into("<i", payload, offset, 1_000_000_000)
        struct.pack_into("<Q", payload, 1071, 27_890_399)
        struct.pack_into("<Q", payload, 1079, 27_890_399)
        payload[1096] = 14
        reading = decode_status(packet(bytes(payload)))
        self.assertIsNotNone(reading)
        assert reading is not None
        self.assertEqual(reading.hottest_control_name, "V1 Board 2")
        self.assertAlmostEqual(reading.hottest_control_c, 33.8)
        self.assertEqual(reading.hottest_internal_name, "Main Board 1")
        self.assertTrue(reading.executing)
        self.assertTrue(reading.error_latched)
        self.assertEqual(reading.error_code, 6)
        self.assertEqual(reading.progress_status, 14)
        self.assertAlmostEqual(reading.profile_elapsed_s or 0, 27.890399)

    def test_decodes_extended_1112_byte_status_layout(self) -> None:
        payload = bytearray(1112)
        payload[:4] = b"\x04\x00\x00\x00"
        struct.pack_into("<H", payload, 6, 1112)
        struct.pack_into("<II", payload, 14, 0, 0)
        offsets = (207, 211, 215, 219, 223, 227, 231, 235, 243, 251)
        expected = (297, 295, 346, 342, 331, 333, 314, 298, 368, 352)
        for offset, value in zip(offsets, expected):
            struct.pack_into("<i", payload, offset, value)
        reading = decode_status(packet(bytes(payload)))
        self.assertIsNotNone(reading)
        assert reading is not None
        self.assertAlmostEqual(reading.temperatures["V1 Board 1"], 29.7)
        self.assertAlmostEqual(reading.temperatures["V2 Board 2"], 34.2)
        self.assertAlmostEqual(reading.hottest_control_c, 34.6)
        self.assertEqual(reading.hottest_control_name, "V1 Board 2")
        self.assertAlmostEqual(reading.hottest_internal_c, 36.8)
        self.assertFalse(reading.executing)

    def test_decodes_extended_progress_trailer(self) -> None:
        payload = bytearray(1138)
        payload[:4] = b"\x04\x00\x00\x00"
        struct.pack_into("<H", payload, 6, 1138)
        for offset in (207, 211, 215, 219, 223, 227, 231, 235, 243, 251):
            struct.pack_into("<i", payload, offset, 510)
        for offset in range(339, 376, 4):
            struct.pack_into("<i", payload, offset, 1_000_000_000)
        struct.pack_into("<Q", payload, 1116, 214_308_000)
        struct.pack_into("<Q", payload, 1124, 214_308_000)
        payload[1137] = 14
        reading = decode_status(packet(bytes(payload)))
        self.assertIsNotNone(reading)
        assert reading is not None
        self.assertTrue(reading.executing)
        self.assertEqual(reading.progress_status, 14)
        self.assertAlmostEqual(reading.profile_elapsed_s or 0, 214.308)

    def test_rejects_unknown_advertised_status_layout(self) -> None:
        payload = bytearray(1120)
        payload[:4] = b"\x04\x00\x00\x00"
        struct.pack_into("<H", payload, 6, 1120)
        self.assertIsNone(decode_status(packet(bytes(payload))))

    def test_rejects_implausible_temperatures(self) -> None:
        payload = bytearray(1067)
        payload[:4] = b"\x04\x00\x00\x00"
        for offset in (197, 201, 205, 209, 213, 217, 221, 225, 233, 241):
            struct.pack_into("<i", payload, offset, 20_000_000)
        self.assertIsNone(decode_status(packet(bytes(payload))))

    def test_decodes_fan_request(self) -> None:
        payload = bytearray(110)
        payload[10] = 0x3F
        request = packet(bytes(payload), src="192.168.1.69", src_port=58299, dst=DEVICE_IP, dst_port=1001)
        self.assertEqual(decode_fan_mode(request), "MAX")

    def test_decodes_extended_fan_request(self) -> None:
        payload = bytearray(124)
        payload[:4] = b"\xFF\xFF\xFF\x00"
        payload[4] = 118
        payload[6:10] = b"\x04\x00\x00\x00"
        payload[11] = 118
        payload[18] = 0x3F
        request = packet(bytes(payload), src="192.168.1.69", src_port=58299, dst=DEVICE_IP, dst_port=1001)
        self.assertEqual(decode_fan_mode(request), "MAX")

    def test_decodes_normal_fan_from_extended_request_even_when_old_assumed_offset_is_zero(self) -> None:
        payload = bytearray(124)
        payload[:4] = b"\xFF\xFF\xFF\x00"
        payload[4] = 118
        payload[6:10] = b"\x04\x00\x00\x00"
        payload[11] = 118
        payload[18] = 0x00
        payload[23] = 0x00
        request = packet(bytes(payload), src="192.168.1.69", src_port=58299, dst=DEVICE_IP, dst_port=1001)
        self.assertEqual(decode_fan_mode(request), "NORMAL")

    def test_extended_fan_decoder_ignores_previous_wrong_offset(self) -> None:
        payload = bytearray(124)
        payload[:4] = b"\xFF\xFF\xFF\x00"
        payload[4] = 118
        payload[6:10] = b"\x04\x00\x00\x00"
        payload[11] = 118
        payload[18] = 0x3F
        payload[23] = 0x00
        request = packet(bytes(payload), src="192.168.1.69", src_port=58299, dst=DEVICE_IP, dst_port=1001)
        self.assertEqual(decode_fan_mode(request), "MAX")

    def test_decodes_129_byte_max_power_request_from_ampro_20260901(self) -> None:
        payload = bytearray(129)
        payload[:4] = b"\xFF\xFF\xFF\x00"
        payload[4] = 123
        payload[6:10] = b"\x04\x00\x00\x00"
        payload[11] = 123
        payload[18] = 0x3F
        request = packet(bytes(payload), src="192.168.1.69", src_port=58299, dst=DEVICE_IP, dst_port=1001)
        self.assertEqual(decode_fan_mode(request), "MAX")

    def test_decodes_129_byte_normal_request(self) -> None:
        payload = bytearray(129)
        payload[:4] = b"\xFF\xFF\xFF\x00"
        payload[4] = 123
        payload[6:10] = b"\x04\x00\x00\x00"
        payload[11] = 123
        payload[18] = 0x00
        request = packet(bytes(payload), src="192.168.1.69", src_port=58299, dst=DEVICE_IP, dst_port=1001)
        self.assertEqual(decode_fan_mode(request), "NORMAL")

    def test_hottest_internal_excludes_hotter_voltage_and_current_sensors(self) -> None:
        payload = bytearray(1067)
        payload[:4] = b"\x04\x00\x00\x00"
        offsets = (197, 201, 205, 209, 213, 217, 221, 225, 233, 241)
        for offset, value in zip(offsets, (297, 294, 520, 336, 328, 518, 311, 294, 372, 359)):
            struct.pack_into("<i", payload, offset, value)
        reading = decode_status(packet(bytes(payload)))
        self.assertIsNotNone(reading)
        assert reading is not None
        self.assertEqual(reading.hottest_control_name, "V1 Board 2")
        self.assertAlmostEqual(reading.hottest_control_c, 52.0)
        self.assertEqual(reading.hottest_internal_name, "Main Board 1")
        self.assertAlmostEqual(reading.hottest_internal_c, 37.2)

    def test_decodes_six_channel_voltage_vector(self) -> None:
        payload = bytearray(95)
        struct.pack_into("<I", payload, 0, 16)
        payload[9] = 1
        for channel in range(6):
            struct.pack_into("<iiI", payload, 13 + channel * 12, 5_000_000, channel * 1_000_000, 60_000_000)
        request = packet(bytes(payload), src="192.168.1.69", src_port=58299, dst=DEVICE_IP, dst_port=1001)
        vector = decode_vector_command(request)
        self.assertIsNotNone(vector)
        assert vector is not None
        self.assertEqual(vector.state_index, 1)
        self.assertEqual(len(vector.channels), 6)
        self.assertEqual(vector.channels[0]["amplitude"], 5.0)


if __name__ == "__main__":
    unittest.main()
