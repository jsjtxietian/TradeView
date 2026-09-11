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
- charting: vendored `lightweight-charts` 5.0.0 (no CDN needed)
- agent access: read-only Streamable HTTP MCP at `/mcp`; see [MCP setup and Hermes configuration](mcp.md)

## Data Source Strategy

### Price Data

Source of truth for price history is local cache under `data/stock/*_3y_history.csv`.

Behavior:

- normal page load: read local cache only
- user clicks `拉新`: allow network and do incremental update
- updated data is merged back into cache CSV

Why:

- API limit is tight
- page must still work offline
- cached CSVs can be committed and shared

### Incremental Refresh

Incremental update logic lives in `trenddeck/market.py`.

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

The server stores watchlists/groups in `data/trade/watchlist.json` and notes/holding fields in `data/trade/notes.json`. The browser mirrors watchlists/groups in `localStorage` and keeps display preferences locally.

Keys:

- `trenddeck_watchlist`
  watchlist symbol array
- `trenddeck_watchlist_groups`
  custom groups and per-group symbol order
- `trenddeck_chart_prefs`
  chart mode and MA visibility
- `trenddeck_symbol_notes`
  legacy notes/holding data migrated to the server and removed from localStorage
- `trenddeck_watchlist_filter_template`
  whether watchlist is filtered to full trend-template matches
- `trenddeck_watchlist_filter_template_variant`
  whether watchlist is filtered to the trend-template variant
- `trenddeck_watchlist_filter_range_below_ma50`
  whether watchlist is filtered to stocks whose latest complete daily range is below the 50-day average range
- `trenddeck_watchlist_filter_holding`
  whether watchlist is filtered to locally marked holdings
- `trenddeck_watchlist_alerts`
  legacy browser-local alerts migrated to the server on first load

Important design choice:

- notes, holding fields, watchlists and grouping are persisted by the server
- filters and chart preferences remain browser-local
- alerts and their comparison snapshot are persisted by the server
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
- server alert snapshot for that symbol after watchlist state is persisted

Historical alerts remain in the shared server log.

Reason:

- keep the main card UI compact
- still provide a safe correction path for typo symbols

## Watchlist Trend Mini-Chart

### Purpose

The mini chart is not meant to mirror raw closing prices.

It is meant to answer one question quickly:

- is the recent trend rising, falling, or flat?

### Current Algorithm

Implementation lives in `build_trend_sparkline()` in `trenddeck/indicators.py`.

Steps:

1. take the last up to `35` trading days for display
2. use closing price directly and forward-fill small gaps
3. apply shape-preserving smoothing: small chop is blended toward a short EMA, but meaningful local peaks and troughs stay close to raw closes
4. display that price-structure line in the watchlist so spikes, pullbacks, and rebounds remain visible
5. keep the line normalized by the front-end SVG so the small chart emphasizes shape rather than absolute price level

Direction color uses the original simple trend logic:

- look at the latest up to `10` points of the displayed line
- compute end-to-start move
- compute simple slope
- classify as:
  - `up` if move >= about `+1.5%` and slope positive
  - `down` if move <= about `-1.5%` and slope negative
  - otherwise `flat`

Reasoning:

- the mini chart is now meant to show the visual path of a correction, rebound, pause, and possible breakout
- shape-preserving smoothing keeps the spikes and pullbacks that matter for spotting a cup-completion / cheat structure while reducing small day-to-day chop
- the watchlist view should emphasize the shape first; the direction color is only a quick hint

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

Alerts are server-generated and summary-based.

Important rule:

- alerts are recalculated after a forced watchlist refresh, including the daily server timer
- comparison uses the server snapshot in `data/trade/alerts_snapshot.json`
- the first server refresh establishes a baseline without emitting historical alerts

Alert types:

- newly satisfied full trend template
- no longer satisfies full trend template
- latest daily close changed at least `+/-5%`
- latest five-session close return reached at least `+/-8%`
- latest close crossed above or below its `50` day moving average
- latest volume reached at least `150%` or at most `50%` of its `50` day average
- latest close reaches recent `6` month closing high
- latest close reaches recent `6` month closing low

Storage behavior:

- alerts are appended to `data/trade/alerts.json`
- same-symbol, same-message alerts are deduplicated within a calendar day
- the UI shows the latest `50` by default and can query the full history

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
- server-side comparison keeps scheduled refreshes and all browsers consistent

## Notes UX

Each watchlist card has a right-aligned `i` button.

Behavior:

- click opens modal only
- no hover preview card
- note modal stores free-form text, `isHolding`, cost basis and share count on the server
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

- prompt template lives under `templates/` as `templates/analysis_prompt.md`
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
- alert history is shared by the server rather than generated independently by each browser
- watchlist trend mini-chart is a shape-preserving price-structure proxy, not a formal technical score
- default historical horizon is fixed and cache-centered rather than user-configurable everywhere

These choices are deliberate to keep:

- API usage low
- offline support strong
- implementation maintainable
- homepage visually dense but still readable
