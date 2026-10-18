"""Sequential first repair-base observation, separate from the VCP score.

Rules are explicit engineering hypotheses, not fitted to META/MSFT or returns.
Every decision uses the current prefix. Failed first attempts retain their ID.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import numpy as np

from trenddeck.setups import MIN_BARS, SetupSeries

RULES = {
    "peak_lookback": 252,
    "min_drawdown": 0.15,
    "min_drawdown_atr": 4.0,
    "min_sessions_after_low": 10,
    "higher_low_atr": 0.5,
    "max_base_depth": 0.16,
    "max_base_age": 60,
    "breakout_observation_bars": 10,
}
STATUS_ORDER = {
    "ready": 0,
    "breakout": 1,
    "accepted": 2,
    "retesting": 3,
    "base_forming": 4,
    "recovering": 4,
    "declining": 5,
    "extended": 6,
    "invalidated": 7,
    "expired": 8,
    "completed": 9,
    "no_pullback": 10,
    "insufficient_history": 11,
}
STATUS_TEXT = {
    "ready": "修复基底已确认，价格接近其上沿；尚未突破。",
    "breakout": "收盘首次越过事先冻结的修复基底阻力带；不等于已成交或突破必然成功。",
    "accepted": "最近三日已有两次收盘站在冻结阻力带上方。",
    "retesting": "此前突破后价格回到阻力带内或下方，暂不视为仍站稳突破。",
    "base_forming": "已识别修复基底，当前尚未临近上沿或趋势条件暂未满足。",
    "recovering": "调整后已有反弹，但尚无满足止跌修复条件的基底。",
    "declining": "下跌或趋势损伤尚未修复，不把低位横盘直接视为止跌。",
    "extended": "修复结构仍在，但当前价格已经偏离突破位置或均线过远。",
    "invalidated": "已识别的基底后来失守；保留失败记录，不回溯改写第一基底。",
    "expired": "基底等待时间已超过观察期限。",
    "completed": "本次修复基底的突破观察期已结束，不沿用旧基底标记新买点。",
    "no_pullback": "可见观察期内尚未出现符合定义的深度调整。",
    "insufficient_history": "历史不足253根日线，暂不判断回调修复结构。",
}


def conditions_markdown():
    return (
        "### 回调修复观察条件\n\n"
        "这是独立的 pullback 分支，不产生百分制分数，阶段排序不代表收益排序。\n\n"
        f"- 深度调整：相对此前{RULES['peak_lookback']}日已知高点下跌至少"
        f"{RULES['min_drawdown']:.0%}，且超过高点前波幅的{RULES['min_drawdown_atr']:g}倍。跌幅本身不加分。\n"
        f"- 止跌观察：距本轮最低点至少{RULES['min_sessions_after_low']}个交易日；"
        "新基底在反弹后形成，底边高于调整低点至少半个事前平均真实波幅。\n"
        "- 局部修复：站上MA20，MA20较五日前上升；MA50位置和方向仅作背景，不作为准入门槛。"
        "必须站上MA200，不因MA200仍在上升而放行其下方的形态。\n"
        f"- 基底：复用确认锚点、回转及横向整理识别，宽度不超过{RULES['max_base_depth']:.0%}；"
        "形成时冻结上下沿，下一交易日起才允许突破。\n"
        "- 第一基底：按当时满足条件的先后固定身份；失败、后续尝试及新低均保留，不能事后挑选成功者。\n"
        "- 突破：收盘越过冻结阻力容差带；三日两次收盘站上标为接受突破。量能单独解释，不作硬门槛。\n"
        "- 风险：两次收盘跌破支撑容差带，或跌破本次确认时的调整低点，结构失效；"
        "超出突破位5%、MA20延伸限制或自身波幅限制，标为延伸。\n"
        "- 接口只读OHLCV，不按市值分组。规则是待验证的工程假设；止跌证据不保证后续不会创新低。"
    )


def fingerprints(model):
    config = hashlib.sha256((model.config_hash + json.dumps(RULES, sort_keys=True)).encode()).hexdigest()[:16]
    engine = hashlib.sha256(model.engine_hash.encode() + Path(__file__).read_bytes()).hexdigest()[:16]
    return {"config_hash": config, "engine_hash": engine}


def compact_context(report):
    base = report["base"]
    keys = [
        "base_id",
        "ordinal",
        "start_session",
        "range_known_session",
        "known_session",
        "upper",
        "lower",
        "breakout_session",
        "accepted_session",
        "invalidated_session",
    ]
    return {
        **{k: report[k] for k in ["drawdown", "max_drawdown", "support_distance"]},
        "history_left_censored": report["cycle"]["history_left_censored"] if report["cycle"] else False,
        "base_role": "active"
        if report["cycle"] and report["cycle"]["base"] and report["status"] != "completed"
        else "historical_attempt"
        if base
        else None,
        "base": {k: base[k] for k in keys} if base else None,
    }


class PullbackSeries:
    def __init__(self, frame, benchmark=None, model=None):
        self.s = SetupSeries(frame, benchmark, model)
        self.timeline = []

    def _trend(self, i):
        s = self.s
        checks = {
            "above_ma20": bool(s.c[i] > s.ma[20][i]),
            "ma20_rising": bool(s.ma[20][i] > s.ma[20][i - 5]),
            "above_ma200": bool(s.c[i] > s.ma[200][i]),
        }
        return checks

    def _build(self):
        s = self.s
        # Catalogue dates are only exposed when known. Future lifecycle fields
        # are deliberately never consulted by this separate state machine.
        s.bases._catalogue()
        by_day = {}
        for r in s.bases.records:
            by_day.setdefault(r["known_index"], []).append(r)
        active, last_cycle = None, None
        known_candidates = []
        for i in range(len(s.c)):
            if i < MIN_BARS - 1:
                self.timeline.append(self._report(i, None, "insufficient_history", {}))
                continue
            trend = self._trend(i)
            known_candidates.extend(by_day.get(i, []))
            known_candidates = [r for r in known_candidates if i - r["start_index"] <= RULES["max_base_age"]]
            if active is None:
                start = max(0, i - int(RULES["peak_lookback"]))
                peak_i = start + int(np.argmax(s.h[start:i]))
                peak = float(s.h[peak_i])
                atr = max(float(np.mean(s.trp[max(0, peak_i - 20) : peak_i + 1])), 0.001)
                depth = 1 - s.low[i] / peak
                if depth >= max(RULES["min_drawdown"], RULES["min_drawdown_atr"] * atr):
                    low_i = peak_i + int(np.argmin(s.low[peak_i : i + 1]))
                    active = {
                        "cycle_id": f"pullback:{s.date(peak_i)}:{s.date(i)}",
                        "peak_session": s.date(peak_i),
                        "peak": peak,
                        "known_session": s.date(i),
                        "history_left_censored": bool(i == MIN_BARS - 1 or peak_i == start),
                        "low_session": s.date(low_i),
                        "low": float(s.low[low_i]),
                        "low_index": low_i,
                        "attempts": [],
                        "rejected": [],
                        "base": None,
                        "completed_session": None,
                    }
            if active is None:
                self.timeline.append(
                    self._report(i, last_cycle, "completed" if last_cycle else "no_pullback", trend)
                )
                continue
            if s.low[i] < active["low"]:
                active.update(low=float(s.low[i]), low_session=s.date(i), low_index=i)
            b = active["base"]
            status = "recovering" if s.c[i] > s.ma[20][i] else "declining"
            if b:
                b["trend_checks"] = trend
                if not b["invalidated_session"]:
                    broken = s.c[max(b["known_index"] + 1, i - 2) : i + 1] < b["lower"] * (1 - b["zone"])
                    if np.sum(broken) >= 2 or s.low[i] < b["correction_low"] * (1 - b["zone"]):
                        b["invalidated_session"] = s.date(i)
                if b["invalidated_session"]:
                    status = "invalidated"
                elif i - b["known_index"] > RULES["max_base_age"] and b["breakout_index"] is None:
                    status = "expired"
                else:
                    status = "base_forming"
                    crossed = i > b["known_index"] and s.c[i] > b["upper"] * (1 + b["zone"])
                    if b["breakout_index"] is None and crossed:
                        # Any first crossing is retained, including a rejected one.
                        b["first_cross_session"] = s.date(i)
                        if all(trend.values()):
                            b["breakout_index"] = i
                            b["breakout_session"] = s.date(i)
                        else:
                            b["invalidated_session"] = s.date(i)
                            b["failure_reason"] = "cross_without_trend_repair"
                            status = "invalidated"
                    if b["breakout_index"] is not None:
                        status = "breakout"
                        accepted = (
                            np.sum(
                                s.c[max(b["known_index"] + 1, i - 2) : i + 1] > b["upper"] * (1 + b["zone"])
                            )
                            >= 2
                        )
                        if accepted and b["accepted_session"] is None:
                            b["accepted_session"] = s.date(i)
                        if b["accepted_session"]:
                            status = "accepted"
                        if s.c[i] <= b["upper"] * (1 + b["zone"]):
                            status = "retesting"
                        if i - b["breakout_index"] > RULES["breakout_observation_bars"]:
                            status = "completed"
                    elif (
                        status != "invalidated"
                        and all(trend.values())
                        and -0.03 <= s.c[i] / b["upper"] - 1 <= b["zone"]
                    ):
                        status = "ready"
                    if status not in {"invalidated", "completed"} and self._extended(i, b):
                        status = "extended"
                if status in {"invalidated", "expired"}:
                    b["terminal_status"] = status
                    b["terminal_index"] = i
                    active["base"] = None
            # A formed range can await trend repair, but must still be intact
            # and never have broken out. Qualification gets its own date.
            if active["base"] is None:
                for r in sorted(
                    known_candidates, key=lambda x: (x["known_index"], x["start_index"], x["scale"])
                ):
                    if r["start_index"] <= active["low_index"]:
                        continue
                    if active["attempts"] and r["start_index"] <= active["attempts"][-1].get(
                        "terminal_index", -1
                    ):
                        continue
                    since_known = s.c[r["known_index"] + 1 : i + 1]
                    if np.any(since_known > r["upper"] * (1 + r["zone"])):
                        continue
                    below = since_known < r["lower"] * (1 - r["zone"])
                    if (
                        len(below) >= 2
                        and np.max(np.convolve(below.astype(int), np.ones(3), mode="full")) >= 2
                    ):
                        continue
                    reasons = []
                    if i - active["low_index"] < RULES["min_sessions_after_low"]:
                        reasons.append("low_not_established")
                    if r["lower"] <= active["low"] * (1 + RULES["higher_low_atr"] * r["atr"]):
                        reasons.append("no_higher_support")
                    if r["recognition_width"] > RULES["max_base_depth"]:
                        reasons.append("base_too_wide")
                    reasons.extend(k for k, v in trend.items() if not v)
                    if reasons:
                        if not any(x["source_base_id"] == r["base_id"] for x in active["rejected"]):
                            active["rejected"].append(
                                {
                                    "source_base_id": r["base_id"],
                                    "start_session": s.date(r["start_index"]),
                                    "known_session": s.date(i),
                                    "upper": r["upper"],
                                    "lower": r["lower"],
                                    "reasons": reasons,
                                }
                            )
                        continue
                    b = {
                        "base_id": f"{active['cycle_id']}:{r['base_id']}",
                        "source_base_id": r["base_id"],
                        "ordinal": len(active["attempts"]) + 1,
                        "start_session": s.date(r["start_index"]),
                        "known_session": s.date(i),
                        "range_known_session": s.date(r["known_index"]),
                        "known_index": i,
                        "upper": r["upper"],
                        "lower": r["lower"],
                        "zone": r["zone"],
                        "correction_low": active["low"],
                        "correction_low_session": active["low_session"],
                        "trend_checks": trend,
                        "breakout_session": None,
                        "breakout_index": None,
                        "first_cross_session": None,
                        "accepted_session": None,
                        "invalidated_session": None,
                    }
                    active["base"] = b
                    active["attempts"].append(b)
                    status = "ready" if -0.03 <= s.c[i] / b["upper"] - 1 <= b["zone"] else "base_forming"
                    if self._extended(i, b):
                        status = "extended"
                    break
            self.timeline.append(self._report(i, active, status, trend))
            # An actual recovery of the frozen old high ends this correction.
            if s.c[i] >= active["peak"]:
                active["completed_session"] = s.date(i)
                last_cycle = deepcopy(active)
                active = None

    def _extended(self, i, b):
        s = self.s
        atr = max(float(np.mean(s.trp[i - 20 : i])), 0.001)
        return bool(
            s.c[i] / b["upper"] - 1 > s.p["max_extension"]
            or s.c[i] / s.ma[20][i] - 1 > min(s.p["max_ma20_extension"], s.p["risk_extension_atr"] * atr)
        )

    def _report(self, i, cycle, status, trend):
        s = self.s
        base = cycle["base"] if cycle else None
        if base is None and cycle and cycle["attempts"]:
            base = cycle["attempts"][-1]
        b = deepcopy(base)
        close = float(s.c[i])
        risk = 1 - b["lower"] * (1 - b["zone"]) / close if b else None
        quality = (
            s.price_mode == "adjusted"
            and float(np.mean(s.c[max(0, i - 49) : i + 1] * s.v[max(0, i - 49) : i + 1]))
            >= s.p["min_dollar_volume"]
        )
        reasons = [STATUS_TEXT[status]]
        if trend and not trend["above_ma200"]:
            reasons.append("当前收盘未站上MA200，不满足回调修复的硬门槛。")
        if b:
            reasons.append(
                f"本轮第{b['ordinal']}个修复基底；识别日{b['known_session']}，阻力{b['upper']:.2f}，支撑{b['lower']:.2f}。"
            )
        if cycle and cycle["history_left_censored"]:
            reasons.append("观察起点未覆盖完整调整历史，编号仅代表可见尝试，不能确认是实际第一基底。")
        if not quality:
            reasons.append("复权或流动性条件不足，不能标记为可操作。")
        trend_context = {
            "above_ma50": bool(s.c[i] > s.ma[50][i]),
            "ma50_not_falling": bool(i >= 59 and s.ma[50][i] >= s.ma[50][i - 10]),
        }
        if b:
            reasons.append(
                "MA50较十日前走平或回升，提供有利的中期趋势背景；不作为准入门槛。"
                if trend_context["ma50_not_falling"]
                else "MA50较十日前仍在下降，中期背景尚弱；不单独否决局部修复基底。"
            )
        if b and risk > s.p["max_risk"]:
            reasons.append("到基底失效参考的距离超过风险门槛。")
        return {
            "setup_type": "pullback",
            "as_of_session": s.date(i),
            "score": None,
            "status": status,
            "reasons": reasons,
            "actionable": bool(
                quality
                and all(trend.values())
                and status in {"ready", "breakout", "accepted"}
                and risk is not None
                and 0 < risk <= s.p["max_risk"]
            ),
            "close": close,
            "drawdown": 1 - close / cycle["peak"] if cycle else None,
            "max_drawdown": 1 - cycle["low"] / cycle["peak"] if cycle else None,
            "support_distance": risk,
            "base": b,
            "trend_checks": dict(trend),
            "trend_context": trend_context,
            "cycle": deepcopy(cycle),
        }

    def report(self, index=None):
        if not self.timeline:
            self._build()
        return deepcopy(self.timeline[-1 if index is None else index])
