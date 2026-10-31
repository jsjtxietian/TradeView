"""Causal base catalogue. A range is recognized before it can break out.

Anchor highs require right-side confirmation. Recognition freezes the outer
support/resistance; subsequent bars cannot move a broken floor downwards.
Local pauses are measured separately from the full base. No outcome fitting.
"""

from __future__ import annotations

import numpy as np

from trenddeck.setups.vcp import vcp_evidence


def clip(x, lo, hi):
    return float(np.clip((x - lo) / (hi - lo), 0, 1))


class BaseSeries:
    def __init__(self, series):
        self.s = series
        self.records = None
        self.cache = {}

    def _catalogue(self):
        s = self.s
        h, low, c, p = s.h, s.low, s.c, s.p
        records = []
        left, right = int(p["base_left_bars"]), int(p["base_confirm_bars"])
        for a in range(left, len(c) - right):
            # Earliest equal high owns a plateau. Availability is a+right.
            if h[a] <= h[a - left : a].max() or h[a] < h[a + 1 : a + right + 1].max():
                continue
            atr = max(float(np.mean(s.trp[max(0, a - 20) : a + 1])), 0.001)
            zone = float(np.clip(atr * p["base_zone_atr"], 0.005, 0.03))
            upper = float(h[a])
            advance = s.prior_advance(a)
            for scale in ["local", "structural"]:
                known = None
                rec = None
                restart_after = None
                for t in range(a + right, min(len(c), a + int(p["base_max_bars"]))):
                    n = t - a + 1
                    prices = c[a : t + 1]
                    accepted = t >= a + 2 and np.sum(c[t - 2 : t + 1] > upper * (1 + zone)) >= 2
                    if rec is None:
                        # Leaving before a range forms is an impulse, not a base.
                        if accepted:
                            break
                        if scale == "local" and n > 25:
                            break
                        if restart_after is not None and t < restart_after + 15:
                            continue
                        if n < (p["base_min_bars"] if scale == "local" else 25):
                            continue
                        bottom = float(low[a : t + 1].min())
                        depth = 1 - bottom / upper
                        if depth <= 0 or depth > p["base_max_depth"]:
                            continue
                        efficiency = abs(prices[-1] - prices[0]) / max(
                            float(np.abs(np.diff(prices)).sum()), 1e-9
                        )
                        recovered = (c[t] - bottom) / max(upper - bottom, 1e-9)
                        bottom_i = a + int(np.argmin(low[a : t + 1]))
                        rotated = bottom_i < t and recovered >= 0.4 and depth >= 0.5 * atr
                        short = (
                            scale == "local"
                            and n <= 25
                            and depth <= 0.16
                            and advance["prior_advance_quality"] >= 0.5
                        )
                        established = scale == "structural" and n >= 25
                        if not (
                            rotated
                            and (short or established)
                            and efficiency <= 0.65
                            and c[t] <= upper * (1 + zone)
                        ):
                            continue
                        known = t
                        rec = {
                            "base_id": f"base:{s.date(a)}:{scale}:{s.date(known)}",
                            "start_index": a,
                            "known_index": known,
                            "reformed_after_index": restart_after,
                            "scale": scale,
                            "upper": upper,
                            "lower": bottom,
                            "zone": zone,
                            "atr": atr,
                            "recognition_bars": n,
                            "recognition_width": depth,
                            "breakout_index": None,
                            "breakout_start_index": None,
                            "first_cross_index": None,
                            "invalidated_index": None,
                            **advance,
                        }
                    else:
                        if rec["first_cross_index"] is None and c[t] > upper:
                            rec["first_cross_index"] = t
                        if rec["breakout_index"] is None and accepted:
                            rec["breakout_index"] = t
                            start = t - 2 + int(np.flatnonzero(c[t - 2 : t + 1] > upper * (1 + zone))[0])
                            while start > known + 1 and c[start - 1] > upper:
                                start -= 1
                            rec["breakout_start_index"] = start
                        # Two closes, rather than a wick, constitute outer-base damage.
                        if np.sum(c[max(known + 1, t - 2) : t + 1] < rec["lower"] * (1 - zone)) >= 2:
                            rec["invalidated_index"] = t
                            if scale == "local":
                                break
                            records.append(rec)
                            rec = None
                            restart_after = t
                if rec:
                    records.append(rec)
        self.records = records

    def snapshot(self, end):
        if end in self.cache:
            return self.cache[end]
        if self.records is None:
            self._catalogue()
        s = self.s
        records, candidates = [], []
        for original in self.records:
            if original["known_index"] >= end:
                continue
            r = dict(original)
            for field in ["breakout_index", "first_cross_index", "invalidated_index"]:
                if r[field] is not None and r[field] >= end:
                    r[field] = None
            if r["breakout_index"] is None:
                r["breakout_start_index"] = None
            stop = min(end, r["breakout_index"] + 1 if r["breakout_index"] is not None else end)
            if r["invalidated_index"] is not None:
                stop = min(stop, r["invalidated_index"] + 1)
            r["base_bars"] = stop - r["start_index"]
            r["width"] = r["recognition_width"]
            r["expired"] = end - r["start_index"] > s.p["base_max_bars"]
            r["lifecycle"] = (
                "invalidated"
                if r["invalidated_index"] is not None
                else "expired"
                if r["expired"]
                else "broken_out"
                if r["breakout_index"] is not None
                else "forming"
            )
            r["parent_id"] = None
            # Older overlapping ranges remain parents, not extra sequential bases.
            parents = [
                x
                for x in records
                if x["start_index"] < r["start_index"]
                and (x["breakout_index"] is None or x["breakout_index"] >= r["start_index"])
                and x["known_index"] <= r["known_index"]
                and (x["invalidated_index"] is None or x["invalidated_index"] > r["known_index"])
                and r["known_index"] - x["start_index"] <= s.p["base_max_bars"]
                and r["upper"] <= x["upper"] * (1 + x["zone"])
                and r["lower"] >= x["lower"]
            ]
            if parents:
                r["parent_id"] = max(parents, key=lambda x: x["start_index"])["base_id"]
            records.append(r)
            if r["lifecycle"] in {"invalidated", "expired"}:
                continue
            if r["breakout_index"] is not None and end - 1 - r["breakout_index"] > 5:
                continue
            candidates.append(self._geometry(r, end))
        # Choose structural evidence first, then proximity. Do not choose based on
        # future performance or turn a tiny interior pause into the outer base.
        selected = max(candidates, key=lambda g: g["merit"]) if candidates else {}
        result = {
            "selected": selected,
            "catalogue": records,
            "active_count": len(candidates),
            "completed_count": sum(r["breakout_index"] is not None for r in records),
        }
        self.cache[end] = result
        return result

    def _geometry(self, r, end):
        s = self.s
        # Freeze pre-departure geometry for already accepted breakouts.
        departure = r["breakout_start_index"]
        e = min(end, departure) if r["breakout_index"] is not None and departure is not None else end
        e = max(e, r["known_index"] + 1)
        a = r["start_index"]
        observed_depth = float(1 - s.low[a:e].min() / s.h[a:e].max())
        ps = max(a, e - 10)
        # A separately formed last-five-session shelf owns its tighter support.
        # Require observed back-and-forth travel, so a five-day vertical rally
        # cannot manufacture a new pause merely by shortening the window.
        if e - a >= 5:
            recent = s.c[e - 5 : e]
            travel = float(np.abs(np.diff(recent)).sum())
            efficiency = abs(recent[-1] - recent[0]) / max(travel, 1e-9)
            recent_width = 1 - float(s.low[e - 5 : e].min() / s.h[e - 5 : e].max())
            if efficiency <= 0.65 and recent_width <= s.p["max_pause_width"]:
                ps = e - 5
        # Terminal pause owns its own support and cannot borrow the outer low.
        terminal_h = float(s.h[ps:e].max())
        floor = float(s.low[ps:e].min())
        width = 1 - floor / terminal_h
        dry = (
            float(np.mean(s.v[max(ps, e - 5) : e]) / max(np.median(s.v[max(0, ps - 50) : ps]), 1))
            if ps
            else 1.0
        )
        atr_ratio = (
            float(np.mean(s.trp[max(ps, e - 5) : e]) / max(np.mean(s.trp[max(0, ps - 30) : ps]), 0.001))
            if ps
            else 1.0
        )
        tight = 0.5 * (1 - clip(width, 0.03, 0.12)) + 0.5 * (1 - clip(atr_ratio, 0.5, 1.2))
        volume = 1 - clip(dry, 0.4, 1.2)
        known = [x for x in s.base_swings if x["confirmed_index"] < e and x["index"] >= a]
        legs = []
        for hi, lo in zip(known, known[1:]):
            if hi["kind"] == "high" and lo["kind"] == "low":
                legs.append(
                    {
                        "peak_date": s.date(hi["index"]),
                        "low_date": s.date(lo["index"]),
                        "confirmed_at": s.date(lo["confirmed_index"]),
                        "low_price": float(s.low[lo["index"]]),
                        "median_volume": float(np.median(s.v[hi["index"] : lo["index"] + 1])),
                        "depth": float(1 - s.low[lo["index"]] / s.h[hi["index"]]),
                    }
                )
        depths = [x["depth"] for x in legs]
        contraction = float(np.mean(np.diff(depths) < 0)) if len(depths) >= 2 else 0.0
        terminal_intact = bool(len(depths) >= 2 and depths[-1] < depths[-2] and depths[-1] <= 0.10)
        if legs:
            last_low_date = legs[-1]["low_date"]
            last_low_index = next(
                x["index"]
                for x in reversed(known)
                if x["kind"] == "low" and s.date(x["index"]) == last_low_date
            )
            terminal_intact = terminal_intact and bool(
                s.low[last_low_index:e].min() >= s.low[last_low_index] * 0.99
            )
        recovered = float(np.clip((s.c[e - 1] - r["lower"]) / (r["upper"] - r["lower"]), 0, 1))
        strength = 0.35 + 0.25 * recovered + 0.2 * min(1.0, (e - a) / 25) + 0.2 * contraction
        strength *= 0.5 + 0.5 * r["prior_advance_quality"]
        vcp = vcp_evidence(legs, terminal_intact, width, dry)
        pattern = (
            "vcp"
            if vcp["qualified"]
            else "flat_base"
            if e - a >= 25 and observed_depth <= 0.15
            else "short_base"
            if e - a < 25
            else "range_base"
        )
        proximity = 1 - clip(abs(s.c[end - 1] / r["upper"] - 1), 0.02, 0.20)
        return {
            **{
                k: r[k]
                for k in [
                    "base_id",
                    "parent_id",
                    "lifecycle",
                    "prior_advance_return",
                    "prior_advance_atr_units",
                    "prior_advance_bars",
                    "prior_advance_quality",
                ]
            },
            "pattern": pattern,
            "pattern_strength": float(strength),
            "merit": 0.65 * strength + 0.15 * tight + 0.1 * volume + 0.1 * proximity,
            "base_start": s.date(a),
            "base_known_session": s.date(r["known_index"]),
            "base_bars": e - a,
            "base_depth": observed_depth,
            "base_low_price": r["lower"],
            "base_low_date": s.date(a + int(np.argmin(s.low[a : r["known_index"] + 1]))),
            "pivot": r["upper"],
            "floor": floor,
            "upper_zone": [r["upper"] * (1 - r["zone"]), r["upper"] * (1 + r["zone"])],
            "pause_start": s.date(ps),
            "pause_bars": e - ps,
            "pause_width": width,
            "pause_drift": float(s.c[e - 1] / s.c[ps] - 1),
            "volume_ratio_5_to_prior50_median": dry,
            "true_range_ratio_5_to_prior30": atr_ratio,
            "tightness": tight,
            "volume_quality": volume,
            "contractions": legs,
            "contraction_quality": contraction,
            "terminal_contraction_intact": terminal_intact,
            "vcp_evidence": vcp,
            "base_invalidation_level": r["lower"] * (1 - r["zone"]),
            "breakout_session": s.date(r["breakout_index"]) if r["breakout_index"] is not None else None,
            "recognition_basis": "confirmed_anchor_rotation_and_sideways_travel",
        }

    def failures(self, end):
        """One outer-structure failure per accepted base; persistent event history."""
        events = []
        catalogue = self.snapshot(end)["catalogue"]
        for r in catalogue:
            b, f = r["breakout_index"], r["invalidated_index"]
            if b is None or f is None or f <= b:
                continue
            recovered = np.flatnonzero(self.s.c[f + 1 : end] > r["upper"])
            resolved = f + 1 + int(recovered[0]) if len(recovered) else None
            repairs = [
                x
                for x in catalogue
                if x["start_index"] > f
                and x["known_index"] >= f + 15
                and x["lower"] > float(self.s.low[f : x["start_index"] + 1].min())
                and self.s.c[x["known_index"]]
                > self.s.ma[20][x["known_index"]]
                > self.s.ma[20][max(0, x["known_index"] - 5)]
            ]
            repair = min((x["known_index"] for x in repairs), default=None)
            reset = repair is not None and (resolved is None or repair < resolved)
            if reset:
                resolved = repair
            events.append(
                {
                    "base_id": r["base_id"],
                    "attempt_id": f"{r['base_id']}:{b}",
                    "breakout_session": self.s.date(b),
                    "failure_session": self.s.date(f),
                    "pivot": r["upper"],
                    "stop": r["lower"] * (1 - r["zone"]),
                    "base_invalidation_level": r["lower"] * (1 - r["zone"]),
                    "failure_kind": "base_failure",
                    "reclaimed": resolved is not None and not reset,
                    "resolved_session": self.s.date(resolved) if resolved is not None else None,
                    "new_base_reset": reset,
                    "active": resolved is None,
                }
            )
        return events
