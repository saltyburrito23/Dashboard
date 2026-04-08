from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FAVORITES_PATH = ROOT / ".dashboard_favorites.json"


def _as_float(value: object) -> float | None:
    if value in (None, "", "NaN"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_favorite_symbols(symbols: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        normalized = str(symbol or "").strip().upper()
        if not normalized or normalized in seen:
            continue
        cleaned.append(normalized)
        seen.add(normalized)
    return tuple(cleaned)


def load_favorite_symbols(path: Path | None = None) -> tuple[str, ...]:
    target = path or DEFAULT_FAVORITES_PATH
    if not target.exists():
        return tuple()
    try:
        payload = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError):
        return tuple()
    if not isinstance(payload, list):
        return tuple()
    return normalize_favorite_symbols([str(item) for item in payload])


def save_favorite_symbols(
    symbols: list[str] | tuple[str, ...],
    path: Path | None = None,
) -> tuple[str, ...]:
    target = path or DEFAULT_FAVORITES_PATH
    normalized = normalize_favorite_symbols(symbols)
    target.write_text(json.dumps(list(normalized), indent=2) + "\n")
    return normalized


def toggle_favorite_symbol(symbol: str, path: Path | None = None) -> tuple[str, ...]:
    normalized_symbol = normalize_favorite_symbols([symbol])
    if not normalized_symbol:
        return load_favorite_symbols(path)
    favorites = list(load_favorite_symbols(path))
    target_symbol = normalized_symbol[0]
    if target_symbol in favorites:
        favorites.remove(target_symbol)
    else:
        favorites.append(target_symbol)
    return save_favorite_symbols(favorites, path)


@dataclass(frozen=True)
class FavoriteQuote:
    symbol: str
    price: float | None
    change: float | None
    percent_change: float | None


def extract_favorite_quote(symbol: str, payload: dict[str, object]) -> FavoriteQuote:
    quote_body = payload.get("quote") if isinstance(payload.get("quote"), dict) else payload
    quote = quote_body if isinstance(quote_body, dict) else {}
    price = None
    for key in ("lastPrice", "mark", "closePrice"):
        candidate = _as_float(quote.get(key))
        if candidate is not None and candidate > 0:
            price = candidate
            break

    close_price = None
    for key in ("closePrice", "previousClose"):
        candidate = _as_float(quote.get(key))
        if candidate is not None and candidate > 0:
            close_price = candidate
            break

    change = None
    percent_change = None
    if price is not None and close_price is not None and close_price > 0:
        change = price - close_price
        percent_change = change / close_price
    else:
        change = _as_float(quote.get("netChange"))
        percent_raw = _as_float(quote.get("netPercentChangeInDouble"))
        if percent_raw is None:
            percent_raw = _as_float(quote.get("percentChange"))
        if percent_raw is not None and abs(percent_raw) > 1.5:
            percent_raw = percent_raw / 100.0
        percent_change = percent_raw

    return FavoriteQuote(
        symbol=symbol.upper(),
        price=price,
        change=change,
        percent_change=percent_change,
    )


def build_favorite_quotes(
    symbols: list[str] | tuple[str, ...],
    payload: dict[str, object],
) -> tuple[FavoriteQuote, ...]:
    favorites: list[FavoriteQuote] = []
    normalized_symbols = normalize_favorite_symbols(symbols)
    payload_map = {str(key).upper(): value for key, value in payload.items()}
    for symbol in normalized_symbols:
        raw = payload_map.get(symbol)
        if isinstance(raw, dict):
            favorites.append(extract_favorite_quote(symbol, raw))
        else:
            favorites.append(FavoriteQuote(symbol=symbol, price=None, change=None, percent_change=None))
    return tuple(favorites)
