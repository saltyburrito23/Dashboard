from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_bias_dashboard.models import (
    AnalysisSnapshot,
    BiasComponent,
    BiasResult,
    ChainMetrics,
    CharmStrikeOverview,
    ConvictionContext,
    StrikeOverview,
)
from options_bias_dashboard.trade_plan import build_intraday_trade_plan
from options_bias_dashboard.volatility import VolatilityPanel


def make_panel(*, vix: float | None, vix1d: float | None = None) -> VolatilityPanel:
    return VolatilityPanel(
        vix=vix,
        vix1d=vix1d,
        vix_odte_multiplier=1.15,
        sigma_source_label="test",
        sigma_base=0.20,
        hours_remaining=3.0,
        in_regular_session=True,
        accel_multiplier=1.2,
        sigma_effective=0.24,
        kurtosis_factor=2.0,
        dow_multiplier=1.0,
        cluster_multiplier=1.0,
        cluster_note="",
        time_to_expiry_years=3.0 / (6.5 * 252),
        sigma_daily_close=0.20 / (252**0.5),
        dollar_daily_1sigma=8.0,
        dollar_session_1sigma=6.0,
        straddle=None,
        straddle_vs_session_ratio=None,
    )


def make_analysis(*, label: str, score: float, confidence: float, strike_rows: tuple[StrikeOverview, ...]) -> AnalysisSnapshot:
    metrics = ChainMetrics(
        underlying_price=100.0,
        total_contract_count=6,
        filtered_contract_count=6,
        call_volume=1000,
        put_volume=900,
        call_open_interest=5000,
        put_open_interest=4500,
        put_call_volume_ratio=0.9,
        put_call_open_interest_ratio=0.9,
        average_call_iv=0.24,
        average_put_iv=0.23,
        iv_skew_available=True,
        average_spread_pct=0.05,
        liquidity_score=0.8,
        expected_move=4.0,
        call_wall=101.0,
        put_wall=99.0,
        gex_peak=100.0,
        gex_flip_estimate=100.0,
    )
    return AnalysisSnapshot(
        result=BiasResult(
            label=label,
            score=score,
            confidence=confidence,
            components=(BiasComponent(name="test", score=0.0, weight=1.0, detail=""),),
        ),
        metrics=metrics,
        filtered_contracts=tuple(),
        strike_overview=strike_rows,
    )


class TradePlanTests(unittest.TestCase):
    def test_positive_gex_bullish_bias_prefers_calls(self) -> None:
        analysis = make_analysis(
            label="bullish",
            score=24.0,
            confidence=72.0,
            strike_rows=(
                StrikeOverview(99.0, 0, 0, 0, 0, 800_000.0, -100_000.0, 700_000.0),
                StrikeOverview(100.0, 0, 0, 0, 0, 900_000.0, -150_000.0, 750_000.0),
            ),
        )
        charm_rows = (
            CharmStrikeOverview(99.0, 20_000.0, -5_000.0, 15_000.0),
            CharmStrikeOverview(100.0, 25_000.0, -6_000.0, 19_000.0),
        )

        plan = build_intraday_trade_plan(analysis, make_panel(vix=19.0), charm_rows)

        self.assertEqual(plan.preferred_trade, "Long Calls")
        self.assertEqual(plan.gex_regime_label, "Positive")
        self.assertEqual(plan.charm_regime_label, "Positive")

    def test_deep_negative_gex_and_all_negative_charm_flags_morning_only(self) -> None:
        analysis = make_analysis(
            label="bearish",
            score=-26.0,
            confidence=68.0,
            strike_rows=(
                StrikeOverview(99.0, 0, 0, 0, 0, 120_000.0, -900_000.0, -780_000.0),
                StrikeOverview(100.0, 0, 0, 0, 0, 80_000.0, -820_000.0, -740_000.0),
            ),
        )
        charm_rows = (
            CharmStrikeOverview(99.0, -20_000.0, -12_000.0, -32_000.0),
            CharmStrikeOverview(100.0, -18_000.0, -10_000.0, -28_000.0),
        )

        plan = build_intraday_trade_plan(analysis, make_panel(vix=27.0), charm_rows)

        self.assertEqual(plan.preferred_trade, "Long Puts")
        self.assertEqual(plan.gex_regime_label, "Deep Negative")
        self.assertEqual(plan.charm_regime_label, "All-Negative")
        self.assertIn("11:30 ET", plan.hold_guidance)

    def test_mixed_bias_prefers_wait(self) -> None:
        analysis = make_analysis(
            label="mixed",
            score=3.0,
            confidence=58.0,
            strike_rows=(
                StrikeOverview(99.0, 0, 0, 0, 0, 200_000.0, -180_000.0, 20_000.0),
                StrikeOverview(100.0, 0, 0, 0, 0, 220_000.0, -210_000.0, 10_000.0),
            ),
        )
        charm_rows = (
            CharmStrikeOverview(99.0, 1_000.0, -900.0, 100.0),
            CharmStrikeOverview(100.0, 900.0, -850.0, 50.0),
        )

        plan = build_intraday_trade_plan(analysis, make_panel(vix=22.0), charm_rows)

        self.assertTrue(plan.preferred_trade.startswith("Wait"))

    def test_bullish_lean_requires_confirmation(self) -> None:
        analysis = make_analysis(
            label="mixed",
            score=16.4,
            confidence=59.0,
            strike_rows=(
                StrikeOverview(99.0, 0, 0, 0, 0, 300_000.0, -200_000.0, 100_000.0),
                StrikeOverview(100.0, 0, 0, 0, 0, 310_000.0, -210_000.0, 100_000.0),
            ),
        )
        charm_rows = (
            CharmStrikeOverview(99.0, 1_000.0, -950.0, 50.0),
            CharmStrikeOverview(100.0, 1_100.0, -1_000.0, 100.0),
        )

        plan = build_intraday_trade_plan(analysis, make_panel(vix=23.0), charm_rows)

        self.assertEqual(plan.bias_bucket_label, "Bullish Lean")
        self.assertIn("confirmation", plan.preferred_trade.lower())
        self.assertIn("lean", plan.trust_guidance.lower())
        self.assertTrue(plan.notes[0].startswith("Bias bucket Bullish Lean"))

    def test_cross_symbol_conviction_boosts_trade_plan(self) -> None:
        analysis = make_analysis(
            label="bullish",
            score=20.0,
            confidence=66.0,
            strike_rows=(
                StrikeOverview(99.0, 0, 0, 0, 0, 700_000.0, -150_000.0, 550_000.0),
                StrikeOverview(100.0, 0, 0, 0, 0, 680_000.0, -140_000.0, 540_000.0),
            ),
        )
        charm_rows = (
            CharmStrikeOverview(99.0, 20_000.0, -6_000.0, 14_000.0),
            CharmStrikeOverview(100.0, 18_000.0, -5_000.0, 13_000.0),
        )
        conviction_contexts = (
            ConvictionContext(
                symbol="SPY",
                present_metrics=4,
                available_metrics=4,
                neutral_metrics=0,
                aligned_metrics=4,
                opposing_metrics=0,
                supporting_metrics=("GEX", "Charm", "Call/Put Volume", "Call/Put OI"),
                conflicting_metrics=tuple(),
                neutral_metric_names=tuple(),
                unavailable_metrics=tuple(),
            ),
            ConvictionContext(
                symbol="QQQ",
                present_metrics=4,
                available_metrics=4,
                neutral_metrics=0,
                aligned_metrics=3,
                opposing_metrics=1,
                supporting_metrics=("GEX", "Charm", "Call/Put OI"),
                conflicting_metrics=("Call/Put Volume",),
                neutral_metric_names=tuple(),
                unavailable_metrics=tuple(),
            ),
        )

        plan = build_intraday_trade_plan(
            analysis,
            make_panel(vix=19.0),
            charm_rows,
            conviction_contexts=conviction_contexts,
        )

        self.assertEqual(plan.conviction_label, "Strong")
        self.assertIn("aligned inputs", plan.conviction_note)
        self.assertIn("strong", plan.trust_guidance.lower())
        self.assertTrue(any(note.startswith("SPY: 4/4 aligned") for note in plan.notes))


if __name__ == "__main__":
    unittest.main()
