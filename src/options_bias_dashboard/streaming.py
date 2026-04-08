from __future__ import annotations

import json
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import zip_longest
from zoneinfo import ZoneInfo

from .app_config import Settings, load_settings
from .models import OptionContract

ET = ZoneInfo("America/New_York")
STREAM_STALE_SECONDS = 45.0
LEVEL_ONE_EQUITY_FIELDS = (0, 1, 2, 3, 8)
LEVEL_ONE_OPTION_FIELDS = (0, 2, 3, 4, 8, 9, 28, 29, 37)
QUOTE_FIELD_ALIASES = {
    "LEVELONE_EQUITIES": {
        "bid": ("bid", "1"),
        "ask": ("ask", "2"),
        "last": ("last", "lastPrice", "3"),
        "mark": ("mark",),
        "volume": ("totalVolume", "volume", "8"),
        "open_interest": ("openInterest",),
    },
    "LEVELONE_OPTIONS": {
        "bid": ("bid", "2"),
        "ask": ("ask", "3"),
        "last": ("last", "lastPrice", "4"),
        "mark": ("mark", "37"),
        "volume": ("totalVolume", "volume", "8"),
        "open_interest": ("openInterest", "9"),
    },
}
_MISSING = object()


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _as_float(value: object) -> float | None:
    if value in (None, "", "NaN"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    if value in (None, "", "NaN"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _coerce_timestamp(raw: object) -> datetime | None:
    if raw in (None, "", "NaN"):
        return None
    if isinstance(raw, (int, float)):
        numeric = float(raw)
        if numeric > 1e12:
            numeric = numeric / 1000.0
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _first_present(payload: dict[str, object], *keys: str) -> object:
    for key in keys:
        if key in payload:
            return payload[key]
    return _MISSING


def _coerce_float(payload: dict[str, object], *keys: str, default: float | None = None) -> float | None:
    raw = _first_present(payload, *keys)
    if raw is _MISSING:
        return default
    parsed = _as_float(raw)
    return default if parsed is None else parsed


def _coerce_int(payload: dict[str, object], *keys: str, default: int | None = None) -> int | None:
    raw = _first_present(payload, *keys)
    if raw is _MISSING:
        return default
    parsed = _as_int(raw)
    return default if parsed is None else parsed


def _coerce_text(payload: dict[str, object], *keys: str, default: str | None = None) -> str | None:
    raw = _first_present(payload, *keys)
    if raw is _MISSING:
        return default
    if raw in (None, ""):
        return default
    return str(raw)


def _bucket_time(timestamp: datetime, interval_minutes: int) -> datetime:
    local = timestamp.astimezone(ET).replace(second=0, microsecond=0)
    minute_floor = local.minute - (local.minute % max(interval_minutes, 1))
    return local.replace(minute=minute_floor)


def _quote_relative_score(last: float | None, bid: float | None, ask: float | None) -> float:
    if last is None or bid is None or ask is None or ask < bid:
        return 0.0
    if ask == bid:
        if last > ask:
            return 1.0
        if last < bid:
            return -1.0
        return 0.0
    midpoint = (bid + ask) / 2.0
    half_spread = max((ask - bid) / 2.0, 0.01)
    return _clamp((last - midpoint) / half_spread, -1.0, 1.0)


@dataclass(frozen=True)
class LiveQuote:
    symbol: str
    service: str
    bid: float | None
    ask: float | None
    last: float | None
    mark: float | None
    volume: int | None
    open_interest: int | None
    updated_at: datetime | None


@dataclass(frozen=True)
class FlowEvent:
    timestamp: datetime
    option_type: str
    signed_contracts: float
    premium_change: float


@dataclass(frozen=True)
class FlowPoint:
    time: datetime
    call_premium: float
    put_premium: float
    call_volume: float
    put_volume: float
    net_volume: float


@dataclass(frozen=True)
class StreamSnapshot:
    status_label: str
    active: bool
    connecting: bool
    message_count: int
    last_message_at: datetime | None
    last_error: str | None
    underlying_symbol: str | None
    underlying_quote: LiveQuote | None
    tracked_option_symbols: tuple[str, ...]
    option_quotes: tuple[LiveQuote, ...]
    flow_started_at: datetime | None

    @property
    def last_option_update_at(self) -> datetime | None:
        stamps = [quote.updated_at for quote in self.option_quotes if quote.updated_at is not None]
        if not stamps:
            return None
        return max(stamps)


def select_stream_contracts(
    contracts: list[OptionContract] | tuple[OptionContract, ...],
    spot: float,
    *,
    limit: int = 8,
) -> tuple[OptionContract, ...]:
    if limit <= 0:
        return ()

    candidates = [contract for contract in contracts if contract.symbol and contract.strike > 0]
    if not candidates:
        return ()

    front_dte = min(contract.days_to_expiration for contract in candidates)
    front = [contract for contract in candidates if contract.days_to_expiration == front_dte]
    if not front:
        front = candidates

    def spread_rank(contract: OptionContract) -> float:
        return contract.spread_pct if contract.spread_pct is not None else 9.99

    def sort_key(contract: OptionContract) -> tuple[float, int, int, float, str]:
        return (
            abs(contract.strike - spot),
            -contract.total_volume,
            -contract.open_interest,
            spread_rank(contract),
            contract.symbol,
        )

    calls = sorted([contract for contract in front if contract.option_type == "call"], key=sort_key)
    puts = sorted([contract for contract in front if contract.option_type == "put"], key=sort_key)

    selected: list[OptionContract] = []
    seen_symbols: set[str] = set()

    for call, put in zip_longest(calls, puts):
        for candidate in (call, put):
            if candidate is None or candidate.symbol in seen_symbols:
                continue
            selected.append(candidate)
            seen_symbols.add(candidate.symbol)
            if len(selected) >= limit:
                return tuple(selected)

    for contract in sorted(front, key=sort_key):
        if contract.symbol in seen_symbols:
            continue
        selected.append(contract)
        seen_symbols.add(contract.symbol)
        if len(selected) >= limit:
            break

    return tuple(selected)


class StreamStateStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._message_count = 0
        self._last_message_at: datetime | None = None
        self._last_error: str | None = None
        self._underlying_symbol: str | None = None
        self._tracked_option_symbols: tuple[str, ...] = ()
        self._underlying_quotes: dict[str, dict[str, object]] = {}
        self._option_quotes: dict[str, dict[str, object]] = {}
        self._option_meta: dict[str, str] = {}
        self._flow_events: list[FlowEvent] = []
        self._flow_day: date | None = None

    def remember_subscriptions(
        self,
        symbol: str | None,
        contracts: list[OptionContract] | tuple[OptionContract, ...] | tuple[str, ...] | list[str],
    ) -> None:
        with self._lock:
            if (
                symbol is not None
                and self._underlying_symbol is not None
                and symbol != self._underlying_symbol
            ):
                self._underlying_quotes = {}
                self._option_quotes = {}
                self._option_meta = {}
                self._flow_events = []
                self._flow_day = None
            self._underlying_symbol = symbol
            tracked_symbols: list[str] = []
            for contract in contracts:
                if isinstance(contract, str):
                    if contract:
                        tracked_symbols.append(contract)
                    continue
                if contract.symbol:
                    tracked_symbols.append(contract.symbol)
                    self._option_meta[contract.symbol] = contract.option_type
            self._tracked_option_symbols = tuple(tracked_symbols)
            for contract in contracts:
                if isinstance(contract, str):
                    continue
                if contract.symbol:
                    self._option_meta[contract.symbol] = contract.option_type
            tracked = set(self._tracked_option_symbols)
            self._option_quotes = {
                key: value for key, value in self._option_quotes.items() if key in tracked
            }

    def record_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message

    def apply_message(self, raw_message: str | dict[str, object]) -> None:
        now = datetime.now(timezone.utc)
        try:
            payload = raw_message if isinstance(raw_message, dict) else json.loads(raw_message)
        except json.JSONDecodeError:
            self.record_error("Stream returned a non-JSON payload.")
            return

        with self._lock:
            self._message_count += 1
            self._last_message_at = now

        if not isinstance(payload, dict):
            return

        responses = payload.get("response")
        if isinstance(responses, list):
            self._apply_responses(responses)

        data_rows = payload.get("data")
        if isinstance(data_rows, list):
            self._apply_data_rows(data_rows)

    def snapshot(self, *, active: bool, connecting: bool = False) -> StreamSnapshot:
        with self._lock:
            underlying_quote = self._quote_from_record(self._underlying_quotes.get(self._underlying_symbol or ""))
            option_quotes = tuple(
                quote
                for quote in (
                    self._quote_from_record(self._option_quotes.get(symbol))
                    for symbol in self._tracked_option_symbols
                )
                if quote is not None
            )
            last_message_at = self._last_message_at
            last_error = self._last_error
            message_count = self._message_count
            underlying_symbol = self._underlying_symbol
            tracked_option_symbols = self._tracked_option_symbols
            flow_started_at = min(
                (event.timestamp.astimezone(ET) for event in self._flow_events),
                default=None,
            )

        status_label = "idle"
        if connecting:
            status_label = "connecting"
        elif active:
            if last_message_at is None:
                status_label = "live"
            else:
                age_seconds = (datetime.now(timezone.utc) - last_message_at).total_seconds()
                status_label = "live" if age_seconds <= STREAM_STALE_SECONDS else "stale"
        elif last_error:
            status_label = "error"
        elif message_count > 0:
            status_label = "stopped"

        return StreamSnapshot(
            status_label=status_label,
            active=active,
            connecting=connecting,
            message_count=message_count,
            last_message_at=last_message_at,
            last_error=last_error,
            underlying_symbol=underlying_symbol,
            underlying_quote=underlying_quote,
            tracked_option_symbols=tracked_option_symbols,
            option_quotes=option_quotes,
            flow_started_at=flow_started_at,
        )

    def flow_series(self, interval_minutes: int) -> tuple[FlowPoint, ...]:
        with self._lock:
            events = tuple(self._flow_events)

        if not events:
            return ()

        buckets: dict[datetime, dict[str, float]] = defaultdict(
            lambda: {
                "call_premium": 0.0,
                "put_premium": 0.0,
                "call_volume": 0.0,
                "put_volume": 0.0,
            }
        )
        for event in events:
            bucket = buckets[_bucket_time(event.timestamp, interval_minutes)]
            if event.option_type == "call":
                bucket["call_premium"] += event.premium_change
                bucket["call_volume"] += event.signed_contracts
            elif event.option_type == "put":
                bucket["put_premium"] += event.premium_change
                bucket["put_volume"] += event.signed_contracts

        call_premium = 0.0
        put_premium = 0.0
        call_volume = 0.0
        put_volume = 0.0
        points: list[FlowPoint] = []
        for timestamp in sorted(buckets):
            bucket = buckets[timestamp]
            call_premium += bucket["call_premium"]
            put_premium += bucket["put_premium"]
            call_volume += bucket["call_volume"]
            put_volume += bucket["put_volume"]
            points.append(
                FlowPoint(
                    time=timestamp,
                    call_premium=call_premium,
                    put_premium=put_premium,
                    call_volume=call_volume,
                    put_volume=put_volume,
                    net_volume=call_volume - put_volume,
                )
            )
        return tuple(points)

    def _apply_responses(self, responses: list[object]) -> None:
        for response in responses:
            if not isinstance(response, dict):
                continue
            content = response.get("content")
            if not isinstance(content, dict):
                continue
            code = _as_int(content.get("code"))
            message = str(content.get("msg") or "").strip()
            with self._lock:
                if code not in (None, 0):
                    self._last_error = message or f"{response.get('service')} {response.get('command')} failed."
                elif message:
                    self._last_error = None

    def _apply_data_rows(self, rows: list[object]) -> None:
        for row in rows:
            if not isinstance(row, dict):
                continue
            service = str(row.get("service") or "")
            content = row.get("content")
            if not isinstance(content, list):
                continue
            timestamp = _coerce_timestamp(row.get("timestamp")) or datetime.now(timezone.utc)
            if service == "LEVELONE_EQUITIES":
                self._apply_quote_rows(self._underlying_quotes, service, content, timestamp)
            elif service == "LEVELONE_OPTIONS":
                self._apply_quote_rows(self._option_quotes, service, content, timestamp)

    def _apply_quote_rows(
        self,
        target: dict[str, dict[str, object]],
        service: str,
        rows: list[object],
        timestamp: datetime,
    ) -> None:
        aliases = QUOTE_FIELD_ALIASES.get(service, {})
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = _coerce_text(row, "key", "0")
            if not symbol:
                continue
            with self._lock:
                existing = dict(target.get(symbol, {}))
                record = {
                    "symbol": symbol,
                    "service": service,
                    "bid": _coerce_float(row, *aliases.get("bid", ("bid",)), default=existing.get("bid")),
                    "ask": _coerce_float(row, *aliases.get("ask", ("ask",)), default=existing.get("ask")),
                    "last": _coerce_float(row, *aliases.get("last", ("last",)), default=existing.get("last")),
                    "mark": _coerce_float(row, *aliases.get("mark", ("mark",)), default=existing.get("mark")),
                    "volume": _coerce_int(row, *aliases.get("volume", ("volume",)), default=existing.get("volume")),
                    "open_interest": _coerce_int(
                        row,
                        *aliases.get("open_interest", ("openInterest",)),
                        default=existing.get("open_interest"),
                    ),
                    "updated_at": timestamp,
                }
                if record["mark"] is None and record["bid"] is not None and record["ask"] is not None:
                    record["mark"] = (float(record["bid"]) + float(record["ask"])) / 2.0
                if service == "LEVELONE_OPTIONS":
                    self._record_option_flow(symbol, existing, record, timestamp)
                target[symbol] = record
                self._last_error = None

    def _record_option_flow(
        self,
        symbol: str,
        previous: dict[str, object],
        current: dict[str, object],
        timestamp: datetime,
    ) -> None:
        option_type = self._option_meta.get(symbol)
        if option_type not in {"call", "put"}:
            return

        previous_volume = _as_int(previous.get("volume"))
        current_volume = _as_int(current.get("volume"))
        if previous_volume is None or current_volume is None:
            return
        volume_delta = current_volume - previous_volume
        if volume_delta <= 0:
            return

        self._ensure_flow_day_locked(timestamp)

        bid = _as_float(current.get("bid"))
        ask = _as_float(current.get("ask"))
        last = _as_float(current.get("last"))
        mark = _as_float(current.get("mark"))
        if last is None:
            last = mark
        if last is None and bid is not None and ask is not None:
            last = (bid + ask) / 2.0
        if last is None:
            return

        side_score = _quote_relative_score(last, bid, ask)
        if abs(side_score) < 1e-6:
            return

        signed_contracts = float(volume_delta) * side_score
        premium_change = signed_contracts * last * 100.0
        self._flow_events.append(
            FlowEvent(
                timestamp=timestamp.astimezone(ET),
                option_type=option_type,
                signed_contracts=signed_contracts,
                premium_change=premium_change,
            )
        )

    def _ensure_flow_day_locked(self, timestamp: datetime) -> None:
        flow_day = timestamp.astimezone(ET).date()
        if self._flow_day is None:
            self._flow_day = flow_day
            return
        if flow_day != self._flow_day:
            self._flow_events = []
            self._flow_day = flow_day

    @staticmethod
    def _quote_from_record(record: dict[str, object] | None) -> LiveQuote | None:
        if not record:
            return None
        return LiveQuote(
            symbol=str(record.get("symbol") or ""),
            service=str(record.get("service") or ""),
            bid=_as_float(record.get("bid")),
            ask=_as_float(record.get("ask")),
            last=_as_float(record.get("last")),
            mark=_as_float(record.get("mark")),
            volume=_as_int(record.get("volume")),
            open_interest=_as_int(record.get("open_interest")),
            updated_at=record.get("updated_at") if isinstance(record.get("updated_at"), datetime) else None,
        )


class SchwabStreamManager:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        if not self.settings.has_credentials:
            raise RuntimeError("APP_KEY and APP_SECRET must be present in the environment.")

        try:
            import schwabdev
        except ImportError as error:
            raise RuntimeError(
                "The `schwabdev` package is not installed. Run `pip install -e .` first."
            ) from error

        self._client = schwabdev.Client(
            self.settings.app_key,
            self.settings.app_secret,
            callback_url=self.settings.callback_url,
        )
        stream_obj = getattr(self._client, "stream", None)
        if stream_obj is None:
            stream_cls = getattr(schwabdev, "Stream", None)
            if stream_cls is None:
                try:
                    from schwabdev.stream import Stream as stream_cls  # type: ignore
                except Exception as error:
                    raise RuntimeError("This schwabdev build does not expose a streaming client.") from error
            stream_obj = stream_cls(self._client)
        self._stream = stream_obj
        self._state = StreamStateStore()
        self._request_lock = threading.Lock()
        self._underlying_symbol: str | None = None
        self._option_symbols: tuple[str, ...] = ()

    def ensure_running(self) -> None:
        thread = getattr(self._stream, "_thread", None)
        if self._stream.active:
            return
        if thread is not None and thread.is_alive():
            return
        try:
            self._stream.start(receiver=self._receiver, daemon=True)
        except Exception as error:
            self._state.record_error(f"Could not start Schwab stream: {error}")

    def sync(self, symbol: str, contracts: list[OptionContract] | tuple[OptionContract, ...]) -> None:
        option_symbols = tuple(contract.symbol for contract in contracts if contract.symbol)
        self._state.remember_subscriptions(symbol, contracts)
        self.ensure_running()

        with self._request_lock:
            if symbol != self._underlying_symbol:
                self._send_request(
                    self._stream.level_one_equities(symbol, LEVEL_ONE_EQUITY_FIELDS, command="SUBS")
                )
                self._underlying_symbol = symbol

            if option_symbols != self._option_symbols:
                if option_symbols:
                    request = self._stream.level_one_options(
                        list(option_symbols),
                        LEVEL_ONE_OPTION_FIELDS,
                        command="SUBS",
                    )
                elif self._option_symbols:
                    request = self._stream.level_one_options(
                        list(self._option_symbols),
                        LEVEL_ONE_OPTION_FIELDS,
                        command="UNSUBS",
                    )
                else:
                    request = None

                if request is not None:
                    self._send_request(request)
                self._option_symbols = option_symbols

    def stop(self) -> None:
        with self._request_lock:
            try:
                if self._stream.active:
                    self._stream.stop(clear_subscriptions=True)
            except Exception as error:
                self._state.record_error(f"Could not stop Schwab stream cleanly: {error}")
            self._underlying_symbol = None
            self._option_symbols = ()
            self._state.remember_subscriptions(None, ())

    def snapshot(self) -> StreamSnapshot:
        thread = getattr(self._stream, "_thread", None)
        active = bool(self._stream.active)
        connecting = bool(thread is not None and thread.is_alive() and not active)
        return self._state.snapshot(active=active, connecting=connecting)

    def flow_series(self, interval_minutes: int) -> tuple[FlowPoint, ...]:
        return self._state.flow_series(interval_minutes)

    def _receiver(self, raw_message: str) -> None:
        self._state.apply_message(raw_message)

    def _send_request(self, request: dict[str, object]) -> None:
        try:
            self._stream.send(request)
        except Exception as error:
            self._state.record_error(f"Schwab stream subscription failed: {error}")
