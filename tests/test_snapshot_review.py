from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_bias_dashboard.snapshot_review import build_snapshot_review
from options_bias_dashboard.snapshots import write_dashboard_snapshot

ET = ZoneInfo("America/New_York")


def make_payload(
    *,
    symbol: str,
    captured_at: datetime,
    bias_label: str,
    bias_score: float,
    confidence: float,
    spot: float,
    history_candles: list[dict[str, object]],
    premarket_label: str,
    gex_regime: str,
    charm_regime: str,
    up1: float,
    down1: float,
    em_dollars: float,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "captured_at": captured_at.isoformat(),
        "raw_snapshot": {
            "symbol": symbol,
            "history": {"candles": history_candles},
            "underlying_price": spot,
            "fetched_at": captured_at.isoformat(),
        },
        "analysis": {
            "result": {
                "label": bias_label,
                "score": bias_score,
                "confidence": confidence,
                "components": [],
            }
        },
        "trade_plan": {
            "gex_regime_label": gex_regime,
            "charm_regime_label": charm_regime,
        },
        "premarket_structure": {
            "label": premarket_label,
        },
        "dynamic_targets": {
            "em_dollars": em_dollars,
            "upside_targets": [
                {"label": "Up 1", "price": up1},
                {"label": "Up 2", "price": up1 + 1.0},
            ],
            "downside_targets": [
                {"label": "Down 1", "price": down1},
                {"label": "Down 2", "price": down1 - 1.0},
            ],
        },
    }


class SnapshotReviewTests(unittest.TestCase):
    def test_build_snapshot_review_scores_archived_snapshots(self) -> None:
        session_bars = [
            {"datetime": "2026-04-01T09:35:00-04:00", "open": 100.0, "high": 100.5, "low": 99.8, "close": 100.2, "volume": 1000},
            {"datetime": "2026-04-01T10:05:00-04:00", "open": 100.2, "high": 101.2, "low": 99.9, "close": 101.0, "volume": 1000},
            {"datetime": "2026-04-01T11:05:00-04:00", "open": 101.0, "high": 102.4, "low": 100.8, "close": 102.0, "volume": 1000},
            {"datetime": "2026-04-01T15:55:00-04:00", "open": 102.0, "high": 103.0, "low": 101.5, "close": 102.8, "volume": 1000},
        ]
        incomplete_bars = [
            {"datetime": "2026-04-02T09:35:00-04:00", "open": 103.0, "high": 103.4, "low": 102.9, "close": 103.2, "volume": 1000},
            {"datetime": "2026-04-02T10:05:00-04:00", "open": 103.2, "high": 103.6, "low": 103.0, "close": 103.5, "volume": 1000},
        ]

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            write_dashboard_snapshot(
                make_payload(
                    symbol="SPY",
                    captured_at=datetime(2026, 4, 1, 10, 0, tzinfo=ET),
                    bias_label="mixed",
                    bias_score=12.0,
                    confidence=70.0,
                    spot=100.0,
                    history_candles=session_bars[:2],
                    premarket_label="Inside Yesterday's Range",
                    gex_regime="Positive",
                    charm_regime="Mixed",
                    up1=101.0,
                    down1=99.0,
                    em_dollars=2.0,
                ),
                captured_at=datetime(2026, 4, 1, 10, 0, tzinfo=ET),
                root=root,
            )
            write_dashboard_snapshot(
                make_payload(
                    symbol="SPY",
                    captured_at=datetime(2026, 4, 1, 11, 0, tzinfo=ET),
                    bias_label="bearish",
                    bias_score=-20.0,
                    confidence=68.0,
                    spot=102.5,
                    history_candles=session_bars[:3],
                    premarket_label="Inside Yesterday's Range",
                    gex_regime="Negative",
                    charm_regime="Negative",
                    up1=103.5,
                    down1=101.5,
                    em_dollars=2.0,
                ),
                captured_at=datetime(2026, 4, 1, 11, 0, tzinfo=ET),
                root=root,
            )
            write_dashboard_snapshot(
                make_payload(
                    symbol="SPY",
                    captured_at=datetime(2026, 4, 1, 16, 15, tzinfo=ET),
                    bias_label="mixed",
                    bias_score=0.0,
                    confidence=50.0,
                    spot=102.8,
                    history_candles=session_bars,
                    premarket_label="Inside Yesterday's Range",
                    gex_regime="Positive",
                    charm_regime="Mixed",
                    up1=103.5,
                    down1=102.0,
                    em_dollars=2.0,
                ),
                captured_at=datetime(2026, 4, 1, 16, 15, tzinfo=ET),
                root=root,
            )
            write_dashboard_snapshot(
                make_payload(
                    symbol="SPY",
                    captured_at=datetime(2026, 4, 2, 10, 0, tzinfo=ET),
                    bias_label="bullish",
                    bias_score=18.0,
                    confidence=61.0,
                    spot=103.2,
                    history_candles=incomplete_bars,
                    premarket_label="Top Overhang",
                    gex_regime="Positive",
                    charm_regime="Positive",
                    up1=104.0,
                    down1=102.4,
                    em_dollars=1.5,
                ),
                captured_at=datetime(2026, 4, 2, 10, 0, tzinfo=ET),
                root=root,
            )

            summary = build_snapshot_review("SPY", root=root)

        self.assertEqual(summary.completed_sessions, 1)
        self.assertEqual(summary.incomplete_sessions, 1)
        self.assertEqual(summary.total_snapshots, 2)
        self.assertEqual(summary.strong_bullish_samples, 0)
        self.assertEqual(summary.bullish_lean_samples, 1)
        self.assertEqual(summary.neutral_samples, 0)
        self.assertEqual(summary.strong_bearish_samples, 1)
        self.assertAlmostEqual(summary.upward_accuracy or 0.0, 1.0)
        self.assertAlmostEqual(summary.downward_accuracy or 0.0, 0.0)
        self.assertAlmostEqual(summary.target_hit_rate or 0.0, 1.0)

        bullish_row = next(row for row in summary.rows if row.bias_bucket == "Bullish Lean")
        bearish_row = next(row for row in summary.rows if row.bias_bucket == "Strong Bearish")

        self.assertTrue(bullish_row.bias_correct)
        self.assertEqual(bullish_row.first_target_hit, "Up 1")
        self.assertAlmostEqual(bullish_row.follow_through_em_ratio or 0.0, 1.5)

        self.assertFalse(bearish_row.bias_correct)
        self.assertEqual(bearish_row.first_target_hit, "Down 1")

        premarket_group = next(group for group in summary.by_premarket if group.label == "Inside Yesterday's Range")
        self.assertEqual(premarket_group.samples, 2)
        self.assertAlmostEqual(premarket_group.bias_accuracy or 0.0, 0.5)
        bias_bucket_group = next(group for group in summary.by_bias_bucket if group.label == "Bullish Lean")
        self.assertEqual(bias_bucket_group.samples, 1)
        self.assertAlmostEqual(bias_bucket_group.upward_accuracy or 0.0, 1.0)

        self.assertTrue(summary.best_setups)
        self.assertIn("Bullish Lean", summary.best_setups[0].label)


if __name__ == "__main__":
    unittest.main()
