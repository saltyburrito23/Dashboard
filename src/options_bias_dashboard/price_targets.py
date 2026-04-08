from __future__ import annotations

import math
from dataclasses import dataclass

from .volatility import VolatilityPanel

# Old-model target ladder: shallow extension, moderate extension, full expected move.
FIBONACCI_RUNGS: tuple[float, float, float] = (0.382, 0.618, 1.0)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _bias_tilt(bias_score: float) -> float:
    """Map model score (roughly ±60) to [-1, 1] for asymmetry."""
    return _clamp(bias_score / 60.0, -1.0, 1.0)


def directional_scales(bias_score: float, *, compression: float = 0.10) -> tuple[float, float]:
    """
    Keep the favored side at full expected move and only mildly compress the weaker side.
    """
    u = _bias_tilt(bias_score)
    if u >= 0:
        return 1.0, max(1.0 - compression * u, 0.90)
    return max(1.0 - compression * abs(u), 0.90), 1.0


def previous_session_bias_score(
    open_price: float | None,
    high_price: float | None,
    low_price: float | None,
    close_price: float | None,
) -> float:
    """
    Derive a mild directional tilt from the prior completed daily candle alone.
    """
    if (
        open_price is None
        or high_price is None
        or low_price is None
        or close_price is None
        or high_price <= low_price
    ):
        return 0.0
    session_range = high_price - low_price
    close_location = ((close_price - low_price) / session_range - 0.5) * 2.0
    body = (close_price - open_price) / session_range
    score = close_location * 18.0 + body * 22.0
    return _clamp(score, -30.0, 30.0)


def structure_expected_move(
    base_em: float,
    session_high: float | None,
    session_low: float | None,
) -> tuple[float, float | None, str]:
    """
    Use the current intraday high-low range as a realism envelope for the expected move.
    The full 1.0 target is capped at half of the session range.
    """
    if (
        session_high is None
        or session_low is None
        or session_high <= session_low
        or base_em <= 0
    ):
        return base_em, None, "No intraday H/L envelope available."
    session_range = session_high - session_low
    envelope = session_range * 0.5
    if envelope <= 0:
        return base_em, None, "Intraday H/L range was not usable."
    em = min(base_em, envelope)
    if em < base_em:
        note = (
            f"Intraday H/L {session_high:.2f}-{session_low:.2f} "
            f"(range {session_range:.2f}) capped EM at {em:.2f}."
        )
    else:
        note = (
            f"Intraday H/L {session_high:.2f}-{session_low:.2f} "
            f"(range {session_range:.2f}, half-range {envelope:.2f})."
        )
    return em, session_range, note


def resolve_expected_move_dollars(
    spot: float,
    vol_panel: VolatilityPanel | None,
    expected_move_chain: float | None,
) -> tuple[float, str]:
    """
    Primary: daily 1σ dollars from VIX hierarchy.
    Fallback: scale chain ATM straddle-style expected_move toward a 1σ daily anchor.
    """
    if vol_panel is not None and vol_panel.dollar_daily_1sigma > 0:
        return vol_panel.dollar_daily_1sigma, "Daily 1σ (VIX / VIX1D stack × DOW × clustering)"
    if expected_move_chain is not None and expected_move_chain > 0:
        scaled = expected_move_chain * 0.42
        return max(scaled, spot * 0.001), "Chain expected move × 0.42 (fallback)"
    sigma_daily = 0.182 / math.sqrt(252)
    return spot * sigma_daily, "Static 18.2% annual → daily (no vol panel)"


@dataclass(frozen=True)
class PriceTargetLadder:
    spot: float
    base_em_dollars: float
    em_dollars: float
    em_source: str
    session_high: float | None
    session_low: float | None
    session_range: float | None
    structure_note: str
    bias_score: float
    up_scale: float
    down_scale: float
    upside: tuple[float, float, float]
    downside: tuple[float, float, float]
    fibonacci_multipliers: tuple[float, float, float]


@dataclass(frozen=True)
class DirectionalTarget:
    label: str
    price: float
    delta_from_spot: float
    percent_from_spot: float


def build_price_target_ladder(
    *,
    spot: float,
    vol_panel: VolatilityPanel | None,
    bias_score: float,
    session_high: float | None,
    session_low: float | None,
    expected_move_chain: float | None = None,
) -> PriceTargetLadder:
    base_em, em_src = resolve_expected_move_dollars(spot, vol_panel, expected_move_chain)
    em, session_range, struct_note = structure_expected_move(base_em, session_high, session_low)
    up_s, down_s = directional_scales(bias_score)
    upside = tuple(spot + em * fib * up_s for fib in FIBONACCI_RUNGS)
    downside = tuple(spot - em * fib * down_s for fib in FIBONACCI_RUNGS)

    return PriceTargetLadder(
        spot=spot,
        base_em_dollars=base_em,
        em_dollars=em,
        em_source=em_src,
        session_high=session_high,
        session_low=session_low,
        session_range=session_range,
        structure_note=struct_note,
        bias_score=bias_score,
        up_scale=up_s,
        down_scale=down_s,
        upside=upside,
        downside=downside,
        fibonacci_multipliers=FIBONACCI_RUNGS,
    )


def build_directional_targets(
    spot: float,
    values: tuple[float, float, float],
    *,
    direction: str,
) -> tuple[DirectionalTarget, DirectionalTarget, DirectionalTarget]:
    prefix = "Up" if direction.lower() == "up" else "Down"
    targets: list[DirectionalTarget] = []
    for index, price in enumerate(values, start=1):
        delta = price - spot
        pct = (delta / spot) if spot > 0 else 0.0
        targets.append(
            DirectionalTarget(
                label=f"{prefix} {index}",
                price=price,
                delta_from_spot=delta,
                percent_from_spot=pct,
            )
        )
    return tuple(targets)  # type: ignore[return-value]
