"""Conservative VCP label evidence; does not alter base selection or score weights."""

from __future__ import annotations


def vcp_evidence(legs, terminal_intact, pause_width, terminal_volume_ratio):
    depths = [leg["depth"] for leg in legs]
    volumes = [leg["median_volume"] for leg in legs]
    enough = len(legs) >= 2
    checks = {
        "multiple_confirmed_pullbacks": enough,
        "progressively_shallower": enough and all(b < a for a, b in zip(depths, depths[1:])),
        "pullback_volume_reduced": enough and volumes[0] > 0 and 0 < volumes[-1] < volumes[0],
        "terminal_volume_dryup": 0 < terminal_volume_ratio < 1,
        "terminal_price_tight": pause_width <= 0.10,
        "terminal_support_intact": bool(terminal_intact),
    }
    return {
        "qualified": all(checks.values()),
        "checks": checks,
        "missing": [key for key, value in checks.items() if not value],
        "contraction_count": len(legs),
        "depths": depths,
        "pullback_median_volumes": volumes,
        "last_to_first_volume_ratio": volumes[-1] / volumes[0] if enough and volumes[0] > 0 else None,
        "terminal_volume_ratio": terminal_volume_ratio,
        # Diagnostic only: a brief undercut/reclaim need not destroy a base.
        "last_low_above_first": bool(enough and legs[-1]["low_price"] > legs[0]["low_price"]),
        "scope": "label_evidence_only_not_extra_score",
    }
