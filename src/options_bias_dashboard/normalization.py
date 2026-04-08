from __future__ import annotations

from datetime import date, datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo

from .models import OptionContract

ET = ZoneInfo("America/New_York")
PREMARKET_OPEN = dt_time(4, 0)
REGULAR_OPEN = dt_time(9, 30)
REGULAR_CLOSE = dt_time(16, 0)


def _as_float(value: object) -> float | None:
    if value in (None, "", "NaN"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int:
    if value in (None, "", "NaN"):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _normalize_implied_volatility(value: object) -> float | None:
    implied_volatility = _as_float(value)
    if implied_volatility is None:
        return None
    if implied_volatility > 1.5:
        implied_volatility = implied_volatility / 100.0
    if implied_volatility <= 0:
        return None
    return implied_volatility


def _parse_expiration(expiration_key: str, payload: dict[str, object]) -> tuple[date, int]:
    if ":" in expiration_key:
        raw_date, raw_dte = expiration_key.split(":", 1)
        return date.fromisoformat(raw_date), _as_int(raw_dte)

    expiration = payload.get("expirationDate")
    if isinstance(expiration, str) and expiration:
        return date.fromisoformat(expiration[:10]), _as_int(payload.get("daysToExpiration"))

    return date.today(), _as_int(payload.get("daysToExpiration"))


def _normalize_side(
    option_map: dict[str, dict[str, list[dict[str, object]]]],
    option_type: str,
) -> list[OptionContract]:
    contracts: list[OptionContract] = []
    for expiration_key, strike_map in option_map.items():
        for strike_key, entries in strike_map.items():
            for entry in entries:
                expiration_date, days_to_expiration = _parse_expiration(expiration_key, entry)
                strike = _as_float(entry.get("strikePrice")) or _as_float(strike_key) or 0.0
                contracts.append(
                    OptionContract(
                        symbol=str(entry.get("symbol") or ""),
                        option_type=option_type,
                        strike=strike,
                        expiration_date=expiration_date,
                        days_to_expiration=days_to_expiration,
                        bid=_as_float(entry.get("bid")),
                        ask=_as_float(entry.get("ask")),
                        mark=_as_float(entry.get("mark")),
                        total_volume=_as_int(entry.get("totalVolume")),
                        open_interest=_as_int(entry.get("openInterest")),
                        implied_volatility=_normalize_implied_volatility(entry.get("volatility")),
                        delta=_as_float(entry.get("delta")),
                        gamma=_as_float(entry.get("gamma")),
                        theta=_as_float(entry.get("theta")),
                        vega=_as_float(entry.get("vega")),
                        in_the_money=bool(entry.get("inTheMoney", False)),
                    )
                )
    return contracts


def normalize_option_chain(chain: dict[str, object]) -> list[OptionContract]:
    call_map = chain.get("callExpDateMap") or {}
    put_map = chain.get("putExpDateMap") or {}
    contracts = _normalize_side(call_map, "call")
    contracts.extend(_normalize_side(put_map, "put"))
    return contracts


def extract_underlying_price(chain: dict[str, object], quote: dict[str, object] | None = None) -> float:
    candidates: list[object] = [
        chain.get("underlyingPrice"),
        chain.get("underlying", {}).get("last") if isinstance(chain.get("underlying"), dict) else None,
        chain.get("underlying", {}).get("mark") if isinstance(chain.get("underlying"), dict) else None,
        chain.get("underlying", {}).get("close") if isinstance(chain.get("underlying"), dict) else None,
    ]

    if quote:
        if isinstance(quote.get("quote"), dict):
            candidates.extend(
                [
                    quote["quote"].get("lastPrice"),
                    quote["quote"].get("mark"),
                    quote["quote"].get("closePrice"),
                ]
            )
        elif len(quote) == 1:
            first_payload = next(iter(quote.values()))
            if isinstance(first_payload, dict) and isinstance(first_payload.get("quote"), dict):
                candidates.extend(
                    [
                        first_payload["quote"].get("lastPrice"),
                        first_payload["quote"].get("mark"),
                        first_payload["quote"].get("closePrice"),
                    ]
                )

    for candidate in candidates:
        value = _as_float(candidate)
        if value is not None and value > 0:
            return value

    raise ValueError("Could not determine underlying price from the chain payload.")


def extract_price_closes(history: dict[str, object]) -> list[float]:
    return [bar["close"] for bar in extract_price_bars(history)]


def _parse_bar_time(raw: object) -> datetime | None:
    if isinstance(raw, (int, float)):
        ts = float(raw) / 1000.0 if float(raw) > 1e12 else float(raw)
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ET)
    if isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(ET)
    return None


def extract_price_bars(history: dict[str, object]) -> list[dict[str, object]]:
    candles = history.get("candles") or []
    bars: list[dict[str, object]] = []
    for candle in candles:
        bar_time = _parse_bar_time(candle.get("datetime"))
        open_ = _as_float(candle.get("open"))
        high = _as_float(candle.get("high"))
        low = _as_float(candle.get("low"))
        close = _as_float(candle.get("close"))
        if bar_time is None or open_ is None or high is None or low is None or close is None:
            continue
        bars.append(
            {
                "time": bar_time,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": _as_int(candle.get("volume")),
            }
        )
    return bars


def _latest_bar_date(bars: list[dict[str, object]]) -> date | None:
    if not bars:
        return None
    return max(bar["time"].date() for bar in bars)


def _filter_session_bars(
    bars: list[dict[str, object]],
    *,
    start_time: dt_time,
    end_time: dt_time,
    session_date: date | None = None,
) -> list[dict[str, object]]:
    target_date = session_date or _latest_bar_date(bars)
    if target_date is None:
        return []
    filtered: list[dict[str, object]] = []
    for bar in bars:
        timestamp = bar["time"].astimezone(ET)
        if timestamp.date() != target_date:
            continue
        local_time = timestamp.time()
        if start_time <= local_time < end_time:
            filtered.append(bar)
    return filtered


def extract_regular_session_bars(
    history: dict[str, object],
    *,
    session_date: date | None = None,
) -> list[dict[str, object]]:
    return _filter_session_bars(
        extract_price_bars(history),
        start_time=REGULAR_OPEN,
        end_time=REGULAR_CLOSE,
        session_date=session_date,
    )


def extract_premarket_bars(
    history: dict[str, object],
    *,
    session_date: date | None = None,
) -> list[dict[str, object]]:
    return _filter_session_bars(
        extract_price_bars(history),
        start_time=PREMARKET_OPEN,
        end_time=REGULAR_OPEN,
        session_date=session_date,
    )


def extract_regular_session_closes(
    history: dict[str, object],
    *,
    session_date: date | None = None,
) -> list[float]:
    return [bar["close"] for bar in extract_regular_session_bars(history, session_date=session_date)]


def extract_intraday_range(history: dict[str, object]) -> tuple[float | None, float | None, float | None]:
    bars = extract_regular_session_bars(history)
    highs: list[float] = []
    lows: list[float] = []
    for bar in bars:
        high = _as_float(bar.get("high"))
        low = _as_float(bar.get("low"))
        if high is not None:
            highs.append(high)
        if low is not None:
            lows.append(low)
    if not highs or not lows:
        return None, None, None
    session_high = max(highs)
    session_low = min(lows)
    return session_high, session_low, session_high - session_low


def extract_premarket_range(
    history: dict[str, object],
    *,
    session_date: date | None = None,
) -> tuple[float | None, float | None]:
    bars = extract_premarket_bars(history, session_date=session_date)
    if not bars:
        return None, None
    lows = [bar["low"] for bar in bars]
    highs = [bar["high"] for bar in bars]
    return min(lows), max(highs)
