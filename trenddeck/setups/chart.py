"""In-memory PNGs from the exact price snapshot and evidence used by a report.

No detection, fetching or file exports here. English chart labels work on the
headless Linux server without requiring a platform-specific CJK font.
"""

from __future__ import annotations

import base64
from io import BytesIO
from threading import Lock

import numpy as np
import pandas as pd

from trenddeck.setups.engine import clean_history

_RENDER_LOCK = Lock()  # Matplotlib rendering/font internals are not thread-safe.


def chart_base(detail, setup_type):
    """Adapt existing evidence; never infer a new box from chart prices."""
    if setup_type == "pullback":
        b = detail.get("base")
        if not b:
            return None
        return {
            "base_id": b["base_id"],
            "ordinal": b["ordinal"],
            "start_session": b["start_session"],
            "known_session": b["known_session"],
            "range_known_session": b["range_known_session"],
            "upper": b["upper"],
            "lower": b["lower"],
            "threshold": b["upper"] * (1 + b["zone"]),
            "threshold_basis": "breakout_confirmation",
            "breakout_session": b["breakout_session"],
            "invalidated_session": b["invalidated_session"],
        }
    g = detail.get("geometry", {})
    if not g.get("base_id"):
        return None
    return {
        "base_id": g["base_id"],
        "ordinal": None,
        "start_session": g["base_start"],
        "known_session": g["base_known_session"],
        "range_known_session": g["base_known_session"],
        "upper": g["pivot"],
        "lower": g["base_low_price"],
        "threshold": g["upper_zone"][1],
        "threshold_basis": "resistance_zone_upper",
        "breakout_session": g.get("breakout_session"),
        "invalidated_session": None,
    }


def render_setup_chart(frame, report, detail, setup_type):
    # Defense in depth: even a caller passing later bars cannot put them on a chart.
    frame = frame.loc[pd.to_datetime(frame.Date) <= pd.Timestamp(report["as_of_session"])]
    full = clean_history(frame)
    base = chart_base(detail, setup_type)
    start = max(0, len(full) - 125)
    if base:
        start = min(start, max(0, int(full.Date.searchsorted(pd.Timestamp(base["start_session"]))) - 20))
    cycle = detail.get("cycle")
    if cycle:
        start = min(start, max(0, int(full.Date.searchsorted(pd.Timestamp(cycle["peak_session"]))) - 12))
    start = max(start, len(full) - 504)
    f = full.iloc[start:].reset_index(drop=True)
    dates = f.Date.dt.strftime("%Y-%m-%d").tolist()
    metadata = {
        "mimeType": "image/png",
        "width": 1600,
        "height": 960,
        "setup_type": setup_type,
        "as_of_session": report["as_of_session"],
        "display_start_session": dates[0],
        "display_end_session": dates[-1],
        "display_bars": len(f),
        "price_mode": str(full.PriceMode.iloc[-1]) if "PriceMode" in full else "unknown",
        "base": base,
        "base_start_clipped": bool(base and base["start_session"] < dates[0]),
        "base_role": report.get("pullback", {}).get("base_role")
        if setup_type == "pullback"
        else "selected"
        if base
        else None,
        "labels_language": "en",
    }
    with _RENDER_LOCK:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.patches import Rectangle

        fig = Figure(figsize=(13.3333333333, 8), dpi=120, facecolor="#f7f9fc")
        canvas = FigureCanvasAgg(fig)
        grid = fig.add_gridspec(
            2,
            2,
            width_ratios=[4.4, 1.6],
            height_ratios=[4, 1],
            left=0.065,
            right=0.98,
            bottom=0.13,
            top=0.82,
            hspace=0.06,
            wspace=0.10,
        )
        ax = fig.add_subplot(grid[0, 0])
        vol = fig.add_subplot(grid[1, 0], sharex=ax)
        aside = fig.add_subplot(grid[:, 1])
        aside.axis("off")
        fig.text(
            0.065,
            0.945,
            f"{report['symbol']}  |  {setup_type.upper()}",
            fontsize=21,
            weight="bold",
            color="#142c47",
        )
        score = "unscored" if report["score"] is None else f"score {report['score']:.2f}"
        fig.text(
            0.065, 0.90, f"As of {report['as_of_session']}  |  {report['status']}  |  {score}", fontsize=12
        )
        fig.text(
            0.065,
            0.86,
            f"{metadata['price_mode']} daily OHLCV  |  Same evidence as report; no future bars",
            fontsize=9,
            color="#53627a",
        )
        colors = np.where(f.Close >= f.Open, "#159777", "#d15b61")
        for j, row in enumerate(f.itertuples()):
            ax.vlines(j, row.Low, row.High, color=colors[j], lw=0.7)
            ax.add_patch(
                Rectangle(
                    (j - 0.3, min(row.Open, row.Close)),
                    0.6,
                    max(abs(row.Close - row.Open), row.Close * 0.0007),
                    facecolor=colors[j],
                    edgecolor=colors[j],
                    lw=0.4,
                )
            )
        for n, color in [(20, "#9575cd"), (50, "#e2a326"), (200, "#7d8c9c")]:
            values = full.Close.rolling(n, min_periods=n).mean().iloc[start:]
            ax.plot(np.arange(len(f)), values, color=color, lw=1, label=f"MA{n}")

        def mark(day, label, marker, color, offset):
            if not day or day not in dates:
                return
            j = dates.index(day)
            y = float(f.Close.iloc[j])
            ax.scatter(j, y, marker=marker, c=color, s=55, zorder=6)
            right = j > len(f) * 0.55
            ax.annotate(
                f"{label}\n{day}",
                (j, y),
                xytext=(-12 if right else 12, offset),
                textcoords="offset points",
                ha="right" if right else "left",
                fontsize=8,
                color=color,
                arrowprops={"arrowstyle": "-", "color": color},
            )

        lines = []
        if base:
            left = max(0, int(np.searchsorted(dates, base["start_session"])))
            end_day = base["breakout_session"] or base["invalidated_session"] or dates[-1]
            right = max(left, min(len(f) - 1, int(np.searchsorted(dates, end_day))))
            ax.add_patch(
                Rectangle(
                    (left - 0.4, base["lower"]),
                    max(0.8, right - left + 0.8),
                    base["upper"] - base["lower"],
                    color="#2d85c5",
                    alpha=0.12,
                )
            )
            ax.hlines(base["upper"], left, len(f) - 1, color="#2d85c5", lw=1.2, label="Base upper")
            ax.hlines(
                base["threshold"],
                left,
                len(f) - 1,
                color="#2d85c5",
                ls=":",
                lw=1,
                label="Breakout threshold" if setup_type == "pullback" else "Resistance zone upper",
            )
            ax.hlines(base["lower"], left, len(f) - 1, color="#d89332", ls="--", lw=1, label="Base lower")
            mark(base["known_session"], "Base confirmed", "D", "#186b9c", 44)
            mark(base["breakout_session"], "Breakout", "^", "#137d60", -44)
            mark(base["invalidated_session"], "Invalidated", "x", "#c23c4b", -44)
            # Base geometry ends at the previous session. Today's crossing is
            # separate evidence, not a backdated first-breakout claim.
            if (
                setup_type == "base"
                and not base["breakout_session"]
                and detail.get("breakout", {}).get("crossed_today")
            ):
                mark(dates[-1], "Cross today", "^", "#137d60", -44)
            lines = [
                f"Repair base #{base['ordinal']}" if base["ordinal"] else "Selected base",
                f"Role: {metadata['base_role']}",
                "",
                f"Start: {base['start_session']}",
                f"Range known: {base['range_known_session']}",
                f"Qualified: {base['known_session']}",
                f"Upper: {base['upper']:.2f}",
                f"{'Threshold' if setup_type == 'pullback' else 'Zone upper'}: {base['threshold']:.2f}",
                f"Lower: {base['lower']:.2f}",
                f"Breakout: {base['breakout_session'] or 'not recorded'}",
                f"Invalidated: {base['invalidated_session'] or 'not recorded'}",
            ]
        else:
            lines = ["No qualifying base", "No box or breakout marker", "has been inferred."]
        lines += ["", f"Actionable: {detail.get('actionable', False)}"]
        if setup_type == "pullback":
            lines += ["MA200: required", "MA50: background only"]
        if metadata["base_start_clipped"]:
            lines += ["", "Base start outside displayed", "504-session window."]
        if report.get("stale"):
            lines += ["", "STALE vs benchmark cache"]
        lines += ["", "Recognition is not an entry order."]
        aside.text(0, 1, "\n".join(lines), va="top", fontsize=9, linespacing=1.65, color="#24384f")
        vol.bar(np.arange(len(f)), f.Volume / 1e6, color=colors, width=0.7)
        for panel in [ax, vol]:
            panel.set_facecolor("white")
            panel.grid(axis="y", alpha=0.15)
            panel.spines[["top", "right"]].set_visible(False)
        ticks = np.unique(np.linspace(0, len(f) - 1, min(7, len(f)), dtype=int))
        vol.set_xticks(ticks, [dates[j] for j in ticks], rotation=25, ha="right", fontsize=8)
        ax.tick_params(axis="x", labelbottom=False)
        ax.set_ylabel("Price / USD")
        vol.set_ylabel("Volume / M")
        ax.legend(loc="upper left", fontsize=7, ncol=2)
        buffer = BytesIO()
        canvas.print_png(buffer)
        raw = buffer.getvalue()
        fig.clear()
    # PNG IHDR reports the exact raster dimensions (avoid float-size rounding).
    metadata.update(width=int.from_bytes(raw[16:20], "big"), height=int.from_bytes(raw[20:24], "big"))
    return {**metadata, "data": base64.b64encode(raw).decode("ascii")}
