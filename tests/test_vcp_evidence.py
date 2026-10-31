from trenddeck.setups.explanations import score_reasons
from trenddeck.setups.model import load_model
from trenddeck.setups.vcp import vcp_evidence


def legs(depths, volumes, lows=None):
    lows = lows or [90 + i for i in range(len(depths))]
    return [
        dict(depth=depth, median_volume=volume, low_price=low)
        for depth, volume, low in zip(depths, volumes, lows)
    ]


def test_vcp_requires_price_and_volume_contraction_not_just_a_tight_box():
    good = legs([0.20, 0.10, 0.05], [100, 90, 60])
    assert vcp_evidence(good, True, 0.06, 0.5)["qualified"]
    assert not vcp_evidence(legs([0.20, 0.10, 0.05], [100, 120, 130]), True, 0.06, 0.5)["qualified"]
    assert not vcp_evidence(legs([0.10, 0.20, 0.05], [100, 90, 60]), True, 0.06, 0.5)["qualified"]
    assert not vcp_evidence(good, True, 0.06, 1.2)["qualified"]
    assert not vcp_evidence(good, False, 0.06, 0.5)["qualified"]
    assert not vcp_evidence(good[:1], True, 0.03, 0.5)["qualified"]
    assert not vcp_evidence([], True, 0.03, 0.5)["qualified"]


def test_higher_lows_are_evidence_not_a_mechanical_gate():
    result = vcp_evidence(legs([0.20, 0.10, 0.05], [100, 90, 60], [90, 91, 89.8]), True, 0.06, 0.5)
    assert result["qualified"] and not result["last_low_above_first"]


def test_invalidation_survives_compact_report_and_distinguishes_scales():
    report = {
        "status": "damaged",
        "warnings": ["failed_rebound_below_prior_low"] * 8,
        "reason_texts": [f"Extra {i}" for i in range(8)],
        "base_context": {"stage": {"maturity": "late"}},
        "invalidation": {
            "known_session": "2025-01-03",
            "terminal_level": 95,
            "terminal_breached": True,
            "outer_level": 80,
            "outer_breached": False,
        },
    }
    reasons = score_reasons(report, load_model())
    assert len(reasons) <= 6
    text = next(r for r in reasons if "失效条件" in r)
    assert "95.00" in text and "末端平台失守（已触发）" in text
    assert "80.00" in text and "整体基底失效（未触发）" in text
    assert any("偏后期" in r for r in reasons)
