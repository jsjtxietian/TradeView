"""Versioned, validated mathematical model specifications (no training at query time)."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

MODEL_VERSION = "base-v4"
MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
DEFAULT_PARAMETERS = {
    "swing_atr_multiplier": 0.8,
    "swing_min_reversal": 0.015,
    "swing_max_reversal": 0.06,
    "max_pause_width": 0.10,
    "breakout_volume_ratio": 1.5,
    "breakout_close_location": 0.6,
    "ready_distance": 0.03,
    "max_extension": 0.05,
    "max_ma20_extension": 0.15,
    "stop_buffer": 0.005,
    "min_dollar_volume": 2_000_000.0,
    "gap_min_return": 0.04,
    "gap_min_volume_ratio": 2.0,
    "gap_min_close_location": 0.6,
    "structure_min_strength": 0.5,
    "ready_min_volume_quality": 0.3,
    "prior_advance_min_atr": 2.0,
    "prior_advance_full_atr": 6.0,
    "max_risk": 0.10,
    "damage_filters_enabled": 1,
    "relative_context_enabled": 1,
    "terminal_integrity_enabled": 1,
    "risk_swing_tolerance": 0.01,
    "risk_lower_low_atr": 1.5,
    "risk_selloff_min": 0.04,
    "risk_selloff_atr": 1.5,
    "risk_extension_atr": 4.0,
    "rs_drawdown_floor": 0.03,
    "rs_drawdown_multiplier": 2.5,
}
DEFAULT_WEIGHTS = {
    "trend": 20.0,
    "structure": 25.0,
    "tightness": 15.0,
    "volume": 15.0,
    "relative_strength": 10.0,
    "location": 0.0,
    "risk": 0.0,
}
BASE_PARAMETERS = {
    "base_left_bars": 5, "base_confirm_bars": 3, "base_min_bars": 5,
    "base_max_bars": 200, "base_zone_atr": .75, "base_max_depth": .45,
    "base_count_min_advance": .20, "base_late_ordinal": 4,
    "base_reset_below_ma_days": 20,
}


@dataclass(frozen=True)
class SetupModel:
    version: str
    config_hash: str
    parameters: Mapping[str, float]
    weights: Mapping[str, float]
    engine_hash: str


def load_model(version: str = MODEL_VERSION) -> SetupModel:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", version):
        raise ValueError("Invalid model version")
    path = MODEL_DIR / f"{version}.json"
    if not path.is_file():
        raise ValueError(f"Unknown model version: {version}")
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = data.get("schema_version")
    if data.get("version") != version or schema != 3:
        raise ValueError("Model identity/schema mismatch")
    p, w = data.get("parameters", {}), data.get("weights", {})
    expected_parameters = set(DEFAULT_PARAMETERS) | {"shape_context_enabled"} | set(BASE_PARAMETERS)
    if set(p) != expected_parameters or set(w) != set(DEFAULT_WEIGHTS):
        raise ValueError("Model must explicitly define every supported parameter and weight")
    if any(
        isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0
        for x in [*p.values(), *w.values()]
    ):
        raise ValueError("Model values must be finite nonnegative numbers")
    if sum(w.values()) <= 0:
        raise ValueError("Relative model weights must have a positive sum")
    if p["shape_context_enabled"] != 1:
        raise ValueError("The current model requires shape_context_enabled = 1")
    # Preserve the existing relative shape weights, without timing/risk points.
    w = {key: value * 100 / sum(w.values()) for key, value in w.items()}
    for key in ['base_left_bars', 'base_confirm_bars', 'base_min_bars', 'base_max_bars',
                'base_late_ordinal', 'base_reset_below_ma_days']:
        if p[key] < 1 or int(p[key]) != p[key]:
            raise ValueError(f'{key} must be a positive integer')
    if not p['base_confirm_bars'] < p['base_min_bars'] <= p['base_max_bars']:
        raise ValueError('Invalid base observation windows')
    if not 0 < p['base_max_depth'] < 1 or not 0 < p['base_count_min_advance'] < 1:
        raise ValueError('Invalid base fraction parameters')
    for key in ["damage_filters_enabled", "relative_context_enabled", "terminal_integrity_enabled"]:
        if p[key] not in (0, 1):
            raise ValueError(f"{key} must be 0 or 1")
    if not 0 < p["swing_min_reversal"] <= p["swing_max_reversal"] < 1:
        raise ValueError("Invalid swing reversal thresholds")
    if not p["prior_advance_min_atr"] < p["prior_advance_full_atr"]:
        raise ValueError("Invalid prior advance thresholds")
    for key in [
        "max_pause_width",
        "breakout_close_location",
        "ready_distance",
        "max_extension",
        "max_ma20_extension",
        "stop_buffer",
        "gap_min_return",
        "gap_min_close_location",
        "structure_min_strength",
        "ready_min_volume_quality",
        "max_risk",
    ]:
        if not 0 < p[key] < 1:
            raise ValueError(f"{key} must be between 0 and 1")
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    engine = hashlib.sha256()
    names = ["setups.py", "setup_risk.py", "setup_research.py", "setup_events.py", "setup_model.py"]
    names.append("setup_shapes.py")
    names.extend(['setup_bases.py', 'setup_base_stage.py'])
    for name in names:
        engine.update(Path(__file__).with_name(name).read_bytes())
    return SetupModel(version, digest, MappingProxyType(p), MappingProxyType(w), engine.hexdigest()[:16])
