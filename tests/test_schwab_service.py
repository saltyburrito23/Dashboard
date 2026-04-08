from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_bias_dashboard.schwab_service import SchwabResearchClient


class SchwabServiceTests(unittest.TestCase):
    def test_intraday_history_window_uses_rolling_lookback(self) -> None:
        end = datetime(2026, 3, 31, 14, 0, tzinfo=timezone.utc)

        start, resolved_end = SchwabResearchClient.intraday_history_window(end, lookback_days=7)

        self.assertEqual(resolved_end, end)
        self.assertEqual((resolved_end - start).days, 7)


if __name__ == "__main__":
    unittest.main()
