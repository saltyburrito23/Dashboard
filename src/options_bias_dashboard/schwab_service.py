from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .app_config import Settings, load_settings
from .normalization import extract_underlying_price


class SchwabApiError(RuntimeError):
    """Raised when Schwab returns an unsuccessful response."""


@dataclass
class SchwabResearchClient:
    settings: Settings | None = None

    def __post_init__(self) -> None:
        self.settings = self.settings or load_settings()
        try:
            import schwabdev
        except ImportError as error:
            raise RuntimeError(
                "The `schwabdev` package is not installed. Run `pip install -e .` first."
            ) from error

        if not self.settings.has_credentials:
            raise RuntimeError("APP_KEY and APP_SECRET must be present in the environment.")

        # Use project-local tokens.db if available (for Streamlit Cloud), otherwise use default
        tokens_db_path = None
        project_tokens = Path(__file__).parent.parent.parent / ".schwab_tokens.db"
        if project_tokens.exists():
            tokens_db_path = str(project_tokens.resolve())

        self._client = schwabdev.Client(
            self.settings.app_key,
            self.settings.app_secret,
            callback_url=self.settings.callback_url,
            tokens_db=tokens_db_path,
        )

    def close(self) -> None:
        self._client.close()

    def _unwrap_json(self, response, label: str) -> dict[str, object]:
        if not response.ok:
            raise SchwabApiError(f"{label} failed with HTTP {response.status_code}: {response.text}")
        payload = response.json()
        if isinstance(payload, dict) and payload.get("status") in {"FAILED", "ERROR"}:
            raise SchwabApiError(f"{label} returned {payload.get('status')}: {payload}")
        return payload

    @staticmethod
    def intraday_history_window(
        now: datetime | None = None,
        *,
        lookback_days: int = 7,
    ) -> tuple[datetime, datetime]:
        end = now or datetime.now(timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        else:
            end = end.astimezone(timezone.utc)
        start = end - timedelta(days=lookback_days)
        return start, end

    def fetch_snapshot(
        self,
        symbol: str,
        min_dte: int,
        max_dte: int | None = None,
        strike_count: int = 0,
        history_frequency: int = 0,
        fetch_max_dte: int | None = None,
    ) -> dict[str, object]:
        effective_max_dte = fetch_max_dte if fetch_max_dte is not None else max_dte
        if effective_max_dte is None:
            raise ValueError("Either `max_dte` or `fetch_max_dte` must be provided.")
        today = datetime.now(timezone.utc).date()
        chain_response = self._client.option_chains(
            symbol=symbol,
            strikeCount=strike_count,
            includeUnderlyingQuote=True,
            range="ALL",
            fromDate=today + timedelta(days=min_dte),
            toDate=today + timedelta(days=effective_max_dte),
        )
        history_start, history_end = self.intraday_history_window()
        history_response = self._client.price_history(
            symbol=symbol,
            frequencyType=self.settings.history_frequency_type,
            frequency=history_frequency,
            startDate=history_start,
            endDate=history_end,
            needExtendedHoursData=True,
            needPreviousClose=True,
        )
        quote_response = self._client.quote(symbol, fields="quote")

        chain = self._unwrap_json(chain_response, "Option chain")
        history = self._unwrap_json(history_response, "Price history")
        quote = self._unwrap_json(quote_response, "Quote")
        underlying_price = extract_underlying_price(chain, quote=quote)

        return {
            "symbol": symbol,
            "chain": chain,
            "history": history,
            "quote": quote,
            "underlying_price": underlying_price,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _quotes_map(payload: dict[str, object]) -> dict[str, object]:
        inner = payload.get("quotes")
        if isinstance(inner, dict):
            return inner
        return payload

    def fetch_market_context(self, symbol: str) -> dict[str, object]:
        """Index vol ($VIX, $VIX1D) plus daily OHLC on the equity for clustering."""
        quote_attempts = [
            ["$VIX", "$VIX1D"],
            ["VIX", "VIX1D"],
            ["$VIX"],
            ["VIX"],
        ]
        quotes_response = None
        for syms in quote_attempts:
            resp = self._client.quotes(syms, fields="quote")
            if resp.ok:
                quotes_response = resp
                break
        if quotes_response is None:
            raise SchwabApiError("Index quotes failed for all symbol variants (VIX / VIX1D).")
        quotes_payload = self._unwrap_json(quotes_response, "Index quotes")
        quotes_map = self._quotes_map(quotes_payload)

        daily_response = self._client.price_history(
            symbol=symbol,
            periodType="month",
            period=3,
            frequencyType="daily",
            frequency=1,
            needExtendedHoursData=False,
            needPreviousClose=False,
        )
        daily_history = self._unwrap_json(daily_response, "Daily price history")

        return {
            "index_quotes": quotes_map,
            "equity_daily_history": daily_history,
        }

    def fetch_quotes(self, symbols: list[str] | tuple[str, ...]) -> dict[str, object]:
        cleaned = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
        if not cleaned:
            return {}
        response = self._client.quotes(cleaned, fields="quote")
        payload = self._unwrap_json(response, "Quotes")
        return self._quotes_map(payload)
