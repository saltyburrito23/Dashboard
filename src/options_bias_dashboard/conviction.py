from __future__ import annotations

from .analytics import classify_bias_bucket
from .models import AnalysisSnapshot, CharmStrikeOverview, ConvictionContext


def _directional_ratio_signal(positive: float, negative: float, *, threshold: float = 0.05) -> tuple[bool, int]:
    total = positive + negative
    if total <= 0:
        return False, 0
    ratio = (positive - negative) / total
    if ratio >= threshold:
        return True, 1
    if ratio <= -threshold:
        return True, -1
    return True, 0


def _gex_signal(analysis: AnalysisSnapshot) -> tuple[bool, int]:
    rows = analysis.strike_overview
    gross = sum(abs(row.call_gex) + abs(row.put_gex) for row in rows)
    if gross <= 0:
        return False, 0
    net = sum(row.net_gex for row in rows)
    ratio = net / gross
    if ratio >= 0.08:
        return True, 1
    if ratio <= -0.05:
        return True, -1
    return True, 0


def _charm_signal(charm_rows: tuple[CharmStrikeOverview, ...]) -> tuple[bool, int]:
    if not charm_rows:
        return False, 0
    gross = sum(abs(row.call_charm) + abs(row.put_charm) for row in charm_rows)
    if gross <= 0:
        return False, 0
    net = sum(row.net_charm for row in charm_rows)
    ratio = net / gross
    if all(row.net_charm <= 0 for row in charm_rows) and net < 0:
        return True, -1
    if ratio >= 0.08:
        return True, 1
    if ratio <= -0.08:
        return True, -1
    return True, 0


def build_conviction_context(
    symbol: str,
    analysis: AnalysisSnapshot | None,
    charm_rows: tuple[CharmStrikeOverview, ...],
    *,
    primary_bias_label: str | None = None,
    primary_bias_score: float | None = None,
) -> ConvictionContext:
    metric_signals: list[tuple[str, bool, int]] = []
    if analysis is not None:
        metric_signals.extend(
            [
                ("GEX", *_gex_signal(analysis)),
                (
                    "Call/Put Volume",
                    *_directional_ratio_signal(
                        float(analysis.metrics.call_volume),
                        float(analysis.metrics.put_volume),
                    ),
                ),
                (
                    "Call/Put OI",
                    *_directional_ratio_signal(
                        float(analysis.metrics.call_open_interest),
                        float(analysis.metrics.put_open_interest),
                    ),
                ),
            ]
        )
    else:
        metric_signals.extend(
            [
                ("GEX", False, 0),
                ("Call/Put Volume", False, 0),
                ("Call/Put OI", False, 0),
            ]
        )
    metric_signals.append(("Charm", *_charm_signal(charm_rows)))

    aligned: list[str] = []
    conflicting: list[str] = []
    neutral: list[str] = []
    unavailable: list[str] = []
    present_metrics = 0
    available_metrics = 0
    opposing_metrics = 0

    if primary_bias_score is not None:
        _, primary_signal = classify_bias_bucket(primary_bias_score)
    elif primary_bias_label not in {"bullish", "bearish"}:
        primary_signal = 0
    else:
        primary_signal = 1 if primary_bias_label == "bullish" else -1

    for name, present, signal in metric_signals:
        if not present:
            unavailable.append(name)
            continue
        present_metrics += 1
        if signal == 0 or primary_signal == 0:
            neutral.append(name)
            continue
        available_metrics += 1
        if signal == primary_signal:
            aligned.append(name)
        else:
            conflicting.append(name)
            opposing_metrics += 1

    return ConvictionContext(
        symbol=symbol,
        present_metrics=present_metrics,
        available_metrics=available_metrics,
        neutral_metrics=len(neutral),
        aligned_metrics=len(aligned),
        opposing_metrics=opposing_metrics,
        supporting_metrics=tuple(aligned),
        conflicting_metrics=tuple(conflicting),
        neutral_metric_names=tuple(neutral),
        unavailable_metrics=tuple(unavailable),
    )


def summarize_conviction(
    contexts: tuple[ConvictionContext, ...],
) -> tuple[str, str]:
    present = sum(context.present_metrics for context in contexts)
    available = sum(context.available_metrics for context in contexts)
    aligned = sum(context.aligned_metrics for context in contexts)
    opposing = sum(context.opposing_metrics for context in contexts)
    if present <= 0:
        return "N/A", "Cross-symbol conviction unavailable from the current option feeds."
    if available <= 0:
        return "Mixed", "Cross-symbol conviction is neutral across the current option feeds."
    ratio = (aligned - opposing) / present
    if ratio >= 0.45:
        label = "Strong"
    elif ratio >= 0.20:
        label = "Supportive"
    elif ratio <= -0.35:
        label = "Conflicting"
    elif ratio <= -0.15:
        label = "Weak"
    else:
        label = "Mixed"
    note = f"{aligned}/{present} aligned inputs across the cross-symbol conviction basket."
    return label, note
