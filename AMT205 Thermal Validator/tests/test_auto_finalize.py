from __future__ import annotations

import unittest
from queue import Queue
from types import SimpleNamespace

from amt205_validator.app import Application, should_auto_finalize
from amt205_validator.protocol import PacketRecord


class CompletedRecordingSession:
    def __init__(self) -> None:
        self.config = {"workflow": {"auto_finalize_recording": True}}
        self.workflow = SimpleNamespace(TERMINAL_CODES={"COMPLETE", "REPEAT_REQUIRED"})
        self.workflow_step = SimpleNamespace(code="POST_TEST_EVIDENCE")
        self.monitor_events: list[tuple] = []

    def process(self, _packet: PacketRecord) -> None:
        self.workflow_step = SimpleNamespace(code="COMPLETE")

    def add_monitor_event(self, *args):
        self.monitor_events.append(args)
        return "automatic-finalization-event"


class AutomaticFinalizationTests(unittest.TestCase):
    def test_enabled_terminal_workflow_is_finalized(self) -> None:
        config = {"workflow": {"auto_finalize_recording": True}}
        self.assertTrue(should_auto_finalize(config, "COMPLETE"))
        self.assertTrue(should_auto_finalize(config, "REPEAT_REQUIRED"))

    def test_injection_and_post_test_steps_remain_recording(self) -> None:
        config = {"workflow": {"auto_finalize_recording": True}}
        self.assertFalse(should_auto_finalize(config, "INJECTION_STABLE"))
        self.assertFalse(should_auto_finalize(config, "POST_TEST_EVIDENCE"))
        self.assertFalse(should_auto_finalize(config, "THERMAL_RECOVERY"))

    def test_setting_can_disable_automatic_finalization(self) -> None:
        config = {"workflow": {"auto_finalize_recording": False}}
        self.assertFalse(should_auto_finalize(config, "COMPLETE"))

    def test_missing_setting_defaults_to_enabled(self) -> None:
        self.assertTrue(should_auto_finalize({}, "COMPLETE"))

    def test_completed_packet_workflow_requests_ui_finalization_once(self) -> None:
        application = object.__new__(Application)
        application.session = None
        application.recording_session = CompletedRecordingSession()
        application.recording_origin_relative = None
        application.auto_finalize_pending = False
        application.messages = Queue()
        packet = PacketRecord(1, 0.0, 42.0, "192.168.1.41", 1001, "192.168.1.2", 5000, b"")

        application._packet_callback(packet)
        application._packet_callback(packet)

        self.assertTrue(application.auto_finalize_pending)
        self.assertEqual(application.messages.get_nowait(), ("event", "automatic-finalization-event"))
        self.assertEqual(application.messages.get_nowait(), ("auto_finalize", None))
        self.assertTrue(application.messages.empty())


if __name__ == "__main__":
    unittest.main()
