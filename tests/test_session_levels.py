from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_bias_dashboard.session_levels import build_premarket_structure, classify_premarket_structure

ET = ZoneInfo("America/New_York")


class SessionLevelsTests(unittest.TestCase):
    def test_classify_premarket_structure_cases(self) -> None:
        self.assertEqual(
            classify_premarket_structure(yesterday_low=100.0, yesterday_high=110.0, premarket_low=102.0, premarket_high=108.0)[0],
            "Inside Yesterday's Range",
        )
        self.assertEqual(
            classify_premarket_structure(yesterday_low=100.0, yesterday_high=110.0, premarket_low=111.0, premarket_high=113.0)[0],
            "Full Gap Above",
        )
        self.assertEqual(
            classify_premarket_structure(yesterday_low=100.0, yesterday_high=110.0, premarket_low=96.0, premarket_high=99.0)[0],
            "Full Gap Below",
        )
        self.assertEqual(
            classify_premarket_structure(yesterday_low=100.0, yesterday_high=110.0, premarket_low=101.0, premarket_high=112.0)[0],
            "Top Overhang",
        )
        self.assertEqual(
            classify_premarket_structure(yesterday_low=100.0, yesterday_high=110.0, premarket_low=98.0, premarket_high=109.0)[0],
            "Bottom Overhang",
        )
        self.assertEqual(
            classify_premarket_structure(yesterday_low=100.0, yesterday_high=110.0, premarket_low=98.0, premarket_high=112.0)[0],
            "Spans Both Sides",
        )

    def test_build_premarket_structure_uses_prior_daily_bar_and_current_premarkarket_range(self) -> None:
        intraday_history = {
            "candles": [
                {"datetime": "2026-03-29T04:00:00-04:00", "open": 103.8, "high": 104.4, "low": 103.6, "close": 104.0, "volume": 300},
                {"datetime": "2026-03-29T08:15:00-04:00", "open": 104.0, "high": 106.0, "low": 103.5, "close": 105.5, "volume": 500},
                {"datetime": "2026-03-29T09:20:00-04:00", "open": 105.5, "high": 107.0, "low": 105.0, "close": 106.8, "volume": 800},
                {"datetime": "2026-03-29T09:35:00-04:00", "open": 106.8, "high": 107.5, "low": 106.2, "close": 106.4, "volume": 1000},
            ]
        }
        daily_history = {
            "candles": [
                {"datetime": "2026-03-27T00:00:00Z", "open": 101.0, "high": 109.0, "low": 100.0, "close": 108.0, "volume": 100000},
                {"datetime": "2026-03-28T00:00:00Z", "open": 108.0, "high": 110.0, "low": 102.0, "close": 104.0, "volume": 100000},
            ]
        }

        structure = build_premarket_structure(
            intraday_history=intraday_history,
            daily_history=daily_history,
            as_of=datetime(2026, 3, 29, 10, 0, tzinfo=ET),
        )

        self.assertEqual(structure.prior_session_date, "2026-03-28")
        self.assertEqual(structure.yesterday_low, 102.0)
        self.assertEqual(structure.yesterday_high, 110.0)
        self.assertEqual(structure.premarket_low, 103.5)
        self.assertEqual(structure.premarket_high, 107.0)
        self.assertEqual(structure.premarket_session_date, "2026-03-29")
        self.assertEqual(structure.label, "Inside Yesterday's Range")

    def test_build_premarket_structure_anchors_weekend_to_latest_premarket_then_prior_rth(self) -> None:
        intraday_history = {
            "candles": [
                {"datetime": "2026-03-27T08:10:00-04:00", "open": 104.0, "high": 105.5, "low": 103.2, "close": 105.0, "volume": 500},
                {"datetime": "2026-03-27T09:15:00-04:00", "open": 105.0, "high": 106.0, "low": 104.6, "close": 105.8, "volume": 800},
                {"datetime": "2026-03-27T09:40:00-04:00", "open": 105.8, "high": 106.5, "low": 105.1, "close": 105.4, "volume": 900},
            ]
        }
        daily_history = {
            "candles": [
                {"datetime": "2026-03-26T00:00:00Z", "open": 101.0, "high": 109.0, "low": 100.0, "close": 108.0, "volume": 100000},
                {"datetime": "2026-03-27T00:00:00Z", "open": 108.0, "high": 110.0, "low": 102.0, "close": 104.0, "volume": 100000},
            ]
        }

        structure = build_premarket_structure(
            intraday_history=intraday_history,
            daily_history=daily_history,
            as_of=datetime(2026, 3, 29, 12, 0, tzinfo=ET),
        )

        self.assertEqual(structure.premarket_session_date, "2026-03-27")
        self.assertEqual(structure.prior_session_date, "2026-03-26")
        self.assertEqual(structure.yesterday_low, 100.0)
        self.assertEqual(structure.yesterday_high, 109.0)
        self.assertEqual(structure.premarket_low, 103.2)
        self.assertEqual(structure.premarket_high, 106.0)

    def test_build_premarket_structure_flags_partial_coverage(self) -> None:
        intraday_history = {
            "candles": [
                {"datetime": "2026-03-31T07:00:00-04:00", "open": 636.8, "high": 637.3, "low": 636.4, "close": 637.2, "volume": 500},
                {"datetime": "2026-03-31T08:09:00-04:00", "open": 639.9, "high": 640.17, "low": 639.7, "close": 640.1, "volume": 800},
                {"datetime": "2026-03-31T09:20:00-04:00", "open": 638.9, "high": 639.5, "low": 638.8, "close": 639.0, "volume": 800},
            ]
        }
        daily_history = {
            "candles": [
                {"datetime": "2026-03-30T00:00:00Z", "open": 632.0, "high": 640.4, "low": 629.3, "close": 637.0, "volume": 100000},
            ]
        }

        structure = build_premarket_structure(
            intraday_history=intraday_history,
            daily_history=daily_history,
            as_of=datetime(2026, 3, 31, 9, 30, tzinfo=ET),
        )

        self.assertFalse(structure.premarket_complete)
        self.assertEqual(structure.premarket_coverage_start, "07:00")
        self.assertEqual(structure.label, "Inside Yesterday's Range")
        self.assertIn("mean-reverting", structure.description.lower())

    def test_build_premarket_structure_ignores_isolated_outlier_wick(self) -> None:
        intraday_history = {
            "candles": [
                {"datetime": "2026-03-31T07:40:00-04:00", "open": 638.55, "high": 639.10, "low": 638.14, "close": 639.08, "volume": 600},
                {"datetime": "2026-03-31T07:45:00-04:00", "open": 639.06, "high": 639.30, "low": 637.95, "close": 638.46, "volume": 500},
                {"datetime": "2026-03-31T07:50:00-04:00", "open": 638.37, "high": 638.46, "low": 637.90, "close": 638.18, "volume": 900},
                {"datetime": "2026-03-31T07:55:00-04:00", "open": 638.25, "high": 638.652, "low": 631.97, "close": 638.58, "volume": 300},
                {"datetime": "2026-03-31T08:00:00-04:00", "open": 638.65, "high": 639.12, "low": 638.65, "close": 639.03, "volume": 500},
                {"datetime": "2026-03-31T08:05:00-04:00", "open": 638.97, "high": 640.17, "low": 638.57, "close": 639.63, "volume": 1900},
                {"datetime": "2026-03-31T09:20:00-04:00", "open": 638.90, "high": 639.50, "low": 638.80, "close": 639.00, "volume": 800},
            ]
        }
        daily_history = {
            "candles": [
                {"datetime": "2026-03-30T00:00:00Z", "open": 632.0, "high": 640.4, "low": 629.3, "close": 637.0, "volume": 100000},
            ]
        }

        structure = build_premarket_structure(
            intraday_history=intraday_history,
            daily_history=daily_history,
            as_of=datetime(2026, 3, 31, 9, 30, tzinfo=ET),
        )

        self.assertAlmostEqual(structure.premarket_low or 0.0, 637.90)
        self.assertAlmostEqual(structure.premarket_high or 0.0, 640.17)
        self.assertNotEqual(structure.premarket_low, 631.97)


if __name__ == "__main__":
    unittest.main()
