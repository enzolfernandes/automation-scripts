from __future__ import annotations

import struct
import unittest

from amt205_validator.configuration import load_config
from amt205_validator.protocol import DEVICE_IP, PacketRecord
from amt205_validator.session import TestSession


def running_status_packet() -> PacketRecord:
    payload = bytearray(1067)
    payload[:4] = b"\x04\x00\x00\x00"
    for offset, value in zip((197, 201, 205, 209, 213, 217, 221, 225, 233, 241), (300, 310, 320, 330, 410, 350, 360, 370, 390, 380)):
        struct.pack_into("<i", payload, offset, value)
    for offset in range(329, 366, 4):
        struct.pack_into("<i", payload, offset, 1_000_000_000)
    return PacketRecord(500, 1_777_000_000.0, 0.0, DEVICE_IP, 1001, "192.168.1.69", 58000, bytes(payload))


class LiveAttachTests(unittest.TestCase):
    def test_attaching_mid_test_still_displays_current_temperatures(self) -> None:
        config = load_config()
        session = TestSession(config, {"profile": "current_50", "fan_mode": "NORMAL"})
        session.process(running_status_packet())
        self.assertEqual(len(session.samples), 1)
        self.assertEqual(session.samples[0].reading.hottest_control_name, "I1 Board 1")
        self.assertEqual(session.samples[0].reading.hottest_control_c, 41.0)
        self.assertEqual(session.evaluation().status, "INCONCLUSIVE")

    def test_free_profile_can_monitor_mid_test(self) -> None:
        config = load_config()
        session = TestSession(config, {"profile": "custom", "fan_mode": "NORMAL"})
        session.process(running_status_packet())
        self.assertEqual(session.evaluation().status, "MONITORING")


if __name__ == "__main__":
    unittest.main()
