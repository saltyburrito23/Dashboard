from __future__ import annotations

import html
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from options_bias_dashboard.analytics import (
    analyze_chain,
    build_charm_strike_overview,
    build_empty_analysis_snapshot,
    build_gex_expiry_overview,
    build_strike_overview,
    classify_bias_bucket,
)
from options_bias_dashboard.app_config import load_settings
from options_bias_dashboard.conviction import build_conviction_context
# Favorites functionality removed to reduce memory usage on Streamlit Cloud
# # Favorites functionality removed to reduce memory usage on Streamlit Cloud
# from options_bias_dashboard.favorites import FavoriteQuote, build_favorite_quotes, load_favorite_symbols, toggle_favorite_symbol
from options_bias_dashboard.models import AnalysisSnapshot, BiasComponent, CharmStrikeOverview, ConvictionContext, ExpiryOverview, IntradayTradePlan, StrikeOverview
from options_bias_dashboard.normalization import extract_intraday_range, extract_regular_session_closes, normalize_option_chain
from options_bias_dashboard.schwab_service import SchwabApiError, SchwabResearchClient
from options_bias_dashboard.price_targets import build_directional_targets, build_price_target_ladder, previous_session_bias_score
from options_bias_dashboard.session_levels import PremarketStructure, build_premarket_structure, latest_completed_daily_bar
from options_bias_dashboard.trade_plan import build_intraday_trade_plan
from options_bias_dashboard.volatility import VolatilityPanel, build_volatility_panel, parse_batch_prev_closes, parse_batch_quotes

CALL_COLOR = "#0F766E"
PUT_COLOR = "#B42318"
NET_COLOR = "#1D4ED8"
POSITIVE_COLOR = "#15803D"
NEGATIVE_COLOR = "#B91C1C"
ZERO_LINE_COLOR = "#475569"
GRID_COLOR = "#D8E1EB"
PLOT_BG_COLOR = "#141826"
PAPER_BG_COLOR = "#141826"
CHART_TEXT_COLOR = "#E2E8F0"
CONVICTION_SYMBOLS = ("SPY", "QQQ", "SOXX", "USO", "VIX")


def format_currency(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.2f}"


def format_percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def format_multiple(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}x"


def format_signed_currency(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}"


def _as_float(value: object) -> float | None:
    if value in (None, "", "NaN"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_previous_close_from_quote(payload: dict[str, object]) -> float | None:
    candidates: list[object] = []
    if isinstance(payload.get("quote"), dict):
        quote = payload["quote"]
        candidates.extend([quote.get("closePrice"), quote.get("previousClose")])
    elif len(payload) == 1:
        first_payload = next(iter(payload.values()))
        if isinstance(first_payload, dict) and isinstance(first_payload.get("quote"), dict):
            quote = first_payload["quote"]
            candidates.extend([quote.get("closePrice"), quote.get("previousClose")])
    for candidate in candidates:
        value = _as_float(candidate)
        if value is not None and value > 0:
            return value
    return None


def apply_chart_theme(figure: go.Figure, *, hovermode: str = "x unified") -> go.Figure:
    figure.update_layout(
        plot_bgcolor=PLOT_BG_COLOR,
        paper_bgcolor=PAPER_BG_COLOR,
        hovermode=hovermode,
        font=dict(color=CHART_TEXT_COLOR),
        legend_title="Series",
    )
    figure.update_xaxes(showline=True, linecolor="#94A3B8", ticks="outside", showgrid=False)
    figure.update_yaxes(showline=True, linecolor="#94A3B8", ticks="outside", gridcolor=GRID_COLOR, zeroline=False)
    return figure


def build_chart_count_options(available_count: int) -> tuple[list[int], int]:
    if available_count <= 0:
        return [0], 0
    options = list(range(10, available_count + 1, 10))
    if not options or options[-1] != available_count:
        options.append(available_count)
    preferred = available_count if available_count <= 20 else 20
    if preferred not in options:
        preferred = options[-1]
    return options, options.index(preferred)


def limit_rows_by_strike_distance(rows: list[object], spot: float, limit: int) -> list[object]:
    if limit <= 0 or len(rows) <= limit:
        return list(rows)
    nearest = sorted(rows, key=lambda row: abs(getattr(row, "strike") - spot))[:limit]
    return sorted(nearest, key=lambda row: getattr(row, "strike"))


def limit_expiry_rows(rows: list[ExpiryOverview], limit: int) -> list[ExpiryOverview]:
    if limit <= 0 or len(rows) <= limit:
        return list(rows)
    return list(rows[:limit])


# Favorite change color removed (favorites disabled)


def render_favorite_cards(quotes) -> None:
    # Favorites functionality disabled to reduce memory usage
    pass


def build_open_interest_figure(rows: list[StrikeOverview]) -> go.Figure:
    figure = go.Figure()
    strikes = [row.strike for row in rows]
    figure.add_bar(
        name="Call OI",
        x=strikes,
        y=[row.call_open_interest for row in rows],
        marker_color=CALL_COLOR,
        marker_line_color="#0B5D58",
        marker_line_width=0.6,
        hovertemplate="Strike %{x}<br>Call OI %{y:,.0f}<extra></extra>",
    )
    figure.add_bar(
        name="Put OI",
        x=strikes,
        y=[-row.put_open_interest for row in rows],
        marker_color=PUT_COLOR,
        marker_line_color="#8B1E1E",
        marker_line_width=0.6,
        hovertemplate="Strike %{x}<br>Put OI %{customdata:,.0f}<extra></extra>",
        customdata=[row.put_open_interest for row in rows],
    )
    figure.update_layout(
        barmode="relative",
        title="Open Interest by Strike",
        xaxis_title="Strike",
        yaxis_title="Contracts",
        legend_title="Side",
        margin=dict(l=20, r=20, t=50, b=20),
    )
    figure.add_hline(y=0.0, line_color=ZERO_LINE_COLOR, line_width=2)
    return apply_chart_theme(figure)


def build_gamma_figure(
    rows: list[StrikeOverview],
    *,
    call_wall: float | None = None,
    put_wall: float | None = None,
    gex_flip: float | None = None,
) -> go.Figure:
    figure = go.Figure()
    strikes = [row.strike for row in rows]
    figure.add_bar(
        name="Call GEX",
        x=strikes,
        y=[row.call_gex for row in rows],
        marker_color=CALL_COLOR,
        marker_line_color="#0B5D58",
        marker_line_width=0.6,
        hovertemplate="Strike %{x}<br>Call GEX %{y:,.0f}<extra></extra>",
    )
    figure.add_bar(
        name="Put GEX",
        x=strikes,
        y=[row.put_gex for row in rows],
        marker_color=PUT_COLOR,
        marker_line_color="#8B1E1E",
        marker_line_width=0.6,
        hovertemplate="Strike %{x}<br>Put GEX %{y:,.0f}<extra></extra>",
    )
    figure.add_scatter(
        name="Net GEX",
        x=strikes,
        y=[row.net_gex for row in rows],
        mode="lines+markers",
        line=dict(color=NET_COLOR, width=3),
        marker=dict(size=7, symbol="diamond"),
        hovertemplate="Strike %{x}<br>Net GEX %{y:,.0f}<extra></extra>",
    )
    figure.update_layout(
        barmode="relative",
        title="Gamma Exposure by Strike (Estimated GEX)",
        xaxis_title="Strike",
        yaxis_title="Estimated GEX ($ delta notional / 1% move)",
        margin=dict(l=20, r=20, t=50, b=20),
    )
    figure.add_hline(y=0.0, line_color=ZERO_LINE_COLOR, line_width=2)
    line_specs = (
        ("Put Wall", put_wall, PUT_COLOR),
        ("Call Wall", call_wall, CALL_COLOR),
        ("GEX Flip", gex_flip, NET_COLOR),
    )
    for label, strike, color in line_specs:
        if strike is None:
            continue
        figure.add_vline(
            x=strike,
            line_color=color,
            line_width=3 if label == "GEX Flip" else 2.5,
            line_dash="dot" if label == "GEX Flip" else "dash",
            opacity=0.95,
            annotation_text=label,
            annotation_position="top",
            annotation_font_color="#FFFFFF",
            annotation_bgcolor=color,
        )
    return apply_chart_theme(figure)


def build_gamma_expiry_figure(rows: list[ExpiryOverview]) -> go.Figure:
    figure = go.Figure()
    labels = [f"{row.expiration_date.isoformat()} ({row.days_to_expiration}DTE)" for row in rows]
    figure.add_bar(
        name="Call GEX",
        x=labels,
        y=[row.call_gex for row in rows],
        marker_color=CALL_COLOR,
        marker_line_color="#0B5D58",
        marker_line_width=0.6,
        hovertemplate="Expiry %{x}<br>Call GEX %{y:,.0f}<extra></extra>",
    )
    figure.add_bar(
        name="Put GEX",
        x=labels,
        y=[row.put_gex for row in rows],
        marker_color=PUT_COLOR,
        marker_line_color="#8B1E1E",
        marker_line_width=0.6,
        hovertemplate="Expiry %{x}<br>Put GEX %{y:,.0f}<extra></extra>",
    )
    figure.add_scatter(
        name="Net GEX",
        x=labels,
        y=[row.net_gex for row in rows],
        mode="lines+markers",
        line=dict(color=NET_COLOR, width=3),
        marker=dict(size=7, symbol="diamond"),
        hovertemplate="Expiry %{x}<br>Net GEX %{y:,.0f}<extra></extra>",
    )
    figure.update_layout(
        barmode="relative",
        title="Gamma Exposure by Expiry (Estimated GEX)",
        xaxis_title="Expiry",
        yaxis_title="Estimated GEX ($ delta notional / 1% move)",
        margin=dict(l=20, r=20, t=50, b=20),
    )
    figure.update_xaxes(type="category")
    figure.add_hline(y=0.0, line_color=ZERO_LINE_COLOR, line_width=2)
    return apply_chart_theme(figure, hovermode="closest")


def build_charm_figure(rows: list[CharmStrikeOverview]) -> go.Figure:
    figure = go.Figure()
    strikes = [row.strike for row in rows]
    figure.add_bar(
        name="Call Charm",
        x=strikes,
        y=[row.call_charm for row in rows],
        marker_color=CALL_COLOR,
        marker_line_color="#0B5D58",
        marker_line_width=0.6,
        hovertemplate="Strike %{x}<br>Call Charm %{y:,.0f}<extra></extra>",
    )
    figure.add_bar(
        name="Put Charm",
        x=strikes,
        y=[row.put_charm for row in rows],
        marker_color=PUT_COLOR,
        marker_line_color="#8B1E1E",
        marker_line_width=0.6,
        hovertemplate="Strike %{x}<br>Put Charm %{y:,.0f}<extra></extra>",
    )
    figure.add_scatter(
        name="Net Charm",
        x=strikes,
        y=[row.net_charm for row in rows],
        mode="lines+markers",
        line=dict(color=NET_COLOR, width=3),
        marker=dict(size=7, symbol="diamond"),
        hovertemplate="Strike %{x}<br>Net Charm %{y:,.0f}<extra></extra>",
    )
    figure.update_layout(
        barmode="relative",
        title="Charm by Strike (Estimated Delta Decay)",
        xaxis_title="Strike",
        yaxis_title="Estimated Charm ($ delta notional over decay horizon)",
        margin=dict(l=20, r=20, t=50, b=20),
    )
    figure.add_hline(y=0.0, line_color=ZERO_LINE_COLOR, line_width=2)
    return apply_chart_theme(figure)


def render_driver(component: BiasComponent) -> None:
    contribution = component.score * component.weight * 100.0
    st.write(f"- **{component.name}**: {component.detail} (`{contribution:+.1f}` impact)")


@st.cache_resource(show_spinner=False)
def get_client() -> SchwabResearchClient:
    return SchwabResearchClient(load_settings())


@st.cache_data(show_spinner=False)
def load_snapshot(
    symbol: str,
    min_dte: int,
    fetch_max_dte: int,
    strike_count: int,
    history_frequency: int,
    refresh_bucket: int,
    manual_nonce: int,
) -> dict[str, object]:
    del refresh_bucket, manual_nonce
    client = get_client()
    return client.fetch_snapshot(
        symbol=symbol,
        min_dte=min_dte,
        max_dte=fetch_max_dte,
        strike_count=strike_count,
        history_frequency=history_frequency,
    )


@st.cache_data(show_spinner=False)
def load_market_context(
    symbol: str,
    refresh_bucket: int,
    manual_nonce: int,
) -> dict[str, object] | None:
    del refresh_bucket, manual_nonce
    client = get_client()
    try:
        return client.fetch_market_context(symbol)
    except SchwabApiError:
        return None


# load_favorite_quotes removed (favorites functionality disabled)
# @st.cache_data(show_spinner=False)
# def load_favorite_quotes(
#     symbols: tuple[str, ...],
#     refresh_bucket: int,
#     manual_nonce: int,
# ) -> tuple[FavoriteQuote, ...]:
#     del refresh_bucket, manual_nonce
#     if not symbols:
#         return tuple()
#     client = get_client()
#     payload = client.fetch_quotes(list(symbols))
#     return build_favorite_quotes(symbols, payload)


def _symbol_variants(symbol: str) -> tuple[str, ...]:
    normalized = symbol.strip().upper()
    if not normalized:
        return tuple()
    variants = [normalized]
    stripped = normalized.removeprefix("$")
    if stripped and stripped not in variants:
        variants.append(stripped)
    if stripped and f"${stripped}" not in variants:
        variants.append(f"${stripped}")
    return tuple(variants)


@st.cache_data(show_spinner=False)
@st.cache_data(show_spinner=False)
def load_conviction_inputs(
    symbols: tuple[str, ...],
    min_dte: int,
    max_dte: int,
    strike_window: int,
    refresh_bucket: int,
    manual_nonce: int,
) -> dict[str, dict[str, object]]:
    del refresh_bucket, manual_nonce
    client = get_client()
    strike_count = max(strike_window * 2, 12)
    results: dict[str, dict[str, object]] = {}
    for requested_symbol in symbols:
        context: dict[str, object] = {
            "analysis": None,
            "charm_rows": tuple(),
            "resolved_symbol": None,
        }
        for variant in _symbol_variants(requested_symbol):
            try:
                raw = client.fetch_snapshot(
                    symbol=variant,
                    min_dte=min_dte,
                    max_dte=max_dte,
                    strike_count=strike_count,
                    history_frequency=5,
                )
            except Exception:
                continue
            contracts = normalize_option_chain(raw["chain"])
            closes = extract_regular_session_closes(raw.get("history", {}))
            try:
                analysis = analyze_chain(
                    contracts=contracts,
                    underlying_price=float(raw["underlying_price"]),
                    price_closes=closes,
                    min_dte=min_dte,
                    max_dte=max_dte,
                    strike_window=strike_window,
                )
            except ValueError:
                analysis = build_empty_analysis_snapshot(
                    underlying_price=float(raw["underlying_price"]),
                    total_contract_count=len(contracts),
                )
            charm_rows = build_charm_strike_overview(
                list(analysis.filtered_contracts),
                analysis.metrics.underlying_price,
                hours_remaining=None,
            )
            context = {
                "analysis": analysis,
                "charm_rows": charm_rows,
                "resolved_symbol": variant,
            }
            break
        results[requested_symbol] = context
    return results


def build_target_payload(
    *,
    spot: float,
    vol_panel: VolatilityPanel | None,
    bias_score: float,
    expected_move_chain: float | None,
    session_high: float | None,
    session_low: float | None,
    session_range: float | None,
    context_note: str | None = None,
) -> dict[str, object]:
    ladder = build_price_target_ladder(
        spot=spot,
        vol_panel=vol_panel,
        bias_score=bias_score,
        session_high=session_high,
        session_low=session_low,
        expected_move_chain=expected_move_chain,
    )
    upside_targets = build_directional_targets(ladder.spot, ladder.upside, direction="up")
    downside_targets = build_directional_targets(ladder.spot, ladder.downside, direction="down")
    return {
        "spot": ladder.spot,
        "base_em_dollars": ladder.base_em_dollars,
        "em_dollars": ladder.em_dollars,
        "em_source": ladder.em_source,
        "session_high": session_high,
        "session_low": session_low,
        "session_range": session_range,
        "structure_note": ladder.structure_note,
        "bias_score": ladder.bias_score,
        "up_scale": ladder.up_scale,
        "down_scale": ladder.down_scale,
        "context_note": context_note,
        "upside_targets": [asdict(target) for target in upside_targets],
        "downside_targets": [asdict(target) for target in downside_targets],
    }


def build_prior_session_target_payload(
    *,
    snapshot: dict[str, object],
    market_ctx: dict[str, object] | None,
    settings,
    as_of: datetime,
) -> dict[str, object] | None:
    prior_bar = None
    if market_ctx is not None:
        prior_bar = latest_completed_daily_bar(dict(market_ctx.get("equity_daily_history", {})), as_of=as_of)

    prior_close = prior_bar["close"] if prior_bar is not None else extract_previous_close_from_quote(dict(snapshot.get("quote", {})))
    if prior_close is None or prior_close <= 0:
        return None

    prior_open = _as_float(prior_bar.get("open")) if prior_bar is not None else prior_close
    prior_high = _as_float(prior_bar.get("high")) if prior_bar is not None else None
    prior_low = _as_float(prior_bar.get("low")) if prior_bar is not None else None
    prior_range = (prior_high - prior_low) if prior_high is not None and prior_low is not None else None
    prior_bias = previous_session_bias_score(prior_open, prior_high, prior_low, prior_close)

    prior_vol_panel: VolatilityPanel | None = None
    if market_ctx is not None:
        prev_close_quotes = parse_batch_prev_closes(dict(market_ctx.get("index_quotes", {})))
        prev_vix = prev_close_quotes.get("VIX")
        prev_vix1d = prev_close_quotes.get("VIX1D")
        if prev_vix is not None or prev_vix1d is not None:
            try:
                prior_session_now = (
                    datetime(
                        prior_bar["time"].year,
                        prior_bar["time"].month,
                        prior_bar["time"].day,
                        16,
                        0,
                        tzinfo=as_of.astimezone().tzinfo,
                    )
                    if prior_bar is not None
                    else as_of
                )
                prior_vol_panel = build_volatility_panel(
                    spot=prior_close,
                    vix=prev_vix,
                    vix1d=prev_vix1d,
                    contracts=[],
                    min_dte=0,
                    max_dte=0,
                    daily_history=dict(market_ctx.get("equity_daily_history", {})),
                    now=prior_session_now,
                    vix_odte_multiplier=settings.vix_odte_multiplier,
                )
            except ValueError:
                prior_vol_panel = None

    note_parts = []
    if prior_bar is not None:
        note_parts.append(f"Prior session: {prior_bar['time'].isoformat()}")
    note_parts.append(f"Previous close anchor {prior_close:.2f}")
    if prior_open is not None and prior_high is not None and prior_low is not None:
        note_parts.append(f"Daily candle O/H/L/C {prior_open:.2f}/{prior_high:.2f}/{prior_low:.2f}/{prior_close:.2f}")
    note_parts.append("Bias tilt comes from the previous completed daily candle.")

    return build_target_payload(
        spot=prior_close,
        vol_panel=prior_vol_panel,
        bias_score=prior_bias,
        expected_move_chain=None,
        session_high=prior_high,
        session_low=prior_low,
        session_range=prior_range,
        context_note=" | ".join(note_parts),
    )


def render_target_snapshot(
    title: str,
    payload: dict[str, object] | None,
    *,
    caption: str,
    empty_message: str,
) -> None:
    with st.container(border=True):
        st.subheader(title)
        if payload is None:
            st.info(empty_message)
            return
        st.caption(caption)
        if payload.get("session_high") is None or payload.get("session_low") is None:
            st.warning(
                "Intraday H/L is unavailable for this dynamic ladder; EM is derived from vol and chain inputs only."
            )
        upside_targets = payload.get("upside_targets") or []
        downside_targets = payload.get("downside_targets") or []

        st.markdown("**Upside**")
        upside_cols = st.columns(3)
        for col, target in zip(upside_cols, upside_targets):
            delta = float(target.get("delta_from_spot", 0.0))
            pct = float(target.get("percent_from_spot", 0.0))
            col.metric(
                str(target.get("label", "Up")),
                format_currency(float(target.get("price", 0.0))),
                f"{delta:+.2f} ({pct:+.2%})",
            )

        st.markdown("**Downside**")
        downside_cols = st.columns(3)
        for col, target in zip(downside_cols, downside_targets):
            delta = float(target.get("delta_from_spot", 0.0))
            pct = float(target.get("percent_from_spot", 0.0))
            col.metric(
                str(target.get("label", "Down")),
                format_currency(float(target.get("price", 0.0))),
                f"{delta:+.2f} ({pct:+.2%})",
            )

        st.markdown(
            "\n".join(
                line
                for line in [
                    f"- Base EM: **{format_currency(float(payload.get('base_em_dollars', 0.0)))}** from **{payload.get('em_source', 'n/a')}**",
                    f"- Target EM used: **{format_currency(float(payload.get('em_dollars', 0.0)))}**",
                    f"- Structure note: {payload.get('structure_note', 'n/a')}",
                    f"- Bias score: **{float(payload.get('bias_score', 0.0)):+.1f}** -> upside scale **x{float(payload.get('up_scale', 0.0)):.2f}**, downside **x{float(payload.get('down_scale', 0.0)):.2f}**",
                    (
                        f"- Spot / intraday H-L: **{format_currency(float(payload.get('spot', 0.0)))}**, "
                        f"**{format_currency(float(payload.get('session_high')) if payload.get('session_high') is not None else None)} / "
                        f"{format_currency(float(payload.get('session_low')) if payload.get('session_low') is not None else None)}** "
                        f"(range **{format_currency(float(payload.get('session_range')) if payload.get('session_range') is not None else None)}**)"
                        if payload.get("session_high") is not None and payload.get("session_low") is not None and payload.get("session_range") is not None
                        else f"- Spot: **{format_currency(float(payload.get('spot', 0.0)))}**"
                    ),
                    f"- Context: {payload.get('context_note')}" if payload.get("context_note") else "",
                ]
                if line
            )
        )


def main() -> None:
    settings = load_settings()
    # favorite_symbols = load_favorite_symbols()  # Disabled to reduce memory
    st.set_page_config(page_title="Dashboard V3 Web", layout="wide")
    st.title("Dashboard V3 Web")

    if "manual_refresh_nonce" not in st.session_state:
        st.session_state.manual_refresh_nonce = 0
    # Favorite feedback removed (favorites disabled)

    with st.sidebar:
        st.header("Controls")
        st.caption("Ticker")
        symbol = st.text_input(
            "Ticker",
            value=settings.default_symbol,
            label_visibility="collapsed",
        ).strip().upper()
        min_dte = st.number_input("Min DTE", min_value=0, max_value=365, value=settings.default_min_dte)
        max_dte = st.number_input("Max DTE", min_value=1, max_value=365, value=settings.default_max_dte)
        expiry_chart_max_dte = st.number_input(
            "Expiry chart max DTE",
            min_value=int(max_dte),
            max_value=365,
            value=max(int(max_dte), settings.default_expiry_chart_max_dte),
        )
        strike_window = st.slider("ATM strikes to score", min_value=4, max_value=24, value=settings.default_strike_window)
        strike_count = st.slider("API strike count", min_value=6, max_value=80, value=settings.default_strike_count)
        refresh_seconds = st.slider("Refresh seconds", min_value=10, max_value=120, value=settings.default_refresh_seconds)

        if st.button("Refresh now", width="stretch"):
            st.session_state.manual_refresh_nonce += 1

    if not settings.has_credentials:
        # Check if tokens file exists as alternative
        from pathlib import Path
        tokens_exist = (Path(__file__).parent / ".schwab_tokens.db").exists()
        if tokens_exist:
            st.info("Using Schwab tokens file for authentication.")
        else:
            st.error("Missing `APP_KEY` or `APP_SECRET` in `.env` and no tokens file found.")

    # Allow app to run if either credentials exist or tokens file exists
    from pathlib import Path
    tokens_file = Path(__file__).parent / ".schwab_tokens.db"
    has_auth = settings.has_credentials or tokens_file.exists()

    if not has_auth:
        st.info("Add your Schwab credentials to `.env` or ensure tokens file exists, then rerun the app.")
        st.stop()

    history_frequency = int(settings.history_frequency)
    
    # Disable auto-refresh on Streamlit Cloud (free tier has memory issues with refresh)
    # Users can manually refresh using the "Refresh now" button instead
    # st_autorefresh(interval=refresh_seconds * 1000, key=f"refresh-{symbol}")
    
    refresh_bucket = int(time.time() // refresh_seconds)

    try:
        with st.spinner(f"Loading live chain for {symbol}..."):
            snapshot = load_snapshot(
                symbol=symbol,
                min_dte=int(min_dte),
                fetch_max_dte=int(expiry_chart_max_dte),
                strike_count=int(strike_count),
                history_frequency=int(history_frequency),
                refresh_bucket=refresh_bucket,
                manual_nonce=st.session_state.manual_refresh_nonce,
            )
    except SchwabApiError as error:
        st.error(f"Schwab API Error: {str(error)}")
        st.stop()
    except Exception as error:
        st.error(f"Error loading snapshot: {str(error)}")
        st.stop()

    market_ctx = load_market_context(
        symbol=symbol,
        refresh_bucket=refresh_bucket,
        manual_nonce=st.session_state.manual_refresh_nonce,
    )

    # Favorite quotes loading removed (favorites functionality disabled)

    contracts = normalize_option_chain(snapshot["chain"])
    closes = extract_regular_session_closes(snapshot.get("history", {}))
    session_high, session_low, session_range = extract_intraday_range(snapshot.get("history", {}))
    previous_session_bar = (
        latest_completed_daily_bar(dict(market_ctx["equity_daily_history"]), as_of=datetime.now(timezone.utc))
        if market_ctx is not None
        else None
    )
    try:
        analysis = analyze_chain(
            contracts=contracts,
            underlying_price=snapshot["underlying_price"],
            price_closes=closes,
            previous_session_bar=previous_session_bar,
            min_dte=int(min_dte),
            max_dte=int(max_dte),
            strike_window=int(strike_window),
        )
    except ValueError as err:
        if "No option contracts matched" in str(err):
            analysis = build_empty_analysis_snapshot(
                underlying_price=float(snapshot["underlying_price"]),
                total_contract_count=len(contracts),
            )
            st.info(
                "No option contracts matched your DTE and strike filters for this ticker right now. "
                "The dashboard will stay visible and mark the unavailable option-derived fields as `n/a`."
            )
        else:
            raise

    vol_panel: VolatilityPanel | None = None
    vol_error: str | None = None
    if market_ctx is not None:
        try:
            quotes_map = parse_batch_quotes(dict(market_ctx["index_quotes"]))
            vol_panel = build_volatility_panel(
                spot=float(snapshot["underlying_price"]),
                vix=quotes_map.get("VIX"),
                vix1d=quotes_map.get("VIX1D"),
                contracts=contracts,
                min_dte=int(min_dte),
                max_dte=int(max_dte),
                daily_history=dict(market_ctx["equity_daily_history"]),
                vix_odte_multiplier=settings.vix_odte_multiplier,
            )
        except ValueError as err:
            vol_error = str(err)
    else:
        vol_error = "Could not load $VIX / $VIX1D quotes (Schwab error)."

    charm_rows = build_charm_strike_overview(
        list(analysis.filtered_contracts),
        analysis.metrics.underlying_price,
        hours_remaining=vol_panel.hours_remaining if vol_panel is not None else None,
    )
    
    try:
        conviction_inputs_raw = load_conviction_inputs(
            CONVICTION_SYMBOLS,
            min_dte=int(min_dte),
            max_dte=int(max_dte),
            strike_window=int(strike_window),
            refresh_bucket=refresh_bucket,
            manual_nonce=st.session_state.manual_refresh_nonce,
        )
        conviction_contexts: tuple[ConvictionContext, ...] = tuple(
            build_conviction_context(
                symbol=requested_symbol,
                analysis=(
                    analysis
                    if requested_symbol.removeprefix("$") == symbol.removeprefix("$")
                    else conviction_inputs_raw[requested_symbol]["analysis"]
                ),
                charm_rows=(
                    charm_rows
                    if requested_symbol.removeprefix("$") == symbol.removeprefix("$")
                    else conviction_inputs_raw[requested_symbol]["charm_rows"]
                ),
                primary_bias_score=analysis.result.score,
            )
            for requested_symbol in CONVICTION_SYMBOLS
        )
    except Exception as e:
        # Conviction loading can timeout on Streamlit Cloud, provide graceful fallback
        conviction_contexts = tuple()
    chart_contracts = [
        contract
        for contract in contracts
        if int(min_dte) <= contract.days_to_expiration <= int(max_dte) and contract.strike > 0
    ]
    expiry_chart_contracts = [
        contract
        for contract in contracts
        if int(min_dte) <= contract.days_to_expiration <= int(expiry_chart_max_dte) and contract.strike > 0
    ]
    chart_strike_rows = build_strike_overview(chart_contracts, analysis.metrics.underlying_price)
    chart_charm_rows = build_charm_strike_overview(
        chart_contracts,
        analysis.metrics.underlying_price,
        hours_remaining=vol_panel.hours_remaining if vol_panel is not None else None,
    )
    chart_expiry_rows = build_gex_expiry_overview(expiry_chart_contracts, analysis.metrics.underlying_price)
    trade_plan = build_intraday_trade_plan(
        analysis,
        vol_panel,
        charm_rows,
        conviction_contexts=conviction_contexts,
    )
    premarket_structure = build_premarket_structure(
        intraday_history=dict(snapshot.get("history", {})),
        daily_history=dict(market_ctx["equity_daily_history"]) if market_ctx is not None else {},
        as_of=datetime.now(timezone.utc),
    )
    dynamic_target_payload = build_target_payload(
        spot=float(snapshot["underlying_price"]),
        vol_panel=vol_panel,
        bias_score=analysis.result.score,
        expected_move_chain=analysis.metrics.expected_move,
        session_high=session_high,
        session_low=session_low,
        session_range=session_range,
        context_note="Live session ladder using current spot, live intraday range, and the current option/vol stack.",
    )
    captured_at = datetime.now(timezone.utc)
    prior_session_target_payload = build_prior_session_target_payload(
        snapshot=snapshot,
        market_ctx=market_ctx,
        settings=settings,
        as_of=captured_at,
    )

    overview_tab, plan_tab, charts_tab, targets_tab = st.tabs(["Overview", "Plan", "Charts", "Targets"])

    with overview_tab:
        render_overview(snapshot, analysis, refresh_seconds, premarket_structure)

    with plan_tab:
        render_intraday_volatility(vol_panel, vol_error)
        render_intraday_trade_plan(trade_plan)

    with charts_tab:
        render_details(
            analysis,
            chart_strike_rows,
            chart_charm_rows,
            chart_expiry_rows,
        )

    with targets_tab:
        render_target_snapshot(
            "Static predictions from previous session close",
            prior_session_target_payload,
            caption="Built directly from the latest completed daily candle and prior-session VIX/VIX1D closes when available.",
            empty_message="Previous-session close data is unavailable for this ticker right now.",
        )
        render_target_snapshot(
            "Dynamic predictions (current daily ladder)",
            dynamic_target_payload,
            caption="Uses the current live session inputs and adapts throughout the day.",
            empty_message="Current dynamic predictions are unavailable.",
        )


def render_overview(
    snapshot: dict[str, object],
    analysis: AnalysisSnapshot,
    refresh_seconds: int,
    premarket_structure: PremarketStructure,
) -> None:
    result = analysis.result
    metrics = analysis.metrics
    fetched_at = datetime.fromisoformat(str(snapshot["fetched_at"]))
    bias_bucket_label, _ = classify_bias_bucket(result.score)

    top = st.columns(5)
    top[0].metric("Bias", bias_bucket_label)
    top[1].metric("Score", f"{result.score:+.1f}")
    top[2].metric("Confidence", f"{result.confidence:.0f}/100")
    top[3].metric("Spot", format_currency(metrics.underlying_price))
    top[4].metric("Expected Move", format_currency(metrics.expected_move))

    second = st.columns(5)
    second[0].metric("P/C Volume Ratio", f"{metrics.put_call_volume_ratio:.2f}" if metrics.put_call_volume_ratio is not None else "n/a")
    second[1].metric("P/C OI Ratio", f"{metrics.put_call_open_interest_ratio:.2f}" if metrics.put_call_open_interest_ratio is not None else "n/a")
    second[2].metric("Liquidity Score", f"{metrics.liquidity_score * 100:.0f}/100")
    second[3].metric("Call Wall", f"{metrics.call_wall:.2f}" if metrics.call_wall is not None else "n/a")
    second[4].metric("Put Wall", f"{metrics.put_wall:.2f}" if metrics.put_wall is not None else "n/a")

    st.caption(
        " | ".join(
            [
                f"Last refresh: {fetched_at.strftime('%Y-%m-%d %H:%M:%S')}",
                f"Refresh interval: {refresh_seconds}s",
                f"Contracts scored: {metrics.filtered_contract_count}/{metrics.total_contract_count}",
                f"GEX flip estimate: {metrics.gex_flip_estimate:.2f}" if metrics.gex_flip_estimate is not None else "GEX flip estimate: n/a",
            ]
        )
    )

    gauge_cols = st.columns(2)
    gauge_cols[0].caption("Confidence")
    gauge_cols[0].progress(int(round(result.confidence)))
    gauge_cols[1].caption("Liquidity")
    gauge_cols[1].progress(int(round(metrics.liquidity_score * 100)))

    health_warnings: list[str] = []
    if not metrics.iv_skew_available:
        health_warnings.append("IV skew is unavailable for the current contracts.")
    if metrics.liquidity_score < 0.70:
        health_warnings.append("Liquidity is weak; wide spreads may hurt execution.")
    if metrics.filtered_contract_count < 120:
        health_warnings.append("Limited contract coverage in the selected strike/DTE window.")
    if health_warnings:
        st.warning("Data health notice: " + " ".join(health_warnings))

    structure_cols = st.columns(5)
    structure_cols[0].metric("YDL", format_currency(premarket_structure.yesterday_low))
    structure_cols[1].metric("PML", format_currency(premarket_structure.premarket_low))
    structure_cols[2].metric("PMH", format_currency(premarket_structure.premarket_high))
    structure_cols[3].metric("YDH", format_currency(premarket_structure.yesterday_high))
    structure_cols[4].metric("Pre-Market Structure", premarket_structure.label)

    with st.container(border=True):
        st.subheader("Pre-Market Structure")
        if premarket_structure.premarket_session_date is not None:
            st.caption(f"Pre-market session: {premarket_structure.premarket_session_date}")
        if premarket_structure.prior_session_date is not None:
            st.caption(f"Reference RTH session: {premarket_structure.prior_session_date}")
        st.write(premarket_structure.description)

    with st.container(border=True):
        st.subheader("Why The Model Leans This Way")
        st.caption(
            "Component impacts are weighted contributions. Live display uses five bias buckets: "
            "Strong Bullish, Bullish Lean, Neutral/Mixed, Bearish Lean, and Strong Bearish. "
            "The old hard bullish/bearish flip still happens at roughly ±18."
        )
        drivers = result.top_drivers()
        if not drivers:
            st.write("- No option-derived drivers are available for the current ticker/filter set.")
        for component in drivers:
            render_driver(component)


def render_intraday_volatility(panel: VolatilityPanel | None, error: str | None) -> None:
    with st.container(border=True):
        st.subheader("Intraday volatility (VIX hierarchy + session σ)")
        if error and panel is None:
            st.warning(error)
            st.caption(
                "Uses **VIX1D** when Schwab returns it; else **VIX × VIX_ODTE_MULTIPLIER** as a 0DTE proxy. "
                "Requires index quotes plus daily history on the ticker for clustering."
            )
            return
        if panel is None:
            return

        st.caption(
            f"{panel.sigma_source_label}. "
            f"Intraday acceleration: ×{panel.accel_multiplier:.3f} (0.6 coeff, 1.8× cap). "
            f"DOW ×{panel.dow_multiplier:.2f}; clustering ×{panel.cluster_multiplier:.2f} — {panel.cluster_note}"
        )
        if not panel.in_regular_session:
            st.caption(
                "Outside regular US equity hours, **6.5h** is used for time-to-expiry and acceleration "
                "(full notional session)."
            )

        row1 = st.columns(5)
        row1[0].metric("σ base (annual)", f"{panel.sigma_base * 100:.2f}%")
        row1[1].metric("σ effective (× accel)", f"{panel.sigma_effective * 100:.2f}%")
        row1[2].metric("σ daily (÷√252)", f"{panel.sigma_daily_close * 100:.3f}%")
        row1[3].metric("1σ day ($)", format_currency(panel.dollar_daily_1sigma))
        row1[4].metric("Kurtosis factor", f"{panel.kurtosis_factor:.1f}×")

        row2 = st.columns(5)
        row2[0].metric("Hours left (ET)", f"{panel.hours_remaining:.2f}")
        row2[1].metric("T (years)", f"{panel.time_to_expiry_years:.5f}")
        row2[2].metric("1σ session ($)", format_currency(panel.dollar_session_1sigma))
        vix_s = f"{panel.vix:.2f}" if panel.vix is not None else "n/a"
        v1d_s = f"{panel.vix1d:.2f}" if panel.vix1d is not None else "n/a"
        row2[3].metric("VIX", vix_s)
        row2[4].metric("VIX1D", v1d_s)

        if panel.straddle is not None:
            st.markdown(
                f"**ATM straddle** (nearest expiry in DTE window): strike **{panel.straddle.strike:.2f}**, "
                f"DTE **{panel.straddle.days_to_expiration}**, "
                f"call mid **{format_currency(panel.straddle.call_mid)}**, "
                f"put mid **{format_currency(panel.straddle.put_mid)}**, "
                f"combined **{format_currency(panel.straddle.straddle_mid)}**."
            )
            if panel.straddle_vs_session_ratio is not None:
                st.caption(
                    f"Straddle ÷ session 1σ move ≈ **{panel.straddle_vs_session_ratio:.2f}** "
                    "(compare market-implied package vs σ·√T heuristic)."
                )
        else:
            st.caption("No ATM call+put mids found in the current DTE window for a straddle check.")


def render_intraday_trade_plan(plan: IntradayTradePlan) -> None:
    with st.container(border=True):
        st.subheader("Intraday Trade Plan (Single Contracts)")
        row = st.columns(7)
        row[0].metric("Preferred", plan.preferred_trade)
        row[1].metric("Bias Bucket", plan.bias_bucket_label)
        row[2].metric("VIX Regime", plan.vix_regime_label)
        row[3].metric("GEX Regime", plan.gex_regime_label)
        row[4].metric("Charm", plan.charm_regime_label)
        row[5].metric("Setup", plan.confidence_label)
        row[6].metric("Conviction", plan.conviction_label)
        st.markdown(plan.summary)
        st.caption(plan.trust_guidance)
        st.caption(plan.hold_guidance)
        st.caption(plan.conviction_note)
        for note in plan.notes:
            st.write(f"- {note}")

def render_details(
    analysis: AnalysisSnapshot,
    chart_strike_rows: tuple[StrikeOverview, ...],
    chart_charm_rows: tuple[CharmStrikeOverview, ...],
    chart_expiry_rows: tuple[ExpiryOverview, ...],
) -> None:
    metrics = analysis.metrics
    chart_left, chart_right = st.columns(2)

    with chart_left:
        oi_header, oi_control = st.columns([5, 1])
        with oi_control:
            oi_options, oi_default_index = build_chart_count_options(len(chart_strike_rows))
            oi_display_count = st.selectbox(
                "OI strikes shown",
                options=oi_options,
                index=oi_default_index,
                key="oi_display_count",
                format_func=lambda value: f"{value} strikes",
                label_visibility="collapsed",
            )
        oi_rows = limit_rows_by_strike_distance(list(chart_strike_rows), metrics.underlying_price, int(oi_display_count))
        st.plotly_chart(build_open_interest_figure(oi_rows), width='stretch')

    with chart_right:
        gex_mode_col, gex_count_col = st.columns([3, 2])
        with gex_mode_col:
            gex_chart_mode = st.selectbox(
                "GEX Chart View",
                options=["By Strike", "By Expiry"],
                key="gex_chart_mode",
            )
        gex_is_expiry = gex_chart_mode == "By Expiry"
        available_gex_count = len(chart_expiry_rows) if gex_is_expiry else len(chart_strike_rows)
        gex_options, gex_default_index = build_chart_count_options(available_gex_count)
        with gex_count_col:
            gex_display_count = st.selectbox(
                "GEX items shown",
                options=gex_options,
                index=gex_default_index,
                key="gex_display_count_expiry" if gex_is_expiry else "gex_display_count_strike",
                format_func=lambda value: f"{value} dates" if gex_is_expiry else f"{value} strikes",
                label_visibility="collapsed",
            )
        if gex_chart_mode == "By Expiry":
            expiry_rows = limit_expiry_rows(list(chart_expiry_rows), int(gex_display_count))
            st.plotly_chart(build_gamma_expiry_figure(expiry_rows), width='stretch')
            st.caption(
                "Expiry mode rolls the same estimated GEX math up by expiration instead of by strike. "
                "It is useful for seeing which maturity bucket is carrying the most net gamma."
            )
        else:
            gex_rows = limit_rows_by_strike_distance(list(chart_strike_rows), metrics.underlying_price, int(gex_display_count))
            st.plotly_chart(
                build_gamma_figure(
                    gex_rows,
                    call_wall=metrics.call_wall,
                    put_wall=metrics.put_wall,
                    gex_flip=metrics.gex_flip_estimate,
                ),
                width='stretch',
            )
            st.caption(
                "Strike mode shows the local gamma structure by price level, including the current Put Wall, "
                "Call Wall, and GEX Flip markers."
            )
        charm_header, charm_control = st.columns([5, 1])
        with charm_control:
            charm_options, charm_default_index = build_chart_count_options(len(chart_charm_rows))
            charm_display_count = st.selectbox(
                "Charm strikes shown",
                options=charm_options,
                index=charm_default_index,
                key="charm_display_count",
                format_func=lambda value: f"{value} strikes",
                label_visibility="collapsed",
            )
        charm_display_rows = limit_rows_by_strike_distance(
            list(chart_charm_rows),
            metrics.underlying_price,
            int(charm_display_count),
        )
        st.plotly_chart(build_charm_figure(charm_display_rows), width='stretch')
        if not chart_charm_rows:
            st.caption("Charm is unavailable for the current ticker/filter set, so this chart is intentionally empty.")
        else:
            st.caption(
                "Charm uses estimated delta decay at unchanged spot. For 0DTE contracts it uses time-to-close when "
                "available; longer DTE contracts use roughly one trading day of decay."
            )
        with st.expander("Structure Notes", expanded=True):
            st.write(f"- Average call IV: {format_percent(metrics.average_call_iv)}")
            st.write(f"- Average put IV: {format_percent(metrics.average_put_iv)}")
            if not metrics.iv_skew_available:
                st.write("- IV skew note: source feed is publishing mirrored call/put IV here, so side-specific skew is unavailable.")
            st.write(f"- Average spread: {format_percent(metrics.average_spread_pct)}")
            st.write(f"- GEX focus strike: `{metrics.gex_peak:.2f}`" if metrics.gex_peak is not None else "- GEX focus strike: n/a")
            st.write(
                f"- Gamma asymmetry: `{metrics.gamma_asymmetry_score:+.2f}`"
                if metrics.gamma_asymmetry_score is not None
                else "- Gamma asymmetry: n/a"
            )
            st.write(
                f"- GEX / volume concordance: `{metrics.gex_vol_score:+.2f}`"
                if metrics.gex_vol_score is not None
                else "- GEX / volume concordance: n/a"
            )
            st.write(
                f"- Prev-day structure score: `{metrics.prev_day_score:+.2f}`"
                if metrics.prev_day_score is not None
                else "- Prev-day structure score: n/a"
            )
            st.write(
                f"- Prev-day gap vs close: `{format_percent(metrics.prev_day_gap_pct)}`"
                if metrics.prev_day_gap_pct is not None
                else "- Prev-day gap vs close: n/a"
            )
            st.write(
                f"- Prev-day range percent: `{format_percent(metrics.prev_day_range_pct)}`"
                if metrics.prev_day_range_pct is not None
                else "- Prev-day range percent: n/a"
            )
            st.write(f"- Front expiry expected move: `{format_currency(metrics.expected_move)}`")


if __name__ == "__main__":
    import subprocess

    from streamlit.runtime.scriptrunner import get_script_run_ctx

    if get_script_run_ctx() is not None:
        main()
    else:
        script = str(Path(__file__).resolve())
        raise SystemExit(subprocess.call([sys.executable, "-m", "streamlit", "run", script, *sys.argv[1:]]))
