from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_bias_dashboard.price_targets import (
    FIBONACCI_RUNGS,
    build_directional_targets,
    build_price_target_ladder,
    directional_scales,
    previous_session_bias_score,
    resolve_expected_move_dollars,
)


class PriceTargetTests(unittest.TestCase):
    def test_directional_scales_bull_bear(self) -> None:
        up_bull, down_bull = directional_scales(40.0)
        up_bear, down_bear = directional_scales(-40.0)
        self.assertEqual(up_bull, 1.0)
        self.assertEqual(down_bear, 1.0)
        self.assertLess(down_bull, 1.0)
        self.assertLess(up_bear, 1.0)

    def test_fibonacci_order(self) -> None:
        self.assertAlmostEqual(FIBONACCI_RUNGS[0], 0.382)
        self.assertAlmostEqual(FIBONACCI_RUNGS[1], 0.618)
        self.assertAlmostEqual(FIBONACCI_RUNGS[2], 1.0)
        self.assertLess(FIBONACCI_RUNGS[0], FIBONACCI_RUNGS[1])
        self.assertLess(FIBONACCI_RUNGS[1], FIBONACCI_RUNGS[2])

    def test_ladder_monotone(self) -> None:
        ladder = build_price_target_ladder(
            spot=650.0,
            vol_panel=None,
            bias_score=0.0,
            session_high=659.0,
            session_low=644.0,
            expected_move_chain=14.0,
        )
        u1, u2, u3 = ladder.upside
        d1, d2, d3 = ladder.downside
        self.assertLess(u1, u2)
        self.assertLess(u2, u3)
        self.assertGreater(d1, d2)
        self.assertGreater(d2, d3)

    def test_intraday_range_caps_expected_move(self) -> None:
        ladder = build_price_target_ladder(
            spot=648.57,
            vol_panel=None,
            bias_score=-20.0,
            session_high=659.80,
            session_low=644.79,
            expected_move_chain=20.0,
        )
        self.assertAlmostEqual(ladder.base_em_dollars, 8.4)
        self.assertAlmostEqual(ladder.session_range or 0.0, 15.01, places=2)
        self.assertLess(ladder.em_dollars, ladder.base_em_dollars)
        self.assertAlmostEqual(ladder.em_dollars, 7.505, places=3)

    def test_resolve_em_prefers_vol_panel(self) -> None:
        from options_bias_dashboard.volatility import VolatilityPanel

        panel = VolatilityPanel(
            vix=18.0,
            vix1d=None,
            vix_odte_multiplier=1.15,
            sigma_source_label="test",
            sigma_base=0.18,
            hours_remaining=6.5,
            in_regular_session=True,
            accel_multiplier=1.0,
            sigma_effective=0.18,
            kurtosis_factor=2.0,
            dow_multiplier=1.0,
            cluster_multiplier=1.0,
            cluster_note="",
            time_to_expiry_years=1 / 252,
            sigma_daily_close=0.18 / math.sqrt(252),
            dollar_daily_1sigma=7.44,
            dollar_session_1sigma=7.44,
            straddle=None,
            straddle_vs_session_ratio=None,
        )
        em, src = resolve_expected_move_dollars(650.0, panel, 20.0)
        self.assertAlmostEqual(em, 7.44)
        self.assertIn("VIX", src)

    def test_build_directional_targets_labels_and_signs(self) -> None:
        upside = build_directional_targets(100.0, (101.5, 103.0, 105.0), direction="up")
        downside = build_directional_targets(100.0, (98.5, 97.0, 95.0), direction="down")

        self.assertEqual([target.label for target in upside], ["Up 1", "Up 2", "Up 3"])
        self.assertEqual([target.label for target in downside], ["Down 1", "Down 2", "Down 3"])
        self.assertTrue(all(target.delta_from_spot > 0 for target in upside))
        self.assertTrue(all(target.delta_from_spot < 0 for target in downside))
        self.assertTrue(all(target.percent_from_spot > 0 for target in upside))
        self.assertTrue(all(target.percent_from_spot < 0 for target in downside))

    def test_previous_session_bias_score_uses_candle_direction_and_close_location(self) -> None:
        bullish = previous_session_bias_score(100.0, 106.0, 99.0, 105.0)
        bearish = previous_session_bias_score(105.0, 106.0, 99.0, 100.0)
        flat = previous_session_bias_score(None, 106.0, 99.0, 100.0)

        self.assertGreater(bullish, 0.0)
        self.assertLess(bearish, 0.0)
        self.assertEqual(flat, 0.0)


if __name__ == "__main__":
    unittest.main()
