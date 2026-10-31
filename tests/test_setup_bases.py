"""Structural/causal tests, with no future-return optimization targets."""
import json

import numpy as np
import pandas as pd
import pytest

from trenddeck.setups.engine import SetupSeries
from trenddeck.setups.model import load_model


@pytest.mark.parametrize('retired', ['pivot-v1', 'pivot-v2', 'shape-v3'])
def test_retired_model_configs_are_unavailable(retired):
    with pytest.raises(ValueError, match='Unknown model version'):
        load_model(retired)


def frame(tail):
    c = np.r_[np.linspace(40, 98, 260), tail]
    return pd.DataFrame({'Date':pd.bdate_range('2023-01-02', periods=len(c)),
                         'Open':c, 'High':c*1.003, 'Low':c*.997, 'Close':c,
                         'Volume':np.full(len(c), 1e6), 'PriceMode':'adjusted'})


def test_monotone_travel_is_not_a_base():
    for c in [np.linspace(99,130,80), np.linspace(98,60,80)]:
        s=SetupSeries(frame(c))
        assert not s.bases.snapshot(len(s.c))['selected']


def test_known_time_frozen_boundaries_and_accepted_breakout_identity():
    f=frame([100,97,94,96,98,96,97,99,98,99,103,104,105])
    s=SetupSeries(f)
    before=s.bases.snapshot(270)
    r=next(x for x in before['catalogue'] if x['start_index']==260 and x['scale']=='local')
    assert r['known_index']>=264
    assert not s.bases.snapshot(r['known_index'])['catalogue']
    after=s.bases.snapshot(273)
    same=next(x for x in after['catalogue'] if x['base_id']==r['base_id'])
    assert same['upper']==r['upper'] and same['lower']==r['lower']
    assert same['breakout_index']==271
    assert after['selected']['base_bars']==10
    assert after['selected']['pattern']=='short_base'


def test_wick_or_single_close_then_reclaim_is_not_outer_invalidation():
    f=frame([100,97,94,96,98,96,97,99,98,99,92,98])
    f.loc[270,'Low']=85
    s=SetupSeries(f)
    r=next(x for x in s.bases.snapshot(272)['catalogue'] if x['start_index']==260)
    assert r['invalidated_index'] is None
    f.loc[271,['Open','High','Low','Close']]=[90,91,89,90]
    damaged=SetupSeries(f).bases.snapshot(272)
    r=next(x for x in damaged['catalogue'] if x['start_index']==260)
    assert r['invalidated_index']==271 and r['lower']>93
    assert damaged['selected'].get('base_id')!=r['base_id']


def test_later_broad_base_does_not_rewrite_failed_short_base():
    tail=[100,97,94,96,98,90,88,86,84,82]
    tail+=list(np.linspace(83,96,22))+[94,95,96,95,97]
    s=SetupSeries(frame(tail))
    snap=s.bases.snapshot(len(s.c))
    same_anchor=[x for x in snap['catalogue'] if x['start_index']==260]
    local=next(x for x in same_anchor if x['scale']=='local')
    broad=next(x for x in same_anchor if x['scale']=='structural')
    assert local['invalidated_index'] is not None
    assert broad['known_index']>local['invalidated_index']
    assert broad['lower']<local['lower'] and broad['base_id']!=local['base_id']


def test_snapshot_and_score_invariant_to_future_and_query_order():
    f=frame([100,97,94,96,98,96,97,99,98,99,103,104,105,90,89,95,97,99])
    full=SetupSeries(f)
    for end in [272,266,277,270,265]:
        prefix=SetupSeries(f.iloc[:end].copy())
        assert full.bases.snapshot(end)==prefix.bases.snapshot(end)
        assert full.score(end-1)==prefix.score()
        json.dumps(full.score(end-1), allow_nan=False)


def test_false_early_cross_does_not_freeze_later_accepted_episode():
    f=frame([100,97,94,96,98,100.4,97,96,98,99,98,99,103,104])
    snap=SetupSeries(f).bases.snapshot(len(f))
    r=next(x for x in snap['catalogue'] if x['start_index']==260)
    assert r['first_cross_index']==265
    assert r['breakout_start_index']==272
    assert snap['selected']['base_bars']==12


def test_wide_base_and_terminal_support_are_distinct_and_scale_invariant():
    f=frame([100,97,90,85,80]+list(np.linspace(81,98,30))+[97,98,97.5,98,98.5])
    first=SetupSeries(f).bases.snapshot(len(f))['selected']
    f[['Open','High','Low','Close']]*=13
    second=SetupSeries(f).bases.snapshot(len(f))['selected']
    assert first['base_depth']>.15 and first['pause_width']<.10
    assert first['floor']>first['base_low_price']
    assert second['base_id']==first['base_id']
    assert second['pivot']==pytest.approx(first['pivot']*13)
    assert second['pattern_strength']==pytest.approx(first['pattern_strength'])


def test_failed_base_event_does_not_expire_after_twenty_sessions():
    f=frame([100,97,94,96,98,96,97,99,98,99,103,104,90,89]+[89]*35)
    s=SetupSeries(f)
    events=s.bases.failures(len(f))
    assert len(events)==1 and events[0]['active']
    assert events[0]['failure_session']==s.date(273)


def test_new_base_repair_retires_old_failure_even_if_new_base_later_fails():
    tail=[100,97,94,96,98,96,97,99,98,99,103,104,90,89]
    tail+=list(np.linspace(90,99,20))+[97,96,98,97,98,97,90,89]
    s=SetupSeries(frame(tail))
    before=s.bases.failures(len(s.c)-2)
    after=s.bases.failures(len(s.c))
    old_before=next(x for x in before if x['breakout_session']==s.date(271))
    old_after=next(x for x in after if x['attempt_id']==old_before['attempt_id'])
    assert old_before['new_base_reset'] and not old_before['active']
    assert old_after==old_before


def test_v4_signal_bar_tests_preexisting_geometry_without_moving_it():
    f=frame([100,97,94,96,98,96,97,99,98,99,99])
    before=SetupSeries(f).score()
    assert before['geometry']['base_id']
    pivot=before['geometry']['pivot']
    f.loc[len(f)-1,['Open','High','Low','Close','Volume']]=[99,103.1,98.9,103,3e6]
    after=SetupSeries(f).score()
    assert after['geometry']==before['geometry']
    assert after['geometry']['pivot']==pivot
    assert after['status']=='breakout_confirmed'
    f.loc[len(f)-1,'Volume']=.2e6
    assert SetupSeries(f).score()['status']=='breakout_unconfirmed'


def test_recent_independent_shelf_cannot_hide_damage_behind_older_low():
    tail=[100,97,90,85,80]+list(np.linspace(81,98,30))+[97.5,98.5,97.8,98.6,98.2,96]
    s=SetupSeries(frame(tail))
    r=s.score()
    assert r['geometry']['pause_bars']==5
    assert r['geometry']['floor']>97
    assert r['geometry']['base_low_price']<81
    assert not r['actionable']
    assert r['status'] in {'invalidated','damaged'}
