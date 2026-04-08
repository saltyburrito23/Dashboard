from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_bias_dashboard.analytics import analyze_chain, build_charm_strike_overview, build_gex_expiry_overview, contract_charm_exposure, rank_relevant_contracts
from options_bias_dashboard.models import OptionContract


def contract(
    *,
    option_type: str,
    strike: float,
    volume: int,
    open_interest: int,
    delta: float,
    gamma: float,
    iv: float | None,
    mark: float = 2.0,
    bid: float = 1.9,
    ask: float = 2.1,
    dte: int = 7,
) -> OptionContract:
    return OptionContract(
        symbol=f"TEST {option_type.upper()} {strike}",
        option_type=option_type,
        strike=strike,
        expiration_date=date(2026, 3, 27),
        days_to_expiration=dte,
        bid=bid,
        ask=ask,
        mark=mark,
        total_volume=volume,
        open_interest=open_interest,
        implied_volatility=iv,
        delta=delta,
        gamma=gamma,
        theta=-0.10,
        vega=0.12,
        in_the_money=False,
    )


class AnalyzeChainTests(unittest.TestCase):
    def test_bullish_chain_scores_positive(self) -> None:
        contracts = [
            contract(option_type="call", strike=99, volume=500, open_interest=1800, delta=0.55, gamma=0.08, iv=0.28),
            contract(option_type="call", strike=100, volume=700, open_interest=2200, delta=0.50, gamma=0.10, iv=0.29),
            contract(option_type="call", strike=101, volume=450, open_interest=1700, delta=0.45, gamma=0.09, iv=0.30),
            contract(option_type="put", strike=99, volume=160, open_interest=900, delta=-0.45, gamma=0.05, iv=0.23),
            contract(option_type="put", strike=100, volume=150, open_interest=850, delta=-0.50, gamma=0.04, iv=0.22),
            contract(option_type="put", strike=101, volume=140, open_interest=800, delta=-0.55, gamma=0.04, iv=0.21),
        ]

        analysis = analyze_chain(contracts, underlying_price=100.0, price_closes=[99.2, 99.8, 100.7])

        self.assertEqual(analysis.result.label, "bullish")
        self.assertGreater(analysis.result.score, 0)
        self.assertGreater(analysis.result.confidence, 50)

    def test_bearish_chain_scores_negative(self) -> None:
        contracts = [
            contract(option_type="call", strike=99, volume=120, open_interest=800, delta=0.55, gamma=0.04, iv=0.20),
            contract(option_type="call", strike=100, volume=100, open_interest=700, delta=0.50, gamma=0.04, iv=0.20),
            contract(option_type="call", strike=101, volume=90, open_interest=650, delta=0.45, gamma=0.03, iv=0.21),
            contract(option_type="put", strike=99, volume=420, open_interest=2000, delta=-0.45, gamma=0.08, iv=0.29),
            contract(option_type="put", strike=100, volume=500, open_interest=2400, delta=-0.50, gamma=0.09, iv=0.30),
            contract(option_type="put", strike=101, volume=460, open_interest=2100, delta=-0.55, gamma=0.08, iv=0.31),
        ]

        analysis = analyze_chain(contracts, underlying_price=100.0, price_closes=[101.0, 100.2, 99.1])

        self.assertEqual(analysis.result.label, "bearish")
        self.assertLess(analysis.result.score, 0)
        self.assertGreater(analysis.metrics.put_call_volume_ratio or 0, 1.0)

    def test_balanced_chain_returns_mixed(self) -> None:
        contracts = [
            contract(option_type="call", strike=99, volume=200, open_interest=1000, delta=0.55, gamma=0.05, iv=0.24),
            contract(option_type="call", strike=100, volume=220, open_interest=1100, delta=0.50, gamma=0.05, iv=0.24),
            contract(option_type="put", strike=99, volume=210, open_interest=1080, delta=-0.45, gamma=0.05, iv=0.24),
            contract(option_type="put", strike=100, volume=205, open_interest=1110, delta=-0.50, gamma=0.05, iv=0.24),
        ]

        analysis = analyze_chain(contracts, underlying_price=100.0, price_closes=[100.0, 100.1, 99.9])

        self.assertEqual(analysis.result.label, "mixed")
        self.assertLess(abs(analysis.result.score), 18)

    def test_strike_overview_uses_gex_notional(self) -> None:
        contracts = [
            contract(option_type="call", strike=100, volume=200, open_interest=1000, delta=0.50, gamma=0.10, iv=0.24),
            contract(option_type="put", strike=100, volume=220, open_interest=500, delta=-0.50, gamma=0.08, iv=0.24),
            contract(option_type="call", strike=101, volume=180, open_interest=600, delta=0.45, gamma=0.06, iv=0.24),
            contract(option_type="put", strike=99, volume=190, open_interest=550, delta=-0.45, gamma=0.07, iv=0.24),
        ]

        analysis = analyze_chain(contracts, underlying_price=100.0, price_closes=[100.0, 100.2, 100.1])

        strike_100 = next(row for row in analysis.strike_overview if row.strike == 100)
        self.assertAlmostEqual(strike_100.call_gex, 1_000_000.0)
        self.assertAlmostEqual(strike_100.put_gex, -400_000.0)
        self.assertAlmostEqual(strike_100.net_gex, 600_000.0)
        self.assertEqual(analysis.metrics.gex_peak, 100.0)

    def test_gex_flip_falls_back_to_nearest_neutral_strike(self) -> None:
        contracts = [
            contract(option_type="call", strike=99, volume=180, open_interest=300, delta=0.55, gamma=0.03, iv=0.24),
            contract(option_type="call", strike=100, volume=220, open_interest=150, delta=0.50, gamma=0.02, iv=0.24),
            contract(option_type="call", strike=101, volume=200, open_interest=500, delta=0.45, gamma=0.06, iv=0.24),
        ]

        analysis = analyze_chain(contracts, underlying_price=100.0, price_closes=[100.0, 100.2, 100.1], strike_window=3)

        self.assertEqual(analysis.metrics.gex_flip_estimate, 100.0)

    def test_build_gex_expiry_overview_aggregates_by_expiration(self) -> None:
        contracts = [
            contract(option_type="call", strike=100, volume=200, open_interest=1000, delta=0.50, gamma=0.10, iv=0.24, dte=0),
            contract(option_type="put", strike=100, volume=220, open_interest=500, delta=-0.50, gamma=0.08, iv=0.24, dte=0),
            OptionContract(
                symbol="TEST CALL 101 LATER",
                option_type="call",
                strike=101.0,
                expiration_date=date(2026, 3, 30),
                days_to_expiration=3,
                bid=1.9,
                ask=2.1,
                mark=2.0,
                total_volume=180,
                open_interest=600,
                implied_volatility=0.24,
                delta=0.45,
                gamma=0.06,
                theta=-0.10,
                vega=0.12,
                in_the_money=False,
            ),
        ]

        rows = build_gex_expiry_overview(contracts, 100.0)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].days_to_expiration, 0)
        self.assertAlmostEqual(rows[0].call_gex, 1_000_000.0)
        self.assertAlmostEqual(rows[0].put_gex, -400_000.0)
        self.assertAlmostEqual(rows[0].net_gex, 600_000.0)
        self.assertEqual(rows[1].days_to_expiration, 3)
        self.assertAlmostEqual(rows[1].call_gex, 360_000.0)

    def test_rank_relevant_contracts_prefers_front_expiry_and_atm(self) -> None:
        contracts = [
            contract(option_type="call", strike=100, volume=400, open_interest=500, delta=0.50, gamma=0.05, iv=0.24, dte=1),
            contract(option_type="call", strike=101, volume=1200, open_interest=1500, delta=0.42, gamma=0.06, iv=0.24, dte=0),
            contract(option_type="put", strike=100, volume=350, open_interest=450, delta=-0.50, gamma=0.05, iv=0.24, dte=0),
        ]

        ranked = rank_relevant_contracts(contracts, 100.0, limit=3)

        self.assertEqual(ranked[0].days_to_expiration, 0)
        self.assertEqual(ranked[0].strike, 100.0)
        self.assertEqual(ranked[1].days_to_expiration, 0)
        self.assertEqual(ranked[2].days_to_expiration, 1)

    def test_contract_charm_exposure_is_stronger_near_expiry(self) -> None:
        near = contract(option_type="call", strike=100, volume=300, open_interest=500, delta=0.50, gamma=0.05, iv=0.22, dte=0)
        later = contract(option_type="call", strike=100, volume=300, open_interest=500, delta=0.50, gamma=0.05, iv=0.22, dte=7)

        near_charm = contract_charm_exposure(100.0, near, hours_remaining=2.0)
        later_charm = contract_charm_exposure(100.0, later, hours_remaining=2.0)

        self.assertGreater(abs(near_charm), abs(later_charm))

    def test_build_charm_strike_overview_aggregates_by_strike(self) -> None:
        contracts = [
            contract(option_type="call", strike=100, volume=250, open_interest=400, delta=0.50, gamma=0.05, iv=0.22, dte=0),
            contract(option_type="put", strike=100, volume=240, open_interest=350, delta=-0.50, gamma=0.05, iv=0.22, dte=0),
        ]

        rows = build_charm_strike_overview(contracts, 100.0, hours_remaining=2.0)

        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].net_charm, rows[0].call_charm + rows[0].put_charm)

    def test_average_iv_ignores_missing_contract_ivs(self) -> None:
        contracts = [
            contract(option_type="call", strike=99, volume=300, open_interest=600, delta=0.55, gamma=0.05, iv=0.28),
            contract(option_type="call", strike=100, volume=320, open_interest=650, delta=0.50, gamma=0.05, iv=None),
            contract(option_type="put", strike=99, volume=280, open_interest=580, delta=-0.45, gamma=0.05, iv=0.31),
            contract(option_type="put", strike=100, volume=290, open_interest=590, delta=-0.50, gamma=0.05, iv=None),
        ]

        analysis = analyze_chain(contracts, underlying_price=100.0, price_closes=[99.8, 100.0, 100.1], strike_window=4)

        self.assertAlmostEqual(analysis.metrics.average_call_iv or 0.0, 0.28)
        self.assertAlmostEqual(analysis.metrics.average_put_iv or 0.0, 0.31)
        self.assertTrue(analysis.metrics.iv_skew_available)

    def test_mirrored_source_iv_marks_skew_unavailable(self) -> None:
        contracts = [
            contract(option_type="call", strike=99, volume=300, open_interest=600, delta=0.55, gamma=0.05, iv=0.28),
            contract(option_type="put", strike=99, volume=280, open_interest=580, delta=-0.45, gamma=0.05, iv=0.28),
            contract(option_type="call", strike=100, volume=320, open_interest=650, delta=0.50, gamma=0.05, iv=0.29),
            contract(option_type="put", strike=100, volume=290, open_interest=590, delta=-0.50, gamma=0.05, iv=0.29),
        ]

        analysis = analyze_chain(contracts, underlying_price=100.0, price_closes=[99.8, 100.0, 100.1], strike_window=4)

        self.assertFalse(analysis.metrics.iv_skew_available)
        iv_skew_component = next(component for component in analysis.result.components if component.name == "IV Skew")
        self.assertEqual(iv_skew_component.score, 0.0)
        self.assertIn("unavailable", iv_skew_component.detail.lower())

    def test_gamma_asymmetry_and_gex_volume_scores_are_populated(self) -> None:
        contracts = [
            contract(option_type="call", strike=99, volume=450, open_interest=1800, delta=0.55, gamma=0.09, iv=0.28),
            contract(option_type="call", strike=100, volume=520, open_interest=2200, delta=0.50, gamma=0.11, iv=0.29),
            contract(option_type="put", strike=99, volume=120, open_interest=700, delta=-0.45, gamma=0.03, iv=0.22),
            contract(option_type="put", strike=100, volume=140, open_interest=800, delta=-0.50, gamma=0.04, iv=0.23),
        ]

        analysis = analyze_chain(contracts, underlying_price=100.0, price_closes=[99.7, 100.1, 100.5], strike_window=4)

        self.assertIsNotNone(analysis.metrics.gamma_asymmetry_score)
        self.assertIsNotNone(analysis.metrics.gex_vol_score)
        self.assertGreater(analysis.metrics.gamma_asymmetry_score or 0.0, 0.0)
        self.assertGreater(analysis.metrics.gex_vol_score or 0.0, 0.0)
        component_names = {component.name for component in analysis.result.components}
        self.assertIn("Gamma Asymmetry", component_names)
        self.assertIn("GEX / Volume", component_names)

    def test_prev_day_metrics_are_scored_when_previous_session_bar_is_available(self) -> None:
        contracts = [
            contract(option_type="call", strike=99, volume=320, open_interest=1200, delta=0.55, gamma=0.07, iv=0.27),
            contract(option_type="call", strike=100, volume=340, open_interest=1300, delta=0.50, gamma=0.08, iv=0.28),
            contract(option_type="put", strike=99, volume=200, open_interest=900, delta=-0.45, gamma=0.05, iv=0.24),
            contract(option_type="put", strike=100, volume=180, open_interest=850, delta=-0.50, gamma=0.05, iv=0.24),
        ]

        analysis = analyze_chain(
            contracts,
            underlying_price=101.0,
            price_closes=[100.2, 100.6, 101.0],
            previous_session_bar={
                "time": date(2026, 3, 26),
                "open": 98.8,
                "high": 100.0,
                "low": 97.9,
                "close": 99.6,
            },
            strike_window=4,
        )

        self.assertIsNotNone(analysis.metrics.prev_day_score)
        self.assertIsNotNone(analysis.metrics.prev_day_gap_pct)
        self.assertIsNotNone(analysis.metrics.prev_day_range_pct)
        self.assertGreater(analysis.metrics.prev_day_score or 0.0, 0.0)
        self.assertGreater(analysis.metrics.prev_day_gap_pct or 0.0, 0.0)
        prev_day_component = next(component for component in analysis.result.components if component.name == "Prev-Day Structure")
        self.assertGreater(prev_day_component.score, 0.0)
        self.assertIn("gap", prev_day_component.detail.lower())


if __name__ == "__main__":
    unittest.main()
