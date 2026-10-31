"""User-facing invalidation evidence from the actual engine rules."""

from __future__ import annotations

import numpy as np


def base_validity(series, i, geometry, terminal_level):
    if not geometry.get("base_id"):
        return None
    outer = geometry["base_invalidation_level"]
    known = int(np.searchsorted(series.dates, np.datetime64(geometry["base_known_session"])))
    closes = series.c[max(known + 1, i - 2) : i + 1]
    return {
        "base_id": geometry["base_id"],
        "known_session": geometry["base_known_session"],
        "terminal_level": terminal_level,
        "terminal_breached": bool(series.c[i] < terminal_level),
        "outer_level": outer,
        "outer_breached": bool(np.sum(closes < outer) >= 2),
        "outer_rule": "two_of_three_closes_below_frozen_lower_zone",
    }


def base_invalidation_reason(report):
    evidence = report.get("invalidation")
    if not evidence:
        records = report.get("base_context", {}).get("catalogue", [])
        if any(r.get("invalidated_index") is not None for r in records):
            return "已有基底失效记录；当前没有选中有效基底，后续反弹不抹去失败，新候选须重新满足整理条件。"
        return None
    return (
        f"本次基底识别于 {evidence['known_session']}。失效条件：收盘低于 {evidence['terminal_level']:.2f}，末端平台失守"
        f"（{'已触发' if evidence['terminal_breached'] else '未触发'}）；"
        f"最近三日中两次收盘低于 {evidence['outer_level']:.2f}，整体基底失效"
        f"（{'已触发' if evidence['outer_breached'] else '未触发'}）。"
    )


def pullback_invalidation_reason(base, active):
    prefix = "当前基底" if active else "历史尝试"
    trigger = f"已于 {base['invalidated_session']} 失效" if base["invalidated_session"] else "尚未记录失效"
    return (
        f"{prefix}的失效条件：最近三日中两次收盘低于 {base['lower'] * (1 - base['zone']):.2f}，"
        f"或盘中低于确认时调整低点的容差线 {base['correction_low'] * (1 - base['zone']):.2f}；{trigger}。"
        "失败后仅接受其后新形成的基底，不重写原尝试编号。"
    )
