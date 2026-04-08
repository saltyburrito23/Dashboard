from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean

from .models import AnalysisSnapshot, BiasComponent, BiasResult, ChainMetrics, CharmStrikeOverview, ExpiryOverview, OptionContract, StrikeOverview
from .price_targets import previous_session_bias_score

CONTRACT_MULTIPLIER = 100.0
ONE_PERCENT_MOVE = 0.01
TRADING_DAYS_PER_YEAR = 252.0
SESSION_HOURS = 6.5
MIN_TIME_TO_EXPIRY_YEARS = 1e-6
STRONG_BULLISH_SCORE = 18.0
BULLISH_LEAN_SCORE = 8.0
BEARISH_LEAN_SCORE = -8.0
STRONG_BEARISH_SCORE = -18.0


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _as_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return mean(values)


def _mid_price(contract: OptionContract) -> float | None:
    if contract.mark is not None and contract.mark > 0:
        return contract.mark
    if contract.bid is None or contract.ask is None:
        return None
    midpoint = (contract.bid + contract.ask) / 2.0
    if midpoint <= 0:
        return None
    return midpoint


def _directional_score(positive: float, negative: float) -> float:
    total = positive + negative
    if total <= 0:
        return 0.0
    return _clamp((positive - negative) / total, -1.0, 1.0)


def _analyze_volume_pressure(contracts: list[OptionContract], underlying_price: float) -> dict[str, float]:
    """Analyze buying vs selling pressure across the option chain."""
    pressure_metrics = {
        "call_buy_pressure": 0.0,
        "call_sell_pressure": 0.0,
        "put_buy_pressure": 0.0,
        "put_sell_pressure": 0.0,
        "aggressive_buy_ratio": 0.0,
        "aggressive_sell_ratio": 0.0,
    }

    for contract in contracts:
        if contract.bid is None or contract.ask is None or contract.total_volume == 0:
            continue

        # Estimate bid/ask volume split (rough approximation)
        spread = contract.ask - contract.bid
        if spread <= 0:
            continue

        bid_volume_ratio = 0.4  # Assume 40% of volume at bid (buying pressure)
        ask_volume_ratio = 0.6  # Assume 60% of volume at ask (selling pressure)

        volume = contract.total_volume
        bid_volume = volume * bid_volume_ratio
        ask_volume = volume * ask_volume_ratio

        # Weight by proximity to spot (near-the-money contracts more significant)
        spot_weight = 1.0 / (1.0 + abs(contract.strike - underlying_price) / underlying_price)

        if contract.option_type == "call":
            pressure_metrics["call_buy_pressure"] += bid_volume * spot_weight
            pressure_metrics["call_sell_pressure"] += ask_volume * spot_weight
        else:
            pressure_metrics["put_buy_pressure"] += bid_volume * spot_weight
            pressure_metrics["put_sell_pressure"] += ask_volume * spot_weight

    # Calculate aggressive ratios
    total_buy = pressure_metrics["call_buy_pressure"] + pressure_metrics["put_buy_pressure"]
    total_sell = pressure_metrics["call_sell_pressure"] + pressure_metrics["put_sell_pressure"]

    if total_buy + total_sell > 0:
        pressure_metrics["aggressive_buy_ratio"] = total_buy / (total_buy + total_sell)
        pressure_metrics["aggressive_sell_ratio"] = total_sell / (total_buy + total_sell)

    return pressure_metrics


def _analyze_liquidity_depth(contracts: list[OptionContract]) -> dict[str, float]:
    """Analyze market depth and liquidity characteristics."""
    depth_metrics = {
        "effective_spread_score": 0.0,
        "volume_concentration": 0.0,
        "liquidity_score": 0.0,
        "market_depth_ratio": 0.0,
    }

    spreads = []
    volumes = []
    oi_ratios = []

    for contract in contracts:
        if contract.spread_pct is not None:
            spreads.append(contract.spread_pct)

        if contract.total_volume > 0 and contract.open_interest > 0:
            oi_ratios.append(contract.open_interest / contract.total_volume)

        volumes.append(contract.total_volume)

    if spreads:
        avg_spread = mean(spreads)
        depth_metrics["effective_spread_score"] = _clamp(1.0 - (avg_spread / 0.10), 0.0, 1.0)

    if volumes:
        total_volume = sum(volumes)
        if total_volume > 0:
            # Concentration: what % of volume is in the top 20% of contracts
            sorted_volumes = sorted(volumes, reverse=True)
            top_20pct_count = max(1, int(len(sorted_volumes) * 0.2))
            top_volume = sum(sorted_volumes[:top_20pct_count])
            depth_metrics["volume_concentration"] = top_volume / total_volume

    if oi_ratios:
        avg_oi_ratio = mean(oi_ratios)
        # Higher OI/Volume ratio suggests more established positions
        depth_metrics["market_depth_ratio"] = _clamp(avg_oi_ratio / 10.0, 0.0, 1.0)

    # Combined liquidity score
    depth_metrics["liquidity_score"] = (
        depth_metrics["effective_spread_score"] * 0.4 +
        (1.0 - depth_metrics["volume_concentration"]) * 0.4 +  # Less concentration = better liquidity
        depth_metrics["market_depth_ratio"] * 0.2
    )

    return depth_metrics


def _analyze_decay_exposure(contracts: list[OptionContract], hours_remaining: float | None = None) -> dict[str, float]:
    """Analyze time decay exposure across the chain."""
    decay_metrics = {
        "net_theta_exposure": 0.0,
        "decay_pressure_score": 0.0,
        "short_term_decay_ratio": 0.0,
        "long_term_decay_ratio": 0.0,
    }

    short_term_theta = 0.0
    long_term_theta = 0.0
    total_theta = 0.0

    for contract in contracts:
        theta = contract.theta or 0.0
        exposure = theta * contract.open_interest * CONTRACT_MULTIPLIER

        total_theta += abs(exposure)

        if contract.days_to_expiration <= 7:
            short_term_theta += exposure
        elif contract.days_to_expiration <= 30:
            long_term_theta += exposure

    decay_metrics["net_theta_exposure"] = short_term_theta + long_term_theta
    decay_metrics["short_term_decay_ratio"] = abs(short_term_theta) / max(total_theta, 1.0)
    decay_metrics["long_term_decay_ratio"] = abs(long_term_theta) / max(total_theta, 1.0)

    # Decay pressure score: positive = net decay pressure (theta sellers winning)
    if total_theta > 0:
        decay_metrics["decay_pressure_score"] = _clamp(decay_metrics["net_theta_exposure"] / (total_theta * 0.1), -1.0, 1.0)

    return decay_metrics


def _analyze_volume_distribution(contracts: list[OptionContract], underlying_price: float) -> dict[str, float]:
    distribution = {
        "itm_call_volume": 0,
        "otm_call_volume": 0,
        "itm_put_volume": 0,
        "otm_put_volume": 0,
        "near_money_volume": 0,
        "far_money_volume": 0,
        "volume_skew_score": 0.0,
        "near_money_volume_ratio": 0.0,
    }

    near_threshold = underlying_price * 0.05
    total_volume = 0

    for contract in contracts:
        volume = contract.total_volume
        total_volume += volume
        strike_diff = abs(contract.strike - underlying_price)
        is_near_money = strike_diff <= near_threshold

        if contract.option_type == "call":
            if contract.in_the_money:
                distribution["itm_call_volume"] += volume
            else:
                distribution["otm_call_volume"] += volume
        else:
            if contract.in_the_money:
                distribution["itm_put_volume"] += volume
            else:
                distribution["otm_put_volume"] += volume

        if is_near_money:
            distribution["near_money_volume"] += volume
        else:
            distribution["far_money_volume"] += volume

    total_itm = distribution["itm_call_volume"] + distribution["itm_put_volume"]
    total_otm = distribution["otm_call_volume"] + distribution["otm_put_volume"]
    near_total = distribution["near_money_volume"] + distribution["far_money_volume"]

    itm_skew = (distribution["itm_call_volume"] - distribution["itm_put_volume"]) / total_itm if total_itm > 0 else 0.0
    otm_skew = (distribution["otm_call_volume"] - distribution["otm_put_volume"]) / total_otm if total_otm > 0 else 0.0
    distribution["volume_skew_score"] = _clamp((itm_skew * 0.6) + (otm_skew * 0.4), -1.0, 1.0)

    if near_total > 0:
        distribution["near_money_volume_ratio"] = distribution["near_money_volume"] / near_total

    return distribution


def _analyze_flow_pressure(contracts: list[OptionContract], underlying_price: float) -> dict[str, float]:
    pressure = {
        "call_buy_pressure": 0.0,
        "call_sell_pressure": 0.0,
        "put_buy_pressure": 0.0,
        "put_sell_pressure": 0.0,
        "aggressive_buy_ratio": 0.0,
        "pressure_score": 0.0,
    }

    total_buy = 0.0
    total_sell = 0.0

    for contract in contracts:
        if contract.bid is None or contract.ask is None or contract.total_volume == 0:
            continue

        strike_diff = abs(contract.strike - underlying_price)
        spot_weight = 1.0 / (1.0 + strike_diff / max(underlying_price, 1.0))
        bid_volume = contract.total_volume * 0.45
        ask_volume = contract.total_volume * 0.55

        if contract.option_type == "call":
            pressure["call_buy_pressure"] += bid_volume * spot_weight
            pressure["call_sell_pressure"] += ask_volume * spot_weight
        else:
            pressure["put_buy_pressure"] += bid_volume * spot_weight
            pressure["put_sell_pressure"] += ask_volume * spot_weight

        total_buy += bid_volume * spot_weight
        total_sell += ask_volume * spot_weight

    if total_buy + total_sell > 0:
        pressure["aggressive_buy_ratio"] = total_buy / (total_buy + total_sell)
        pressure["pressure_score"] = _directional_score(total_buy, total_sell)

    return pressure


def _nearest_negative_gamma_distance(strike_overview: tuple[StrikeOverview, ...], underlying_price: float) -> float | None:
    negative_gamma_strikes = [row for row in strike_overview if row.net_gex < 0]
    if not negative_gamma_strikes:
        return None
    closest = min(negative_gamma_strikes, key=lambda row: abs(row.strike - underlying_price))
    return abs(closest.strike - underlying_price)


def classify_bias_bucket(score: float) -> tuple[str, int]:
    if score >= STRONG_BULLISH_SCORE:
        return "Strong Bullish", 1
    if score >= BULLISH_LEAN_SCORE:
        return "Bullish Lean", 1
    if score <= STRONG_BEARISH_SCORE:
        return "Strong Bearish", -1
    if score <= BEARISH_LEAN_SCORE:
        return "Bearish Lean", -1
    return "Neutral/Mixed", 0


def classify_core_bias_label(score: float) -> str:
    if score >= STRONG_BULLISH_SCORE:
        return "bullish"
    if score <= STRONG_BEARISH_SCORE:
        return "bearish"
    return "mixed"


def _closest_strikes(contracts: list[OptionContract], underlying_price: float, strike_window: int) -> set[float]:
    strikes = sorted({contract.strike for contract in contracts}, key=lambda strike: abs(strike - underlying_price))
    return set(strikes[:strike_window])


def _filter_contracts(
    contracts: list[OptionContract],
    underlying_price: float,
    min_dte: int,
    max_dte: int,
    strike_window: int,
) -> list[OptionContract]:
    bounded = [
        contract
        for contract in contracts
        if min_dte <= contract.days_to_expiration <= max_dte and contract.strike > 0
    ]
    if not bounded:
        return []
    tracked_strikes = _closest_strikes(bounded, underlying_price, strike_window)
    return [contract for contract in bounded if contract.strike in tracked_strikes]


def _estimate_expected_move(contracts: list[OptionContract], underlying_price: float) -> float | None:
    by_expiry: dict[tuple[str, int], list[OptionContract]] = defaultdict(list)
    for contract in contracts:
        by_expiry[(contract.expiration_date.isoformat(), contract.days_to_expiration)].append(contract)

    for (_, _), expiry_contracts in sorted(by_expiry.items(), key=lambda item: item[0][1]):
        calls = [contract for contract in expiry_contracts if contract.option_type == "call"]
        puts = [contract for contract in expiry_contracts if contract.option_type == "put"]
        if not calls or not puts:
            continue

        call = min(calls, key=lambda contract: abs(contract.strike - underlying_price))
        matched_puts = [contract for contract in puts if abs(contract.strike - call.strike) < 0.0001]
        if not matched_puts:
            continue
        put = matched_puts[0]
        call_mid = _mid_price(call)
        put_mid = _mid_price(put)
        if call_mid is None or put_mid is None:
            continue
        return call_mid + put_mid

    return None


def _gex_notional_per_1pct_move(spot: float, contract: OptionContract) -> float:
    gamma = contract.gamma or 0.0
    if gamma <= 0 or contract.open_interest <= 0 or spot <= 0:
        return 0.0
    gex = gamma * contract.open_interest * CONTRACT_MULTIPLIER * (spot**2) * ONE_PERCENT_MOVE
    return gex if contract.option_type == "call" else -gex


def contract_gex_notional(spot: float, contract: OptionContract) -> float:
    return _gex_notional_per_1pct_move(spot, contract)


def rank_relevant_contracts(
    contracts: list[OptionContract],
    spot: float,
    *,
    limit: int = 24,
) -> list[OptionContract]:
    def spread_rank_value(contract: OptionContract) -> float:
        return contract.spread_pct if contract.spread_pct is not None else 9.99

    ranked = sorted(
        contracts,
        key=lambda contract: (
            contract.days_to_expiration,
            abs(contract.strike - spot),
            -contract.total_volume,
            -contract.open_interest,
            spread_rank_value(contract),
            -abs(_gex_notional_per_1pct_move(spot, contract)),
            contract.option_type,
            contract.strike,
        ),
    )
    return ranked[:limit]


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _bs_delta(
    spot: float,
    strike: float,
    sigma: float,
    time_years: float,
    option_type: str,
) -> float:
    if spot <= 0 or strike <= 0 or sigma <= 0 or time_years <= 0:
        return 0.0
    sqrt_t = math.sqrt(time_years)
    vol_t = sigma * sqrt_t
    if vol_t <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * time_years) / vol_t
    if option_type == "call":
        return _normal_cdf(d1)
    return _normal_cdf(d1) - 1.0


def _contract_time_to_expiry_years(contract: OptionContract, hours_remaining: float | None = None) -> float:
    if contract.days_to_expiration <= 0:
        if hours_remaining is not None and hours_remaining > 0:
            return max(hours_remaining / (SESSION_HOURS * TRADING_DAYS_PER_YEAR), MIN_TIME_TO_EXPIRY_YEARS)
        return 1.0 / TRADING_DAYS_PER_YEAR
    return max(contract.days_to_expiration / TRADING_DAYS_PER_YEAR, MIN_TIME_TO_EXPIRY_YEARS)


def contract_charm_exposure(
    spot: float,
    contract: OptionContract,
    *,
    hours_remaining: float | None = None,
) -> float:
    sigma = contract.implied_volatility or 0.0
    if sigma <= 0 or contract.open_interest <= 0 or spot <= 0 or contract.strike <= 0:
        return 0.0
    current_t = _contract_time_to_expiry_years(contract, hours_remaining)
    horizon = min(1.0 / TRADING_DAYS_PER_YEAR, max(current_t - MIN_TIME_TO_EXPIRY_YEARS, 0.0))
    if horizon <= 0:
        return 0.0
    future_t = max(current_t - horizon, MIN_TIME_TO_EXPIRY_YEARS)
    delta_now = _bs_delta(spot, contract.strike, sigma, current_t, contract.option_type)
    delta_future = _bs_delta(spot, contract.strike, sigma, future_t, contract.option_type)
    delta_change = delta_future - delta_now
    return delta_change * contract.open_interest * CONTRACT_MULTIPLIER * spot


def build_charm_strike_overview(
    contracts: list[OptionContract],
    spot: float,
    *,
    hours_remaining: float | None = None,
) -> tuple[CharmStrikeOverview, ...]:
    buckets: dict[float, dict[str, float]] = defaultdict(
        lambda: {
            "call_charm": 0.0,
            "put_charm": 0.0,
        }
    )
    for contract in contracts:
        bucket = buckets[contract.strike]
        charm = contract_charm_exposure(spot, contract, hours_remaining=hours_remaining)
        if contract.option_type == "call":
            bucket["call_charm"] += charm
        else:
            bucket["put_charm"] += charm

    overview: list[CharmStrikeOverview] = []
    for strike, values in sorted(buckets.items()):
        overview.append(
            CharmStrikeOverview(
                strike=strike,
                call_charm=values["call_charm"],
                put_charm=values["put_charm"],
                net_charm=values["call_charm"] + values["put_charm"],
            )
        )
    return tuple(overview)


def build_strike_overview(
    contracts: list[OptionContract] | tuple[OptionContract, ...],
    underlying_price: float,
) -> tuple[StrikeOverview, ...]:
    buckets: dict[float, dict[str, float]] = defaultdict(
        lambda: {
            "call_open_interest": 0,
            "put_open_interest": 0,
            "call_volume": 0,
            "put_volume": 0,
            "call_gex": 0.0,
            "put_gex": 0.0,
        }
    )

    for contract in contracts:
        bucket = buckets[contract.strike]
        gex = _gex_notional_per_1pct_move(underlying_price, contract)
        if contract.option_type == "call":
            bucket["call_open_interest"] += contract.open_interest
            bucket["call_volume"] += contract.total_volume
            bucket["call_gex"] += gex
        else:
            bucket["put_open_interest"] += contract.open_interest
            bucket["put_volume"] += contract.total_volume
            bucket["put_gex"] += gex

    overview: list[StrikeOverview] = []
    for strike, values in sorted(buckets.items()):
        overview.append(
            StrikeOverview(
                strike=strike,
                call_open_interest=int(values["call_open_interest"]),
                put_open_interest=int(values["put_open_interest"]),
                call_volume=int(values["call_volume"]),
                put_volume=int(values["put_volume"]),
                call_gex=values["call_gex"],
                put_gex=values["put_gex"],
                net_gex=values["call_gex"] + values["put_gex"],
            )
        )
    return tuple(overview)


def build_gex_expiry_overview(
    contracts: list[OptionContract] | tuple[OptionContract, ...],
    underlying_price: float,
) -> tuple[ExpiryOverview, ...]:
    buckets: dict[tuple[str, int], dict[str, float]] = defaultdict(
        lambda: {
            "call_open_interest": 0,
            "put_open_interest": 0,
            "call_volume": 0,
            "put_volume": 0,
            "call_gex": 0.0,
            "put_gex": 0.0,
        }
    )
    expiry_map: dict[tuple[str, int], object] = {}

    for contract in contracts:
        key = (contract.expiration_date.isoformat(), contract.days_to_expiration)
        expiry_map[key] = contract.expiration_date
        bucket = buckets[key]
        gex = _gex_notional_per_1pct_move(underlying_price, contract)
        if contract.option_type == "call":
            bucket["call_open_interest"] += contract.open_interest
            bucket["call_volume"] += contract.total_volume
            bucket["call_gex"] += gex
        else:
            bucket["put_open_interest"] += contract.open_interest
            bucket["put_volume"] += contract.total_volume
            bucket["put_gex"] += gex

    overview: list[ExpiryOverview] = []
    for key in sorted(buckets, key=lambda item: (item[1], item[0])):
        values = buckets[key]
        expiration_date = expiry_map[key]
        overview.append(
            ExpiryOverview(
                expiration_date=expiration_date,
                days_to_expiration=key[1],
                call_open_interest=int(values["call_open_interest"]),
                put_open_interest=int(values["put_open_interest"]),
                call_volume=int(values["call_volume"]),
                put_volume=int(values["put_volume"]),
                call_gex=values["call_gex"],
                put_gex=values["put_gex"],
                net_gex=values["call_gex"] + values["put_gex"],
            )
        )
    return tuple(overview)


def _estimate_gex_flip(rows: tuple[StrikeOverview, ...]) -> float | None:
    if not rows:
        return None
    for previous, current in zip(rows, rows[1:]):
        prev_value = previous.net_gex
        curr_value = current.net_gex
        if prev_value == 0:
            return previous.strike
        if prev_value * curr_value < 0:
            span = current.strike - previous.strike
            total = abs(prev_value) + abs(curr_value)
            if span <= 0 or total == 0:
                return previous.strike
            weight = abs(prev_value) / total
            return previous.strike + (span * weight)
    return min(rows, key=lambda row: abs(row.net_gex)).strike


def _source_iv_skew_available(calls: list[OptionContract], puts: list[OptionContract]) -> bool:
    put_ivs = {
        (contract.expiration_date, contract.strike): contract.implied_volatility
        for contract in puts
        if contract.implied_volatility is not None
    }
    paired_count = 0
    mirrored_count = 0
    for contract in calls:
        if contract.implied_volatility is None:
            continue
        put_iv = put_ivs.get((contract.expiration_date, contract.strike))
        if put_iv is None:
            continue
        paired_count += 1
        if math.isclose(contract.implied_volatility, put_iv, rel_tol=0.0, abs_tol=1e-9):
            mirrored_count += 1
    return paired_count > 0 and mirrored_count < paired_count


def _compute_prev_day_signal(
    previous_session_bar: dict[str, object] | None,
    spot: float,
) -> tuple[float, float | None, float | None, str]:
    if previous_session_bar is None:
        return 0.0, None, None, "Previous-session metrics unavailable."

    open_price = _as_float(previous_session_bar.get("open"))
    high_price = _as_float(previous_session_bar.get("high"))
    low_price = _as_float(previous_session_bar.get("low"))
    close_price = _as_float(previous_session_bar.get("close"))
    session_date = previous_session_bar.get("time")
    if (
        open_price is None
        or high_price is None
        or low_price is None
        or close_price is None
        or close_price <= 0
        or high_price <= low_price
    ):
        return 0.0, None, None, "Previous-session metrics unavailable."

    prior_range = high_price - low_price
    range_pct = prior_range / close_price if close_price > 0 else None
    gap_pct = (spot - close_price) / close_price if close_price > 0 else None
    midpoint = (high_price + low_price) / 2.0
    candle_bias = previous_session_bias_score(open_price, high_price, low_price, close_price) / 30.0
    gap_score = _clamp((spot - close_price) / max(prior_range, close_price * 0.006), -1.0, 1.0)
    location_score = _clamp((spot - midpoint) / max(prior_range / 2.0, close_price * 0.003), -1.0, 1.0)
    prev_day_score = _clamp((candle_bias * 0.45) + (gap_score * 0.35) + (location_score * 0.20), -1.0, 1.0)

    position_note = "inside yesterday's range"
    if spot > high_price:
        position_note = "above yesterday's high"
    elif spot < low_price:
        position_note = "below yesterday's low"

    session_label = session_date.isoformat() if hasattr(session_date, "isoformat") else "prior session"
    detail = (
        f"{session_label}: gap {gap_pct:+.2%}, prior range {prior_range:.2f} ({range_pct:.2%}), "
        f"spot is {position_note}."
    )
    return prev_day_score, gap_pct, range_pct, detail


def analyze_chain(
    contracts: list[OptionContract],
    underlying_price: float,
    price_closes: list[float] | None = None,
    previous_session_bar: dict[str, object] | None = None,
    min_dte: int = 0,
    max_dte: int = 30,
    strike_window: int = 8,
) -> AnalysisSnapshot:
    filtered_contracts = _filter_contracts(
        contracts=contracts,
        underlying_price=underlying_price,
        min_dte=min_dte,
        max_dte=max_dte,
        strike_window=strike_window,
    )
    if not filtered_contracts:
        raise ValueError("No option contracts matched the selected DTE and strike filters.")

    calls = [contract for contract in filtered_contracts if contract.option_type == "call"]
    puts = [contract for contract in filtered_contracts if contract.option_type == "put"]

    call_volume = sum(contract.total_volume for contract in calls)
    put_volume = sum(contract.total_volume for contract in puts)
    call_open_interest = sum(contract.open_interest for contract in calls)
    put_open_interest = sum(contract.open_interest for contract in puts)
    call_gamma = sum((contract.gamma or 0.0) * contract.open_interest for contract in calls)
    put_gamma = sum((contract.gamma or 0.0) * contract.open_interest for contract in puts)
    gamma_asymmetry_score = _directional_score(call_gamma, put_gamma)

    call_ivs = [contract.implied_volatility for contract in calls if contract.implied_volatility is not None]
    put_ivs = [contract.implied_volatility for contract in puts if contract.implied_volatility is not None]
    avg_call_iv = _mean(call_ivs)
    avg_put_iv = _mean(put_ivs)
    iv_skew_available = _source_iv_skew_available(calls, puts)

    spreads = [contract.spread_pct for contract in filtered_contracts if contract.spread_pct is not None]
    average_spread_pct = _mean([spread for spread in spreads if spread is not None])
    liquidity_score = 1.0
    if average_spread_pct is not None:
        liquidity_score = _clamp(1.0 - (average_spread_pct / 0.20), 0.0, 1.0)

    iv_skew_score = 0.0
    if avg_call_iv is not None and avg_put_iv is not None:
        iv_skew_score = _clamp((avg_call_iv - avg_put_iv) / 0.15, -1.0, 1.0)

    trend_score = 0.0
    if price_closes and len(price_closes) >= 2 and price_closes[0] > 0:
        short_return = (price_closes[-1] - price_closes[0]) / price_closes[0]
        trend_score = _clamp(short_return / 0.03, -1.0, 1.0)

    strike_overview = build_strike_overview(filtered_contracts, underlying_price)
    gross_gex = sum(abs(row.call_gex) + abs(row.put_gex) for row in strike_overview)
    net_gex = sum(row.net_gex for row in strike_overview)
    gex_ratio = _clamp(net_gex / gross_gex, -1.0, 1.0) if gross_gex > 0 else 0.0
    volume_score = _directional_score(call_volume, put_volume)
    gex_vol_score = _clamp((gex_ratio * 0.7) + (volume_score * 0.3), -1.0, 1.0)
    prev_day_score, prev_day_gap_pct, prev_day_range_pct, prev_day_detail = _compute_prev_day_signal(
        previous_session_bar,
        underlying_price,
    )

    # Enhanced volume and pressure analysis
    volume_distribution = _analyze_volume_distribution(filtered_contracts, underlying_price)
    pressure_analysis = _analyze_flow_pressure(filtered_contracts, underlying_price)
    liquidity_depth = _analyze_liquidity_depth(filtered_contracts)
    decay_exposure = _analyze_decay_exposure(filtered_contracts)

    # Enhanced scoring incorporating new metrics
    enhanced_volume_score = _clamp(
        (volume_score * 0.6) + (volume_distribution["volume_skew_score"] * 0.4), -1.0, 1.0
    )

    # Buying vs selling pressure score
    buy_pressure = pressure_analysis["call_buy_pressure"] + pressure_analysis["put_buy_pressure"]
    sell_pressure = pressure_analysis["call_sell_pressure"] + pressure_analysis["put_sell_pressure"]
    pressure_score = _directional_score(buy_pressure, sell_pressure)

    # Decay pressure score
    decay_score = decay_exposure["decay_pressure_score"]

    # Enhanced liquidity score
    enhanced_liquidity_score = _clamp(
        (liquidity_score * 0.7) + (liquidity_depth["liquidity_score"] * 0.3), 0.0, 1.0
    )

    components = (
        BiasComponent(
            name="Enhanced Volume Analysis",
            score=enhanced_volume_score,
            weight=0.20,
            detail=(
                f"Volume skew {volume_distribution['volume_skew_score']:+.2f}; "
                f"calls {call_volume:,} vs puts {put_volume:,}."
            ),
        ),
        BiasComponent(
            name="Buying vs Selling Pressure",
            score=pressure_score,
            weight=0.16,
            detail=(
                f"Buy pressure {buy_pressure:,.0f} vs sell pressure {sell_pressure:,.0f}; "
                f"aggressive buy ratio {pressure_analysis['aggressive_buy_ratio']:.1%}."
            ),
        ),
        BiasComponent(
            name="Call vs Put Open Interest",
            score=_directional_score(call_open_interest, put_open_interest),
            weight=0.14,
            detail=f"Calls {call_open_interest:,} vs puts {put_open_interest:,} open interest.",
        ),
        BiasComponent(
            name="IV Skew",
            score=iv_skew_score,
            weight=0.12,
            detail=(
                "Side-specific IV skew unavailable from the current source feed."
                if not iv_skew_available
                else (
                    f"Call IV {avg_call_iv:.2%} vs put IV {avg_put_iv:.2%}."
                    if avg_call_iv is not None and avg_put_iv is not None
                    else "IV skew unavailable from the current contracts."
                )
            ),
        ),
        BiasComponent(
            name="Gamma Asymmetry",
            score=gamma_asymmetry_score,
            weight=0.12,
            detail=f"Call gamma estimate {call_gamma:,.1f} vs put gamma estimate {put_gamma:,.1f}.",
        ),
        BiasComponent(
            name="GEX / Volume",
            score=gex_vol_score,
            weight=0.12,
            detail=(
                f"Net GEX {net_gex:,.0f} on gross {gross_gex:,.0f}; "
                f"volume skew {volume_score:+.2f}."
                if gross_gex > 0
                else "GEX unavailable; falling back to neutral."
            ),
        ),
        BiasComponent(
            name="Time Decay Pressure",
            score=decay_score,
            weight=0.08,
            detail=(
                f"Net theta exposure {decay_exposure['net_theta_exposure']:,.0f}; "
                f"short-term decay {decay_exposure['short_term_decay_ratio']:.1%}."
            ),
        ),
        BiasComponent(
            name="Prev-Day Structure",
            score=prev_day_score,
            weight=0.06,
            detail=prev_day_detail,
        ),
    )

    score = sum(component.contribution for component in components)
    label = classify_core_bias_label(score)

    greek_ready = [
        contract
        for contract in filtered_contracts
        if contract.delta is not None and contract.gamma is not None and contract.implied_volatility is not None
    ]
    data_quality = len(greek_ready) / len(filtered_contracts)
    conviction = min(abs(score) / 60.0, 1.0)
    confidence = round(
        100.0 * ((conviction * 0.50) + (data_quality * 0.20) + (enhanced_liquidity_score * 0.30)),
        1,
    )

    gex_flip_estimate = _estimate_gex_flip(strike_overview)

    call_wall = None
    put_wall = None
    gex_peak = None
    if strike_overview:
        call_wall = max(strike_overview, key=lambda row: row.call_open_interest).strike
        put_wall = max(strike_overview, key=lambda row: row.put_open_interest).strike
        gex_peak = max(
            strike_overview,
            key=lambda row: abs(row.call_gex) + abs(row.put_gex),
        ).strike

    metrics = ChainMetrics(
        underlying_price=underlying_price,
        total_contract_count=len(contracts),
        filtered_contract_count=len(filtered_contracts),
        call_volume=call_volume,
        put_volume=put_volume,
        call_open_interest=call_open_interest,
        put_open_interest=put_open_interest,
        put_call_volume_ratio=_safe_ratio(put_volume, call_volume),
        put_call_open_interest_ratio=_safe_ratio(put_open_interest, call_open_interest),
        average_call_iv=avg_call_iv,
        average_put_iv=avg_put_iv,
        iv_skew_available=iv_skew_available,
        average_spread_pct=average_spread_pct,
        liquidity_score=liquidity_score,
        enhanced_liquidity_score=enhanced_liquidity_score,
        effective_spread_score=liquidity_depth["effective_spread_score"],
        volume_concentration=liquidity_depth["volume_concentration"],
        expected_move=_estimate_expected_move(filtered_contracts, underlying_price),
        call_wall=call_wall,
        put_wall=put_wall,
        gex_peak=gex_peak,
        gex_flip_estimate=gex_flip_estimate,
        gamma_asymmetry_score=gamma_asymmetry_score,
        gex_vol_score=gex_vol_score,
        pressure_score=pressure_score,
        aggressive_buy_ratio=pressure_analysis["aggressive_buy_ratio"],
        volume_skew_score=volume_distribution["volume_skew_score"],
        near_money_volume_ratio=volume_distribution["near_money_volume_ratio"],
        decay_pressure_score=decay_score,
        negative_gamma_nearest_dist=_nearest_negative_gamma_distance(strike_overview, underlying_price),
        prev_day_score=prev_day_score if previous_session_bar is not None else None,
        prev_day_gap_pct=prev_day_gap_pct,
        prev_day_range_pct=prev_day_range_pct,
    )

    return AnalysisSnapshot(
        result=BiasResult(label=label, score=round(score, 1), confidence=confidence, components=components),
        metrics=metrics,
        filtered_contracts=tuple(filtered_contracts),
        strike_overview=strike_overview,
    )


def build_empty_analysis_snapshot(
    *,
    underlying_price: float,
    total_contract_count: int,
) -> AnalysisSnapshot:
    return AnalysisSnapshot(
        result=BiasResult(label="mixed", score=0.0, confidence=0.0, components=tuple()),
        metrics=ChainMetrics(
            underlying_price=underlying_price,
            total_contract_count=total_contract_count,
            filtered_contract_count=0,
            call_volume=0,
            put_volume=0,
            call_open_interest=0,
            put_open_interest=0,
            put_call_volume_ratio=None,
            put_call_open_interest_ratio=None,
            average_call_iv=None,
            average_put_iv=None,
            iv_skew_available=False,
            average_spread_pct=None,
            liquidity_score=0.0,
            expected_move=None,
            call_wall=None,
            put_wall=None,
            gex_peak=None,
            gex_flip_estimate=None,
        ),
        filtered_contracts=tuple(),
        strike_overview=tuple(),
    )
