from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from trenddeck.setups import queries as setup_queries
from trenddeck.setups.explanations import conditions_markdown, score_reasons
from trenddeck.setups.model import load_model


def test_report_default_is_compact_and_details_are_opt_in(cached_data):
    compact = setup_queries.get_setup_report("NVDA")
    full = setup_queries.get_setup_report("NVDA", include_details=True)
    assert compact["schema_version"] == 2
    assert compact["as_of_session"] == cached_data["date"]
    assert compact["reasons"] and "###" in compact["conditions_markdown"]
    assert not {"details", "geometry", "components", "warnings", "risk_evidence", "model"} & compact.keys()
    assert {k: v for k, v in full.items() if k != "details"} == compact
    assert full["details"]["score"] == compact["score"]
    assert "geometry" in full["details"] and "parameters" in full["details"]["model"]
    assert any("流动性不足" in reason for reason in compact["reasons"])
    assert not any("average_dollar_volume" in reason for reason in compact["reasons"])


def test_rankings_share_conditions_and_do_not_leak_item_diagnostics(cached_data):
    compact = setup_queries.get_setup_rankings(symbols=["NVDA", "OLDER", "MISSING"])
    full = setup_queries.get_setup_rankings(symbols=["NVDA", "OLDER", "MISSING"], include_details=True)
    report = setup_queries.get_setup_report("NVDA")
    assert compact["schema_version"] == 2
    assert compact["conditions_markdown"] == report["conditions_markdown"]
    assert compact["coverage"]["scored"] == 1
    assert len(compact["coverage"]["excluded"]) == 2
    assert set(compact["items"][0]) == {"symbol", "rank", "score", "status", "reasons"}
    assert compact["items"][0]["reasons"] == report["reasons"]
    assert full["items"][0]["details"]["geometry"]
    assert full["details"]["universe"] == ["NVDA", "OLDER", "MISSING"]
    assert {k: v for k, v in full["items"][0].items() if k != "details"} == compact["items"][0]


def test_public_http_report_and_rankings_use_same_detail_option(cached_data):
    from app import app

    client = TestClient(app)
    try:
        for path, params in [("/api/setups/report/NVDA", {}),
                             ("/api/setups/rankings", {"symbols": ["NVDA"]})]:
            compact = client.get(path, params=params)
            full = client.get(path, params={**params, "include_details": True})
            assert compact.status_code == full.status_code == 200
            assert compact.json()["schema_version"] == 2
            assert "details" not in compact.json() and "details" in full.json()
            assert compact.json()["conditions_markdown"] == full.json()["conditions_markdown"]
    finally:
        client.close()


def test_conditions_use_selected_weights_and_enabled_rules():
    model = load_model()
    changed = replace(model, weights={**model.weights, "trend": 30, "structure": 15})
    text = conditions_markdown(changed)
    assert "| 上升趋势 | 30 |" in text
    assert "| 整理形态 | 15 |" in text
    assert "| 上升趋势 | 20 |" not in text
    assert "未修复的下降高低点" in text


def test_shape_conditions_describe_actual_weights_and_separate_entry_timing():
    model = load_model()
    text = conditions_markdown(model)
    assert f"| 上升趋势 | {model.weights['trend']:.2f} |" in text
    assert "| 接近突破位置 |" not in text and "| 结构距离 |" not in text
    assert "趋势修复路径" in text and "已确认波段连续" in text
    assert "三种状态不单独封顶" in text
    assert "超过阻力 5%" not in text


def test_repair_and_extension_explanations_do_not_claim_full_stage_two():
    report = {
        "status": "extended", "trend_phase": "repairing",
        "warnings": ["early_trend_repair", "extended_from_pivot_or_ma20"],
        "components": {"trend": {"points": 20, "maximum": 23.53}},
    }
    reasons = score_reasons(report, load_model())
    assert any("长期均线仍在修复" in reason for reason in reasons)
    assert any("自身波动尺度" in reason for reason in reasons)
    assert not any("整体排列向上" in reason or "远离突破位置" in reason for reason in reasons)


def test_shape_reason_requires_usable_boundaries_and_returns_at_most_one():
    model = load_model()
    usable = {"usable": True, "tentative_two_touch_line": False}
    window = {"upper_boundary": usable, "lower_boundary": usable,
              "geometry_evidence": ["converging_range", "ascending_triangle_geometry"]}
    report = {"status": "developing", "shape_context": {"windows": [window, window]}}
    reasons = score_reasons(report, model)
    assert len([reason for reason in reasons if "形态背景诊断" in reason]) == 1
    assert any("上沿较平、下沿抬高" in reason for reason in reasons)
    report["shape_context"]["windows"] = [{**window, "lower_boundary": {"available": False}}]
    assert not any("几何特征" in reason or "区间收敛" in reason for reason in score_reasons(report, model))
    report["shape_context"]["windows"] = [{**window, "flag_like_context": True,
                                         "upper_boundary": {**usable, "tentative_two_touch_line": True}}]
    assert any("初步观察到上沿下移" in reason for reason in score_reasons(report, model))


def test_reasons_describe_actual_damage_and_allow_new_engine_evidence():
    report = {
        "status": "damaged", "warnings": ["failed_rebound_below_prior_low"],
        "reason_texts": ["右侧整理比左侧更加松散。"],
        "components": {"volume": {"points": 1, "maximum": 15}},
    }
    reasons = score_reasons(report, load_model())
    assert any("反弹后再次跌破前低" in reason for reason in reasons)
    assert "右侧整理比左侧更加松散。" in reasons
    assert any("量能收缩不足" in reason for reason in reasons)
    assert not any("明显减少" in reason for reason in reasons)


def test_insufficient_history_is_not_presented_as_zero_score(cached_data):
    result = setup_queries.get_setup_report("NVDA", as_of="2025-02-03")
    assert result["score"] is None
    assert result["status"] == "insufficient_history"
    assert "暂不评分" in result["reasons"][0]
    assert "details" not in result


def test_base_rubric_explains_base_first_and_uses_selected_thresholds():
    model = load_model("base-v4")
    changed = replace(model, parameters={**model.parameters, "base_min_bars": 8,
                                        "base_late_ordinal": 5, "base_count_min_advance": .25})
    text = conditions_markdown(changed)
    assert "VCP 是基底的一类特征" in text
    assert "基底可以存在而没有 VCP 特征" in text
    assert "短期基底不等同于平底" in text
    assert "至少观察 8 日整理" in text and "至少 5 个独立基底" in text
    assert "25% 的工程门槛" in text
    assert "末端平台宽度不超过 10%" in text
    assert "不额外加分，也不放宽追价限制" in text


@pytest.mark.parametrize(("stage", "expected"), [
    ({"maturity": "early", "history_left_censored": False}, "新周期的早期基底"),
    ({"maturity": "mature"}, "周期逐渐成熟"),
    ({"maturity": "late", "history_left_censored": True}, "呈偏后期特征"),
    ({"maturity": "unknown", "history_left_censored": True}, "不能因观察到的基底较少"),
    ({"maturity": "unknown"}, "阶段暂记为未知"),
    ({"maturity": "late", "reset_pending": True}, "新周期尚未确认"),
])
def test_base_stage_explanation_is_context_not_a_score_reward(stage, expected):
    report = {"status": "developing", "pattern": "range_base",
              "geometry": {"base_id": "base:2025-01-01"}, "base_context": {"stage": stage}}
    reasons = score_reasons(report, load_model("base-v4"))
    assert any(expected in reason for reason in reasons)
    assert any("区间基底" in reason for reason in reasons)
    assert not any("base:2025" in reason for reason in reasons)
    without_context = {key: value for key, value in report.items() if key != "base_context"}
    assert not any(expected in reason for reason in score_reasons(without_context, load_model()))


def test_short_base_is_not_presented_as_flat_or_vcp():
    report = {"status": "ready", "pattern": "short_base",
              "geometry": {"base_id": "example", "lifecycle": "forming"},
              "components": {"structure": {"points": 29, "maximum": 30}}}
    reasons = score_reasons(report, load_model("base-v4"))
    assert any("短期基底" in reason and "尚不能称为平底" in reason for reason in reasons)
    assert not any("多轮波动收缩" in reason or "识别到平底" in reason for reason in reasons)
    report["pattern"] = "vcp"
    assert any("缺少完整的 VCP" in reason for reason in score_reasons(report, load_model("base-v4")))
    report['geometry']['vcp_evidence'] = {'qualified': True, 'depths': [.15, .08, .04]}
    assert any("先识别出基底" in reason for reason in score_reasons(report, load_model("base-v4")))


def test_late_context_remains_visible_in_compact_reasons_with_many_warnings():
    report = {"status": "damaged", "warnings": [
        "failed_rebound_below_prior_low", "material_lower_low_sequence", "repeated_unrepaired_selloffs",
        "falling_intermediate_trend", "failed_frozen_pivot", "early_trend_repair",
    ], "base_context": {"stage": {"maturity": "late"}}}
    reasons = score_reasons(report, load_model("base-v4"))
    assert len(reasons) <= 6
    assert "反弹后再次跌破前低" in reasons[0]
    assert any("偏后期" in reason and "不加分" in reason for reason in reasons)


def test_base_api_preserves_compact_schema_and_hides_catalogue(cached_data):
    compact = setup_queries.get_setup_report("NVDA", model_version="base-v4")
    details = setup_queries.get_setup_report("NVDA", model_version="base-v4", include_details=True)
    rankings = setup_queries.get_setup_rankings(symbols=["NVDA"], model_version="base-v4")
    assert compact["schema_version"] == rankings["schema_version"] == 2
    assert not {"base_context", "base_id", "geometry"} & compact.keys()
    assert "base_context" in details["details"]
    assert compact["conditions_markdown"] == rankings["conditions_markdown"]
    assert {k: v for k, v in details.items() if k != "details"} == compact
