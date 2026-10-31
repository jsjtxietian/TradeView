"""Shared human-readable public presentation of the detailed setup engine."""

from __future__ import annotations

from typing import Any

from trenddeck.setups.model import SetupModel
from trenddeck.setups.validity import base_invalidation_reason

COMPONENTS = {
    "trend": ("上升趋势", "检查价格与长期均线、均线方向及一年高低点的位置"),
    "structure": (
        "整理形态",
        "先识别有确认边界的基底，再评价恢复程度、持续时间、内部收缩和前段推进；VCP 是基底的一类特征",
    ),
    "tightness": ("价格收紧", "比较近期平台宽度与前期真实波幅，避免只把低波动当作好形态"),
    "volume": ("成交量收缩", "比较末端整理近五日均量与该平台开始前五十日成交量中位数，观察供给是否减少"),
    "relative_strength": ("相对 SPY 强度", "比较个股与 SPY 在相同交易日的相对表现"),
    "location": ("接近突破位置", "衡量当前价格与观察时已经形成的阻力区域的距离"),
    "risk": ("结构距离", "衡量当前价格距整理支撑的距离，距离较近得到较高分"),
}

PATTERNS = {
    "vcp": "多轮波动收缩",
    "flat_base": "平底整理",
    "short_base": "短期基底",
    "range_base": "区间基底",
}

# Extend this mapping when the engine adds a new condition; raw machine codes
# remain available only in details, while optional engine reason_texts can
# introduce explanatory prose without changing the public response contract.
WARNING_TEXT = {
    "completed_base_needs_new_consolidation": "较早的基底已经完成突破，当前尚未识别出合格的新整理，不能沿用旧形态的收缩得分。",
    "early_trend_repair": "长期均线仍在修复，但价格已站上 MA20、MA50 和 MA200，短中均线向上，已确认的高低点连续抬高并收复最近反弹高点。",
    "core_stage2_not_met": "尚未满足长期上升趋势，也未完成价格、短中均线和连续高低点共同确认的趋势修复。",
    "prior_advance_weak_relative_to_own_volatility": "整理前的上涨相对自身波动偏弱，缺少明确的前段推动。",
    "zero_volume_in_last_60_bars": "最近六十个交易日存在零成交量记录，量能证据不完整。",
    "adjusted_prices_required": "缺少一致的复权价格，评分受到数据质量限制。",
    "aligned_benchmark_unavailable_no_rs_points": "缺少同期 SPY 数据，相对强度部分没有加分。",
    "extended_from_pivot_or_ma20": "价格偏离 MA20 或超过自身波动尺度的延伸限制，当前买点偏迟。",
    "unrepaired_lower_highs_and_lower_lows": "近期高点和低点逐步下移，下降结构尚未修复。",
    "failed_rebound_below_prior_low": "反弹后再次跌破前低，反弹支撑已经失守。",
    "material_lower_low_sequence": "连续更低低点伴随实质下跌，尚未收复此前水平。",
    "repeated_unrepaired_selloffs": "近期多次大幅下跌，价格仍未完成修复。",
    "falling_intermediate_trend": "短中期趋势仍在下行，长期均线的上行不足以抵消近期走弱。",
    "failed_frozen_pivot": "此前基底突破后跌破已冻结的整体支撑，当前仍存在相关结构损伤。",
}

STATUS_TEXT = {
    "outside_stage2": "趋势尚未成立，当前形态得分受到趋势门槛限制。",
    "damaged": "当前结构受损，较早的强势表现没有抵消近期破坏。",
    "no_base": "尚未找到符合要求的整理结构。",
    "invalidated": "价格已跌破本次整理的支撑范围。",
    "extended": "价格已明显延伸，当前不属于靠近突破的买点。",
    "ready": "已接近整理区的突破位置，价格与量能达到模型的准备条件。",
    "breakout_confirmed": "本次突破已有收盘位置和成交量确认。",
    "breakout_unconfirmed": "价格已越过阻力，但成交量或收盘位置尚未充分确认。",
    "above_pivot": "价格已在原阻力上方，需要区分持续推进与新的整理机会。",
    "developing": "结构仍在形成，尚未同时满足临近突破与量能准备条件。",
}


def _shape_reason(report: dict[str, Any]) -> str | None:
    """Explain at most one supported boundary observation without implying a signal."""
    for window in report.get("shape_context", {}).get("windows", []):
        upper, lower = window.get("upper_boundary", {}), window.get("lower_boundary", {})
        if not (upper.get("usable") and lower.get("usable")):
            continue
        evidence = window.get("geometry_evidence", [])
        if window.get("flag_like_context"):
            description = "上沿下移，呈前段推进后下降整理的特征"
        elif "ascending_triangle_geometry" in evidence:
            description = "上沿较平、下沿抬高，呈上升三角形的几何特征"
        elif "converging_range" in evidence:
            description = "上下边界逐渐靠拢，呈区间收敛的特征"
        else:
            continue
        tentative = upper.get("tentative_two_touch_line") or lower.get("tentative_two_touch_line")
        return f"{'初步观察到' if tentative else '整理区'}{description}；这项形态背景诊断不额外加分。"
    return None


def _base_stage_reason(report: dict[str, Any]) -> str | None:
    stage = report.get("base_context", {}).get("stage", {})
    if not stage:
        return None
    if stage.get("reset_pending"):
        return "出现周期重置迹象，但新周期尚未确认，暂不判定基底早晚。"
    maturity = stage.get("maturity")
    if maturity == "late":
        return "已观察到较多独立基底，呈偏后期特征；这只是风险背景，不加分，也不放宽追价限制。"
    if maturity == "mature":
        return "已观察到多轮独立基底，周期逐渐成熟；这只是风险背景，不加分，也不放宽追价限制。"
    if stage.get("history_left_censored"):
        return "可见历史未覆盖完整周期，不能因观察到的基底较少就认定处于早期。"
    if maturity == "early":
        return "已观察到新周期的早期基底；早期背景不加分，也不放宽追价限制。"
    return "当前证据不足以判断基底早晚，阶段暂记为未知。"


def score_reasons(report: dict[str, Any], model: SetupModel) -> list[str]:
    """Explain observed evidence, including score restrictions, without leaking diagnostics."""
    if report.get("status") == "insufficient_history":
        return [
            f"历史数据不足：目前 {report.get('bars', 0)} 个交易日，"
            f"至少需要 {report.get('required_bars', 253)} 个交易日，暂不评分。"
        ]
    reasons = []
    base_stage_reason = _base_stage_reason(report)

    def add(text: str) -> None:
        if text and text not in reasons:
            reasons.append(text)

    for warning in report.get("warnings", []):
        if warning == "average_dollar_volume_below_2m":
            add(
                f"近期平均日成交额低于模型要求的 {model.parameters['min_dollar_volume']:,.0f} 美元，流动性不足。"
            )
        elif warning in WARNING_TEXT:
            add(WARNING_TEXT[warning])
    for explanation in report.get("reason_texts", []):
        if isinstance(explanation, str):
            add(explanation)
    add(STATUS_TEXT.get(report.get("status"), "分数综合反映当前趋势和整理形态的证据。"))
    geometry = report.get("geometry", {})
    base_identified = geometry.get("base_id") and report.get("pattern") != "none"
    if base_identified:
        pattern = report.get("pattern")
        if pattern == "short_base":
            add("已识别出短期基底；整理时间较短，尚不能称为平底。")
        elif pattern == "vcp":
            evidence = geometry.get("vcp_evidence", {})
            depths = " → ".join(f"{d:.1%}" for d in evidence.get("depths", []))
            if evidence.get("qualified"):
                add(
                    f"已先识别出基底，内部已确认回调逐次收窄（{depths}），末轮回调量低于首轮，末端缩量且支撑保持；符合 VCP 标签条件。"
                )
            else:
                add("已识别出基底，但缺少完整的 VCP 标签验证证据。")
        else:
            add(f"已识别出{PATTERNS.get(pattern, '基底')}，整体边界与末端平台分别衡量。")
    add(base_stage_reason or "")
    if report.get("penalties", {}).get("distribution", 0) > 0:
        add("近期出现多次放量弱收盘下跌，评分已扣除相应分数。")
    add(_shape_reason(report) or "")
    components = report.get("components", {})
    supportive = {
        "trend": "价格与均线位置等趋势检查通过较多，提供了趋势支持。",
        "structure": f"识别到{PATTERNS.get(report.get('pattern'), '整理结构')}，结构证据较完整。",
        "tightness": "近期价格区间和真实波幅有所收紧，整理趋于稳定。",
        "volume": "整理阶段成交量明显减少，提供了量能收缩证据。",
        "relative_strength": "个股相对 SPY 的强度对本次评分有较明显贡献。",
    }
    if report.get("trend_phase") == "repairing":
        supportive["trend"] = "短中期趋势和已确认波段正在改善，长期均线尚未全部形成标准上升排列。"
    if base_identified:
        supportive.pop("structure")
    weaknesses = {
        "trend": "趋势条件通过较少，上升趋势的支持不足。",
        "structure": "整理结构证据偏弱，单纯窄幅波动不足以得到高分。",
        "tightness": "近期价格仍较松散，波动收紧的证据不足。",
        "volume": "整理期间量能收缩不足，尚未看到明显缩量。",
        "relative_strength": "相对 SPY 的强度加分较少，价格领导性不足或数据尚不完整。",
    }
    ratios = {
        key: value["points"] / value["maximum"]
        for key, value in components.items()
        if value.get("maximum", 0) > 0
    }
    weakest = sorted((key for key in weaknesses if ratios.get(key, 1) < 0.35), key=ratios.get)
    for key in weakest[:1]:
        add(weaknesses[key])
    strongest = sorted(
        (key for key in supportive if ratios.get(key, 0) >= 0.7),
        key=lambda key: components[key]["points"],
        reverse=True,
    )
    for key in strongest[:2]:
        add(supportive[key])
    if geometry.get("terminal_contraction_intact") is False and report.get("pattern") == "none":
        add("最后一轮收缩仍不完整，当前不能认定为成熟的多轮收缩形态。")
    # Invalidation must survive compact output; it is not an optional positive.
    required = [x for x in [base_invalidation_reason(report), base_stage_reason] if x]
    compact = [x for x in reasons if x not in required][: max(0, 6 - len(required))] + required
    return compact


def conditions_markdown(model: SetupModel) -> str:
    """Use the selected model's weights and thresholds, never a stale static table."""
    p = model.parameters
    lines = [
        f"### 买点评分条件（{model.version}）",
        "",
        "分数评价观察日的整体趋势与整理形态；突破时机另外标记。它不预测盈利，也不规定卖出方式。",
        "",
        "| 项目 | 满分 | 依据 |",
        "| --- | ---: | --- |",
    ]
    for key, weight in model.weights.items():
        if weight == 0:
            continue
        name, description = COMPONENTS.get(key, (key, "按所选模型的对应证据计算"))
        if key == "relative_strength":
            description += (
                "；综合六十三日表现、二十一日表现与相对强度线距近期高点的位置"
                if p.get("relative_context_enabled")
                else "；使用六十三日相对表现"
            )
        points = f"{weight:.2f}".rstrip("0").rstrip(".")
        lines.append(f"| {name} | {points} | {description} |")
    lines.extend(
        [
            "",
            "各项按证据强弱连续计分，再扣除深度回撤和分配压力；数据、趋势、结构状态也可能限制总分。",
            "",
            "- 长期趋势：价格高于 MA150/MA200、MA150 高于 MA200，且 MA200 较二十一日前上升。",
            f"- 临近突破：价格距阻力不超过下方 {p['ready_distance']:.0%}；"
            f"末端平台宽度不超过 {p['max_pause_width']:.0%}，并满足形态和缩量门槛。",
            f"- 放量确认：当日收盘越过事先形成的阻力，成交量至少为此前五十日均量的 "
            f"{p['breakout_volume_ratio']:g} 倍，收盘位于当日区间上方 "
            f"{1 - p['breakout_close_location']:.0%} 内。",
            f"- 延伸限制：高于 MA20 {p['max_ma20_extension']:.0%}，形态得分受到限制；"
            "仅越过局部平台阻力不单独视为过度延伸。",
            f"- 数据：至少二百五十三个交易日、一致复权价格，平均日成交额至少 "
            f"{p['min_dollar_volume']:,.0f} 美元；相对强度使用同期 SPY。",
        ]
    )
    lines.extend(
        [
            "- 趋势修复路径：长期均线尚未完全转好时，须同时站上 MA20、MA50、MA200，"
            "MA20/MA50 上行，已确认波段连续形成更高高点和更高低点，并收复最近反弹高点；仍接受全部损伤和延伸检查。",
            "- VCP 标签：至少两轮已确认回调、幅度逐次缩小、末轮回调成交量中位数低于首轮、"
            "末端五日均量低于整理前基准、末端区间不超过10%且支撑完整；仅影响标签，不额外加分。"
            "低点是否抬高单独记录，不要求每轮低点机械抬高。",
            "- 失效：末端平台收盘跌破含缓冲支撑，与整体基底最近三日中两次收盘跌破冻结底边容差线，分别说明。"
            "原尝试失败后保留记录；同一结构锚点重建至少等待15日，并重新满足形成条件。",
            "- 基底优先：先确认锚点与反复整理，再识别 VCP、平底、短期基底或一般区间基底；"
            "短期基底不等同于平底，基底可以存在而没有 VCP 特征。整体支撑、阻力带与末端平台分开记录。",
            "- 分数与时机：上述有效权重归一至 100 分；枢轴距离和结构距离不直接加分。"
            "形成中、已越过阻力、尚未放量确认三种状态不单独封顶，趋势不足、无形态、受损、失效和明确延伸仍限制分数。",
            "- 形态背景诊断单独返回，不额外加分。表内小数为展示舍入，计算使用完整权重。",
            f"- 基底识别：锚点使用前 {p['base_left_bars']:g} 日与后 {p['base_confirm_bars']:g} 日确认，"
            f"至少观察 {p['base_min_bars']:g} 日整理，识别时深度不超过 {p['base_max_depth']:.0%}；"
            "还需回转、恢复和横向运动证据。锚点日期不等于当时可知日期，识别后外部边界冻结。",
            f"- 周期背景：基底计数区分重叠、嵌套与独立推进，独立推进使用 {p['base_count_min_advance']:.0%} 的工程门槛；"
            f"观察到至少 {p['base_late_ordinal']:g} 个独立基底可提示偏后期。"
            "历史未覆盖周期起点时不能凭少量基底判断早期；重置待确认时保持未知。",
            "- 早期、成熟、偏后期与未知都是风险背景，不额外加分，也不放宽追价限制；"
            "阶段计数不是作者人工判图的精确复刻。",
        ]
    )
    if p.get("damage_filters_enabled"):
        lines.append(
            "- 结构受损：未修复的下降高低点、失败反弹、连续实质下跌、重复大跌或原突破失效会限制分数；"
            "当前相关基底的失败事件按其冻结边界追踪。"
        )
        lines.append(
            f"- 自身波动延伸：高于 MA20 的距离超过此前平均日真实波幅的 "
            f"{p['risk_extension_atr']:g} 倍，也会限制买点评分。"
        )
    lines.extend(["", "只使用截至观察日的 OHLCV；不按证券是否为 ETF 排除。未来走势仅用于独立验证。"])
    return "\n".join(lines)
