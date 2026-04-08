from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .volatility import session_context

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_ROOT = ROOT / "dashboard_snapshots"

logger = logging.getLogger(__name__)

# Global thread pool for background snapshot writing
_snapshot_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="snapshot-writer")


def _symbol_filename(symbol: str) -> str:
    cleaned = symbol.strip().upper().replace("/", "_")
    return f"{cleaned}.json"


def snapshot_bucket(dt: datetime, *, interval_minutes: int = 15) -> datetime:
    dt = dt.astimezone(ET)
    floored_minute = (dt.minute // interval_minutes) * interval_minutes
    return dt.replace(minute=floored_minute, second=0, microsecond=0)


def snapshot_dir_for(dt: datetime, *, root: Path | None = None, interval_minutes: int = 15) -> Path:
    bucket = snapshot_bucket(dt, interval_minutes=interval_minutes)
    target_root = root or SNAPSHOT_ROOT
    return target_root / bucket.strftime("%Y-%m-%d_%H-%M")


def _json_default(value: object) -> object:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Unsupported snapshot value: {type(value)!r}")


def _validate_snapshot_payload(payload: dict[str, object]) -> bool:
    """Validate that snapshot payload contains essential data."""
    # Require symbol at minimum
    symbol = payload.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        logger.warning("Snapshot payload missing valid symbol")
        return False

    # For full analysis payloads, require more fields
    if "raw_snapshot" in payload or "analysis" in payload:
        required_keys = {"symbol", "captured_at", "raw_snapshot", "analysis"}
        if not all(key in payload for key in required_keys):
            logger.warning(f"Analysis snapshot payload missing required keys: {required_keys - set(payload.keys())}")
            return False

        # Validate analysis has result
        analysis = payload.get("analysis")
        if not isinstance(analysis, dict) or "result" not in analysis:
            logger.warning("Snapshot payload missing analysis result")
            return False

    return True


def _write_snapshot_file(
    payload: dict[str, object],
    captured_at: datetime,
    root: Path | None,
    interval_minutes: int,
) -> Path | None:
    """Internal function to perform the actual file writing."""
    try:
        target_dir = snapshot_dir_for(captured_at, root=root, interval_minutes=interval_minutes)
        target_dir.mkdir(parents=True, exist_ok=True)

        symbol = str(payload.get("symbol", "")).strip().upper()
        target_file = target_dir / (_symbol_filename(symbol) if symbol else "dashboard_snapshot.json")

        # Only write if file doesn't exist (maintains 15-min bucketing)
        if not target_file.exists():
            json_content = json.dumps(payload, indent=2, default=_json_default)
            target_file.write_text(json_content + "\n")
            logger.debug(f"Snapshot written: {target_file}")
            return target_file
        else:
            logger.debug(f"Snapshot already exists: {target_file}")
            return target_file

    except Exception as e:
        logger.error(f"Failed to write snapshot file: {e}")
        return None


def write_dashboard_snapshot(
    payload: dict[str, object],
    *,
    captured_at: datetime,
    root: Path | None = None,
    interval_minutes: int = 15,
    background: bool = False,
) -> Path | None:
    """Write dashboard snapshot with validation and market hours checking.

    Args:
        payload: Snapshot data payload
        captured_at: Timestamp when data was captured
        root: Optional custom root directory for snapshots
        interval_minutes: Bucketing interval in minutes
        background: If True, write in background thread to avoid blocking UI
    """
    # Validate payload before proceeding
    if not _validate_snapshot_payload(payload):
        logger.error("Invalid snapshot payload, skipping write")
        return None

    # Skip market hours check for simple test payloads (no analysis data)
    has_analysis = "analysis" in payload
    if has_analysis:
        # Check if we're in regular trading hours
        _, in_rth, _ = session_context(captured_at)
        if not in_rth:
            logger.debug("Outside regular trading hours, skipping snapshot")
            return None

    target_dir = snapshot_dir_for(captured_at, root=root, interval_minutes=interval_minutes)
    target_dir.mkdir(parents=True, exist_ok=True)

    symbol = str(payload.get("symbol", "")).strip().upper()
    target_file = target_dir / (_symbol_filename(symbol) if symbol else "dashboard_snapshot.json")

    # Only write if file doesn't exist (maintains 15-min bucketing)
    if not target_file.exists():
        if background:
            # Submit to thread pool for non-blocking write
            _snapshot_executor.submit(_write_snapshot_file, payload, captured_at, root, interval_minutes)
        else:
            # Synchronous write
            _write_snapshot_file(payload, captured_at, root, interval_minutes)

    return target_file


def detect_snapshot_gaps(symbol: str, days_back: int = 5, *, root: Path | None = None) -> list[dict[str, object]]:
    """Detect gaps in snapshot coverage for recent trading days."""
    target_root = root or SNAPSHOT_ROOT
    if not target_root.exists():
        return []

    gaps = []
    now = datetime.now(ET)

    # Check last N trading days
    for day_offset in range(days_back):
        check_date = now.date()
        for _ in range(day_offset + 1):
            check_date = check_date - timedelta(days=1)
            # Skip weekends
            while check_date.weekday() >= 5:
                check_date = check_date - timedelta(days=1)

        # Find snapshots for this date
        date_snapshots = []
        try:
            for entry in target_root.iterdir():
                if not entry.is_dir():
                    continue
                try:
                    bucket_dt = datetime.strptime(entry.name, "%Y-%m-%d_%H-%M").replace(tzinfo=ET)
                except ValueError:
                    continue

                if bucket_dt.date() == check_date:
                    symbol_file = entry / _symbol_filename(symbol)
                    if symbol_file.exists():
                        date_snapshots.append(bucket_dt)

        except Exception as e:
            logger.error(f"Error scanning snapshots for {check_date}: {e}")
            continue

        # Check for expected 15-min intervals during market hours (9:30-16:00)
        expected_times = []
        current = datetime.combine(check_date, time(9, 30), ET)
        end_time = datetime.combine(check_date, time(16, 0), ET)

        while current <= end_time:
            expected_times.append(current)
            current = current + timedelta(minutes=15)

        actual_times = {dt.replace(second=0, microsecond=0) for dt in date_snapshots}

        missing_times = []
        for expected in expected_times:
            if expected not in actual_times:
                missing_times.append(expected)

        if missing_times:
            gaps.append({
                "date": check_date.isoformat(),
                "missing_snapshots": len(missing_times),
                "total_expected": len(expected_times),
                "coverage_percent": (len(expected_times) - len(missing_times)) / len(expected_times) * 100,
                "missing_times": [t.strftime("%H:%M") for t in missing_times[:5]],  # First 5 for brevity
            })

    return gaps


def get_snapshot_health_status(symbol: str, *, root: Path | None = None) -> dict[str, object]:
    """Get health status of the snapshot system for a symbol."""
    target_root = root or SNAPSHOT_ROOT
    if not target_root.exists():
        return {"status": "error", "message": "Snapshot root directory does not exist"}

    # Count recent snapshots (last 5 trading days)
    recent_snapshots = 0
    total_size = 0
    newest_snapshot = None
    oldest_snapshot = None

    try:
        for entry in target_root.iterdir():
            if not entry.is_dir():
                continue
            try:
                bucket_dt = datetime.strptime(entry.name, "%Y-%m-%d_%H-%M").replace(tzinfo=ET)
            except ValueError:
                continue

            symbol_file = entry / _symbol_filename(symbol)
            if symbol_file.exists():
                stat = symbol_file.stat()
                total_size += stat.st_size
                recent_snapshots += 1

                if newest_snapshot is None or bucket_dt > newest_snapshot:
                    newest_snapshot = bucket_dt
                if oldest_snapshot is None or bucket_dt < oldest_snapshot:
                    oldest_snapshot = bucket_dt

    except Exception as e:
        return {"status": "error", "message": f"Failed to scan snapshots: {e}"}

    # Check if we have recent snapshots (within last 24 hours)
    now = datetime.now(ET)
    has_recent = newest_snapshot and (now - newest_snapshot).total_seconds() < 24 * 3600

    return {
        "status": "healthy" if has_recent else "warning",
        "total_snapshots": recent_snapshots,
        "total_size_mb": total_size / (1024 * 1024),
        "newest_snapshot": newest_snapshot.isoformat() if newest_snapshot else None,
        "oldest_snapshot": oldest_snapshot.isoformat() if oldest_snapshot else None,
        "has_recent_snapshot": has_recent,
    }


def load_latest_prior_session_snapshot(
    symbol: str,
    *,
    as_of: datetime,
    root: Path | None = None,
) -> dict[str, object] | None:
    target_root = root or SNAPSHOT_ROOT
    if not target_root.exists():
        return None
    normalized_symbol = symbol.strip().upper()
    as_of_date = as_of.astimezone(ET).date()
    candidates: list[tuple[datetime, Path]] = []
    for entry in target_root.iterdir():
        if not entry.is_dir():
            continue
        try:
            bucket_dt = datetime.strptime(entry.name, "%Y-%m-%d_%H-%M").replace(tzinfo=ET)
        except ValueError:
            continue
        if bucket_dt.date() >= as_of_date:
            continue
        symbol_file = entry / _symbol_filename(normalized_symbol)
        if symbol_file.exists():
            candidates.append((bucket_dt, symbol_file))
            continue
        target_file = entry / "dashboard_snapshot.json"
        if target_file.exists():
            candidates.append((bucket_dt, target_file))
    for _, file_path in sorted(candidates, key=lambda item: item[0], reverse=True):
        try:
            payload = json.loads(file_path.read_text())
        except json.JSONDecodeError:
            continue
        if str(payload.get("symbol", "")).upper() == normalized_symbol:
            return payload
    return None
