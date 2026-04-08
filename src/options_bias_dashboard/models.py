from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    option_type: str
    strike: float
    expiration_date: date
    days_to_expiration: int
    bid: float | None
    ask: float | None
    mark: float | None
    total_volume: int
    open_interest: int
    implied_volatility: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    in_the_money: bool

    @property
    def spread_pct(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        midpoint = self.mark
        if midpoint is None or midpoint <= 0:
            midpoint = (self.bid + self.ask) / 2.0
        if midpoint <= 0:
            return None
        return max(self.ask - self.bid, 0.0) / midpoint


@dataclass(frozen=True)
class BiasComponent:
    name: str
    score: float
    weight: float
    detail: str

    @property
    def contribution(self) -> float:
        return self.score * self.weight * 100.0


@dataclass(frozen=True)
class BiasResult:
    label: str
    score: float
    confidence: float
    components: tuple[BiasComponent, ...]

    def top_drivers(self, count: int = 3) -> tuple[BiasComponent, ...]:
        ordered = sorted(self.components, key=lambda component: abs(component.contribution), reverse=True)
        return tuple(ordered[:count])


@dataclass(frozen=True)
class StrikeOverview:
    strike: float
    call_open_interest: int
    put_open_interest: int
    call_volume: int
    put_volume: int
    call_gex: float
    put_gex: float
    net_gex: float


@dataclass(frozen=True)
class CharmStrikeOverview:
    strike: float
    call_charm: float
    put_charm: float
    net_charm: float


@dataclass(frozen=True)
class ExpiryOverview:
    expiration_date: date
    days_to_expiration: int
    call_open_interest: int
    put_open_interest: int
    call_volume: int
    put_volume: int
    call_gex: float
    put_gex: float
    net_gex: float


@dataclass(frozen=True)
class ChainMetrics:
    underlying_price: float
    total_contract_count: int
    filtered_contract_count: int
    call_volume: int
    put_volume: int
    call_open_interest: int
    put_open_interest: int
    put_call_volume_ratio: float | None
    put_call_open_interest_ratio: float | None
    average_call_iv: float | None
    average_put_iv: float | None
    iv_skew_available: bool
    average_spread_pct: float | None
    liquidity_score: float
    enhanced_liquidity_score: float | None = None
    effective_spread_score: float | None = None
    volume_concentration: float | None = None
    expected_move: float | None = None
    call_wall: float | None = None
    put_wall: float | None = None
    gex_peak: float | None = None
    gex_flip_estimate: float | None = None
    gamma_asymmetry_score: float | None = None
    gex_vol_score: float | None = None
    pressure_score: float | None = None
    aggressive_buy_ratio: float | None = None
    volume_skew_score: float | None = None
    near_money_volume_ratio: float | None = None
    decay_pressure_score: float | None = None
    negative_gamma_nearest_dist: float | None = None
    prev_day_score: float | None = None
    prev_day_gap_pct: float | None = None
    prev_day_range_pct: float | None = None


@dataclass(frozen=True)
class ConvictionContext:
    symbol: str
    present_metrics: int
    available_metrics: int
    neutral_metrics: int
    aligned_metrics: int
    opposing_metrics: int
    supporting_metrics: tuple[str, ...]
    conflicting_metrics: tuple[str, ...]
    neutral_metric_names: tuple[str, ...]
    unavailable_metrics: tuple[str, ...]


@dataclass(frozen=True)
class IntradayTradePlan:
    bias_bucket_label: str
    vix_regime_label: str
    gex_regime_label: str
    charm_regime_label: str
    preferred_trade: str
    hold_guidance: str
    trust_guidance: str
    confidence_label: str
    conviction_label: str
    conviction_note: str
    conviction_contexts: tuple[ConvictionContext, ...]
    summary: str
    notes: tuple[str, ...]


@dataclass(frozen=True)
class AnalysisSnapshot:
    result: BiasResult
    metrics: ChainMetrics
    filtered_contracts: tuple[OptionContract, ...]
    strike_overview: tuple[StrikeOverview, ...]
