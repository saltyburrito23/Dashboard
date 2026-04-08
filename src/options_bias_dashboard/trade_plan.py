from __future__ import annotations

from .analytics import classify_bias_bucket
from .conviction import summarize_conviction
from .models import AnalysisSnapshot, CharmStrikeOverview, ConvictionContext, IntradayTradePlan
from .volatility import VolatilityPanel


def _pick_regime_vol(panel: VolatilityPanel | None) -> float | None:
    if panel is None:
        return None
    if panel.vix is not None and panel.vix > 0:
        return panel.vix
    if panel.vix1d is not None and panel.vix1d > 0:
        return panel.vix1d
    if panel.sigma_base > 0:
        return panel.sigma_base * 100.0
    return None


def _classify_vix_regime(panel: VolatilityPanel | None) -> tuple[str, str]:
    regime_vol = _pick_regime_vol(panel)
    if regime_vol is None:
        return "Unknown", "Vol regime unavailable."
    if regime_vol >= 30:
        return "Stress", f"Vol regime {regime_vol:.1f}: stress conditions."
    if regime_vol >= 24:
        return "Elevated", f"Vol regime {regime_vol:.1f}: elevated-vol directional conditions."
    if regime_vol >= 18:
        return "Firm", f"Vol regime {regime_vol:.1f}: active but not extreme."
    return "Calm", f"Vol regime {regime_vol:.1f}: calmer tape."


def _classify_gex_regime(analysis: AnalysisSnapshot) -> tuple[str, str]:
    rows = analysis.strike_overview
    gross = sum(abs(row.call_gex) + abs(row.put_gex) for row in rows)
    if gross <= 0:
        return "Unknown", "GEX unavailable."
    net = sum(row.net_gex for row in rows)
    ratio = net / gross
    if ratio <= -0.18:
        return "Deep Negative", "Walls are fragile; treat the session as directional and morning-heavy."
    if ratio < -0.05:
        return "Negative", "Walls are less reliable; directional trades should dominate."
    if ratio >= 0.08:
        return "Positive", "Walls are more trustworthy and late-day holds are safer."
    return "Neutral", "Walls matter, but they need confirmation from price and charm."


def _classify_charm_regime(charm_rows: tuple[CharmStrikeOverview, ...]) -> tuple[str, str]:
    if not charm_rows:
        return "Unknown", "Charm unavailable."
    net = sum(row.net_charm for row in charm_rows)
    gross = sum(abs(row.call_charm) + abs(row.put_charm) for row in charm_rows)
    if gross <= 0:
        return "Flat", "Charm is close to neutral."
    if all(row.net_charm <= 0 for row in charm_rows) and net < 0:
        return "All-Negative", "Broad decay pressure favors shorter hold windows."
    ratio = net / gross
    if ratio <= -0.08:
        return "Negative", "Decay pressure is net negative; do not overstay long premium."
    if ratio >= 0.08:
        return "Positive", "Decay backdrop is supportive enough to hold winners longer."
    return "Mixed", "Charm is split; let price confirm before pressing size."


def _confidence_label(
    analysis: AnalysisSnapshot,
    *,
    bias_bucket_label: str,
    gex_regime_label: str,
    charm_regime_label: str,
    conviction_label: str,
) -> str:
    confidence = analysis.result.confidence
    if bias_bucket_label == "Neutral/Mixed":
        confidence -= 12
    elif bias_bucket_label in {"Bullish Lean", "Bearish Lean"}:
        confidence -= 6
    if gex_regime_label == "Positive":
        confidence += 8
    elif gex_regime_label == "Deep Negative":
        confidence -= 8
    if charm_regime_label == "All-Negative":
        confidence -= 6
    elif charm_regime_label == "Positive":
        confidence += 4
    if conviction_label == "Strong":
        confidence += 8
    elif conviction_label == "Supportive":
        confidence += 4
    elif conviction_label == "Weak":
        confidence -= 4
    elif conviction_label == "Conflicting":
        confidence -= 8
    if confidence >= 72:
        return "High"
    if confidence >= 56:
        return "Medium"
    return "Low"


def _build_wait_reasons(
    analysis: AnalysisSnapshot,
    conviction_label: str,
    gex_regime_label: str,
    charm_regime_label: str,
) -> list[str]:
    reasons: list[str] = []
    if conviction_label in {"Mixed", "N/A", "Weak", "Conflicting"}:
        reasons.append(f"{conviction_label.lower()} conviction")
    if analysis.metrics.liquidity_score < 0.70:
        reasons.append("low liquidity")
    if not analysis.metrics.iv_skew_available:
        reasons.append("IV skew unavailable")
    if gex_regime_label in {"Unknown", "Neutral"} and charm_regime_label in {"Unknown", "Mixed"}:
        reasons.append("unclear regime signals")
    return reasons


def build_intraday_trade_plan(
    analysis: AnalysisSnapshot,
    panel: VolatilityPanel | None,
    charm_rows: tuple[CharmStrikeOverview, ...],
    conviction_contexts: tuple[ConvictionContext, ...] = (),
) -> IntradayTradePlan:
    vix_regime_label, vix_note = _classify_vix_regime(panel)
    gex_regime_label, gex_note = _classify_gex_regime(analysis)
    charm_regime_label, charm_note = _classify_charm_regime(charm_rows)
    conviction_label, conviction_note = summarize_conviction(conviction_contexts)

    bias_bucket_label, bias_direction = classify_bias_bucket(analysis.result.score)
    preferred_trade = "Wait / No New Premium"
    if bias_bucket_label == "Strong Bullish":
        preferred_trade = "Long Calls"
    elif bias_bucket_label == "Strong Bearish":
        preferred_trade = "Long Puts"
    elif bias_bucket_label == "Bullish Lean":
        preferred_trade = "Bullish Lean / Wait For Confirmation"
    elif bias_bucket_label == "Bearish Lean":
        preferred_trade = "Bearish Lean / Wait For Confirmation"
    else:
        preferred_trade = "Wait / No New Premium"

    if gex_regime_label == "Deep Negative":
        hold_guidance = "Morning only. Favor exits by 11:30 ET unless price is impulsive and paying immediately."
    elif gex_regime_label == "Negative" or vix_regime_label in {"Elevated", "Stress"}:
        hold_guidance = "Directional only. Favor taking gains by about 1:00 ET instead of assuming afternoon continuation."
    elif gex_regime_label == "Positive" and charm_regime_label not in {"Negative", "All-Negative"}:
        hold_guidance = "You can hold winners longer, but still reassess into the 2:30 ET area."
    else:
        hold_guidance = "Take profits faster than usual and avoid late premium buys unless momentum is obvious."

    trust_guidance = gex_note
    if charm_regime_label == "All-Negative":
        trust_guidance += " Charm is all-negative, so time decay should force quicker exits."
    if conviction_label not in {"N/A", "Mixed"}:
        trust_guidance += f" Cross-symbol conviction is {conviction_label.lower()}."

    if bias_bucket_label in {"Bullish Lean", "Bearish Lean"}:
        trust_guidance += " Lean buckets need price confirmation before sizing up."

    wait_reasons = _build_wait_reasons(analysis, conviction_label, gex_regime_label, charm_regime_label)
    wait_reason_text = ""
    if wait_reasons:
        wait_reason_text = " Reason: " + "; ".join(wait_reasons).capitalize() + "."

    if preferred_trade.startswith("Long") and bias_direction != 0:
        summary = (
            f"{bias_bucket_label}. {preferred_trade} favored. {gex_note} {charm_note} {conviction_note}"
        )
    elif bias_bucket_label in {"Bullish Lean", "Bearish Lean"}:
        summary = (
            f"{bias_bucket_label}. Confirmation is still required before pressing the directional side. "
            f"{gex_note} {charm_note} {conviction_note}"
        )
    else:
        summary = (
            f"{bias_bucket_label}. No clean naked-premium setup yet. {gex_note} {charm_note} {conviction_note}{wait_reason_text}"
        )

    context_notes = []
    for context in conviction_contexts:
        if context.present_metrics > 0:
            note = f"{context.symbol}: {context.aligned_metrics}/{context.present_metrics} aligned"
        else:
            note = f"{context.symbol}: conviction inputs unavailable"
        if context.supporting_metrics:
            note += f" | support: {', '.join(context.supporting_metrics)}"
        if context.conflicting_metrics:
            note += f" | conflict: {', '.join(context.conflicting_metrics)}"
        if context.neutral_metric_names:
            note += f" | neutral: {', '.join(context.neutral_metric_names)}"
        if context.unavailable_metrics and context.present_metrics > 0:
            note += f" | n/a: {', '.join(context.unavailable_metrics)}"
        context_notes.append(note)

    notes = tuple(
        [
            f"Bias bucket {bias_bucket_label} on score {analysis.result.score:+.1f} with {analysis.result.confidence:.0f}/100 confidence.",
            vix_note,
            gex_note,
            charm_note,
            conviction_note,
            *context_notes,
        ]
    )

    return IntradayTradePlan(
        bias_bucket_label=bias_bucket_label,
        vix_regime_label=vix_regime_label,
        gex_regime_label=gex_regime_label,
        charm_regime_label=charm_regime_label,
        preferred_trade=preferred_trade,
        hold_guidance=hold_guidance,
        trust_guidance=trust_guidance,
        confidence_label=_confidence_label(
            analysis,
            bias_bucket_label=bias_bucket_label,
            gex_regime_label=gex_regime_label,
            charm_regime_label=charm_regime_label,
            conviction_label=conviction_label,
        ),
        conviction_label=conviction_label,
        conviction_note=conviction_note,
        conviction_contexts=conviction_contexts,
        summary=summary,
        notes=notes,
    )
