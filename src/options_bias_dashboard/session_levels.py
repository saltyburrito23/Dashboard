from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timezone
from statistics import median

from .normalization import ET, extract_premarket_bars, extract_price_bars

PREMARKET_OPEN = dt_time(4, 0)
REGULAR_OPEN = dt_time(9, 30)
PREMARKET_OUTLIER_WICK_MULTIPLIER = 2.5
PREMARKET_OUTLIER_MIN_POINTS = 1.0


@dataclass(frozen=True)
class PremarketStructure:
    yesterday_low: float | None
    yesterday_high: float | None
    premarket_low: float | None
    premarket_high: float | None
    premarket_complete: bool
    premarket_coverage_start: str | None
    label: str
    description: str
    prior_session_date: str | None
    premarket_session_date: str | None


def _robust_premarket_extremes(bars: list[dict[str, object]]) -> tuple[float | None, float | None]:
    if not bars:
        return None, None

    if len(bars) < 3:
        return min(bar["low"] for bar in bars), max(bar["high"] for bar in bars)

    ranges = [max(0.0, float(bar["high"]) - float(bar["low"])) for bar in bars]
    threshold = max(PREMARKET_OUTLIER_MIN_POINTS, median(ranges) * PREMARKET_OUTLIER_WICK_MULTIPLIER)

    accepted_lows: list[float] = []
    accepted_highs: list[float] = []
    for index, bar in enumerate(bars):
        open_ = float(bar["open"])
        close = float(bar["close"])
        low = float(bar["low"])
        high = float(bar["high"])
        body_low = min(open_, close)
        body_high = max(open_, close)
        lower_wick = body_low - low
        upper_wick = high - body_high

        low_outlier = False
        high_outlier = False
        if 0 < index < len(bars) - 1:
            previous_bar = bars[index - 1]
            next_bar = bars[index + 1]
            neighbor_low = min(float(previous_bar["low"]), float(next_bar["low"]))
            neighbor_high = max(float(previous_bar["high"]), float(next_bar["high"]))
            low_outlier = lower_wick > threshold and (neighbor_low - low) > (threshold / 2.0)
            high_outlier = upper_wick > threshold and (high - neighbor_high) > (threshold / 2.0)

        if not low_outlier:
            accepted_lows.append(low)
        if not high_outlier:
            accepted_highs.append(high)

    lows = accepted_lows or [float(bar["low"]) for bar in bars]
    highs = accepted_highs or [float(bar["high"]) for bar in bars]
    return min(lows), max(highs)


def latest_completed_daily_bar(history: dict[str, object], *, as_of: datetime) -> dict[str, object] | None:
    return latest_daily_bar_before(history, cutoff_date=as_of.astimezone().date())


def latest_daily_bar_before(history: dict[str, object], *, cutoff_date: date) -> dict[str, object] | None:
    raw_candles = history.get("candles") or []
    bars: list[dict[str, object]] = []
    for candle in raw_candles:
        if not isinstance(candle, dict):
            continue
        raw_dt = candle.get("datetime")
        bar_date: date | None = None
        if isinstance(raw_dt, str) and len(raw_dt) >= 10:
            try:
                bar_date = date.fromisoformat(raw_dt[:10])
            except ValueError:
                bar_date = None
        elif isinstance(raw_dt, (int, float)):
            ts = float(raw_dt) / 1000.0 if float(raw_dt) > 1e12 else float(raw_dt)
            bar_date = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        low = candle.get("low")
        high = candle.get("high")
        open_ = candle.get("open")
        close = candle.get("close")
        if bar_date is None or None in (open_, high, low, close):
            continue
        bars.append(
            {
                "time": bar_date,
                "open": float(open_),
                "high": float(high),
                "low": float(low),
                "close": float(close),
            }
        )
    if not bars:
        return None
    bars.sort(key=lambda bar: bar["time"])
    candidates = [bar for bar in bars if bar["time"] < cutoff_date]
    if candidates:
        return candidates[-1]
    return bars[-1]


def latest_premarket_session_date(history: dict[str, object]) -> date | None:
    bars = extract_price_bars(history)
    dates = sorted(
        {
            bar["time"].date()
            for bar in bars
            if PREMARKET_OPEN <= bar["time"].astimezone(ET).time() < REGULAR_OPEN
        }
    )
    if not dates:
        return None
    return dates[-1]


def classify_premarket_structure(
    *,
    yesterday_low: float | None,
    yesterday_high: float | None,
    premarket_low: float | None,
    premarket_high: float | None,
) -> tuple[str, str]:
    if (
        yesterday_low is None
        or yesterday_high is None
        or premarket_low is None
        or premarket_high is None
        or yesterday_low > yesterday_high
        or premarket_low > premarket_high
    ):
        return "N/A", "Pre-market structure is unavailable for this ticker right now."
    if premarket_low < yesterday_low and premarket_high > yesterday_high:
        return "Spans Both Sides", "Expansion regime; typically high intraday volatility."
    if yesterday_high < premarket_low <= premarket_high:
        return "Full Gap Above", "Breakaway potential; high odds of a backtest first."
    if premarket_high < yesterday_low:
        return "Full Gap Below", "Bearish skew; frequent retests of the breakdown area."
    if yesterday_low <= premarket_low <= premarket_high <= yesterday_high:
        return "Inside Yesterday's Range", "Lower-volatility; mean-reverting sessions are more common."
    if premarket_high > yesterday_high and premarket_low >= yesterday_low:
        return "Top Overhang", "Short-trap dynamics; often fuels continuation higher."
    if premarket_low < yesterday_low and premarket_high <= yesterday_high:
        return "Bottom Overhang", "Long-trap dynamics; often sets up reversal rallies."
    return "Mixed Overlap", "Pre-market overlaps the prior session unevenly; expect a more conditional open."


def build_premarket_structure(
    *,
    intraday_history: dict[str, object],
    daily_history: dict[str, object],
    as_of: datetime,
) -> PremarketStructure:
    premarket_complete = True
    premarket_coverage_start: str | None = None
    premarket_session_date = latest_premarket_session_date(intraday_history)
    if premarket_session_date is not None:
        premarket_bars = extract_premarket_bars(intraday_history, session_date=premarket_session_date)
        if premarket_bars:
            premarket_coverage_start = premarket_bars[0]["time"].astimezone(ET).strftime("%H:%M")
            premarket_complete = premarket_bars[0]["time"].astimezone(ET).time() <= dt_time(4, 5)
        else:
            premarket_complete = False
        prior_bar = latest_daily_bar_before(daily_history, cutoff_date=premarket_session_date)
        premarket_low, premarket_high = _robust_premarket_extremes(premarket_bars)
    else:
        prior_bar = latest_completed_daily_bar(daily_history, as_of=as_of)
        premarket_low, premarket_high = None, None
        premarket_complete = False
    yesterday_low = prior_bar["low"] if prior_bar is not None else None
    yesterday_high = prior_bar["high"] if prior_bar is not None else None
    label, description = classify_premarket_structure(
        yesterday_low=yesterday_low,
        yesterday_high=yesterday_high,
        premarket_low=premarket_low,
        premarket_high=premarket_high,
    )
    return PremarketStructure(
        yesterday_low=yesterday_low,
        yesterday_high=yesterday_high,
        premarket_low=premarket_low,
        premarket_high=premarket_high,
        premarket_complete=premarket_complete,
        premarket_coverage_start=premarket_coverage_start,
        label=label,
        description=description,
        prior_session_date=prior_bar["time"].isoformat() if prior_bar is not None else None,
        premarket_session_date=premarket_session_date.isoformat() if premarket_session_date is not None else None,
    )
