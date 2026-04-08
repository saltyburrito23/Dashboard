from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_bias_dashboard.conviction import build_conviction_context, summarize_conviction
from options_bias_dashboard.models import (
    AnalysisSnapshot,
    BiasComponent,
    BiasResult,
    ChainMetrics,
    CharmStrikeOverview,
    ConvictionContext,
    StrikeOverview,
)


def make_analysis(
    *,
    call_volume: int,
    put_volume: int,
    call_open_interest: int,
    put_open_interest: int,
    strike_rows: tuple[StrikeOverview, ...],
) -> AnalysisSnapshot:
    return AnalysisSnapshot(
        result=BiasResult(
            label="bullish",
            score=22.0,
            confidence=70.0,
            components=(BiasComponent(name="test", score=0.0, weight=1.0, detail=""),),
        ),
        metrics=ChainMetrics(
            underlying_price=100.0,
            total_contract_count=8,
            filtered_contract_count=8,
            call_volume=call_volume,
            put_volume=put_volume,
            call_open_interest=call_open_interest,
            put_open_interest=put_open_interest,
            put_call_volume_ratio=put_volume / call_volume if call_volume else None,
            put_call_open_interest_ratio=put_open_interest / call_open_interest if call_open_interest else None,
            average_call_iv=0.20,
            average_put_iv=0.21,
            iv_skew_available=True,
            average_spread_pct=0.02,
            liquidity_score=0.9,
            expected_move=4.0,
            call_wall=101.0,
            put_wall=99.0,
            gex_peak=100.0,
            gex_flip_estimate=100.0,
        ),
        filtered_contracts=tuple(),
        strike_overview=strike_rows,
    )


class ConvictionTests(unittest.TestCase):
    def test_build_conviction_context_counts_aligned_inputs(self) -> None:
        analysis = make_analysis(
            call_volume=1200,
            put_volume=700,
            call_open_interest=6000,
            put_open_interest=4200,
            strike_rows=(
                StrikeOverview(99.0, 0, 0, 0, 0, 900_000.0, -200_000.0, 700_000.0),
                StrikeOverview(100.0, 0, 0, 0, 0, 850_000.0, -150_000.0, 700_000.0),
            ),
        )
        charm_rows = (
            CharmStrikeOverview(99.0, 15_000.0, -3_000.0, 12_000.0),
            CharmStrikeOverview(100.0, 18_000.0, -4_000.0, 14_000.0),
        )

        context = build_conviction_context("SPY", analysis, charm_rows, primary_bias_label="bullish")

        self.assertEqual(context.present_metrics, 4)
        self.assertEqual(context.available_metrics, 4)
        self.assertEqual(context.neutral_metrics, 0)
        self.assertEqual(context.aligned_metrics, 4)
        self.assertEqual(context.opposing_metrics, 0)
        self.assertEqual(context.supporting_metrics, ("GEX", "Call/Put Volume", "Call/Put OI", "Charm"))
        self.assertEqual(context.conflicting_metrics, tuple())
        self.assertEqual(context.neutral_metric_names, tuple())
        self.assertEqual(context.unavailable_metrics, tuple())

    def test_build_conviction_context_handles_missing_data(self) -> None:
        context = build_conviction_context("USO", None, tuple(), primary_bias_label="bullish")

        self.assertEqual(context.present_metrics, 0)
        self.assertEqual(context.available_metrics, 0)
        self.assertEqual(context.neutral_metrics, 0)
        self.assertEqual(context.aligned_metrics, 0)
        self.assertEqual(
            context.unavailable_metrics,
            ("GEX", "Call/Put Volume", "Call/Put OI", "Charm"),
        )

    def test_build_conviction_context_marks_neutral_inputs_as_present(self) -> None:
        analysis = make_analysis(
            call_volume=1000,
            put_volume=980,
            call_open_interest=5000,
            put_open_interest=4900,
            strike_rows=(
                StrikeOverview(99.0, 0, 0, 0, 0, 600_000.0, -570_000.0, 30_000.0),
                StrikeOverview(100.0, 0, 0, 0, 0, 620_000.0, -600_000.0, 20_000.0),
            ),
        )
        charm_rows = (
            CharmStrikeOverview(99.0, 10_000.0, -9_800.0, 200.0),
            CharmStrikeOverview(100.0, 10_100.0, -10_000.0, 100.0),
        )

        context = build_conviction_context("SPY", analysis, charm_rows, primary_bias_label="bullish")

        self.assertEqual(context.present_metrics, 4)
        self.assertEqual(context.available_metrics, 0)
        self.assertEqual(context.neutral_metrics, 4)
        self.assertEqual(
            context.neutral_metric_names,
            ("GEX", "Call/Put Volume", "Call/Put OI", "Charm"),
        )
        self.assertEqual(context.unavailable_metrics, tuple())

    def test_summarize_conviction_labels_missing_basket(self) -> None:
        contexts = (
            build_conviction_context("SPY", None, tuple(), primary_bias_label="bearish"),
            build_conviction_context("QQQ", None, tuple(), primary_bias_label="bearish"),
        )

        label, note = summarize_conviction(contexts)

        self.assertEqual(label, "N/A")
        self.assertIn("unavailable", note.lower())

    def test_summarize_conviction_labels_neutral_basket(self) -> None:
        contexts = (
            ConvictionContext(
                symbol="SPY",
                present_metrics=4,
                available_metrics=0,
                neutral_metrics=4,
                aligned_metrics=0,
                opposing_metrics=0,
                supporting_metrics=tuple(),
                conflicting_metrics=tuple(),
                neutral_metric_names=("GEX", "Call/Put Volume", "Call/Put OI", "Charm"),
                unavailable_metrics=tuple(),
            ),
        )

        label, note = summarize_conviction(contexts)

        self.assertEqual(label, "Mixed")
        self.assertIn("neutral", note.lower())


if __name__ == "__main__":
    unittest.main()
