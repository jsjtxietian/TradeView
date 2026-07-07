# TrendDeck Design Notes

## Overview

This project is a cache-first US stock trend dashboard.

Primary goals:

- offline-capable display from local cached data
- controlled online refresh against rate-limited APIs
- compact watchlist view optimized for quick trend scanning
- richer detail view for actual analysis

Current runtime shape:

- backend: `FastAPI`
- frontend: static `HTML + JS + CSS`
- market data: `Tiingo + local CSV cache`
- charting: `lightweight-charts`

## Data Source Strategy

### Price Data

Source of truth for price history is local cache under `.cache/*_3y_history.csv`.

Behavior:

- normal page load: read local cache only
- user clicks `拉新`: allow network and do incremental update
- updated data is merged back into cache CSV

Why:

- API limit is tight
- page must still work offline
- cached CSVs can be committed and shared

### Incremental Refresh

Incremental update logic lives in `app.py`.

Rules:

- if cache exists, refresh starts from `last_cached_date + 1 day`
- incoming Tiingo rows are merged with cached rows
- duplicates are deduplicated by `Date`, keeping newest row

Result:

- no full re-download on every refresh
- offline display keeps working from latest local snapshot

## Symbol Normalization

Frontend and backend both normalize symbols in the same way.

Rules:

- uppercase everything
- whitespace and `.` become `-`
- repeated `-` collapse to one

Examples:

- `brk b` -> `BRK-B`
- `BRK.B` -> `BRK-B`
- ` msft ` -> `MSFT`

Reason:

- Tiingo commonly expects dash-separated share class symbols
- frontend and backend must agree to avoid cache mismatches

## Local Storage Model

Frontend keeps user-specific state in `localStorage`.

Keys:

- `trenddeck_watchlist`
  watchlist symbol array
- `trenddeck_watchlist_groups`
  custom groups and per-group symbol order
- `trenddeck_chart_prefs`
  chart mode and MA visibility
- `trenddeck_symbol_notes`
  per-symbol notes and holding flag
- `trenddeck_watchlist_filter_template`
  whether watchlist is filtered to full trend-template matches
- `trenddeck_watchlist_filter_template_variant`
  whether watchlist is filtered to the trend-template variant
- `trenddeck_watchlist_filter_range_below_ma50`
  whether watchlist is filtered to stocks whose latest complete daily range is below the 50-day average range
- `trenddeck_watchlist_filter_holding`
  whether watchlist is filtered to locally marked holdings
- `trenddeck_watchlist_alerts`
  recent alert list shown in alerts modal
- `trenddeck_watchlist_alerts_snapshot`
  last seen summary snapshot used to detect changes

Important design choice:

- notes, filters, alerts and grouping are purely local user state
- price history and analysis results come from cached market data

## Watchlist Rendering

### Group Ordering

Watchlist group order and in-group symbol order come from the saved group definition itself, not from global watchlist order.

If the user reorders symbols inside a group editor row and saves, homepage rendering follows that exact order.

Reason:

- user expects group editor order to be authoritative

### Adding Symbols

There are two ways symbols enter watchlist:

- from the top `添加` form
- from editing group contents

When group save introduces new symbols:

- they are appended into watchlist
- persisted immediately
- a refresh is triggered automatically

Reason:

- user should not need two separate actions to add and fetch

### Removing Symbols

Removal is done from the note modal.

Deleting a symbol removes:

- watchlist membership
- group membership
- local note
- local alert snapshot for that symbol
- stored alerts mentioning that symbol

Reason:

- keep the main card UI compact
- still provide a safe correction path for typo symbols

## Watchlist Trend Mini-Chart

### Purpose

The mini chart is not meant to mirror raw closing prices.

It is meant to answer one question quickly:

- is the recent trend rising, falling, or flat?

### Current Algorithm

Implementation lives in `build_trend_sparkline()` in `app.py`.

Steps:

1. take the last up to `35` trading days for display
2. build a `MA20` base line
3. if early rows do not have `MA20` yet, backfill with cumulative mean of all history up to that date
4. blend the base with raw close using `0.7 * MA20_base + 0.3 * Close`
5. run a `3-period EMA` over that blended line
6. display only the resulting smoothed series in the watchlist

Direction color:

- look at the latest up to `10` points of the smoothed line
- compute end-to-start move
- compute simple slope
- classify as:
  - `up` if move >= about `+1.5%` and slope positive
  - `down` if move <= about `-1.5%` and slope negative
  - otherwise `flat`

Reasoning:

- the `70/30` blend keeps the line anchored to recent structure while reacting faster to sharp reversals and breakouts
- MA20 reflects recent price structure better than raw close
- EMA removes jagged turns without drifting too far
- the watchlist view should emphasize direction, not candle noise

## Trend Template Logic

The trend template uses the base checks `1-8`.

Base implementation note:

- trend `8` uses a local relative-to-SPY performance score rather than the official IBD RS Rating
- benchmark is `SPY`
- the proxy aligns stock and benchmark closes by trading date, then compares weighted excess returns over `63`, `126`, `189`, and `252` trading days
- weights are `40%`, `20%`, `20%`, and `20%`, so the latest quarter has the largest influence
- formula: `clip(50 + 100 * weighted_excess_return, 1, 99)`
- trend `8` passes at `60`, which means about `+10%` weighted excess return versus SPY
- practical interpretation: `70` means about `+20%` weighted excess return versus SPY, `80` about `+30%`, and `90` about `+40%`
- this is only an approximation of relative strength versus SPY; it is not a cross-sectional market percentile and should not be treated as the official IBD ranking

Indicator implementation notes:

- the breakout follow-through card lives under `卖出指标观察` and checks MA20 support, at least `3/4` or `6/8` up days after a breakout, consecutive lower lows, and upper-half closes versus lower-half closes
- `7/8` or `8/8` up days are called out as stronger institutional-accumulation behavior, while the pass threshold remains `6/8`
- three or more consecutive lower lows trigger attention even without expanding volume; sequentially rising volume raises the stated severity
- MVP is shown as `MVP 指标` immediately before Power Play and requires at least `12/15` up days, a `20%` 15-day gain, and `1.25x` recent-versus-prior average volume

The watchlist filter `趋势模板` currently means:

- only show stocks where `trendPassCount === trendTotal`
- effectively full pass on the base template set

The watchlist filter `趋势模板变种` currently means:

- ignore the base template item `相对 SPY 表现分不低于 60`
- require all other base trend-template items to pass

The watchlist filter `前一日振幅低于50日均振幅` currently means:

- require the latest complete daily intraday range to be below its 50-day average range

Reason:

- the homepage filter should stay simple and unambiguous

## Alerts Logic

Alerts are frontend-local and summary-based.

Important rule:

- alerts are recalculated whenever summaries are refreshed into the page
- comparison uses `trenddeck_watchlist_alerts_snapshot`

Alert types:

- newly satisfied full trend template
- no longer satisfies full trend template
- latest close changed more than `+/-5%` versus previous snapshot
- latest close reaches recent `6` month closing high
- latest close reaches recent `6` month closing low

Storage behavior:

- fresh alerts are prepended
- alert list is capped to the latest `20`

UI behavior:

- clicking the top-right icon opens a modal

## Check Rule Tooltip

Extended checks `9-13` show a small `i` hover hint in the detail panel.

Design choice:

- keep the visible card text focused on conclusion and current measurement
- move calculation rules into short hover copy instead of expanding every card
- keep tooltip copy aligned with backend logic so later formula changes only need one text update

Reason:

- alerts should be visible but not occupy permanent page space
- local snapshot comparison is enough for this product stage

## Notes UX

Each watchlist card has a right-aligned `i` button.

Behavior:

- click opens modal only
- no hover preview card
- note modal stores both free-form text and a local `isHolding` flag
- holding symbols get a highlighted card background in the watchlist
- watchlist cards show only floating P/L for holdings; cost basis stays inside the note modal and detail area
- button `title` text shows:
  - custom note if it exists
  - otherwise `持仓股` when only the holding flag is set
  - otherwise a generic "查看或编辑笔记"

Reason:

- hover card looked visually noisy
- modal is the primary editing surface
- a lightweight tooltip is still useful, but symbol-name mapping was removed to avoid stale manual metadata

## Messaging UX

Top message bar is kept for error state only.

Short successful actions use a temporary toast instead.

Examples:

- symbol added
- group saved
- symbol deleted

Reason:

- success feedback should not push layout downward
- loading/error still needs a persistent visible status area

## AI Prompt Export

The detail chart toolbar includes a `复制 Prompt` action.

Design:

- prompt template lives in repo root as `prompt_template.md`
- template is local and user-editable without changing application code
- backend fills placeholders such as symbol, latest date, condensed technical summary, and current note text
- prompt wording explicitly reflects the user's trend-following preference:
  - do not bottom-fish
  - focus on big trend segments
  - prefer leader stocks
  - check for `Code 33` style acceleration in earnings, sales, and margins
- technical summary is intentionally compressed into:
  - latest close / daily change / latest volume
  - 5/20/60/126 day returns
  - MA20/50/150/200 relative position
  - six-month high/low position
  - trend-template pass count and failed items
  - RS detail
  - advanced trend-check summary

Reason:

- LLMs benefit more from structured state summaries than raw OHLC history
- the user can iterate on prompt wording independently from the implementation
- if the note modal is open, unsaved textarea content is preferred; otherwise the saved local note is used

## Chart Detail Panel

Detail panel semantics:

- `最新收盘`: latest available daily close, not realtime price
- `较前收盘`: change versus previous day close
- `收盘日成交量`: volume of the same latest daily close session

Reason:

- avoid confusion with live intraday terminology

## Current Tradeoffs

Known intentional simplifications:

- company full names come from a local map, not a dedicated metadata API
- alerts are local and user-specific, not server-synced
- watchlist trend mini-chart is a smoothed price-structure proxy, not a formal technical score
- default historical horizon is fixed and cache-centered rather than user-configurable everywhere

These choices are deliberate to keep:

- API usage low
- offline support strong
- implementation maintainable
- homepage visually dense but still readable
# IBKR 交易导入

建议每次从 IBKR 下载 YTD Transaction History。导出的时间范围可以与之前重叠，
同一份 CSV 用于更新两类本地 JSON：

- `.trade/trades.json`：完整多头/空头交易周期与手工复盘
- `.trade/ledger.json`：佣金、股息、预扣税、贷方/借方利息和其他收支

应用运行时只读取这两个 JSON，不直接读取 CSV。

## 导入交易周期

先将股票买卖转换成独立 JSON 供检查：

```powershell
python tools/import_ibkr_transactions.py convert ibkr-transactions-ytd.csv --output ibkr-trades.json
```

确认后追加到交易复盘数据：

```powershell
python tools/import_ibkr_transactions.py append ibkr-trades.json --output .trade/trades.json
```

交易追加阶段会按股票、币种和完整成交明细去重。重复运行不会重复写入已有交易，
也不会覆盖已经填写的整体复盘 `note`。独立转换结果按每笔交易的首次操作日期正序编号；
append 保留已有 ID，并将新识别的完整交易按时间顺序追加到末尾。网页仍按最后卖出日期
倒序展示。

## 导入账户账务

将同一份 Transaction History CSV 中的佣金、股息、预扣税、利息和其他收支写入账务 JSON：

```powershell
python tools/import_ibkr_transactions.py ledger ibkr-transactions-ytd.csv --output .trade/ledger.json
```

ledger 命令每次完整覆盖 `.trade/ledger.json`，因此应优先使用覆盖开户至今或完整 YTD
范围的新账单，不能只导入一个很短的增量区间，否则 JSON 中较早的账务记录会消失。
导入完成后，`/review` 的统计区间会同时筛选交易、佣金、股息、利息和其他收支。

交易复盘的数据统计按完整平仓周期计算，时间筛选以最后平仓日期为准。多头单笔收益率为
`(总卖出收入 - 总买入成本) / 总买入成本`；空头单笔收益率为
`(总做空收入 - 总回补成本) / 总做空收入`。当前 JSON 不保存佣金，因此统计结果未计佣金。
调整后胜负比为 `平均盈利 / 平均亏损 × 胜率 / 败率`。
同一币种内另行统计已实现盈亏、Profit Factor 和按金额计算的最大单笔盈利/亏损；
不同币种的金额不会直接相加。

导入器同时识别完整多头与空头周期。持仓由负转正或由正转负时，同一笔成交会先按
原持仓数量完成平仓周期，再将剩余数量作为反向新仓。交易数据统计位于 `/review`，
复盘弹窗里的“数据统计”按钮会在新窗口打开该页面。BOXX 的完整平仓价差不进入普通交易
胜率和盈亏分布，而是在账户收入与费用中归入“现金利息 / 类现金”。
