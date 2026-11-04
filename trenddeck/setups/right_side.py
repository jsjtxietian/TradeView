"""Observed recovery -> pause -> boundary tests, without pattern-name bonuses.

Candidate bounds freeze at recognition;
subsequent undercuts, reclaims and failures never redraw those bounds. All inputs
are sliced at ``end`` and every dated event records when it became observable.
"""

from __future__ import annotations

import numpy as np

RULES = {
    "anchor_left": 3,
    "anchor_right": 3,
    "minimum_pause_bars": 5,
    "maximum_pause_bars": 25,
    "maximum_pause_width": 0.10,
    "maximum_efficiency": 0.65,
    "minimum_recovery_atr": 2.0,
    "reclaim_settle_bars": 3,
    "nested_width_ratio": 0.75,
}


def pause_metrics(s, pause, end):
    """Measure the actual consolidation; exclude its departure, never move bounds.

    Full-formation measurements feed the existing score components. The last
    third versus the earlier part describes deterioration without a second bonus.
    """
    start = pause["start_index"]
    stop = end
    # An ongoing breakout retains its pre-departure measurement. Once it comes
    # back into the range, include that deterioration rather than freezing a
    # flattering pre-breakout picture. BaseSeries freezes outer-base geometry
    # separately; this pause timeline must not rewind when that happens.
    events = ("breakout_session", "invalidated_session") if pause.get("state") == "broken_out" else ("invalidated_session",)
    for key in events:
        if pause.get(key):
            stop = min(stop, int(np.searchsorted(s.dates, np.datetime64(pause[key]))))
    stop = max(pause["known_index"] + 1, stop)
    prices = slice(start, stop)
    volume_reference = s.v[max(0, start - 50):start]
    tr_reference = s.trp[max(0, start - 30):start]
    valid_volume = bool(len(volume_reference) and np.all(volume_reference > 0)
                        and np.all(s.v[prices] > 0))
    volume_ratio = (float(s.v[prices].mean() / np.median(volume_reference))
                    if valid_volume else None)
    n = stop - start
    split = stop - max(2, n // 3)
    early, recent = slice(start, split), slice(split, stop)
    tr_change = float(s.trp[recent].mean() / max(s.trp[early].mean(), .001))
    volume_change = (float(s.v[recent].mean() / s.v[early].mean()) if valid_volume else None)
    returns = s.c[split:stop] / s.c[split-1:stop-1] - 1
    clv = (s.c[recent] - s.low[recent]) / np.maximum(s.h[recent] - s.low[recent], 1e-9)
    distribution = int(np.sum((returns < 0) & (clv < .4)
                             & (s.v[recent] > s.v[early].mean())))
    return {
        "start_session": s.date(start), "end_session": s.date(stop - 1), "bars": n,
        "width": float(1 - s.low[prices].min() / s.h[prices].max()),
        "volume_ratio": volume_ratio,
        "true_range_ratio": float(s.trp[prices].mean() / max(tr_reference.mean(), .001)),
        "recent_bars": stop - split, "recent_to_earlier_range": tr_change,
        "recent_to_earlier_volume": volume_change,
        "price_change": "tightening" if tr_change < .8 else "loosening" if tr_change > 1.2 else "stable",
        "volume_change": "unknown" if volume_change is None else "drying" if volume_change < .8
                         else "increasing" if volume_change > 1.2 else "stable",
        "recent_distribution_bars": distribution,
    }


def _select_attempts(s, candidates, end):
    """Replay decisions at recognition time, not with the final outcome known."""
    accepted = []
    for candidate in sorted(candidates, key=lambda c: (c["known_index"], c["start_index"])):
        relation, parent_id = "first_pause", None
        if accepted:
            previous = _observe(s, accepted[-1], candidate["known_index"] + 1)
            if candidate["start_index"] <= previous["known_index"]:
                continue
            failed = previous["invalidated_session"]
            breakout = previous["breakout_session"]
            if failed:
                if candidate["start_session"] <= failed:
                    continue
                relation = "new_after_failure"
            elif breakout:
                if candidate["start_session"] <= breakout or candidate["lower"] < previous["lower"]:
                    continue
                relation = "post_breakout_pause"
            elif candidate["start_index"] > previous["known_index"] + RULES["maximum_pause_bars"]:
                relation = "new_after_expiry"
            elif (candidate["lower"] >= previous["lower"]
                  and candidate["upper"] <= previous["upper"] * (1 + previous["zone"])
                  and candidate["width"] <= previous["width"] * RULES["nested_width_ratio"]):
                relation, parent_id = "nested_tightening", previous["pause_id"]
            else:
                continue
        candidate = {**candidate, "relationship": relation, "parent_pause_id": parent_id}
        candidate["episode_id"] = accepted[-1]["episode_id"] if parent_id else candidate["pause_id"]
        accepted.append(candidate)
    return [_observe(s, candidate, end) for candidate in accepted]


def _observe(s, pause, end):
    """Advance one frozen pause; an invalidated attempt stays invalidated."""
    k = pause["known_index"]
    floor, roof, zone = pause["lower"], pause["upper"], pause["zone"]
    events, pending = [], None
    breakout, invalidated = None, None
    state = "intact"
    for i in range(k + 1, end):
        below = s.c[max(k + 1, i - 2) : i + 1] < floor * (1 - zone)
        recovery_lost = (pause["role"] != "standalone_base"
                         and s.low[i] < pause["recovery_low"] * (1 - zone))
        if np.sum(below) >= 2 or recovery_lost:
            invalidated = s.date(i)
            state = "invalidated"
            break
        meaningful_undercut = s.low[i] < floor * (1 - 0.25 * pause["prior_atr"])
        if pending is None and (meaningful_undercut or s.c[i] < floor):
            pending = {
                "support": floor,
                "breach_session": s.date(i),
                "lowest_price": float(s.low[i]),
                "reclaim_session": None,
                "settled_session": None,
                "state": "under_test",
            }
            events.append(pending)
        if pending is not None:
            pending["lowest_price"] = min(pending["lowest_price"], float(s.low[i]))
            if s.c[i] >= floor and pending["reclaim_session"] is None:
                pending["reclaim_session"] = s.date(i)
                pending["state"] = "reclaimed"
            elif s.c[i] < floor:
                # A new loss of support requires a new reclaim, not the old date.
                pending["reclaim_session"] = None
                pending["state"] = "under_test"
            if pending["reclaim_session"]:
                r = int(np.searchsorted(s.dates, np.datetime64(pending["reclaim_session"])))
                n = RULES["reclaim_settle_bars"]
                if i - r + 1 >= n:
                    tail = slice(i - n + 1, i + 1)
                    width = 1 - s.low[tail].min() / s.h[tail].max()
                    if (
                        np.all(s.c[tail] >= floor)
                        and s.low[tail].min() >= pending["lowest_price"]
                        and width <= pause["width"]
                    ):
                        pending.update(state="settled", settled_session=s.date(i))
                        pending = None
        state = pending["state"] if pending else "settled" if events else "intact"
        if s.c[i] > roof * (1 + zone):
            if breakout is None:
                breakout = s.date(i)
            state = "broken_out"
        elif breakout:
            state = "retesting" if s.c[i] >= floor else "under_test"
    if invalidated and pending:
        pending["state"] = "invalidated"
    observed = {
        **pause,
        "state": state,
        "breakout_session": breakout,
        "invalidated_session": invalidated,
        "support_tests": events,
        "bars_since_recognition": end - k - 1,
        "historical_only": bool(end - k - 1 > RULES["maximum_pause_bars"]),
    }
    observed["metrics"] = pause_metrics(s, observed, end)
    return observed


def right_side_context(s, parent, end):
    """Find independently observed pauses, never simply take the last N bars."""
    end = int(min(end, len(s.c)))
    result = {
        "available": False,
        "method": "observed-pause",
        "diagnostic_only": True,
        "score_effect": "none",
        "as_of_session": s.date(end - 1),
        "parent": None,
        "pause": None,
        "attempts": [],
    }
    if not parent or parent["known_index"] >= end:
        return result
    a = parent["start_index"]
    result["parent"] = {
        **{k: parent[k] for k in ["base_id", "upper", "lower", "role"]},
        "start_session": s.date(a),
        "known_session": s.date(parent["known_index"]),
    }
    left, right = RULES["anchor_left"], RULES["anchor_right"]
    candidates = []
    # An already recognized shallow base is itself a consolidation. It need not
    # contain a second cup/handle to earn terminal-quality points.
    k = parent["known_index"]
    depth = 1 - parent["lower"] / parent["upper"]
    if parent.get("allow_base_pause") and 0 < depth <= RULES["maximum_pause_width"]:
        atr = max(float(s.trp[max(0, a - 20):a].mean()), .001)
        reference = s.v[max(0, a - 50):a]
        ratio = (float(s.v[a:k+1].mean() / np.median(reference))
                 if len(reference) and np.all(reference > 0) and np.all(s.v[a:k+1] > 0) else None)
        candidates.append({
            "pause_id": f"pause:{s.date(a)}:{s.date(k)}", "start_index": a, "known_index": k,
            "start_session": s.date(a), "known_session": s.date(k), "recognition_bars": k-a+1,
            "upper": parent["upper"], "lower": parent["lower"], "width": depth,
            "prior_atr": atr, "zone": float(np.clip(.75*atr, .005, .03)),
            "recovery_low": parent["lower"], "recovery_low_session": s.date(a + int(np.argmin(s.low[a:k+1]))),
            "recovery_high_session": s.date(a), "recovery_fraction": 1.,
            "lower_position_in_parent": 0., "relative_depth": 1.,
            "volume_ratio_at_recognition": ratio, "role": "standalone_base",
        })
    for p in range(a + left, end - right):
        atr = max(float(np.mean(s.trp[max(0, p - 20) : p])), 0.001)
        tolerance = float(np.clip(.75 * atr, .005, .03))
        if (s.h[p] <= s.h[p - left : p].max()
                or s.h[p + 1 : p + right + 1].max() > s.h[p] * (1 + tolerance)):
            continue
        b = a + int(np.argmin(s.low[a : p + 1]))
        atr = max(float(np.mean(s.trp[max(0, p - 20) : p])), 0.001)
        if p - b < 2 or s.h[p] / s.low[b] - 1 < RULES["minimum_recovery_atr"] * atr:
            continue
        # Parent boundaries are interpretation context, not a moving stop.
        span = parent["upper"] - s.low[b]
        if span <= 0 or s.h[p] > parent["upper"] * (1 + 2 * atr):
            continue
        for t in range(p + max(right, RULES["minimum_pause_bars"] - 1),
                       min(end, p + RULES["maximum_pause_bars"])):
            roof, floor = float(s.h[p : t + 1].max()), float(s.low[p : t + 1].min())
            if s.c[t] > s.h[p] * (1 + atr) or floor <= s.low[b]:
                break
            prices = s.c[p : t + 1]
            width = 1 - floor / roof
            travel = float(np.abs(np.diff(prices)).sum())
            efficiency = abs(prices[-1] - prices[0]) / max(travel, 1e-9)
            if width > RULES["maximum_pause_width"] or efficiency > RULES["maximum_efficiency"]:
                continue
            # Require a rebound within the pause; a quiet descending staircase
            # must not qualify just because its total width is small.
            if prices[-1] < floor + 0.4 * (roof - floor):
                continue
            known = max(t, parent["known_index"])
            if known >= end:
                continue
            # Do not freeze a child retroactively before its parent was available.
            if known != t:
                continue
            reference = s.v[max(0, p - 50) : p]
            volume = float(np.mean(s.v[p : t + 1]))
            volume_ratio = (
                volume / float(np.median(reference))
                if len(reference) and np.all(reference > 0) and np.all(s.v[p : t + 1] > 0)
                else None
            )
            depth = 1 - s.low[b] / parent["upper"]
            position = float((floor - s.low[b]) / span)
            candidate = {
                "pause_id": f"pause:{s.date(p)}:{s.date(known)}",
                "start_index": p,
                "known_index": known,
                "start_session": s.date(p),
                "known_session": s.date(known),
                "recognition_bars": t - p + 1,
                "upper": roof,
                "lower": floor,
                "width": float(width),
                "prior_atr": atr,
                "zone": float(np.clip(0.75 * atr, 0.005, 0.03)),
                "recovery_low": float(s.low[b]),
                "recovery_low_session": s.date(b),
                "recovery_high_session": s.date(p),
                "recovery_fraction": float((s.h[p] - s.low[b]) / span),
                "lower_position_in_parent": position,
                "relative_depth": float(width / depth),
                "volume_ratio_at_recognition": volume_ratio,
                "role": "upper_base_pause" if position >= 0.5 else "lower_repair_pause",
            }
            candidates.append(candidate)
            break
    attempts = _select_attempts(s, candidates, end)
    if attempts:
        result.update(available=True, pause=attempts[-1], attempts=attempts)
    return result


def right_side_reason(context):
    if not context or not context.get("parent"):
        return None
    p = context.get("pause")
    if not p:
        return "右侧结构诊断：尚未确认独立的“恢复后再停顿”，近几日窄幅不单独证明柄部已形成。"
    position = ("独立基底" if p["role"] == "standalone_base" else
                "大基底上半部" if p["role"] == "upper_base_pause" else "调整区间较低位置")
    state = {
        "intact": "平台仍完整",
        "under_test": "支撑正在被测试，尚未完成收复",
        "reclaimed": "已收复平台支撑，但还未观察到后续稳定",
        "settled": "下探后收复，并已观察到连续站稳和重新收紧",
        "broken_out": "已越过事先确认的小平台上沿",
        "retesting": "突破小平台后回测，尚不能视为继续站稳其上沿",
        "invalidated": "该小平台已失效，后来的反弹不撤销失败记录",
    }[p["state"]]
    if p.get("historical_only"):
        state = "这是较早的整理记录，不能直接代表当前已形成新的买点；" + state
    volume = p["volume_ratio_at_recognition"]
    volume_text = f"形成时均量为整理前基准的 {volume:.2f} 倍" if volume is not None else "量能基准不足"
    m = p.get("metrics", {})
    changes = {"tightening": "继续收紧", "loosening": "开始变松", "stable": "大致稳定"}
    current = (f"实际整理 {m['bars']} 日、当前测量宽 {m['width']:.1%}，"
               f"末段波幅{changes[m['price_change']]}" if m else "")
    if m and m.get("volume_ratio") is not None:
        direction = {"drying": "减少", "increasing": "增加", "stable": "大致稳定"}
        current += f"、量能{direction[m['volume_change']]}（全段量比 {m['volume_ratio']:.2f}）"
    if m.get("recent_distribution_bars"):
        current += f"，末段有 {m['recent_distribution_bars']} 根放量弱收盘"
    effect = ("实际整理已用于原有收紧与量能评分，不另加形态分。"
              if context.get("score_effect") == "existing_tightness_and_volume" else "不替代主基底状态。")
    return (
        f"右侧结构诊断：{position}形成 {p['recognition_bars']} 日小整理，"
        f"识别时宽 {p['width']:.1%}、约为大整理深度的 {p['relative_depth']:.0%}，"
        f"{volume_text}；{current}；{state}。{effect}"
    )
