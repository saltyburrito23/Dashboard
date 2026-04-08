from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time
from pathlib import Path
from statistics import mean

from .analytics import classify_bias_bucket
from .normalization import ET, extract_regular_session_bars
from .snapshots import SNAPSHOT_ROOT

SETTLED_SESSION_LAST_BAR = dt_time(15, 55)


@dataclass(frozen=True)
class SnapshotReviewRow:
    captured_at: datetime
    session_date: date
    bias_label: str
    bias_bucket: str
    directional_signal: int
    bias_score: float
    confidence: float
    premarket_structure: str
    gex_regime: str
    charm_regime: str
    spot: float
    end_close: float
    terminal_move: float
    bias_correct: bool | None
    first_target_hit: str | None
    first_target_side: str
    minutes_to_first_target: float | None
    follow_through_points: float | None
    follow_through_em_ratio: float | None


@dataclass(frozen=True)
class SnapshotReviewGroup:
    label: str
    samples: int
    directional_samples: int
    bias_accuracy: float | None
    upward_accuracy: float | None
    downward_accuracy: float | None
    target_hit_rate: float | None
    avg_follow_through_em: float | None


@dataclass(frozen=True)
class SnapshotReviewSummary:
    total_snapshots: int
    completed_sessions: int
    incomplete_sessions: int
    strong_bullish_samples: int
    bullish_lean_samples: int
    neutral_samples: int
    bearish_lean_samples: int
    strong_bearish_samples: int
    upward_accuracy: float | None
    downward_accuracy: float | None
    target_hit_rate: float | None
    avg_follow_through_em: float | None
    rows: tuple[SnapshotReviewRow, ...]
    by_bias_bucket: tuple[SnapshotReviewGroup, ...]
    by_premarket: tuple[SnapshotReviewGroup, ...]
    by_regime: tuple[SnapshotReviewGroup, ...]
    best_setups: tuple[SnapshotReviewGroup, ...]


def _as_float(value: object) -> float | None:
    if value in (None, "", "NaN"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ET)
    return parsed.astimezone(ET)


def _load_symbol_snapshots(symbol: str, *, root: Path | None = None) -> list[dict[str, object]]:
    target_root = root or SNAPSHOT_ROOT
    if not target_root.exists():
        return []
    normalized = symbol.strip().upper()
    payloads: list[dict[str, object]] = []
    for entry in sorted(target_root.iterdir()):
        if not entry.is_dir():
            continue
        target_file = entry / f"{normalized.replace('/', '_')}.json"
        if not target_file.exists():
            continue
        try:
            payload = json.loads(target_file.read_text())
        except json.JSONDecodeError:
            continue
        if str(payload.get("symbol", "")).strip().upper() != normalized:
            continue
        payloads.append(payload)
    payloads.sort(key=lambda payload: _parse_dt(payload.get("captured_at")) or datetime.min.replace(tzinfo=ET))
    return payloads


def _session_payloads(payloads: list[dict[str, object]]) -> dict[date, list[dict[str, object]]]:
    grouped: dict[date, list[dict[str, object]]] = {}
    for payload in payloads:
        captured_at = _parse_dt(payload.get("captured_at"))
        if captured_at is None:
            continue
        grouped.setdefault(captured_at.date(), []).append(payload)
    return grouped


def _settled_session_bars(payloads: list[dict[str, object]], session_date: date) -> tuple[list[dict[str, object]], bool]:
    latest_payload = max(
        payloads,
        key=lambda payload: _parse_dt(payload.get("captured_at")) or datetime.min.replace(tzinfo=ET),
    )
    history = dict(latest_payload.get("raw_snapshot", {}).get("history", {}))
    bars = extract_regular_session_bars(history, session_date=session_date)
    if not bars:
        return [], False
    last_bar_time = bars[-1]["time"].astimezone(ET).time()
    return bars, last_bar_time >= SETTLED_SESSION_LAST_BAR


def _target_sequence(dynamic_targets: dict[str, object]) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    raw_upside = dynamic_targets.get("upside_targets") or []
    raw_downside = dynamic_targets.get("downside_targets") or []
    upside = [
        (str(target.get("label", "Up")), float(target["price"]))
        for target in raw_upside
        if isinstance(target, dict) and _as_float(target.get("price")) is not None
    ]
    downside = [
        (str(target.get("label", "Down")), float(target["price"]))
        for target in raw_downside
        if isinstance(target, dict) and _as_float(target.get("price")) is not None
    ]
    upside.sort(key=lambda item: item[1])
    downside.sort(key=lambda item: item[1], reverse=True)
    return upside, downside


def _first_target_hit(
    dynamic_targets: dict[str, object],
    *,
    future_bars: list[dict[str, object]],
    captured_at: datetime,
) -> tuple[str | None, str, float | None]:
    upside, downside = _target_sequence(dynamic_targets)
    if not future_bars or (not upside and not downside):
        return None, "None", None

    for bar in future_bars:
        hit_up = next((label for label, price in upside if bar["high"] >= price), None)
        hit_down = next((label for label, price in downside if bar["low"] <= price), None)
        if hit_up and hit_down:
            minutes = max((bar["time"].astimezone(ET) - captured_at).total_seconds() / 60.0, 0.0)
            return "Ambiguous", "Ambiguous", minutes
        if hit_up:
            minutes = max((bar["time"].astimezone(ET) - captured_at).total_seconds() / 60.0, 0.0)
            return hit_up, "Up", minutes
        if hit_down:
            minutes = max((bar["time"].astimezone(ET) - captured_at).total_seconds() / 60.0, 0.0)
            return hit_down, "Down", minutes
    return None, "None", None


def _follow_through(
    directional_signal: int,
    *,
    future_bars: list[dict[str, object]],
    spot: float,
    em_dollars: float | None,
) -> tuple[float | None, float | None]:
    if not future_bars or spot <= 0 or directional_signal == 0:
        return None, None
    if directional_signal > 0:
        favorable_move = max(bar["high"] for bar in future_bars) - spot
    else:
        favorable_move = spot - min(bar["low"] for bar in future_bars)
    if favorable_move < 0:
        favorable_move = 0.0
    if em_dollars is None or em_dollars <= 0:
        return favorable_move, None
    return favorable_move, favorable_move / em_dollars


def _build_review_row(
    payload: dict[str, object],
    *,
    future_bars: list[dict[str, object]],
) -> SnapshotReviewRow | None:
    captured_at = _parse_dt(payload.get("captured_at"))
    if captured_at is None or not future_bars:
        return None

    analysis = payload.get("analysis") or {}
    result = analysis.get("result") or {}
    trade_plan = payload.get("trade_plan") or {}
    premarket_structure = payload.get("premarket_structure") or {}
    dynamic_targets = payload.get("dynamic_targets") or {}
    raw_snapshot = payload.get("raw_snapshot") or {}

    spot = _as_float(raw_snapshot.get("underlying_price"))
    if spot is None or spot <= 0:
        return None

    bias_label = str(result.get("label") or "mixed").lower()
    bias_score = _as_float(result.get("score")) or 0.0
    bias_bucket, directional_signal = classify_bias_bucket(bias_score)
    confidence = _as_float(result.get("confidence")) or 0.0
    end_close = float(future_bars[-1]["close"])
    terminal_move = end_close - spot
    if directional_signal > 0:
        bias_correct = end_close > spot
    elif directional_signal < 0:
        bias_correct = end_close < spot
    else:
        bias_correct = None

    first_target_hit, first_target_side, minutes_to_first_target = _first_target_hit(
        dynamic_targets,
        future_bars=future_bars,
        captured_at=captured_at,
    )
    follow_points, follow_ratio = _follow_through(
        directional_signal,
        future_bars=future_bars,
        spot=spot,
        em_dollars=_as_float(dynamic_targets.get("em_dollars")),
    )

    return SnapshotReviewRow(
        captured_at=captured_at,
        session_date=captured_at.date(),
        bias_label=bias_label,
        bias_bucket=bias_bucket,
        directional_signal=directional_signal,
        bias_score=bias_score,
        confidence=confidence,
        premarket_structure=str(premarket_structure.get("label") or "N/A"),
        gex_regime=str(trade_plan.get("gex_regime_label") or "Unknown"),
        charm_regime=str(trade_plan.get("charm_regime_label") or "Unknown"),
        spot=spot,
        end_close=end_close,
        terminal_move=terminal_move,
        bias_correct=bias_correct,
        first_target_hit=first_target_hit,
        first_target_side=first_target_side,
        minutes_to_first_target=minutes_to_first_target,
        follow_through_points=follow_points,
        follow_through_em_ratio=follow_ratio,
    )


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _avg(values: list[float | None]) -> float | None:
    cleaned = [value for value in values if value is not None]
    if not cleaned:
        return None
    return mean(cleaned)


def _group_rows(rows: list[SnapshotReviewRow], key_fn) -> tuple[SnapshotReviewGroup, ...]:
    grouped: dict[str, list[SnapshotReviewRow]] = {}
    for row in rows:
        grouped.setdefault(key_fn(row), []).append(row)

    groups: list[SnapshotReviewGroup] = []
    for label, group_rows in grouped.items():
        directional_rows = [row for row in group_rows if row.bias_correct is not None]
        upward_rows = [row for row in group_rows if row.directional_signal > 0]
        downward_rows = [row for row in group_rows if row.directional_signal < 0]
        groups.append(
            SnapshotReviewGroup(
                label=label,
                samples=len(group_rows),
                directional_samples=len(directional_rows),
                bias_accuracy=_pct(sum(1 for row in directional_rows if row.bias_correct), len(directional_rows)),
                upward_accuracy=_pct(sum(1 for row in upward_rows if row.bias_correct), len(upward_rows)),
                downward_accuracy=_pct(sum(1 for row in downward_rows if row.bias_correct), len(downward_rows)),
                target_hit_rate=_pct(sum(1 for row in group_rows if row.first_target_side != "None"), len(group_rows)),
                avg_follow_through_em=_avg([row.follow_through_em_ratio for row in group_rows]),
            )
        )

    return tuple(sorted(groups, key=lambda group: (-group.samples, group.label)))


def build_snapshot_review(symbol: str, *, root: Path | None = None) -> SnapshotReviewSummary:
    payloads = _load_symbol_snapshots(symbol, root=root)
    grouped_payloads = _session_payloads(payloads)

    rows: list[SnapshotReviewRow] = []
    completed_sessions = 0
    incomplete_sessions = 0
    for session_date, session_payloads in grouped_payloads.items():
        settled_bars, is_settled = _settled_session_bars(session_payloads, session_date)
        if not is_settled:
            incomplete_sessions += 1
            continue
        completed_sessions += 1
        for payload in session_payloads:
            captured_at = _parse_dt(payload.get("captured_at"))
            if captured_at is None:
                continue
            future_bars = [bar for bar in settled_bars if bar["time"].astimezone(ET) > captured_at]
            row = _build_review_row(payload, future_bars=future_bars)
            if row is not None:
                rows.append(row)

    rows.sort(key=lambda row: row.captured_at, reverse=True)
    strong_bullish_rows = [row for row in rows if row.bias_bucket == "Strong Bullish"]
    bullish_lean_rows = [row for row in rows if row.bias_bucket == "Bullish Lean"]
    neutral_rows = [row for row in rows if row.bias_bucket == "Neutral/Mixed"]
    bearish_lean_rows = [row for row in rows if row.bias_bucket == "Bearish Lean"]
    strong_bearish_rows = [row for row in rows if row.bias_bucket == "Strong Bearish"]
    upward_rows = [row for row in rows if row.directional_signal > 0]
    downward_rows = [row for row in rows if row.directional_signal < 0]
    best_setup_groups = _group_rows(
        [row for row in rows if row.directional_signal != 0],
        lambda row: f"{row.bias_bucket} | {row.premarket_structure} | {row.gex_regime} GEX | {row.charm_regime} Charm",
    )
    best_setup_groups = tuple(
        sorted(
            best_setup_groups,
            key=lambda group: (
                group.avg_follow_through_em if group.avg_follow_through_em is not None else -1.0,
                group.samples,
            ),
            reverse=True,
        )[:8]
    )

    return SnapshotReviewSummary(
        total_snapshots=len(rows),
        completed_sessions=completed_sessions,
        incomplete_sessions=incomplete_sessions,
        strong_bullish_samples=len(strong_bullish_rows),
        bullish_lean_samples=len(bullish_lean_rows),
        neutral_samples=len(neutral_rows),
        bearish_lean_samples=len(bearish_lean_rows),
        strong_bearish_samples=len(strong_bearish_rows),
        upward_accuracy=_pct(sum(1 for row in upward_rows if row.bias_correct), len(upward_rows)),
        downward_accuracy=_pct(sum(1 for row in downward_rows if row.bias_correct), len(downward_rows)),
        target_hit_rate=_pct(sum(1 for row in rows if row.first_target_side != "None"), len(rows)),
        avg_follow_through_em=_avg([row.follow_through_em_ratio for row in rows]),
        rows=tuple(rows),
        by_bias_bucket=_group_rows(rows, lambda row: row.bias_bucket),
        by_premarket=_group_rows(rows, lambda row: row.premarket_structure),
        by_regime=_group_rows(rows, lambda row: f"{row.gex_regime} GEX / {row.charm_regime} Charm"),
        best_setups=best_setup_groups,
    )
