"""Behavioural checks for the diagnostic: availability, frozen levels and repair."""
import json

import numpy as np
import pandas as pd
import pytest

from trenddeck.setups.engine import SetupSeries
from trenddeck.setups.right_side import _observe, right_side_context, right_side_reason


def series(tail):
    c = np.r_[np.linspace(40, 98, 260), tail]
    return SetupSeries(pd.DataFrame({
        "Date": pd.bdate_range("2023-01-02", periods=len(c)),
        "Open": c, "High": c * 1.003, "Low": c * .997, "Close": c,
        "Volume": np.full(len(c), 1e6), "PriceMode": "adjusted",
    }))


PARENT = {"base_id": "outer", "start_index": 260, "known_index": 267,
          "upper": 100.3, "lower": 79.76, "role": "selected_base"}
RECOVERY = [100, 95, 90, 85, 80, 82, 86, 90, 94, 98]
PAUSE = [97, 96, 97.5, 96.8, 97.4]


def test_vertical_recovery_and_quiet_downtrend_do_not_manufacture_handle():
    s = series(RECOVERY + [99, 100, 101, 102, 103])
    assert not right_side_context(s, PARENT, len(s.c))["available"]
    s = series(RECOVERY + [97.5, 97, 96.5, 96, 95.5, 95, 94.5])
    assert not right_side_context(s, PARENT, len(s.c))["available"]


def test_recognition_records_actual_anchor_and_never_uses_future_confirmation():
    s = series(RECOVERY + PAUSE + [98, 99, 103, 104, 92, 91])
    context = right_side_context(s, PARENT, 275)
    p = context["pause"]
    assert p["start_session"] == s.date(269)
    assert p["known_session"] == s.date(273)
    assert p["relative_depth"] < 0.5 and p["role"] == "upper_base_pause"
    assert not right_side_context(s, PARENT, 273)["available"]
    for end in [276, 273, len(s.c), 274, 275]:
        short = SetupSeries(s.frame.iloc[:end])
        assert right_side_context(s, PARENT, end) == right_side_context(short, PARENT, end)
    json.dumps(context, allow_nan=False)


def test_undercut_reclaim_then_settling_are_different_and_failure_is_permanent():
    s = series(RECOVERY + PAUSE + [95.4, 97, 96.9, 97.1, 93, 92, 98])
    pause = right_side_context(s, PARENT, 275)["pause"]
    upper, lower = pause["upper"], pause["lower"]
    tested = _observe(s, pause, 276)
    reclaimed = _observe(s, pause, 277)
    settled = _observe(s, pause, 279)
    failed = _observe(s, pause, len(s.c))
    assert tested["state"] == "under_test"
    assert reclaimed["state"] == "reclaimed"
    assert settled["state"] == "settled"
    assert failed["state"] == "invalidated"
    assert failed["invalidated_session"] == s.date(280)
    for p in [tested, reclaimed, settled, failed]:
        assert (p["upper"], p["lower"]) == (upper, lower)
    assert "还未观察到后续稳定" in right_side_reason({"parent": PARENT, "pause": reclaimed})
    assert "不撤销失败记录" in right_side_reason({"parent": PARENT, "pause": failed})


def test_lower_cheat_is_not_rejected_for_failing_upper_half_rule():
    s = series([100, 94, 88, 82, 80, 81, 83, 85, 87, 89] + [88, 86.5, 88, 87.5, 88])
    p = right_side_context(s, PARENT, len(s.c))["pause"]
    assert p and p["role"] == "lower_repair_pause"
    assert p["lower_position_in_parent"] < 0.5


def test_same_day_cross_cannot_be_recognition_and_breakout_and_price_scaling_is_invariant():
    s = series(RECOVERY + PAUSE + [103])
    first = right_side_context(s, PARENT, len(s.c))["pause"]
    assert first["breakout_session"] > first["known_session"]
    frame = s.frame.copy()
    frame[["Open", "High", "Low", "Close"]] *= 7
    parent = {**PARENT, "upper": PARENT["upper"] * 7, "lower": PARENT["lower"] * 7}
    other = right_side_context(SetupSeries(frame), parent, len(frame))["pause"]
    assert other["state"] == first["state"]
    assert other["width"] == pytest.approx(first["width"])
    assert other["upper"] == pytest.approx(first["upper"] * 7)


def test_overlapping_proposals_do_not_replace_pause_or_hide_its_failure():
    s = series(RECOVERY + PAUSE + [97, 96.2, 97.8, 96.5, 97.3, 93, 92, 98])
    first = right_side_context(s, PARENT, 275)["pause"]
    for end in range(276, len(s.c) + 1):
        result = right_side_context(s, PARENT, end)
        assert result["pause"]["pause_id"] == first["pause_id"]
        assert result["pause"]["lower"] == first["lower"]
        assert len(result["attempts"]) == 1
    assert result["pause"]["state"] == "invalidated"


def test_nested_shelf_preserves_episode_and_new_attempt_starts_after_failure():
    s = series(RECOVERY + PAUSE + [97.6, 97.3, 97.7, 97.4, 97.6,
                                 93, 92, 94, 98, 97, 96, 97.5, 96.8])
    initial = right_side_context(s, PARENT, 275)["pause"]
    nested = right_side_context(s, PARENT, 280)["pause"]
    assert nested["pause_id"] != initial["pause_id"]
    assert nested["parent_pause_id"] == initial["pause_id"]
    assert nested["episode_id"] == initial["episode_id"]
    assert nested["relationship"] == "nested_tightening"
    failure = right_side_context(s, PARENT, 283)["pause"]
    assert failure["state"] == "invalidated"
    result = right_side_context(s, PARENT, len(s.c))
    fresh = result["pause"]
    assert fresh["relationship"] == "new_after_failure"
    assert fresh["start_session"] > failure["invalidated_session"]
    assert fresh["episode_id"] != initial["episode_id"]
    assert result["attempts"][-2]["state"] == "invalidated"


def test_actual_interval_grows_and_deterioration_does_not_redraw_bounds():
    s = series(RECOVERY + PAUSE + [97, 96.1, 97.8, 96.2])
    s.v[-3:] = 3e6
    s.h[-3:] *= 1.012
    first = right_side_context(s, PARENT, 275)["pause"]
    last = right_side_context(s, PARENT, len(s.c))["pause"]
    assert last["pause_id"] == first["pause_id"]
    assert last["metrics"]["bars"] == len(s.c) - first["start_index"]
    assert last["metrics"]["bars"] > first["metrics"]["bars"]
    assert last["width"] == first["width"]
    assert last["volume_ratio_at_recognition"] == first["volume_ratio_at_recognition"]
    assert last["metrics"]["volume_ratio"] > first["metrics"]["volume_ratio"]
    assert last["metrics"]["volume_change"] == "increasing"
    assert last["metrics"]["recent_distribution_bars"] > 0


def test_base_score_uses_actual_pause_and_signal_bar_cannot_change_formation():
    s = series([100, 98, 96, 97, 98, 97.4, 98, 97, 98.2, 97.8, 98.1])
    r = s.score()
    g = r["geometry"]
    m = g["right_side"]["pause"]["metrics"]
    assert g["pause_bars"] == m["bars"]
    assert g["volume_ratio_to_prior50_median"] == m["volume_ratio"]
    assert g["floor"] == g["right_side"]["pause"]["lower"]
    changed = s.frame.copy()
    changed.loc[len(changed)-1, ["High", "Close", "Volume"]] = [104, 103, 20e6]
    breakout = SetupSeries(changed).score()
    assert breakout["geometry"] == g
    assert breakout["components"]["tightness"] == r["components"]["tightness"]
    assert breakout["components"]["volume"] == r["components"]["volume"]


def test_breakout_retest_updates_measurement_and_is_not_ready():
    s = series([100, 98, 96, 97, 98, 97.4, 98, 101, 98])
    report = s.score()
    p = report["right_side"]["pause"]
    assert p["state"] == "retesting"
    assert p["metrics"]["end_session"] == report["as_of_session"]
    assert p["metrics"]["width"] > p["width"]
    assert report["status"] != "ready"
    assert not report["actionable"]


def test_old_pause_expires_without_being_slid_forward_every_day():
    s = series(RECOVERY + PAUSE + [97, 96.1] * 18)
    first = right_side_context(s, PARENT, 275)["pause"]
    last = right_side_context(s, PARENT, len(s.c))["pause"]
    assert last["pause_id"] == first["pause_id"]
    assert last["historical_only"]
