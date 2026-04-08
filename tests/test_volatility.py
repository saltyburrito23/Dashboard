from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_bias_dashboard.models import OptionContract
from options_bias_dashboard.volatility import (
    ET,
    find_atm_straddle,
    intraday_acceleration_multiplier,
    parse_batch_prev_closes,
    parse_batch_quotes,
    resolve_sigma_base,
    session_context,
    time_to_expiry_years,
    build_volatility_panel,
)


class VolatilityMathTests(unittest.TestCase):
    def test_resolve_sigma_base_prefers_vix1d(self) -> None:
        s, label = resolve_sigma_base(vix=20.0, vix1d=18.5)
        self.assertAlmostEqual(s, 0.185)
        self.assertIn("VIX1D", label)

    def test_resolve_sigma_base_vix_fallback(self) -> None:
        s, label = resolve_sigma_base(vix=20.0, vix1d=None, vix_odte_multiplier=1.15)
        self.assertAlmostEqual(s, 0.23)
        self.assertIn("1.15", label)

    def test_accel_at_full_session_is_one(self) -> None:
        self.assertAlmostEqual(intraday_acceleration_multiplier(6.5), 1.0, places=6)

    def test_accel_increases_into_close(self) -> None:
        early = intraday_acceleration_multiplier(6.5)
        late = intraday_acceleration_multiplier(1.0)
        self.assertGreater(late, early)
        self.assertLessEqual(late, 1.8)

    def test_time_to_expiry_open(self) -> None:
        T = time_to_expiry_years(6.5)
        self.assertAlmostEqual(T, 1.0 / 252.0, places=9)

    def test_parse_batch_quotes(self) -> None:
        payload = {
            "$VIX": {"quote": {"lastPrice": 18.2}},
            "$VIX1D": {"quote": {"mark": 17.5}},
        }
        m = parse_batch_quotes(payload)
        self.assertAlmostEqual(m["VIX"], 18.2)
        self.assertAlmostEqual(m["VIX1D"], 17.5)

    def test_parse_batch_prev_closes(self) -> None:
        payload = {
            "$VIX": {"quote": {"closePrice": 19.1}},
            "$VIX1D": {"quote": {"previousClose": 18.4}},
        }
        m = parse_batch_prev_closes(payload)
        self.assertAlmostEqual(m["VIX"], 19.1)
        self.assertAlmostEqual(m["VIX1D"], 18.4)

    def test_session_context_weekend_uses_full_hours(self) -> None:
        sat = datetime(2026, 3, 21, 12, 0, tzinfo=ET)
        h, rth, d = session_context(sat)
        self.assertFalse(rth)
        self.assertAlmostEqual(h, 6.5)
        self.assertEqual(d, date(2026, 3, 21))

    def test_find_atm_straddle(self) -> None:
        exp = date(2026, 3, 28)
        contracts = [
            OptionContract(
                "SPY 100C", "call", 100.0, exp, 7, 1.0, 1.1, 1.05, 0, 0, 0.2, 0.5, 0, 0, 0, False
            ),
            OptionContract(
                "SPY 100P", "put", 100.0, exp, 7, 0.9, 1.0, 0.95, 0, 0, 0.2, -0.5, 0, 0, 0, False
            ),
            OptionContract(
                "SPY 105C", "call", 105.0, exp, 7, 0.5, 0.6, 0.55, 0, 0, 0.2, 0.3, 0, 0, 0, False
            ),
        ]
        row = find_atm_straddle(contracts, spot=100.25, min_dte=1, max_dte=30)
        assert row is not None
        self.assertEqual(row.strike, 100.0)
        self.assertAlmostEqual(row.straddle_mid, 1.05 + 0.95)

    def test_build_volatility_panel_daily_move_matches_sqrt252(self) -> None:
        exp = date(2026, 3, 28)
        contracts = [
            OptionContract(
                "SPY 100C", "call", 100.0, exp, 7, 1.0, 1.1, 1.05, 0, 0, 0.2, 0.5, 0, 0, 0, False
            ),
            OptionContract(
                "SPY 100P", "put", 100.0, exp, 7, 0.9, 1.0, 0.95, 0, 0, 0.2, -0.5, 0, 0, 0, False
            ),
        ]
        daily = {
            "candles": [
                {"datetime": "2026-03-10T00:00:00Z", "high": 102, "low": 100, "close": 101},
                {"datetime": "2026-03-11T00:00:00Z", "high": 103, "low": 101, "close": 102},
                {"datetime": "2026-03-12T00:00:00Z", "high": 104, "low": 102, "close": 103},
                {"datetime": "2026-03-13T00:00:00Z", "high": 105, "low": 103, "close": 104},
                {"datetime": "2026-03-14T00:00:00Z", "high": 106, "low": 104, "close": 105},
            ]
        }
        now = datetime(2026, 3, 15, 14, 0, 0, tzinfo=ET)
        panel = build_volatility_panel(
            spot=648.57,
            vix=18.2,
            vix1d=None,
            contracts=contracts,
            min_dte=1,
            max_dte=30,
            daily_history=daily,
            now=now,
            vix_odte_multiplier=1.0,
        )
        sigma_annual = 0.182
        sigma_daily = sigma_annual / (252**0.5)
        self.assertAlmostEqual(panel.sigma_base, sigma_annual, places=6)
        self.assertAlmostEqual(panel.sigma_daily_close, sigma_daily, places=6)
        self.assertAlmostEqual(panel.dollar_daily_1sigma, 648.57 * sigma_daily * panel.dow_multiplier * panel.cluster_multiplier, places=4)


if __name__ == "__main__":
    unittest.main()
