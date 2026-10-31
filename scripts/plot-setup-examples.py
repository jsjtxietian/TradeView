"""Render auditable, prefix-only examples from the current model and local cache.

Plotting dependency is installed with the app. Outputs are local research artifacts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from trenddeck.queries import read_history  # noqa: E402
from trenddeck.setups.queries import get_setup_rankings, get_setup_report  # noqa: E402

CASES = [
    (
        "AAPL",
        "2024-11-27",
        "大基底与内部整理",
        "模型选中 7 月开始的大基底。\n青色虚线是目录中另一个内部基底。\n内部已收紧，但尚未越过大基底上沿。",
    ),
    (
        "TSLA",
        "2024-12-04",
        "突破后的独立短基底",
        "11 月的推进之后，形成新的整理。\n它有自己的底边与年龄，\n不借用前面大基底的持续时间。",
    ),
    (
        "DELL",
        "2026-05-04",
        "强势推进后的局部停顿",
        "模型识别到 4 月末的新整理。\n短基底已经存在，仍处于形成状态；\n不能仅因为此前涨得强就判为 ready。",
    ),
    (
        "QQQ",
        "2025-02-21",
        "大基底尚在，小平台先失守",
        "当天跌破了最近五日平台的支撑。\n即使大基底底边仍然更低，\n也不能继续标记为“准备突破”。",
    ),
]
STATUS = {
    "ready": "准备突破",
    "developing": "形成中",
    "invalidated": "局部平台失效",
    "damaged": "结构受损",
    "extended": "已经延伸",
    "above_pivot": "已越过阻力",
    "breakout_confirmed": "放量突破",
    "breakout_unconfirmed": "突破待量能确认",
}


def render(symbol, day, title, note, out):
    report = get_setup_report(symbol, day, include_details=True)
    detail, g = report["details"], report["details"]["geometry"]
    history = read_history(symbol, day).reset_index(drop=True)
    history["Date"] = pd.to_datetime(history.Date)
    assert str(history.Date.iloc[-1])[:10] == day
    full = history.copy()
    anchor = int(history.Date.searchsorted(pd.Timestamp(g["base_start"])))
    # Keep the actual selected base start visible, even for a long consolidation.
    first = max(0, min(len(history) - 125, anchor - 20))
    history = history.iloc[first:].reset_index(drop=True)
    dates = history.Date.dt.strftime("%Y-%m-%d").tolist()
    last = len(history) - 1

    def x(date):
        return int(np.searchsorted(dates, date))

    fig = plt.figure(figsize=(13.8, 8.0), facecolor="#f8fafc")
    grid = fig.add_gridspec(
        2,
        2,
        height_ratios=[4, 1],
        width_ratios=[4.7, 1.4],
        left=0.067,
        right=0.975,
        bottom=0.12,
        top=0.84,
        hspace=0.06,
        wspace=0.08,
    )
    ax = fig.add_subplot(grid[0, 0])
    vol = fig.add_subplot(grid[1, 0], sharex=ax)
    aside = fig.add_subplot(grid[:, 1])
    aside.axis("off")
    for panel in [ax, vol]:
        panel.set_facecolor("white")
        panel.grid(axis="y", color="#e8edf3", linewidth=0.7)
        panel.spines[["top", "right"]].set_visible(False)
        panel.spines[["left", "bottom"]].set_color("#cbd5e1")
    fig.text(0.067, 0.947, f"{symbol}  ·  {title}", fontsize=22, weight="bold", color="#15263d")
    fig.text(
        0.067,
        0.896,
        f"观察日 {day}  |  结构截至 {detail['setup_as_of_session']}  |  复权日 K · 无未来行情",
        fontsize=11,
        color="#53627a",
    )
    start = x(g["base_start"])
    known = x(g["base_known_session"])
    end = last - 0.45
    upper, lower = g["pivot"], g["base_low_price"]
    ax.add_patch(
        Rectangle(
            (start - 0.4, lower),
            end - start + 0.4,
            upper - lower,
            facecolor="#3b82f6",
            alpha=0.075,
            edgecolor="none",
            zorder=0,
        )
    )
    ax.hlines(upper, start - 0.4, end, color="#2563eb", lw=1.8, label="选中基底上沿")
    ax.hlines(lower, start - 0.4, end, color="#2563eb", lw=1.2, linestyle=":", label="冻结的整体底边")
    ax.vlines(start, lower, upper, color="#2563eb", lw=0.8, linestyle=":")
    ax.scatter([known], [upper], marker="D", s=37, color="#2563eb", zorder=6, label="基底被确认的日期")
    zone = g.get("upper_zone")
    if zone:
        ax.fill_between([start, end], zone[0], zone[1], color="#2563eb", alpha=0.05)
    ps = x(g["pause_start"])
    pause_high = float(full.loc[(full.Date >= g["pause_start"]) & (full.Date < day), "High"].max())
    ax.add_patch(
        Rectangle(
            (ps - 0.4, g["floor"]),
            end - ps + 0.4,
            pause_high - g["floor"],
            facecolor="#f59e0b",
            alpha=0.20,
            edgecolor="#d97706",
            lw=1.3,
        )
    )
    ax.hlines(g["floor"], ps - 0.4, last + 0.6, color="#d97706", lw=1.5, linestyle="--", label="末端平台支撑")
    nested = None
    if symbol == "AAPL":
        children = [
            r
            for r in detail["base_context"]["catalogue"]
            if r["base_id"] != g["base_id"]
            and r["lifecycle"] == "forming"
            and str(full.Date.iloc[r["start_index"]])[:10] > g["base_start"]
            and r["upper"] <= upper * 1.03
            and r["lower"] > lower
        ]
        if children:
            nested = max(children, key=lambda r: (r["start_index"], r["known_index"]))
            cx = x(str(full.Date.iloc[nested["start_index"]])[:10])
            ax.add_patch(
                Rectangle(
                    (cx - 0.4, nested["lower"]),
                    end - cx + 0.4,
                    nested["upper"] - nested["lower"],
                    facecolor="none",
                    edgecolor="#0f766e",
                    ls="--",
                    lw=1.3,
                    label="内部基底候选",
                )
            )
            nx = x(str(full.Date.iloc[nested["known_index"]])[:10])
            ax.scatter([nx], [nested["upper"]], marker="D", s=25, color="#0f766e", zorder=6)
    for j, row in history.iterrows():
        color = "#138b79" if row.Close >= row.Open else "#d94e5d"
        ax.vlines(j, row.Low, row.High, color=color, lw=0.85, zorder=3)
        ax.add_patch(
            Rectangle(
                (j - 0.30, min(row.Open, row.Close)),
                0.6,
                max(abs(row.Close - row.Open), row.Close * 0.00035),
                facecolor=color,
                edgecolor=color,
                lw=0.5,
                zorder=4,
            )
        )
        vol.bar(j, row.Volume / 1e6, color=color, width=0.72, alpha=0.8)
    ma = full.Close.rolling(50).mean().iloc[first:].to_numpy()
    ax.plot(np.arange(len(history)), ma, color="#9b8db1", lw=1, label="MA50", alpha=0.9)
    ax.axvline(last, color="#334155", lw=0.8, linestyle="--", alpha=0.6)
    ax.annotate(
        f"{history.Close.iloc[-1]:.2f}",
        (last, history.Close.iloc[-1]),
        xytext=(7, 0),
        textcoords="offset points",
        fontsize=10,
        color="#15263d",
        va="center",
    )
    ax.set_ylabel("价格 / USD", fontsize=10, color="#53627a")
    vol.set_ylabel("量 / 百万", fontsize=10, color="#53627a")
    ax.tick_params(axis="x", labelbottom=False)
    ticks = np.linspace(0, last, 7, dtype=int)
    vol.set_xticks(ticks, [dates[i] for i in ticks], rotation=20, ha="right", fontsize=9)
    ax.set_xlim(-1, last + 7)
    ax.legend(loc="upper left", fontsize=8.5, frameon=True, facecolor="white", framealpha=0.9)
    aside.text(0, 0.95, f"{report['score']:.2f}", fontsize=34, weight="bold", color="#15263d")
    aside.text(
        0,
        0.86,
        STATUS.get(report["status"], report["status"]),
        fontsize=15,
        color="#b45309" if report["status"] == "invalidated" else "#2563eb",
    )
    aside.text(0, 0.78, "形态分，不是上涨概率", fontsize=10, color="#64748b")
    info = (
        f"基底起点  {g['base_start']}\n确认日期  {g['base_known_session']}\n"
        f"基底年龄  {g['base_bars']} 个交易日\n\n上沿参考  {upper:.2f}\n"
        f"整体底边  {lower:.2f}\n末端支撑  {g['floor']:.2f}\n末端长度  {g['pause_bars']} 日"
    )
    aside.text(0, 0.70, info, fontsize=10.5, color="#334155", va="top", linespacing=1.65)
    aside.text(0, 0.29, note, fontsize=10.5, color="#334155", va="top", linespacing=1.75)
    fig.text(
        0.067,
        0.028,
        "蓝色：选中基底及阻力带    橙色：当前末端平台    菱形之前的边界是事后回看标注，当时尚未确认",
        fontsize=10,
        color="#64748b",
    )
    path = out / f"{symbol}-{day}.png"
    fig.savefig(path, dpi=165, facecolor=fig.get_facecolor())
    plt.close(fig)
    return {
        "symbol": symbol,
        "as_of": day,
        "image": str(path.resolve()),
        "report": report,
        "nested_candidate": nested,
        "observed_bars": len(full),
        "future_bars_in_chart": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "local/artifacts/setup-examples")
    parser.add_argument("--latest", action="store_true", help="Plot highest-scoring current cached bases")
    parser.add_argument("--count", type=int, default=3, choices=range(1, 13), metavar="1..12")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for path in [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]:
        if path.is_file():
            font_manager.fontManager.addfont(str(path))
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(path)).get_name()
            break
    plt.rcParams["axes.unicode_minus"] = False
    cases = CASES
    if args.latest:
        ranking = get_setup_rankings(universe="cache", limit=100, include_details=True)
        (args.output / "rankings.json").write_text(
            json.dumps(ranking, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
        )
        candidates = [row for row in ranking["items"] if row["details"]["geometry"].get("base_id")]
        cases = []
        for row in candidates[: args.count]:
            d, g = row["details"], row["details"]["geometry"]
            distance = d["distance_to_pivot"]
            position = (
                f"现价高于所选上沿 {distance:.1%}。" if distance > 0 else f"现价距所选上沿 {-distance:.1%}。"
            )
            volume = g["volume_ratio_5_to_prior50_median"]
            note = (
                f"{position}\n末端宽度 {g['pause_width']:.1%}，量比 {volume:.2f}。\n"
                + ("量能尚未明显收缩。" if volume >= 0.9 else "量能较前期有所收缩。")
                + "\n排名靠前不等于已触发买点。"
            )
            cases.append(
                (
                    row["symbol"],
                    ranking["as_of_session"],
                    f"当前第 {row['rank']} 名 · {STATUS.get(row['status'], row['status'])}",
                    note,
                )
            )
    results = [render(*case, args.output) for case in cases]
    (args.output / "manifest.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    print("\n".join(r["image"] for r in results))


if __name__ == "__main__":
    main()
