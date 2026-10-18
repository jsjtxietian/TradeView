from __future__ import annotations

import numpy as np
import pandas as pd

from trenddeck.setup_base_stage import stage_context


def base(name, start, known, upper, lower, breakout=None):
    return {"base_id": name, "start_index": start, "known_index": known,
            "breakout_index": breakout, "upper": upper, "lower": lower,
            "base_bars": known - start + 1, "width": 1 - lower / upper}


def inputs(size=200):
    return {"close": np.linspace(100., 200., size), "ma200": np.full(size, 90.),
            "dates": pd.bdate_range("2024-01-01", periods=size).to_numpy(),
            "end": size, "p": {}}


def test_one_observed_base_is_not_proof_of_early_cycle():
    result = stage_context([base("a", 10, 35, 115, 100, 37)], **inputs())
    assert result["observed_completed_bases"] == 1
    assert result["history_left_censored"]
    assert result["maturity"] == "unknown"
    assert result["current_ordinal_exact"] is None
    assert result["diagnostic_only"]


def test_nested_overlapping_and_duplicate_snapshots_do_not_multiply_count():
    records = [base("outer", 10, 55, 125, 100, 57),
               base("handle", 40, 53, 124, 120, 56),
               base("outer", 10, 55, 125, 100, 57),
               base("variant", 12, 56, 126, 101, 58)]
    result = stage_context(records, **inputs())
    assert result["observed_completed_bases"] == 1
    assert len(result["independent_base_events"]) == 1
    assert len(result["base_on_base_relations"]) == 2


def test_independent_advances_count_but_small_shelves_share_parent():
    records = [base("a", 5, 25, 110, 100, 27),
               base("b", 40, 55, 125, 113, 57),
               base("c", 80, 100, 145, 132, 102),
               base("d", 160, 180, 185, 170)]
    result = stage_context(records, **inputs())
    assert result["observed_completed_bases"] == 2
    assert result["current_ordinal_lower_bound"] == 3
    assert result["maturity"] == "mature"
    assert result["base_on_base_relations"][0]["relation"] == "base_on_base_candidate"
    assert result["base_on_base_relations"][0]["parent_base_id"] == "a"


def test_new_higher_roof_without_actual_intervening_advance_does_not_count():
    args = inputs()
    args["close"][:] = 112
    result = stage_context([base("a", 5, 25, 110, 100, 27),
                            base("b", 40, 55, 145, 110, 57)], **args)
    assert result["observed_completed_bases"] == 1


def test_internal_cheat_breakout_does_not_complete_outer_base():
    records = [base("outer", 10, 55, 145, 100),
               base("cheat", 40, 53, 124, 120, 56)]
    result = stage_context(records, **inputs())
    assert result["observed_completed_bases"] == 0
    assert result["independent_base_events"][0]["breakout_index"] is None


def test_obsolete_unaccepted_proposal_does_not_swallow_later_true_advances():
    records = [base("old", 1, 10, 105, 95),
               base("accepted", 20, 35, 115, 100, 37),
               base("next", 100, 125, 160, 145, 127)]
    result = stage_context(records, **inputs())
    assert result["observed_completed_bases"] == 2
    assert result["independent_base_events"][0]["base_id"] == "accepted"


def test_late_count_is_only_a_lower_bound_when_left_history_missing():
    args = inputs(300)
    args["close"] = np.linspace(100., 270., 300)
    records = [base("a", 5, 25, 110, 100, 27),
               base("b", 70, 90, 145, 125, 92),
               base("c", 145, 165, 185, 165, 167),
               base("d", 230, 250, 235, 215)]
    result = stage_context(records, **args)
    assert result["maturity"] == "late"
    assert result["current_ordinal_lower_bound"] == 4
    assert result["history_left_censored"]
    assert result["current_ordinal_exact"] is None


def test_accepted_break_failure_does_not_reset_to_early():
    args = inputs()
    args["close"][80:90] = 85
    result = stage_context([base("a", 10, 35, 115, 100, 37)], **args)
    assert result["reset_pending"]
    assert result["maturity"] == "unknown"
    assert result["observed_completed_bases"] == 1
    assert result["reset_evidence"][0]["observed_index"] == 80


def test_full_observed_ma_repair_then_new_base_can_establish_early_cycle():
    args = inputs()
    args["ma200"][:50] = np.linspace(120, 110, 50)
    args["ma200"][50:] = 110
    args["close"][:50] = 100
    records = [base("repair", 60, 85, 145, 125, 90)]
    result = stage_context(records, **args)
    assert not result["history_left_censored"]
    assert result["maturity"] == "early"
    assert not result["reset_pending"]
    assert result["cycle_anchor"]["confirmed_index"] == 90


def test_decline_alone_does_not_prove_new_cycle():
    args = inputs()
    args["ma200"][:] = np.linspace(130., 110., 200)
    args["close"][:] = 100
    result = stage_context([base("unrepaired", 60, 85, 115, 95)], **args)
    assert result["history_left_censored"]
    assert result["reset_pending"]
    assert result["maturity"] == "unknown"


def test_future_rows_records_and_acceptances_do_not_change_prefix():
    args = inputs(100)
    records = [base("a", 10, 35, 115, 100, 37), base("b", 80, 90, 190, 170)]
    expected = stage_context(records, **args)
    args["close"] = np.r_[args["close"], np.full(100, np.nan)]
    args["ma200"] = np.r_[args["ma200"], np.full(100, np.nan)]
    args["dates"] = np.r_[args["dates"], pd.bdate_range("2030-01-01", periods=100).to_numpy()]
    records[1]["breakout_index"] = 110
    records += [base("future", 5, 150, 500, 1, 170)]
    assert stage_context(records, **args) == expected


def test_empty_prefix_and_nan_ma_do_not_invent_anchor():
    args = inputs()
    args["end"] = 0
    result = stage_context([], **args)
    assert result["maturity"] == "unknown"
    assert result["as_of"] is None
    args["end"] = 200
    args["ma200"][:] = np.nan
    assert stage_context([base("a", 10, 35, 115, 100, 37)], **args)["history_left_censored"]
