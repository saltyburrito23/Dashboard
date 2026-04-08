from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .models import OptionContract

TRADING_DAYS_PER_YEAR = 252
SESSION_HOURS = 6.5
ACCEL_COEFFICIENT = 0.6
ACCEL_CAP = 1.8
HOURS_FLOOR = 0.25

ET = ZoneInfo("America/New_York")


def _as_float(value: object) -> float | None:
    if value in (None, "", "NaN"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_batch_quotes(payload: dict[str, object]) -> dict[str, float]:
    """Extract last/mark prices from Schwab GET /marketdata/v1/quotes response."""
    out: dict[str, float] = {}
    for sym, body in payload.items():
        if not isinstance(body, dict):
            continue
        quote = body.get("quote")
        if not isinstance(quote, dict):
            continue
        for key in ("lastPrice", "mark", "closePrice"):
            v = _as_float(quote.get(key))
            if v is not None and v > 0:
                norm = str(sym).upper().removeprefix("$")
                out[norm] = v
                break
    return out


def parse_batch_prev_closes(payload: dict[str, object]) -> dict[str, float]:
    """Extract prior close values from Schwab batch quote payloads."""
    out: dict[str, float] = {}
    for sym, body in payload.items():
        if not isinstance(body, dict):
            continue
        quote = body.get("quote")
        if not isinstance(quote, dict):
            continue
        for key in ("closePrice", "previousClose"):
            v = _as_float(quote.get(key))
            if v is not None and v > 0:
                norm = str(sym).upper().removeprefix("$")
                out[norm] = v
                break
    return out


def resolve_sigma_base(
    vix: float | None,
    vix1d: float | None,
    *,
    vix_odte_multiplier: float = 1.15,
) -> tuple[float, str]:
    """
    Annualized IV as decimal (e.g. 0.18 for 18%).
    Primary: VIX1D/100 when present; else VIX * vix_odte_multiplier / 100.
    """
    if vix1d is not None and vix1d > 0:
        return vix1d / 100.0, "VIX1D (same-day index)"
    if vix is not None and vix > 0:
        return (vix * vix_odte_multiplier) / 100.0, f"VIX × {vix_odte_multiplier:.2f} (0DTE proxy)"
    raise ValueError("Need at least one of VIX or VIX1D from quotes.")


def session_context(now: datetime | None = None) -> tuple[float, bool, date]:
    """
    Regular-session hours remaining (ET), whether now is inside RTH, and ET calendar date.
    Outside RTH, returns 6.5h and in_rth=False so T matches a full notional session.
    """
    if now is None:
        now = datetime.now(ET)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    else:
        now = now.astimezone(ET)
    d = now.date()
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if now.weekday() >= 5:
        return SESSION_HOURS, False, d
    if now < open_t:
        return SESSION_HOURS, False, d
    if now > close_t:
        return SESSION_HOURS, False, d
    remaining = max((close_t - now).total_seconds() / 3600.0, HOURS_FLOOR)
    return remaining, True, d


def intraday_acceleration_multiplier(hours_remaining: float) -> float:
    """σ multiplier 1.0 at open (6.5h left), rising into the close; capped at ACCEL_CAP."""
    h = max(hours_remaining, HOURS_FLOOR)
    raw = 1.0 + ACCEL_COEFFICIENT * (1.0 / h - 1.0 / SESSION_HOURS)
    return min(max(raw, 1.0), ACCEL_CAP)


def time_to_expiry_years(hours_remaining: float) -> float:
    """Aligns with common 0DTE tooling: T = hours_remaining / (6.5 × 252)."""
    return max(hours_remaining, HOURS_FLOOR) / (SESSION_HOURS * TRADING_DAYS_PER_YEAR)


def kurtosis_factor(vix: float | None) -> float:
    """Fat-tail scaling bands (informational; mirrors stepped PoP adjustments)."""
    if vix is None:
        return 2.0
    if vix < 15:
        return 1.5
    if vix < 20:
        return 2.0
    if vix < 25:
        return 2.5
    if vix < 30:
        return 3.0
    return 3.5


def dow_range_multiplier(weekday: int) -> float:
    """Monday narrower, Thursday wider; empirical day-of-week scaling on range."""
    if weekday == 0:
        return 0.94
    if weekday == 3:
        return 1.04
    if weekday == 4:
        return 1.02
    return 1.0


def extract_daily_candles(history: dict[str, Any]) -> list[dict[str, Any]]:
    raw = history.get("candles")
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, dict)]


def range_cluster_multiplier(
    candles: list[dict[str, Any]],
    as_of: date,
    *,
    lookback: int = 20,
    vix: float | None = None,
) -> tuple[float, str]:
    """
    Yesterday's high-low range vs trailing median; widen today's band after wide prior days.
    """
    if len(candles) < 5:
        return 1.0, "Not enough daily candles for clustering."

    def bar_date(c: dict[str, Any]) -> date | None:
        t = c.get("datetime")
        if isinstance(t, (int, float)):
            ts = float(t) / 1000.0 if float(t) > 1e12 else float(t)
            return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ET).date()
        if isinstance(t, str) and len(t) >= 10:
            return date.fromisoformat(t[:10])
        return None

    def rng_pct(c: dict[str, Any]) -> float | None:
        h, low, cl = _as_float(c.get("high")), _as_float(c.get("low")), _as_float(c.get("close"))
        if h is None or low is None or cl is None or cl <= 0:
            return None
        return (h - low) / cl

    dated: list[tuple[date, float]] = []
    for c in candles:
        bd = bar_date(c)
        rp = rng_pct(c)
        if bd is not None and rp is not None:
            dated.append((bd, rp))
    dated.sort(key=lambda x: x[0])
    completed = [(d0, r) for d0, r in dated if d0 < as_of]
    if len(completed) < 5:
        return 1.0, "No completed sessions before as-of date."

    yesterday_range = completed[-1][1]
    history_before = completed[:-1]
    prior = [r for _, r in history_before[-lookback:]]
    if not prior:
        return 1.0, "No prior sessions to rank yesterday's range."
    prior_sorted = sorted(prior)
    median = prior_sorted[len(prior_sorted) // 2]
    if median <= 0:
        return 1.0, "Median range is zero."

    ratio = yesterday_range / median
    mult = 1.0 + min(0.87, max(0.0, (ratio - 1.0) * 0.45))
    if vix is not None and vix >= 20 and ratio > 1.2:
        mult = min(1.87, mult + 0.15)
    return mult, f"Yesterday H-L / trailing median ≈ {ratio:.2f}×"


def option_mid(contract: OptionContract) -> float | None:
    if contract.mark is not None and contract.mark > 0:
        return contract.mark
    if (
        contract.bid is not None
        and contract.ask is not None
        and contract.ask >= contract.bid >= 0
        and contract.ask > 0
    ):
        return (contract.bid + contract.ask) / 2.0
    return None


@dataclass(frozen=True)
class AtmStraddle:
    strike: float
    expiration_date: date
    days_to_expiration: int
    call_mid: float
    put_mid: float
    straddle_mid: float


def find_atm_straddle(
    contracts: list[OptionContract],
    spot: float,
    min_dte: int,
    max_dte: int,
) -> AtmStraddle | None:
    """Front expiry in DTE window; strike nearest spot; ATM call + put mids."""
    eligible = [c for c in contracts if min_dte <= c.days_to_expiration <= max_dte]
    if not eligible:
        return None
    min_dte_found = min(c.days_to_expiration for c in eligible)
    layer = [c for c in eligible if c.days_to_expiration == min_dte_found]
    by_strike: dict[float, dict[str, OptionContract]] = {}
    for c in layer:
        by_strike.setdefault(c.strike, {})[c.option_type] = c
    best: tuple[float, float, AtmStraddle] | None = None
    for strike, sides in by_strike.items():
        call = sides.get("call")
        put = sides.get("put")
        if call is None or put is None:
            continue
        cm, pm = option_mid(call), option_mid(put)
        if cm is None or pm is None:
            continue
        dist = abs(strike - spot)
        row = AtmStraddle(
            strike=strike,
            expiration_date=call.expiration_date,
            days_to_expiration=call.days_to_expiration,
            call_mid=cm,
            put_mid=pm,
            straddle_mid=cm + pm,
        )
        if best is None or dist < best[0] or (dist == best[0] and strike < best[1]):
            best = (dist, strike, row)
    return best[2] if best else None


@dataclass(frozen=True)
class VolatilityPanel:
    vix: float | None
    vix1d: float | None
    vix_odte_multiplier: float
    sigma_source_label: str
    sigma_base: float
    hours_remaining: float
    in_regular_session: bool
    accel_multiplier: float
    sigma_effective: float
    kurtosis_factor: float
    dow_multiplier: float
    cluster_multiplier: float
    cluster_note: str
    time_to_expiry_years: float
    sigma_daily_close: float
    dollar_daily_1sigma: float
    dollar_session_1sigma: float
    straddle: AtmStraddle | None
    straddle_vs_session_ratio: float | None


def build_volatility_panel(
    *,
    spot: float,
    vix: float | None,
    vix1d: float | None,
    contracts: list[OptionContract],
    min_dte: int,
    max_dte: int,
    daily_history: dict[str, Any],
    now: datetime | None = None,
    vix_odte_multiplier: float = 1.15,
) -> VolatilityPanel:
    sigma_base, sigma_label = resolve_sigma_base(vix, vix1d, vix_odte_multiplier=vix_odte_multiplier)
    hours_left, in_rth, et_date = session_context(now)
    accel = intraday_acceleration_multiplier(hours_left)
    sigma_eff = sigma_base * accel
    kf = kurtosis_factor(vix)
    dow = dow_range_multiplier(et_date.weekday())
    candles = extract_daily_candles(daily_history)
    cl_mult, cl_note = range_cluster_multiplier(candles, et_date, vix=vix)
    regime = dow * cl_mult
    T = time_to_expiry_years(hours_left)
    sigma_daily = sigma_base / math.sqrt(TRADING_DAYS_PER_YEAR)
    dollar_daily = spot * sigma_daily * regime
    dollar_session = spot * sigma_eff * math.sqrt(T) * regime

    straddle = find_atm_straddle(contracts, spot, min_dte, max_dte)
    ratio: float | None = None
    if straddle is not None and dollar_session > 0:
        ratio = straddle.straddle_mid / dollar_session

    return VolatilityPanel(
        vix=vix,
        vix1d=vix1d,
        vix_odte_multiplier=vix_odte_multiplier,
        sigma_source_label=sigma_label,
        sigma_base=sigma_base,
        hours_remaining=hours_left,
        in_regular_session=in_rth,
        accel_multiplier=accel,
        sigma_effective=sigma_eff,
        kurtosis_factor=kf,
        dow_multiplier=dow,
        cluster_multiplier=cl_mult,
        cluster_note=cl_note,
        time_to_expiry_years=T,
        sigma_daily_close=sigma_daily,
        dollar_daily_1sigma=dollar_daily,
        dollar_session_1sigma=dollar_session,
        straddle=straddle,
        straddle_vs_session_ratio=ratio,
    )
