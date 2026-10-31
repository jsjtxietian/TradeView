from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trenddeck.setups.engine import SetupSeries
from trenddeck.setups.events import breach_episodes, pivot_outcome, summarize_events


def fixture_path(future=40):
    c = np.r_[np.linspace(40, 99, 306), np.full(future, 99.0)]
    f = pd.DataFrame(
        {
            "Date": pd.bdate_range("2024-01-02", periods=len(c)),
            "Open": c,
            "High": c + 0.4,
            "Low": c - 0.4,
            "Close": c,
            "Volume": np.full(len(c), 1_000_000.0),
            "PriceMode": "adjusted",
        }
    )
    report = {
        "eligible": True,
        "status": "ready",
        "close": 99.0,
        "as_of_session": f.Date.iloc[305].strftime("%Y-%m-%d"),
        "geometry": {"pivot": 100.0, "pattern_strength": 1.0, "pause_width": 0.04},
        "invalidation_level": 96.0,
        "breakout": {"confirmed": False},
    }
    return f, report


def bar(f, i, close, low=None, volume=3_000_000, open_price=None):
    o = close if open_price is None else open_price
    f.loc[i, ["Open", "High", "Low", "Close", "Volume"]] = [
        o,
        max(o, close) + 0.2,
        min(o, close) - 0.4 if low is None else low,
        close,
        volume,
    ]


def test_breakout_first_then_failure_uses_next_open_not_ideal_pivot_fill():
    f, r = fixture_path()
    bar(f, 306, 102)
    bar(f, 307, 103, open_price=104)
    bar(f, 308, 95, low=94)
    x = pivot_outcome(SetupSeries(f), r)
    assert x["race_8pct"] == "breakout_first"
    assert x["post_breakout"]["entry_open"] == 104
    assert x["post_breakout"]["hit_8pct_stop"]
    assert x["post_breakout"]["stop_8pct_from_entry"] == pytest.approx(104 * 0.92)


def test_same_day_stop_before_close_breakout_is_still_failure_after_recovery():
    f, r = fixture_path()
    bar(f, 306, 102, low=90)
    x = pivot_outcome(SetupSeries(f), r)
    assert x["race_8pct"] == "stop_first"
    assert x["first_8pct_stop"] == x["confirmed_breakout"]
    assert x["post_breakout"]["status"] == "not_triggered"


def test_intraday_touch_and_undercut_reclaim_are_not_automatically_failure():
    f, r = fixture_path()
    f.loc[306, "High"] = 102
    x = pivot_outcome(SetupSeries(f), r)
    assert x["first_intraday_touch"] is not None
    assert x["confirmed_breakout"] is None and x["race_8pct"] == "neither"
    bar(f, 306, 102)
    bar(f, 307, 99, low=98, open_price=101)
    bar(f, 308, 103)
    x = pivot_outcome(SetupSeries(f), r)
    post = x["post_breakout"]
    assert post["closed_back_below_pivot"]
    assert not post["hit_8pct_stop"] and not post["hit_structure_stop"]
    assert post["pivot_breaches"][0]["sessions_to_reclaim"] == 1
    assert x["price_acceptance_known_at"] == str(f.Date.iloc[308])[:10]


def test_stop_before_later_breakout_and_structure_reference_stays_frozen():
    f, r = fixture_path()
    bar(f, 306, 89)
    bar(f, 310, 102)
    x = pivot_outcome(SetupSeries(f), r)
    assert x["race_8pct"] == "stop_first"
    assert x["frozen_pivot"] == 100 and x["structural_stop"] == 96
    assert x["confirmed_breakout"] == str(f.Date.iloc[310])[:10]


def test_structure_undercut_can_reclaim_without_reaching_8pct_line():
    f, r = fixture_path()
    bar(f, 306, 95, low=94)
    bar(f, 307, 102)
    x = pivot_outcome(SetupSeries(f), r)
    assert x["race_structure"] == "stop_first"
    assert x["race_8pct"] == "breakout_first"


def test_missing_tail_is_censored_not_counted_as_surviving_breakout():
    f, r = fixture_path(25)
    bar(f, 324, 102)
    x = pivot_outcome(SetupSeries(f), r)
    assert x["status"] == "evaluated" and x["post_breakout"]["status"] == "censored"
    summary = summarize_events([x])
    assert summary["post_breakout_complete"] == 0
    assert summary["post_breakout_8pct_failure_rate"] is None
    assert summary["post_breakout_censored"] == 1


def test_future_beyond_label_window_cannot_change_outcome():
    f, r = fixture_path(50)
    bar(f, 306, 102)
    expected = pivot_outcome(SetupSeries(f), r)
    f.loc[327:, ["Open", "High", "Low", "Close"]] *= 10
    assert pivot_outcome(SetupSeries(f), r) == expected


def test_missing_middle_session_is_not_silently_compressed():
    f, r = fixture_path()
    benchmark = f.copy()
    s = SetupSeries(f.drop(index=310), benchmark)
    assert pivot_outcome(s, r)["status"] == "missing_sessions"


def test_weak_volume_can_have_price_acceptance_without_volume_confirmation():
    f, r = fixture_path()
    for i in range(306, 310):
        bar(f, i, 102, volume=500_000)
    x = pivot_outcome(SetupSeries(f), r)
    assert x["confirmed_breakout"] is None
    assert x["race_price_acceptance_8pct"] == "breakout_first"
    assert x["price_acceptance_known_at"] == str(f.Date.iloc[307])[:10]
    assert x["price_cross_post_breakout"]["status"] == "evaluated"
    assert x["price_cross_post_breakout"]["entry_session"] == str(f.Date.iloc[307])[:10]
    assert x["price_acceptance_post_breakout"]["entry_session"] == str(f.Date.iloc[308])[:10]


def test_late_volume_gap_does_not_replace_the_earlier_price_break():
    f, r = fixture_path()
    bar(f, 306, 102, volume=500_000)
    bar(f, 307, 102, volume=500_000)
    bar(f, 308, 99, volume=500_000)
    bar(f, 309, 108, volume=4_000_000)
    bar(f, 310, 109, volume=2_000_000)
    x = pivot_outcome(SetupSeries(f), r)
    assert x["price_cross_post_breakout"]["entry_open"] == 102
    assert x["post_breakout"]["entry_open"] == 109
    assert x["post_breakout"]["entry_extended"]
    assert summarize_events([x])["post_breakout_usable_entries"] == 0


def test_episode_distinguishes_wick_next_day_reclaim_and_persistent_damage():
    f, _ = fixture_path()
    for i, (c, low) in enumerate([(101, 99), (99, 98), (102, 101), (98, 97), (97, 96), (96, 95)], 306):
        bar(f, i, c, low=low)
    episodes = breach_episodes(SetupSeries(f), 306, 311, 100)
    assert [e["sessions_to_reclaim"] for e in episodes] == [0, 1, None]
    assert [e["persistent_3_closes"] for e in episodes] == [False, False, True]


def test_already_confirmed_is_not_counted_as_a_successful_prediction():
    f, r = fixture_path()
    r["breakout"]["confirmed"] = True
    x = pivot_outcome(SetupSeries(f), r)
    s = summarize_events([x])
    assert s["already_confirmed"] == 1 and s["pre_breakout_setups"] == 0
    assert s["breakout_before_8pct_rate"] is None


def test_already_confirmed_does_not_skip_next_day_risk_on_price_branch():
    f, r = fixture_path()
    bar(f, 305, 102)
    r.update(close=102)
    r["breakout"]["confirmed"] = True
    bar(f, 306, 89, open_price=102)
    bar(f, 307, 103)
    bar(f, 308, 103)
    x = pivot_outcome(SetupSeries(f), r)
    assert x["price_cross_post_breakout"]["entry_session"] == str(f.Date.iloc[306])[:10]
    assert x["price_cross_post_breakout"]["hit_8pct_stop"]
    assert x["race_price_acceptance_8pct"] == "stop_first"


def test_all_confirmation_variants_use_their_own_prior_structure_breach():
    f, r = fixture_path()
    bar(f, 306, 95, low=94)
    bar(f, 307, 102)
    bar(f, 308, 102)
    x = pivot_outcome(SetupSeries(f), r)
    s = summarize_events([x])
    assert s["post_breakout_usable_entries"] == 0
    assert all(v["usable_entries"] == 0 for v in s["price_confirmation_variants"].values())


def test_two_future_closes_can_complete_three_session_window_with_observation():
    f, r = fixture_path()
    bar(f, 306, 102, volume=500_000)
    bar(f, 307, 102, volume=500_000)
    bar(f, 308, 89)
    x = pivot_outcome(SetupSeries(f), r)
    assert x["price_acceptance_known_at"] == str(f.Date.iloc[307])[:10]
    assert x["race_price_acceptance_8pct"] == "breakout_first"
