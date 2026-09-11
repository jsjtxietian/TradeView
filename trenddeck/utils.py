from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd


def normalize_symbol(raw_symbol: str) -> str:
    text = raw_symbol.strip().upper()
    text = re.sub(r"[\s.]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text


def strip_check_name_prefix(name: str) -> str:
    text = str(name or "").strip()
    for separator in ("：", ":"):
        index = text.find(separator)
        if index >= 0:
            return text[index + 1 :].strip() or text
    return text


def require_values(*values: float | None) -> bool:
    return all(pd.notna(value) for value in values)


def fmt_short_date(value: pd.Timestamp) -> str:
    return value.strftime("%m-%d")


def fmt_price(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value:,.2f}"


def fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value:.1%}"


def fmt_signed_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1%}"


def fmt_volume(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    absolute = abs(float(value))
    if absolute >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if absolute >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,.0f}"


def coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric):
        return None
    return numeric


def fmt_signed_price(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.2f}"


def normalize_symbol_list(symbols: list[str] | str) -> list[str]:
    raw_symbols = symbols.split(",") if isinstance(symbols, str) else symbols
    normalized_symbols = [normalize_symbol(str(symbol)) for symbol in raw_symbols]
    return [symbol for symbol in normalized_symbols if symbol]
