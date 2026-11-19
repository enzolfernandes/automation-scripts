from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from amt205_validator.capture import LiveStatusWatchdog
from amt205_validator.configuration import load_config
from amt205_validator.session import TestSession


class LiveStatusWatchdogTests(unittest.TestCase):
    def test_reports_disconnect_once_and_then_restoration(self) -> None:
        watchdog = LiveStatusWatchdog(timeout_s=5.0)
        watchdog.start(10.0)
        self.assertFalse(watchdog.poll(14.99))
        self.assertTrue(watchdog.poll(15.0))
        self.assertFalse(watchdog.poll(20.0))
        self.assertTrue(watchdog.observe(20.1))
        self.assertFalse(watchdog.observe(21.0))
        self.assertTrue(watchdog.poll(26.0))

    def test_observed_status_refreshes_the_timeout(self) -> None:
        watchdog = LiveStatusWatchdog(timeout_s=5.0)
        watchdog.start(0.0)
        self.assertFalse(watchdog.observe(4.0))
        self.assertFalse(watchdog.poll(8.9))
        self.assertTrue(watchdog.poll(9.0))

    def test_monitor_events_are_preserved_in_session_history(self) -> None:
        session = TestSession(load_config(), {"profile": "custom", "fan_mode": "NORMAL"})
        event = session.add_monitor_event(
            "connection_lost",
            "WARNING",
            "AMT205 disconnected; temperature updates stopped.",
            12.5,
            {"timeout_s": 5.0},
        )
        self.assertEqual(event.source, "monitor")
        self.assertEqual(session.events[-1].kind, "connection_lost")
        self.assertEqual(session.events[-1].relative, 12.5)
        with TemporaryDirectory() as temporary:
            bundle = session.save(Path(temporary))
            saved_events = bundle["events"].read_text(encoding="utf-8-sig")
        self.assertIn("connection_lost", saved_events)
        self.assertIn("AMT205 disconnected", saved_events)


if __name__ == "__main__":
    unittest.main()
