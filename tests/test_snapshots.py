from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_bias_dashboard.snapshots import (
    ET,
    load_latest_prior_session_snapshot,
    snapshot_bucket,
    snapshot_dir_for,
    write_dashboard_snapshot,
)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_bucket_floors_to_fifteen_minutes(self) -> None:
        observed = datetime(2026, 3, 29, 10, 37, 52, tzinfo=ET)

        bucket = snapshot_bucket(observed)

        self.assertEqual(bucket, datetime(2026, 3, 29, 10, 30, 0, tzinfo=ET))

    def test_write_dashboard_snapshot_stores_each_symbol_in_same_bucket_folder(self) -> None:
        observed = datetime(2026, 3, 29, 15, 44, tzinfo=ET)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            spy_file = write_dashboard_snapshot({"symbol": "SPY", "value": 1}, captured_at=observed, root=root)
            qqq_file = write_dashboard_snapshot({"symbol": "QQQ", "value": 2}, captured_at=observed, root=root)

            self.assertEqual(spy_file.parent, qqq_file.parent)
            self.assertEqual(spy_file.name, "SPY.json")
            self.assertEqual(qqq_file.name, "QQQ.json")
            self.assertEqual(json.loads(spy_file.read_text())["value"], 1)
            self.assertEqual(json.loads(qqq_file.read_text())["value"], 2)

    def test_load_latest_prior_session_snapshot_prefers_symbol_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_dashboard_snapshot(
                {"symbol": "SPY", "captured_at": "2026-03-28T15:45:00-04:00", "value": "older"},
                captured_at=datetime(2026, 3, 28, 15, 45, tzinfo=ET),
                root=root,
            )
            write_dashboard_snapshot(
                {"symbol": "SPY", "captured_at": "2026-03-28T16:00:00-04:00", "value": "latest-prior"},
                captured_at=datetime(2026, 3, 28, 16, 0, tzinfo=ET),
                root=root,
            )
            write_dashboard_snapshot(
                {"symbol": "SPY", "captured_at": "2026-03-29T09:45:00-04:00", "value": "today"},
                captured_at=datetime(2026, 3, 29, 9, 45, tzinfo=ET),
                root=root,
            )

            payload = load_latest_prior_session_snapshot(
                "SPY",
                as_of=datetime(2026, 3, 29, 10, 0, tzinfo=ET),
                root=root,
            )

            self.assertIsNotNone(payload)
            self.assertEqual(payload["value"], "latest-prior")

    def test_load_latest_prior_session_snapshot_falls_back_to_legacy_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy_dir = snapshot_dir_for(datetime(2026, 3, 28, 15, 45, tzinfo=ET), root=root)
            legacy_dir.mkdir(parents=True, exist_ok=True)
            (legacy_dir / "dashboard_snapshot.json").write_text(
                json.dumps({"symbol": "SPY", "value": "legacy"}) + "\n"
            )

            payload = load_latest_prior_session_snapshot(
                "SPY",
                as_of=datetime(2026, 3, 29, 10, 0, tzinfo=ET),
                root=root,
            )

            self.assertIsNotNone(payload)
            self.assertEqual(payload["value"], "legacy")


if __name__ == "__main__":
    unittest.main()
