from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_bias_dashboard.normalization import (
    extract_intraday_range,
    extract_premarket_range,
    extract_price_bars,
    extract_price_closes,
    extract_regular_session_closes,
    normalize_option_chain,
)


class NormalizationTests(unittest.TestCase):
    def test_normalize_option_chain_drops_invalid_iv_sentinels(self) -> None:
        chain = {
            "callExpDateMap": {
                "2026-03-22:0": {
                    "500": [
                        {
                            "symbol": "TESTC",
                            "strikePrice": 500.0,
                            "daysToExpiration": 0,
                            "volatility": -999.0,
                        }
                    ]
                }
            },
            "putExpDateMap": {
                "2026-03-22:0": {
                    "500": [
                        {
                            "symbol": "TESTP",
                            "strikePrice": 500.0,
                            "daysToExpiration": 0,
                            "volatility": 24.5,
                        }
                    ]
                }
            },
        }

        contracts = normalize_option_chain(chain)

        self.assertEqual(len(contracts), 2)
        self.assertIsNone(contracts[0].implied_volatility)
        self.assertAlmostEqual(contracts[1].implied_volatility or 0.0, 0.245)

    def test_extract_price_bars_parses_intraday_timestamps(self) -> None:
        history = {
            "candles": [
                {
                    "datetime": 1711391100000,
                    "open": 520.0,
                    "high": 521.0,
                    "low": 519.5,
                    "close": 520.5,
                    "volume": 1000,
                },
                {
                    "datetime": "2026-03-22T14:40:00Z",
                    "open": 520.5,
                    "high": 521.2,
                    "low": 520.1,
                    "close": 521.0,
                    "volume": 1200,
                },
            ]
        }

        bars = extract_price_bars(history)

        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0]["time"].tzinfo is not None, True)
        self.assertAlmostEqual(bars[0]["close"], 520.5)
        self.assertAlmostEqual(bars[1]["open"], 520.5)
        self.assertEqual(extract_price_closes(history), [520.5, 521.0])

    def test_regular_session_and_premarket_extractors_split_extended_hours(self) -> None:
        history = {
            "candles": [
                {"datetime": "2026-03-29T08:00:00-04:00", "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 500},
                {"datetime": "2026-03-29T09:25:00-04:00", "open": 100.5, "high": 102.0, "low": 100.2, "close": 101.8, "volume": 700},
                {"datetime": "2026-03-29T09:35:00-04:00", "open": 101.8, "high": 102.2, "low": 101.1, "close": 101.4, "volume": 1000},
                {"datetime": "2026-03-29T10:00:00-04:00", "open": 101.4, "high": 102.8, "low": 100.9, "close": 102.5, "volume": 1200},
            ]
        }

        self.assertEqual(extract_regular_session_closes(history), [101.4, 102.5])
        self.assertEqual(extract_premarket_range(history), (99.5, 102.0))
        session_high, session_low, session_range = extract_intraday_range(history)
        self.assertEqual((session_high, session_low), (102.8, 100.9))
        self.assertAlmostEqual(session_range or 0.0, 1.9)


if __name__ == "__main__":
    unittest.main()
