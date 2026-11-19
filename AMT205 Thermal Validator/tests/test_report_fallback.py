from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import patch

from amt205_validator.app import Application


class EvidenceOnlySession:
    def save(self, evidence: Path, _source: Path) -> dict[str, Path]:
        evidence.mkdir(parents=True)
        bundle = {
            "metadata": evidence / "metadata.json",
            "samples": evidence / "readings.csv",
            "events": evidence / "events.csv",
        }
        for path in bundle.values():
            path.write_text("preserved", encoding="utf-8")
        return bundle


class ReportFallbackTests(unittest.TestCase):
    def test_evidence_is_reported_as_saved_when_excel_generation_fails(self) -> None:
        application = object.__new__(Application)
        application.messages = Queue()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "AMT205_session"
            source = Path(temporary) / "capture.pcapng"
            source.write_bytes(b"capture")
            with patch("amt205_validator.app.create_excel", side_effect=RuntimeError("generator missing")):
                application._finalize(EvidenceOnlySession(), output, source)
            kind, value = application.messages.get_nowait()
            self.assertEqual(kind, "report_unavailable")
            self.assertEqual(value[0], output)
            self.assertIn("generator missing", value[1])
            self.assertTrue((output / "evidence" / "metadata.json").exists())


if __name__ == "__main__":
    unittest.main()
