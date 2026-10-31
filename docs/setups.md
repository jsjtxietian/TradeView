# 当前基底评分与研究模型

项目只运行当前模型 `base-v4`，后续直接在这个模型上迭代。它用本地复权 OHLCV 识别上涨后的整理基底，再评价内部收缩、趋势和相对强弱。分数是形态证据汇总，不是胜率；ETF 与个股采用相同规则。

当前接口不联网刷新、不调用 LLM、不自动交易。书籍、提取文本和临时研究图放在被忽略的 `local/ref/books/`、`local/artifacts/`。

## 先识别基底

1. 高点必须高于左侧五日，并经右侧三日确认。分别记录高点发生日和结构可知日，未确认的未来波段不能提前使用。
2. 从高点后观察回落、承接、恢复及横向往返。局部整理至少五日，并在25日内满足前段推进条件；较大结构至少25日。单向运动不能只凭区间极值成为基底。
3. 形成记录有独立 `base_id`、开始日、可知日和冻结上下沿。局部失败不永久否定同一高点后形成的大基底；旧失败记录保留，新形成记录使用新 ID。较大结构失效后至少再观察15日并重新满足形成条件，不能直接降低旧底边。
4. 最近三日中两次收盘越过上沿容差带，才在基底目录中接受突破；两次收盘跌破整体底边容差带则失效。单根影线不等于整体结构失效。突破后的几何冻结在本次被接受的突破之前，不能借几周前的失败试探冻结位置。
5. 大基底和内部局部整理可以并存。局部整理拥有自己的年龄和支撑；`floor` 是末端支撑，`base_low_price` 是整体冻结支撑，两者不混用。
6. 末端通常测量十日；最近五日已有横向往返且振幅不超过10%时，使用这个独立小平台。新平台已损坏时不能借旧低点维持 ready。

标签包括 `short_base`、`range_base`、`flat_base`、`vcp`。平底检查整个实际区间的宽度；VCP 是基底内部的收缩标签，不是所有窄幅走势的统称。下降整理、三角收敛等斜边证据在 `shape_context` 中提供诊断，尚未完整成为交易触发线。跳空保留为事件，未接入财报日历，原因标为未知。

VCP 标签现在要求至少两轮已确认回调，回调幅度逐次缩小，末轮回调成交量中位数低于首轮，末端相对整理前基准缩量，末端区间不超过10%，且最后支撑保持完整。低点是否抬高是诊断证据，不强制每轮抬高。详细的通过条件、缺失条件、各轮幅度及成交量在 `details.geometry.vcp_evidence`。只收紧标签，原有基底选择、权重和分数计算不变；普通基底仍可得到高分。这些是显式工程条件，不宣称完全复刻人工判图。

默认 `reasons` 固定保留本次基底识别日期与具体失效条件，不受六条精简上限截断。`details.invalidation` 区分末端平台一次收盘失守与整体底边最近三日中两次收盘失守，分别给出价格和是否触发。Pullback 的理由则按其自己的冻结底边和调整低点规则说明，并标明当前基底或历史尝试。识别失效不等于保证能按该价成交。

每个报告用前一交易日已知的结构检验当日价格，所以 `as_of_session` 与 `setup_as_of_session` 相差一根交易日。确认波段、基底边界及已知时间都保留；不使用网页趋势图的事后压缩序列，也不用平滑抹掉暴跌。

## 证据、分数与状态

趋势既检查核心 Stage 2 均线条件，也接受由更高的确认高低点、收复反弹高点及均线修复支持的早期修复；`trend_phase` 区分 `stage2`、`repairing`、`unconfirmed`。此前推进按自身真实波幅衡量，不因低波动或 ETF 身份直接排除。

| 证据 | 配置相对权重，计算时归一到100 |
| --- | ---: |
| 趋势 | 20 |
| 基底结构与此前推进 | 25 |
| 价格收紧 | 15 |
| 成交量收缩 | 15 |
| 相对 SPY 强弱 | 10 |
| 精确枢轴距离、结构风险距离 | 0 |

`quality_score` 是证据分经相应扣分后的结果；`score` 还受趋势不足、无基底、损伤、失效或明显延伸等限制。突破距离和风险仍参与状态、可操作条件及风险检查，权重为零不表示忽略风险。模型并不只在精确碰到 pivot 时才承认好的整体形态。

损伤检查包括持续 lower highs/lower lows、失败反弹、连续暴跌、下行趋势及未修复的失败结构。已收复的单次下探与持续破坏分开；原始 High/Low、成交量及事件时间均保留。

RS 返回相对 SPY 的63日及21日表现、相对强弱线距高点、同区间回撤和市场下跌日表现，不是 IBD 全市场 RS Rating。SPY 状态作为背景输出，目前不以弱市一刀切否决全部个股。缺失基准不给相对强弱分。

常见状态为 `insufficient_history`、`outside_stage2`、`no_base`、`developing`、`ready`、`breakout_unconfirmed`、`breakout_confirmed`、`above_pivot`、`extended`、`invalidated`、`damaged`。价格/成交量突破确认与基底目录的多日接受突破是不同口径，须结合各自日期理解。

- `eligible` 检查趋势、复权资料与流动性，不保证已有成熟基底。
- `candidate_eligible` 进一步检查结构及损伤、失效、延伸。
- `actionable` 要求准备或确认阶段以及合格结构风险，仅是条件标记，不是下单指令。
- 不足253根日线返回空分及原因，不用零分冒充有效计算。

`base_context.stage` 提供早期/成熟/后期/未知背景。嵌套结构、同一阻力重试不重复计数；独立推进后形成新整理才追加阶段。可见历史不完整时明确 `history_left_censored`，少量观测不等于早期。阶段背景不直接加分或封顶。

## 参数与可复现性

### Pullback：深度调整后的第一修复基底

单股和排行接受 `setup_type="pullback"`，默认仍为 `"base"`，不改变已有基底评分。
Pullback 是独立观察分支，返回阶段、理由、调整幅度、支撑距离和冻结基底，`score=null`；
排行按阶段、代码排序并返回 `ordering=status_then_symbol_not_quality_score`，没有形态百分位。
`actionable_only` 仍需通过复权、流动性、趋势修复及最多10%的支撑风险距离检查。
只识别了突破，不代表一定满足可操作条件。

过程按日推进：

1. 相对此前252日已知高点回落至少15%，且超过高点附近事前平均真实波幅的4倍，记录调整周期。
2. 低点至少经过10个交易日观察；反弹后形成的基底底边高于调整低点至少半个事前波幅，宽度最多16%。
3. 局部修复要求价格站上MA20、MA20较五日前上行；MA50位置和方向仅作为 `trend_context` 背景，不作为准入门槛，以免中期均线滞后挡住早期价格修复。收盘必须站上MA200，即使MA200仍在上升也不放行其下方的候选。已识别的历史基底保留，当前跌回MA200下方则不可操作。
4. 复用当前基底目录的确认锚点、回转和横向整理。区间可以先形成、再等待趋势修复，但已经突破或失效的区间不能事后取得修复资格。分别返回 `range_known_session` 与 `known_session`。
5. 第一基底按当时资格固定身份和边界，下一日起收盘越过上沿容差带记录首次突破，三日两次站上记录接受。返回箱顶 `upper`；容差 `zone` 在详细结果中，触发阈值为 `upper*(1+zone)`。
6. 失效或过期保留在尝试历史中，后续基底不得回溯编号为第一。跌破确认时调整低点，或三日两次收盘跌破支撑容差带，记录失效。突破后回落至阻力带内标为 `retesting`；超过十个交易日不沿用旧突破作为新信号。

`pullback.base_role` 区分正在观察的基底和仅保留的历史尝试；`include_details=true` 返回调整周期、未通过检查的候选、全部尝试与趋势条件。未通过的候选是当时证据快照，后续可以修复，不等于永久排除。
参数位于 `trenddeck/setups/pullback.py` 的 `RULES`，沿用模型的流动性、延伸和风险限制，并提供独立配置和引擎指纹。
这些阈值是工程假设，不是书中原话或根据案例收益拟合的最优值；止跌证据不能保证未来不再下跌。

```json
{"tool": "get_setup_report", "arguments": {"symbol": "META", "as_of": "2025-06-04", "setup_type": "pullback", "include_details": true}}
{"tool": "get_setup_rankings", "arguments": {"symbols": ["META", "MSFT"], "setup_type": "pullback"}}
```

HTTP 同样支持 `?setup_type=pullback`。`python scripts/plot-pullback-examples.py` 生成 META、MSFT 五个正反案例及审计清单到被忽略的 `local/artifacts/pullback-examples/`；每例核对完整历史回放与当日截断结果一致。图只展示截至观察日的数据，未来20日路径放在单独字段。案例由人工挑选，不是未触碰的验证集；本地2023年4月开始的数据不能检验2022年大底。MSFT 2025年快速恢复没有被强行补标为一个新的第一基底。

配置为 `trenddeck/resources/models/base-v4.json`；加载器校验参数和权重，配置 schema 为3。公共精简接口 schema 为2，与配置 schema 是不同概念。输出保留配置、引擎和研究数据指纹；仅保存配置不足以复现算法改动。

| 参数 | 当前值 | 含义 |
| --- | ---: | --- |
| `base_left_bars` / `base_confirm_bars` | 5 / 3 | 高点左侧比较与右侧确认 |
| `base_min_bars` / `base_max_bars` | 5 / 200 | 局部最短观察及锚点最长观察 |
| `base_zone_atr` | 0.75 | 事前波幅形成阻力/支撑容差，代码另限制范围 |
| `base_max_depth` | 0.45 | 允许识别的最大回撤，并不代表高质量 |
| `base_count_min_advance` / `base_late_ordinal` | 0.20 / 4 | 独立推进分隔与后期背景约定 |
| `base_reset_below_ma_days` | 20 | 长期均线下降背景的观察时长 |
| `prior_advance_min_atr` / `prior_advance_full_atr` | 2 / 6 | 此前推进证据的自身波幅尺度 |
| `breakout_volume_ratio` / `breakout_close_location` | 1.5 / 0.6 | 当日量价突破确认 |
| `ready_distance` / `max_risk` | 0.03 / 0.10 | 准备位置及可操作结构风险范围 |
| `min_dollar_volume` | 2,000,000 | 流动性门槛 |

完整参数见配置及详细接口。结构形成的25日分界、失败后15日重新观察、末端5/10日观察等仍有代码常量。它们及各项权重是明确的工程假设，不冒充书中原话或统计最优值。

主要模块为 `trenddeck/setups/bases.py`（基底目录）、`trenddeck/setups/base_stage.py`（阶段背景）、`trenddeck/setups/shapes.py`（附加几何诊断）、`trenddeck/setups/engine.py`（综合评分）、`trenddeck/setups/research.py`（历史研究）。修改当前模型时先写假设与验收条件，再记录配置/引擎指纹及独立验证；日常刷新行情仅重新算分，不自动训练参数。

## HTTP 与 MCP

```json
{"tool": "get_setup_rankings", "arguments": {"universe": "cache", "limit": 20}}
{"tool": "get_setup_rankings", "arguments": {"actionable_only": true}}
{"tool": "get_setup_report", "arguments": {"symbol": "DELL", "as_of": "2026-09-02"}}
{"tool": "get_setup_report", "arguments": {"symbol": "DELL", "include_details": true}}
```

```text
GET /api/setups/rankings?universe=cache&limit=20
GET /api/setups/report/DELL?as_of=2026-09-02
GET /api/setups/report/DELL?include_details=true
GET /api/setups/backtest?start=2024-06-03&end=2026-10-16&holdout_start=2026-01-01&universe=cache
```

MCP 仅提供单股评分和排行，不暴露回测工具；回测保留为 HTTP 研究接口和本地脚本。

兼容参数 `model_version` 可以省略；显式传值只接受当前 `base-v4`，其他值报错。CLI没有版本选择或多版本比较功能。

排行默认 watchlist；`universe=cache` 读取本地行情集合并默认排除基准 SPY，显式 `symbols` 覆盖集合，最多200个。`limit` 为1–100，支持 `offset` 与 `next_offset` 分页。`actionable_only` 过滤后保留原集合名次，不重新编号。

单股和排行默认返回分数、状态、中文理由及按实际参数生成的 Markdown 评分条件。传 `include_details=true` 获取完整诊断：单股在 `details`，排行在 `items[].details`；其中 `base_context.catalogue` 提供基底身份与生命周期，`base_context.stage` 提供阶段背景。

排行按 SPY 可用交易日对齐，旧缓存、缺交易日与历史不足分别报告。单股查询返回实际使用日期和 `stale`。工具仅读取本地缓存；HTTP复用现有网页服务，MCP复用现有 Bearer 验证及端口。

## 当前研究命令与解释

```powershell
python scripts/research-setups.py --end 2026-10-16 --output local/artifacts/setup-research/current-20.json
python scripts/research-setups.py --end 2026-10-16 --symbols AAPL,TSLA,QQQ --horizon 10 --step 20 --output local/artifacts/setup-research/current-10.json
```

CLI只运行当前模型，JSON保留顶层 `base-v4` 键以兼容现有结果读取方式。`--end` 同时限制信号与未来观察资料；默认包含逐条研究事件。API默认返回汇总，`include_event_records=true` 可取明细。

`pivot_events` 比较突破与观察价下跌8%的先后、回踩收复及确认后风险。价格穿越、多日接受、放量确认分别报告；已经发生的突破不冒充预测命中。确认后使用下一交易日开盘观察，不回填理想 pivot 成交价。未来标签不进入当天评分。

固定持有期收益、RS排序及 SPY 对照、结构止损/2R退出属于辅助研究，不是模型对卖出方式的规定。时间拆分剔除跨界窗口，末端资料不足单独报告；`step < horizon` 时观察重叠，不能当独立样本。

当前缓存包含幸存者与集合选择偏差，2026年及部分案例已经查看，不能称为未触碰的前瞻留出集。现有结果未证明排序效果已经提高。

## 静态图例

```powershell
python scripts/plot-setup-examples.py
python scripts/plot-setup-examples.py --latest --count 3 --output local/artifacts/setup-current
```

生成 AAPL、TSLA、DELL、QQQ 四个历史日期的日 K 图及原始评分 manifest，保存于被忽略的
`local/artifacts/setup-examples/`。蓝色标当前选中基底，橙色标末端平台，菱形标确认日期；每张图只显示截至观察日的行情。
`--latest` 则按 SPY 最新缓存日期对齐，跑全缓存排行并画得分靠前且有基底的标的，另存完整 `rankings.json`；
不会自动刷新过期缓存，也不会因排名靠前而把形成中或失效状态改成准备突破。
这是离线研究绘图，不修改网页。需要可选的 matplotlib，当前环境可复用 `local/artifacts/research-python/` 中的绘图依赖。

## 阅读依据与下一步

设计参考 Minervini 的趋势、收缩与3-C，O’Neil 的杯柄、失败基底、相对强弱与市场背景，及 Zanger 的旗形和跳空后整理。实际图例阅读涵盖正例和反例；书中形态不直接等于程序标签。当前尚未完整识别杯柄弧度、复杂斜边触发，也未引入基本面、行业或宏观消息。

当前形态评分先对每只标的独立计算，再按候选集合排序；更换集合不会改变单股分数。排名只代表集合内位置，不替代形态质量判断。

当前待完善的重点：

1. **右侧阶段识别的验收。** 末端默认十日窗口还不能保证已经形成横向停顿，但最后几日的实际收紧也可能有效，不能仅因此前快速反弹而否定。先用图例检查恢复、停顿、收紧和突破的区别，不预先增加更严格的等待条件。
2. **父基底与内部平台的选择稳定性。** 已有独立身份及嵌套关系，但每天选取最优候选仍可能切换结构。需要区分同一基底的改善与切换到另一个基底，并完善较大结构和局部准备区的解释。
3. **分数含义与跨形态一致性。** 公开分数评价当前形态是否提供好的买点，延伸过多本身会降低买点质量，因此保留相应限制。已有内部 `quality_score` 与受状态限制的 `score`，不要求为了单独表达历史形态美观而取消延伸封顶。仍需检查短平台、长箱体、VCP 的同分是否代表相近质量，不能靠集合百分位掩盖评分标准的问题。
4. **同一结构的评分轨迹。** 已能查询历史 `as_of`，尚无专门的连续评分接口、持久快照或变化原因输出；应结合基底身份解释分数变化。
5. **独立形态验收。** 扩充只看截至观察日图形的标注与负例，再检查后续突破、下探收复和结构失效。现有因果性测试、图例及历史事件统计不足以证明高分筛选已经可靠。

若增加宏观、财报或消息，须独立记录资料何时可知，避免修订数据和事后事件时间泄漏。
