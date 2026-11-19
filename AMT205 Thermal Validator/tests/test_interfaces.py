import unittest

from amt205_validator.capture import interface_alias, preferred_interface_index


class InterfaceSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.interfaces = [
            ("1", r"\Device\NPF_{AAAA} (Local Area Connection* 10)"),
            ("5", r"\Device\NPF_{BBBB} (Wi-Fi)"),
            ("6", r"\Device\NPF_{CCCC} (Ethernet)"),
        ]

    def test_extracts_readable_alias(self) -> None:
        self.assertEqual(interface_alias(self.interfaces[2][1]), "Ethernet")

    def test_prefers_the_adapter_reported_by_the_route(self) -> None:
        self.assertEqual(preferred_interface_index(self.interfaces, "Ethernet"), 2)

    def test_falls_back_to_a_physical_network_adapter(self) -> None:
        self.assertEqual(preferred_interface_index(self.interfaces, None), 2)


if __name__ == "__main__":
    unittest.main()
