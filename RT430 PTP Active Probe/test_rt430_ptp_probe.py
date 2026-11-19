import unittest

from scapy.all import Dot1Q, Ether

from rt430_ptp_probe import (
    PTP_ETHERTYPE,
    __version__,
    build_pdelay_frame,
    mac_to_clock_identity,
    parse_ptp_frame,
)


class PtpProbeTests(unittest.TestCase):
    def setUp(self):
        self.frame = build_pdelay_frame(
            source_mac="b4:45:06:90:5d:5f",
            destination_mac="01:80:c2:00:00:0e",
            vlan=400,
            pcp=6,
            domain=10,
            sequence_id=44,
            timestamp_mode="zero",
            utc_offset=37,
        )

    def test_eui64_clock_identity(self):
        self.assertEqual(
            mac_to_clock_identity("b4:45:06:90:5d:5f"),
            bytes.fromhex("b44506fffe905d5f"),
        )

    def test_version_is_archived_release(self):
        self.assertEqual(__version__, "0.1.0")

    def test_layer2_and_vlan_header(self):
        self.assertEqual(self.frame[Ether].dst, "01:80:c2:00:00:0e")
        self.assertEqual(self.frame[Dot1Q].vlan, 400)
        self.assertEqual(self.frame[Dot1Q].prio, 6)
        self.assertEqual(self.frame[Dot1Q].type, PTP_ETHERTYPE)

    def test_ptp_header_matches_reference_shape(self):
        parsed = parse_ptp_frame(self.frame)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.message_type, 2)
        self.assertEqual(parsed.domain, 10)
        self.assertEqual(parsed.flags, 0)
        self.assertEqual(parsed.sequence_id, 44)
        self.assertEqual(parsed.clock_identity, "b4:45:06:ff:fe:90:5d:5f")
        self.assertEqual(len(bytes(self.frame)), 72)


if __name__ == "__main__":
    unittest.main()
