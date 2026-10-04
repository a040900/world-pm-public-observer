"""Bound input/CLI and truncated-journal guards. Zero network operations."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.research import world_pm_subsecond_analyze_r1 as analyze
from tools.research import world_pm_subsecond_capture_r1 as capture


def config():
    start = 1_800_000_000
    return {"schemaVersion": capture.SCHEMA, "startTs": start, "endTs": start + 43200,
            "bindingRatified": False,
            "markets": [{"startTs": start + i * 300, "endTs": start + (i + 1) * 300,
                         "worldTicker": f"SYNTHETIC-{i}", "pmUpToken": f"up-{i}",
                         "pmDownToken": f"down-{i}"} for i in range(144)],
            "pm": {"mode": "rest", "booksUrl": "https://pm.invalid/books"},
            "world": {"method": "GET", "url": "https://world.invalid/history",
                      "query": {"ticker": "{ticker}", "resolution": 1,
                                "start_ts": "{query_start}", "end_ts": "{query_end}"},
                      "sidePaths": {"yes": ["yes"], "no": ["no"]},
                      "fields": {"source_time": "ts", "bid": "bid", "ask": "ask"},
                      "identityFields": ["ts", "slotId"], "lookbackSeconds": 8}}


class OfflineCliTests(unittest.TestCase):
    def test_config_validation_and_unratified_capture_never_touch_network(self):
        c = config()
        with patch.object(capture, "http_json", side_effect=AssertionError("no network")):
            capture.validate_config(c)
            with self.assertRaisesRegex(ValueError, "BINDING_NOT_RATIFIED"):
                capture.capture(c, "must-not-create.jsonl")

    def test_direct_file_cli_default_is_validation_only(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(json.dumps(config()), encoding="utf-8")
            result = subprocess.run([sys.executable, "tools/research/world_pm_subsecond_capture_r1.py",
                                     "--config", str(path)], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout)["networkRequests"], 0)

    def test_offline_analyzer_cli_emits_no_result_for_empty_cohort(self):
        with tempfile.TemporaryDirectory() as td:
            path, output = Path(td) / "capture.jsonl", Path(td) / "analysis.json"
            journal = capture.Journal(path)
            c = config()
            journal.emit("header", schemaVersion=capture.SCHEMA, startTs=c["startTs"],
                         endTs=c["endTs"], configuration=c)
            journal.emit("footer", completed=False, clockReliability=journal.clock.report())
            journal.close()
            result = subprocess.run([sys.executable, "tools/research/world_pm_subsecond_analyze_r1.py",
                                     "--input", str(path), "--output", str(output)],
                                    capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout)["verdict"], "NO_RESULT_DATA_INSUFFICIENT")
            report = json.loads(output.read_text())
            self.assertEqual(len(report["inputSha256"]), 64)

    def test_broken_sequence_cannot_be_treated_as_valid_capture(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "capture.jsonl"
            path.write_text(json.dumps({"sequence": 5, "type": "header"}) + "\n")
            with self.assertRaisesRegex(ValueError, "SEQUENCE_BROKEN"):
                analyze.read_journal(path)


if __name__ == "__main__":
    unittest.main()
