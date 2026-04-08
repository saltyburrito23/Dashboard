from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_bias_dashboard.models import OptionContract
from options_bias_dashboard.streaming import StreamStateStore, _quote_relative_score, select_stream_contracts


def contract(
    *,
    symbol: str,
    option_type: str,
    strike: float,
    volume: int,
    open_interest: int,
    dte: int,
    bid: float = 1.0,
    ask: float = 1.1,
) -> OptionContract:
    return OptionContract(
        symbol=symbol,
        option_type=option_type,
        strike=strike,
        expiration_date=date(2026, 3, 27),
        days_to_expiration=dte,
        bid=bid,
        ask=ask,
        mark=(bid + ask) / 2.0,
        total_volume=volume,
        open_interest=open_interest,
        implied_volatility=0.24,
        delta=0.50 if option_type == "call" else -0.50,
        gamma=0.05,
        theta=-0.10,
        vega=0.12,
        in_the_money=False,
    )


class SelectStreamContractsTests(unittest.TestCase):
    def test_prefers_front_expiry_and_balanced_calls_puts(self) -> None:
        contracts = [
            contract(symbol="LATE_CALL", option_type="call", strike=100, volume=900, open_interest=900, dte=1),
            contract(symbol="CLOSE_CALL", option_type="call", strike=100, volume=600, open_interest=700, dte=0),
            contract(symbol="CLOSE_PUT", option_type="put", strike=100, volume=550, open_interest=680, dte=0),
            contract(symbol="OTM_CALL", option_type="call", strike=102, volume=300, open_interest=400, dte=0),
            contract(symbol="OTM_PUT", option_type="put", strike=98, volume=310, open_interest=420, dte=0),
        ]

        selected = select_stream_contracts(contracts, 100.0, limit=4)

        self.assertEqual([item.days_to_expiration for item in selected], [0, 0, 0, 0])
        self.assertEqual(selected[0].symbol, "CLOSE_CALL")
        self.assertEqual(selected[1].symbol, "CLOSE_PUT")
        self.assertEqual(len(selected), 4)


class StreamStateStoreTests(unittest.TestCase):
    def test_merges_partial_option_updates(self) -> None:
        store = StreamStateStore()
        store.remember_subscriptions("SPY", ("SPY_OPT",))

        store.apply_message(
            json.dumps(
                {
                    "data": [
                        {
                            "service": "LEVELONE_OPTIONS",
                            "timestamp": 1_710_000_000_000,
                            "content": [
                                {
                                    "key": "SPY_OPT",
                                    "2": 1.10,
                                    "3": 1.20,
                                    "4": 1.15,
                                    "8": 1200,
                                    "9": 800,
                                    "37": 1.16,
                                }
                            ],
                        }
                    ]
                }
            )
        )
        store.apply_message(
            json.dumps(
                {
                    "data": [
                        {
                            "service": "LEVELONE_OPTIONS",
                            "timestamp": 1_710_000_030_000,
                            "content": [
                                {
                                    "key": "SPY_OPT",
                                    "8": 1400,
                                }
                            ],
                        }
                    ]
                }
            )
        )

        snapshot = store.snapshot(active=True)

        self.assertEqual(snapshot.status_label, "live")
        self.assertEqual(len(snapshot.option_quotes), 1)
        quote = snapshot.option_quotes[0]
        self.assertAlmostEqual(quote.bid or 0.0, 1.10)
        self.assertAlmostEqual(quote.ask or 0.0, 1.20)
        self.assertAlmostEqual(quote.last or 0.0, 1.15)
        self.assertAlmostEqual(quote.mark or 0.0, 1.16)
        self.assertEqual(quote.volume, 1400)
        self.assertEqual(quote.open_interest, 800)

    def test_tracks_underlying_equity_quote(self) -> None:
        store = StreamStateStore()
        store.remember_subscriptions("SPY", ())

        store.apply_message(
            json.dumps(
                {
                    "data": [
                        {
                            "service": "LEVELONE_EQUITIES",
                            "timestamp": 1_710_000_000_000,
                            "content": [
                                {
                                    "key": "SPY",
                                    "1": 510.10,
                                    "2": 510.14,
                                    "3": 510.12,
                                    "8": 250000,
                                }
                            ],
                        }
                    ]
                }
            )
        )

        snapshot = store.snapshot(active=True)

        self.assertIsNotNone(snapshot.underlying_quote)
        self.assertAlmostEqual(snapshot.underlying_quote.bid or 0.0, 510.10)
        self.assertAlmostEqual(snapshot.underlying_quote.ask or 0.0, 510.14)
        self.assertAlmostEqual(snapshot.underlying_quote.last or 0.0, 510.12)
        self.assertAlmostEqual(snapshot.underlying_quote.mark or 0.0, 510.12)
        self.assertEqual(snapshot.underlying_quote.volume, 250000)

    def test_flow_series_accumulates_call_and_put_premium_and_net_volume(self) -> None:
        store = StreamStateStore()
        store.remember_subscriptions(
            "SPY",
            (
                contract(symbol="CALL_100", option_type="call", strike=100, volume=100, open_interest=500, dte=0),
                contract(symbol="PUT_100", option_type="put", strike=100, volume=200, open_interest=550, dte=0),
            ),
        )

        store.apply_message(
            json.dumps(
                {
                    "data": [
                        {
                            "service": "LEVELONE_OPTIONS",
                            "timestamp": "2026-03-22T14:30:00+00:00",
                            "content": [
                                {"key": "CALL_100", "2": 2.00, "3": 2.20, "4": 2.20, "8": 100, "9": 500},
                                {"key": "PUT_100", "2": 1.80, "3": 2.00, "4": 2.00, "8": 200, "9": 550},
                            ],
                        }
                    ]
                }
            )
        )
        store.apply_message(
            json.dumps(
                {
                    "data": [
                        {
                            "service": "LEVELONE_OPTIONS",
                            "timestamp": "2026-03-22T14:31:00+00:00",
                            "content": [
                                {"key": "CALL_100", "2": 2.00, "3": 2.20, "4": 2.20, "8": 110},
                                {"key": "PUT_100", "2": 1.80, "3": 2.00, "4": 2.00, "8": 205},
                            ],
                        }
                    ]
                }
            )
        )

        points = store.flow_series(5)

        self.assertEqual(len(points), 1)
        point = points[0]
        self.assertAlmostEqual(point.call_volume, 10.0)
        self.assertAlmostEqual(point.put_volume, 5.0)
        self.assertAlmostEqual(point.net_volume, 5.0)
        self.assertAlmostEqual(point.call_premium, 2200.0)
        self.assertAlmostEqual(point.put_premium, 1000.0)


class FlowClassificationTests(unittest.TestCase):
    def test_quote_relative_score_is_directional_inside_spread(self) -> None:
        self.assertGreater(_quote_relative_score(2.20, 2.00, 2.20), 0.95)
        self.assertLess(_quote_relative_score(2.00, 2.00, 2.20), -0.95)
        self.assertAlmostEqual(_quote_relative_score(2.10, 2.00, 2.20), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
