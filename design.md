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
  per-symbol notes
- `trenddeck_watchlist_filter_template`
  whether watchlist is filtered to full trend-template matches
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
2. use `MA20` as the base trend line
3. if early rows do not have `MA20` yet, backfill with cumulative mean of all history up to that date
4. run a `3-period EMA` over that base line
5. display only the resulting smoothed series in the watchlist

Direction color:

- look at the latest up to `10` points of the smoothed line
- compute end-to-start move
- compute simple slope
- classify as:
  - `up` if move >= about `+1.5%` and slope positive
  - `down` if move <= about `-1.5%` and slope negative
  - otherwise `flat`

Reasoning:

- MA20 reflects recent price structure better than raw close
- EMA removes jagged turns without drifting too far
- the watchlist view should emphasize direction, not candle noise

## Trend Template Logic

There are two analysis groups:

- base trend template `1-8`
- extended checks `9-13`

The watchlist filter `只看趋势模板` currently means:

- only show stocks where `trendPassCount === trendTotal`
- effectively full pass on the base template set

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

Reason:

- alerts should be visible but not occupy permanent page space
- local snapshot comparison is enough for this product stage

## Notes UX

Each watchlist card has a right-aligned `i` button.

Behavior:

- click opens modal only
- no hover preview card
- button `title` text shows:
  - custom note if it exists
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

## Chart Detail Panel

Detail panel semantics:

- `最新收盘`: latest available daily close, not realtime price
- `较前收盘`: change versus previous day close
- `当日成交量`: volume of the same latest trading day

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
