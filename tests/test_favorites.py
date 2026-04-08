from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_bias_dashboard.favorites import build_favorite_quotes, load_favorite_symbols, save_favorite_symbols, toggle_favorite_symbol


class FavoritesTests(unittest.TestCase):
    def test_save_load_and_toggle_favorites(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "favorites.json"

            saved = save_favorite_symbols(["spy", "qqq", "SPY"], path)
            self.assertEqual(saved, ("SPY", "QQQ"))
            self.assertEqual(load_favorite_symbols(path), ("SPY", "QQQ"))

            toggled = toggle_favorite_symbol("QQQ", path)
            self.assertEqual(toggled, ("SPY",))

            toggled = toggle_favorite_symbol("iwm", path)
            self.assertEqual(toggled, ("SPY", "IWM"))

    def test_build_favorite_quotes_prefers_price_minus_close(self) -> None:
        payload = {
            "SPY": {
                "quote": {
                    "lastPrice": 645.12,
                    "closePrice": 640.00,
                    "netChange": 99.0,
                    "percentChange": 99.0,
                }
            },
            "QQQ": {
                "quote": {
                    "mark": 540.00,
                    "closePrice": 542.00,
                }
            },
        }

        quotes = build_favorite_quotes(["spy", "qqq", "iwm"], payload)

        self.assertEqual(quotes[0].symbol, "SPY")
        self.assertAlmostEqual(quotes[0].change or 0.0, 5.12)
        self.assertAlmostEqual(quotes[0].percent_change or 0.0, 5.12 / 640.00)
        self.assertAlmostEqual(quotes[1].change or 0.0, -2.0)
        self.assertAlmostEqual(quotes[2].price or 0.0, 0.0)
        self.assertIsNone(quotes[2].change)


if __name__ == "__main__":
    unittest.main()
