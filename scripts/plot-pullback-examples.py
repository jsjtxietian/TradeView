"""Prefix-only META/MSFT repair-base cases and audit manifest from local cache."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if (ROOT / "tmp/research-python").is_dir():
    sys.path.insert(0, str(ROOT / "tmp/research-python"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from trenddeck.queries import read_history  # noqa: E402
from trenddeck.setup_pullback import PullbackSeries, fingerprints  # noqa: E402

CASES = [
    ("META", "2025-06-04", "深度调整后的第一修复基底"),
    ("MSFT", "2026-10-06", "较新一轮调整后的第一修复基底"),
    ("META", "2024-09-09", "反例：第一候选没有突破便失守"),
    ("MSFT", "2025-02-26", "反例：有修复、有突破，后来仍失败"),
    ("MSFT", "2025-04-08", "下跌阶段：不把反弹平台当止跌确认"),
]


def render(symbol, day, title, series, out):
    s = series.s
    i = int(np.searchsorted(s.dates, np.datetime64(day)))
    report = series.report(i)
    prefix = PullbackSeries(s.frame.iloc[: i + 1]).report()
    assert report == prefix, (symbol, day, "future-dependent report")
    cycle, base = report["cycle"], report["base"]
    start = max(0, int(np.searchsorted(s.dates, np.datetime64(cycle["peak_session"]))) - 15)
    f = s.frame.iloc[start : i + 1].reset_index(drop=True)
    dates = f.Date.dt.strftime("%Y-%m-%d").tolist()

    def x(date):
        return int(np.searchsorted(dates, date))

    fig = plt.figure(figsize=(14, 8), facecolor="#f7f9fc")
    grid = fig.add_gridspec(
        2,
        2,
        width_ratios=[4.5, 1.65],
        height_ratios=[4, 1],
        left=0.065,
        right=0.98,
        bottom=0.13,
        top=0.84,
        hspace=0.06,
        wspace=0.10,
    )
    ax = fig.add_subplot(grid[0, 0])
    vol = fig.add_subplot(grid[1, 0], sharex=ax)
    aside = fig.add_subplot(grid[:, 1])
    aside.axis("off")
    fig.text(0.065, 0.94, f"{symbol} · {title}", fontsize=20, weight="bold", color="#142c47")
    fig.text(
        0.065,
        0.89,
        f"仅显示截至 {day} 的行情 · 状态：{report['status']} · pullback 独立观察，无百分制分数",
        fontsize=11,
    )
    colors = np.where(f.Close >= f.Open, "#159777", "#d15b61")
    for j, row in enumerate(f.itertuples()):
        ax.vlines(j, row.Low, row.High, color=colors[j], lw=0.8)
        ax.add_patch(
            Rectangle(
                (j - 0.3, min(row.Open, row.Close)),
                0.6,
                max(abs(row.Close - row.Open), row.Close * 0.0007),
                facecolor=colors[j],
                edgecolor=colors[j],
                lw=0.5,
            )
        )
    for n, color in [(20, "#9575cd"), (50, "#e2a326"), (200, "#7d8c9c")]:
        ax.plot(np.arange(len(f)), s.ma[n][start : i + 1], color=color, lw=1, label=f"MA{n}")
    if base:
        left = x(base["start_session"])
        right = x(base["breakout_session"] or base["invalidated_session"] or day)
        ax.add_patch(
            Rectangle(
                (left, base["lower"]),
                max(1, right - left),
                base["upper"] - base["lower"],
                facecolor="#2d85c5",
                alpha=0.12,
                edgecolor="#2d85c5",
            )
        )
        ax.hlines(base["upper"], left, len(f) - 1, color="#2d85c5", lw=1.2, label="冻结箱顶")
        ax.hlines(
            base["upper"] * (1 + base["zone"]),
            left,
            len(f) - 1,
            color="#2d85c5",
            ls=":",
            lw=1,
            label="突破确认阈值（含容差）",
        )
        ax.hlines(base["lower"], left, len(f) - 1, color="#d89332", ls="--", lw=1, label="冻结支撑")
        markers = [
            ("known_session", "D", "#186b9c", "修复确认"),
            ("breakout_session", "^", "#137d60", "突破"),
            ("invalidated_session", "x", "#c23c4b", "失效"),
        ]
        for key, marker, color, label in markers:
            if base[key] and base[key] <= day:
                j = x(base[key])
                y = float(f.Close.iloc[j])
                ax.scatter(j, y, marker=marker, c=color, s=65, zorder=6)
                offset = 42 if key == "known_session" else -34
                ax.annotate(
                    label + " " + base[key],
                    (j, y),
                    xytext=(-10, offset),
                    textcoords="offset points",
                    ha="right",
                    fontsize=9,
                    color=color,
                    arrowprops={"arrowstyle": "-", "color": color},
                )
    li = x(cycle["low_session"])
    ax.scatter(li, cycle["low"], marker="o", s=25, color="#333333")
    near_right = li > len(f) * 0.85
    ax.annotate(
        "截至当日最低点",
        (li, cycle["low"]),
        xytext=(-8 if near_right else 8, 12),
        ha="right" if near_right else "left",
        textcoords="offset points",
        fontsize=9,
    )
    vol.bar(np.arange(len(f)), f.Volume / 1e6, color=colors, width=0.7)
    vol.set_ylabel("成交量 / 百万")
    for panel in [ax, vol]:
        panel.set_facecolor("white")
        panel.grid(axis="y", alpha=0.15)
        panel.spines[["top", "right"]].set_visible(False)
    ticks = np.linspace(0, len(f) - 1, min(7, len(f)), dtype=int)
    vol.set_xticks(ticks, [dates[j] for j in ticks], rotation=25, ha="right", fontsize=9)
    ax.tick_params(axis="x", labelbottom=False)
    ax.set_ylabel("复权价格 / 美元")
    ax.legend(loc="upper left", fontsize=8, ncol=2)
    lines = [
        f"最大调整：{report['max_drawdown']:.1%}",
        f"观察日距旧高：{report['drawdown']:.1%}",
        f"可操作条件：{report['actionable']}",
        f"距失效参考：{report['support_distance']:.1%}" if report["support_distance"] is not None else "",
        "",
    ]
    if base:
        lines += [
            f"本轮第 {base['ordinal']} 个修复基底",
            f"区间起点：{base['start_session']}",
            f"区间可知：{base['range_known_session']}",
            f"修复可知：{base['known_session']}",
            f"箱顶：{base['upper']:.2f}",
            f"突破阈值：{base['upper'] * (1 + base['zone']):.2f}",
            f"底边：{base['lower']:.2f}",
            f"首次突破：{base['breakout_session'] or '未发生'}",
            f"结构失效：{base['invalidated_session'] or '尚未观察到'}",
            "",
        ]
    labels = {
        "above_ma20": "站上20日均线",
        "ma20_rising": "20日均线回升",
        "above_ma200": "站上200日线",
    }
    lines += ["当日趋势证据："] + [
        ("通过 " if v else "未过 ") + labels[k] for k, v in report["trend_checks"].items()
    ]
    lines += ["MA50：仅作背景，不作门槛"]
    lines += ["", "确认日期与区间起点分开。", "标记只用当时已知行情。", "首次失败不会被后来的成功替换。"]
    aside.text(0, 1, "\n".join(lines), va="top", fontsize=10, linespacing=1.65, color="#24384f")
    path = out / f"{symbol}-{day}.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    # Outcomes are separate, never passed back into the detector.
    outcome = {"status": "no_breakout"}
    if base and base["breakout_session"]:
        b = int(np.searchsorted(s.dates, np.datetime64(base["breakout_session"])))
        if b + 20 < len(s.c):
            entry = float(s.o[b + 1])
            outcome = {
                "status": "evaluated",
                "entry_session": s.date(b + 1),
                "entry_open": entry,
                "end_session": s.date(b + 20),
                "mae": float(s.low[b + 1 : b + 21].min() / entry - 1),
                "mfe": float(s.h[b + 1 : b + 21].max() / entry - 1),
                "hit_minus8": bool(s.low[b + 1 : b + 21].min() <= entry * 0.92),
            }
        else:
            outcome = {"status": "censored", "available_sessions": len(s.c) - b - 1}
    return {
        "symbol": symbol,
        "as_of": day,
        "image": str(path.resolve()),
        "report": report,
        "prefix_invariance_passed": True,
        "forward_20_sessions": outcome,
        **fingerprints(s.model),
    }


def main():
    plt.rcParams["font.family"] = "Microsoft YaHei"
    plt.rcParams["axes.unicode_minus"] = False
    out = ROOT / "tmp/pullback-examples"
    out.mkdir(parents=True, exist_ok=True)
    series = {symbol: PullbackSeries(read_history(symbol)) for symbol in ["META", "MSFT"]}
    manifest = [render(*case, series[case[0]], out) for case in CASES]
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in manifest:
        print(json.dumps({k: r[k] for k in ["symbol", "as_of", "forward_20_sessions"]}, ensure_ascii=True))


if __name__ == "__main__":
    main()
