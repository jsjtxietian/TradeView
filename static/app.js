const STORAGE_KEY = "trenddeck_watchlist";
const GROUPS_STORAGE_KEY = "trenddeck_watchlist_groups";
const CHART_PREFS_KEY = "trenddeck_chart_prefs";
const NOTES_STORAGE_KEY = "trenddeck_symbol_notes";
const TREND_FILTER_STORAGE_KEY = "trenddeck_watchlist_filter_template";
const HOLDING_FILTER_STORAGE_KEY = "trenddeck_watchlist_filter_holding";
const VOLUME_BELOW_MA50_FILTER_STORAGE_KEY = "trenddeck_watchlist_filter_volume_below_ma50";
const ALERTS_STORAGE_KEY = "trenddeck_watchlist_alerts";
const ALERTS_SNAPSHOT_STORAGE_KEY = "trenddeck_watchlist_alerts_snapshot";
const DEFAULT_VISIBLE_BARS = 126;
const WATCHLIST_COLUMN_MIN_WIDTH = 280;
const WATCHLIST_COLUMN_GAP = 10;
const SUBDUED_CHECK_NAMES = new Set(["近期波动明显收窄", "收缩末端成交量极度萎缩"]);
const BUY_CHECK_RULES = {
  "相对回撤": "在所选窗口内，个股与 SPY 分别按收盘价独立计算最大峰谷回撤；个股不得超过 SPY 最大回撤的 2.5 倍。",
  "绝对回撤": "在所选窗口内，按收盘价计算个股最大峰谷回撤，要求小于 35%。",
  "Leaders Bottom First": "定位 SPY 最大回撤段，个股需更早见低，并在 SPY 见低前不再跌破该低点。",
  "当前成交量低于 50 日均量": "最新交易日成交量低于包含该交易日在内的 50 日平均成交量。",
  "近 10 日收盘区间小于 10%": "最近 10 个交易日按最高收盘价和最低收盘价计算：1 - 最低收盘 / 最高收盘。",
  "8 周涨幅达到 100%": "整理开始前约 8 周内，最低价到之后最高价的最大上涨幅度达到 100%。",
  "3-6 周收盘区间不超过 20%": "依次检查最近 15 至 30 个交易日，按最高收盘价和最低收盘价计算整理区间。",
};
const ADVANCED_CHECK_RULES = {
  "回撤小于大盘或形成更高低点": "近63个交易日先定位 SPY 最大回撤段，再比较个股同时间窗内的最大回撤；若个股近期低点抬高，也视为加分项。",
  "30个交易日内放量上涨日明显多于放量下跌日": "近30日仅统计成交量高于 1.05 倍 50 日均量的交易日；强度 = abs(日涨跌幅) * (Volume / VolumeMA50)。",
  "距近期高点回调不超过 35%": "看近6个月最高收盘到当前收盘的回撤幅度；35% 以内更健康，50% 以上视为过深。",
  "近期波动明显收窄": "把近45日拆成三段比较平均振幅，要求逐段收缩；最近10日至少出现 2 根小实体 K 线。",
  "收缩末端成交量极度萎缩": "近10日均量需明显低于 50 日均量，最新成交量接近盘整低位，且近10日振幅不宜超过 8%。",
};
const PATTERN_RISK_RULES = {
  "MVP 动量量价共振": "严格按简化版 MVP：近15个交易日至少12天上涨、累计涨幅至少20%、近15日均量至少高于此前30日均量25%。",
  "Power Play 高位紧凑旗形": "用近8周最大推进幅度近似前期暴涨，再看最近10-30日是否形成不超过20%的紧凑整理；低价股放宽到25%。",
  "VCP 收缩递减结构": "简化版 VCP：近55日切成5段，观察振幅是否逐段收窄、末段是否较首段明显压缩，且末段成交量低于50日均量。",
  "突破后跟进买盘占优": "近10日若识别到放量突破，则统计突破后4日与8日上涨天数；4天至少3涨或8天至少6涨更健康。",
  "近期好收盘天数占优": "近10日按收盘在日内区间的位置计数；收于区间上半部算好收盘，下半部算弱收盘。",
  "未出现三连阴破位": "看最近6日是否出现连续3天更低低点，同时近3日均量高于近20日常态；若出现则视为破位风险。",
  "未出现放量破 20/50 日线": "若最新收盘跌破MA20或MA50，且当日成交量至少高于对应均量25%，则视为放量破均线风险。",
  "近 10 日未见明显放量滞涨": "近10日若出现量比至少1.5倍、日涨跌幅绝对值不超过1%、且收盘不强的交易日，则记为放量滞涨。",
  "近 7-15 日未进入高潮式加速": "在最近7到15日窗口内，若上涨天数占比超过70%且累计涨幅达到25%，视为高潮式加速风险升温。",
};

let toastTimer = null;

const state = {
  watchlist: [],
  watchlistGroups: [],
  notes: {},
  alerts: [],
  alertsSnapshot: {},
  filterTrendTemplateOnly: false,
  filterHoldingOnly: false,
  filterVolumeBelowMA50Only: false,
  buyIndicatorWindow: 63,
  activeNoteSymbol: null,
  draggingGroupId: null,
  draggingSymbol: "",
  selectedSymbol: null,
  chartMode: "close",
  compareBenchmark: false,
  currentChartData: [],
  currentBenchmarkData: [],
  currentBenchmarkSymbol: "SPY",
  tradeReviews: new Map(),
  allTradeReviews: [],
  tradeReviewActive: false,
  tradeReviewSymbol: null,
  tradeReviewId: null,
  summaries: new Map(),
  details: new Map(),
  benchmarkChart: null,
  priceChart: null,
  volumeChart: null,
  benchmarkCandleSeries: null,
  benchmarkLineSeries: null,
  candleSeries: null,
  closeLineSeries: null,
  candleTradePrimitive: null,
  closeLineTradePrimitive: null,
  volumeSeries: null,
  volumeMa50Series: null,
  maSeries: [],
  maVisibility: { MA20: true, MA50: true, MA150: true, MA200: true, VolumeMA50: true },
  crosshairSyncing: false,
  chartWheelBound: false,
  watchlistResizeTimer: null,
};

const elements = {
  watchlistBoard: document.getElementById("watchlistBoard"),
  addSymbolForm: document.getElementById("addSymbolForm"),
  symbolInput: document.getElementById("symbolInput"),
  alertsButton: document.getElementById("alertsButton"),
  alertsDialog: document.getElementById("alertsDialog"),
  alertsList: document.getElementById("alertsList"),
  closeAlertsButton: document.getElementById("closeAlertsButton"),
  watchlistFilterButton: document.getElementById("watchlistFilterButton"),
  watchlistFilterCount: document.getElementById("watchlistFilterCount"),
  watchlistFilterPanel: document.getElementById("watchlistFilterPanel"),
  trendFilterInput: document.getElementById("trendFilterInput"),
  holdingFilterInput: document.getElementById("holdingFilterInput"),
  volumeBelowMA50FilterInput: document.getElementById("volumeBelowMA50FilterInput"),
  clearWatchlistFiltersButton: document.getElementById("clearWatchlistFiltersButton"),
  editGroupsButton: document.getElementById("editGroupsButton"),
  refreshButton: document.getElementById("refreshButton"),
  chartMode: document.getElementById("chartMode"),
  compareBenchmarkToggle: document.getElementById("compareBenchmarkToggle"),
  benchmarkChartPanel: document.getElementById("benchmarkChartPanel"),
  benchmarkChartLabel: document.getElementById("benchmarkChartLabel"),
  benchmarkChartContainer: document.getElementById("benchmarkChartContainer"),
  stockChartLabel: document.getElementById("stockChartLabel"),
  maToggleGroup: document.getElementById("maToggleGroup"),
  chartHoverCard: document.getElementById("chartHoverCard"),
  messageBar: document.getElementById("messageBar"),
  detailSection: document.getElementById("detailSection"),
  chartTitle: document.getElementById("chartTitle"),
  chartHeadlineStats: document.getElementById("chartHeadlineStats"),
  chartHoldingStats: document.getElementById("chartHoldingStats"),
  trendChecks: document.getElementById("trendChecks"),
  advancedTrendChecks: document.getElementById("advancedTrendChecks"),
  patternRiskChecks: document.getElementById("patternRiskChecks"),
  tempAdvancedTrendChecks: document.getElementById("tempAdvancedTrendChecks"),
  buyIndicatorWindow: document.getElementById("buyIndicatorWindow"),
  chartStatusNote: document.getElementById("chartStatusNote"),
  priceChartContainer: document.getElementById("priceChartContainer"),
  volumeChartContainer: document.getElementById("volumeChartContainer"),
  groupEditorDialog: document.getElementById("groupEditorDialog"),
  groupEditorList: document.getElementById("groupEditorList"),
  addGroupRowButton: document.getElementById("addGroupRowButton"),
  cancelGroupEditButton: document.getElementById("cancelGroupEditButton"),
  saveGroupEditButton: document.getElementById("saveGroupEditButton"),
  noteDialog: document.getElementById("noteDialog"),
  noteDialogTitle: document.getElementById("noteDialogTitle"),
  noteHoldingCheckbox: document.getElementById("noteHoldingCheckbox"),
  noteTextarea: document.getElementById("noteTextarea"),
  noteCostBasisInput: document.getElementById("noteCostBasisInput"),
  noteSharesInput: document.getElementById("noteSharesInput"),
  deleteSymbolButton: document.getElementById("deleteSymbolButton"),
  copyPromptButton: document.getElementById("copyPromptButton"),
  tradeReviewButton: document.getElementById("tradeReviewButton"),
  tradeReviewDialog: document.getElementById("tradeReviewDialog"),
  tradeReviewForm: document.getElementById("tradeReviewForm"),
  tradeReviewTitle: document.getElementById("tradeReviewTitle"),
  tradeReviewChooser: document.getElementById("tradeReviewChooser"),
  tradeReviewEditor: document.getElementById("tradeReviewEditor"),
  tradeReviewEditorTitle: document.getElementById("tradeReviewEditorTitle"),
  tradeSymbolSelect: document.getElementById("tradeSymbolSelect"),
  tradeReviewSelect: document.getElementById("tradeReviewSelect"),
  manageTradeReviewButton: document.getElementById("manageTradeReviewButton"),
  backToTradeReviewChooserButton: document.getElementById("backToTradeReviewChooserButton"),
  createTradeReviewButton: document.getElementById("createTradeReviewButton"),
  tradeDateInput: document.getElementById("tradeDateInput"),
  tradePriceInput: document.getElementById("tradePriceInput"),
  tradeQuantityInput: document.getElementById("tradeQuantityInput"),
  tradeNoteInput: document.getElementById("tradeNoteInput"),
  tradeRecordCount: document.getElementById("tradeRecordCount"),
  tradeRecordList: document.getElementById("tradeRecordList"),
  activateTradeReviewButton: document.getElementById("activateTradeReviewButton"),
  closeTradeReviewButton: document.getElementById("closeTradeReviewButton"),
  cancelNoteButton: document.getElementById("cancelNoteButton"),
  saveNoteButton: document.getElementById("saveNoteButton"),
  toast: document.getElementById("toast"),
};

init().catch((error) => {
  showMessage(error.message || String(error), true);
});

async function init() {
  bindEvents();
  const [config, tradesPayload] = await Promise.all([
    fetchJson("/api/config"),
    fetchJson("/api/trades"),
  ]);
  state.allTradeReviews = tradesPayload.reviews || [];
  loadChartPrefs();
  state.notes = loadStoredNotes();
  state.alerts = loadStoredAlerts();
  state.alertsSnapshot = loadStoredAlertsSnapshot();
  state.filterTrendTemplateOnly = loadStoredTrendFilter();
  state.filterHoldingOnly = loadStoredHoldingFilter();
  state.filterVolumeBelowMA50Only = loadStoredVolumeBelowMA50Filter();
  state.watchlistGroups = loadStoredWatchlistGroups(
    normalizeWatchlistGroups(config.watchlistGroups || []),
  );
  elements.chartMode.value = state.chartMode;
  elements.compareBenchmarkToggle.checked = state.compareBenchmark;
  syncWatchlistFilters();
  renderAlerts();

  const storedWatchlist = loadStoredWatchlist();
  state.watchlist = storedWatchlist.length ? storedWatchlist : config.defaultWatchlist || ["AAPL"];
  state.selectedSymbol = state.watchlist[0] || null;
  await refreshSummaries();
}

function bindEvents() {
  elements.addSymbolForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const candidate = normalizeSymbol(elements.symbolInput.value);
    if (!candidate) {
      showToast("请输入有效的股票代码。", true);
      return;
    }
    if (state.watchlist.includes(candidate)) {
      showToast(`${candidate} 已在自选股里。`);
      elements.symbolInput.value = "";
      return;
    }
    state.watchlist.push(candidate);
    state.selectedSymbol = candidate;
    removeSymbolFromGroups(candidate);
    persistWatchlistGroups();
    persistWatchlist();
    elements.symbolInput.value = "";
    await refreshSingleSymbol(candidate, true);
    showToast(`${candidate} 已添加。`);
  });

  elements.refreshButton.addEventListener("click", async () => {
    state.summaries.clear();
    state.details.clear();
    await refreshSummaries(true);
  });

  elements.editGroupsButton.addEventListener("click", () => {
    openGroupEditor();
  });

  elements.alertsButton.addEventListener("click", () => {
    elements.alertsDialog.showModal();
  });

  elements.closeAlertsButton.addEventListener("click", () => {
    elements.alertsDialog.close();
  });

  elements.watchlistFilterButton.addEventListener("click", () => {
    const shouldOpen = elements.watchlistFilterPanel.hidden;
    elements.watchlistFilterPanel.hidden = !shouldOpen;
    elements.watchlistFilterButton.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
  });

  elements.trendFilterInput.addEventListener("change", () => {
    updateWatchlistFilter("filterTrendTemplateOnly", elements.trendFilterInput.checked);
  });

  elements.holdingFilterInput.addEventListener("change", () => {
    updateWatchlistFilter("filterHoldingOnly", elements.holdingFilterInput.checked);
  });

  elements.volumeBelowMA50FilterInput.addEventListener("change", () => {
    updateWatchlistFilter(
      "filterVolumeBelowMA50Only",
      elements.volumeBelowMA50FilterInput.checked,
    );
  });

  elements.clearWatchlistFiltersButton.addEventListener("click", () => {
    state.filterTrendTemplateOnly = false;
    state.filterHoldingOnly = false;
    state.filterVolumeBelowMA50Only = false;
    persistTrendFilter();
    persistHoldingFilter();
    persistVolumeBelowMA50Filter();
    syncWatchlistFilters();
    renderWatchlist();
    syncSelectionWithFilter().catch((error) => {
      showMessage(error.message || String(error), true);
    });
  });

  elements.addGroupRowButton.addEventListener("click", () => {
    appendGroupEditorRow();
  });

  elements.cancelGroupEditButton.addEventListener("click", () => {
    elements.groupEditorDialog.close();
  });

  elements.saveGroupEditButton.addEventListener("click", () => {
    saveGroupEditor().catch((error) => {
      showMessage(error.message || String(error), true);
    });
  });

  elements.cancelNoteButton.addEventListener("click", () => {
    elements.noteDialog.close();
  });

  elements.deleteSymbolButton.addEventListener("click", () => {
    deleteActiveSymbol().catch((error) => {
      showMessage(error.message || String(error), true);
    });
  });

  elements.copyPromptButton.addEventListener("click", () => {
    copyPromptForActiveSymbol().catch((error) => {
      showMessage(error.message || String(error), true);
    });
  });

  elements.tradeReviewButton.addEventListener("click", () => {
    if (state.tradeReviewActive) {
      const symbol = state.tradeReviewSymbol;
      exitTradeReview();
      showToast(`${symbol} 已退出复盘模式。`);
      return;
    }
    openTradeReview().catch((error) => {
      showMessage(error.message || String(error), true);
    });
  });

  elements.activateTradeReviewButton.addEventListener("click", () => {
    activateTradeReview().catch((error) => {
      showToast(error.message || String(error), true);
    });
  });

  elements.manageTradeReviewButton.addEventListener("click", () => {
    openTradeReviewEditor(elements.tradeReviewSelect.value).catch((error) => {
      showToast(error.message || String(error), true);
    });
  });

  elements.backToTradeReviewChooserButton.addEventListener("click", () => {
    showTradeReviewChooser();
  });

  elements.closeTradeReviewButton.addEventListener("click", () => {
    elements.tradeReviewDialog.close();
  });

  elements.tradeReviewForm.addEventListener("submit", (event) => {
    event.preventDefault();
    saveTradeRecord().catch((error) => {
      showToast(error.message || String(error), true);
    });
  });

  elements.tradeDateInput.addEventListener("change", () => {
    const row = state.currentChartData.find((item) => item.Date === elements.tradeDateInput.value);
    if (row?.Close != null) {
      elements.tradePriceInput.value = Number(row.Close).toFixed(4);
    }
  });

  elements.createTradeReviewButton.addEventListener("click", () => {
    createTradeReview().catch((error) => {
      showToast(error.message || String(error), true);
    });
  });

  elements.tradeRecordList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-trade-delete]");
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    deleteTradeRecord(button.dataset.tradeDelete).catch((error) => {
      showToast(error.message || String(error), true);
    });
  });

  elements.saveNoteButton.addEventListener("click", () => {
    saveNote();
  });

  elements.noteDialog.addEventListener("close", () => {
    state.activeNoteSymbol = null;
  });

  elements.chartMode.addEventListener("change", () => {
    state.chartMode = elements.chartMode.value;
    persistChartPrefs();
    applyChartMode();
  });

  elements.compareBenchmarkToggle.addEventListener("change", () => {
    state.compareBenchmark = elements.compareBenchmarkToggle.checked;
    persistChartPrefs();
    applyChartMode();
    syncChartRanges(state.priceChart?.timeScale().getVisibleLogicalRange());
    resizeCharts();
  });

  elements.maToggleGroup.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) {
      return;
    }
    state.maVisibility[target.dataset.ma] = target.checked;
    persistChartPrefs();
    applyMaVisibility();
    updateHoverCard(state.currentChartData.at(-1), null, true);
  });

  elements.buyIndicatorWindow.addEventListener("click", (event) => {
    const button = event.target.closest("[data-buy-window]");
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    state.buyIndicatorWindow = Number(button.dataset.buyWindow) || 63;
    renderBuyIndicatorChecks();
  });

  window.addEventListener("resize", () => {
    resizeCharts();
    queueWatchlistLayoutRefresh();
  });

  document.addEventListener("click", (event) => {
    if (
      !elements.watchlistFilterPanel.hidden &&
      !event.target.closest(".watchlist-filter")
    ) {
      closeWatchlistFilterPanel();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !elements.watchlistFilterPanel.hidden) {
      closeWatchlistFilterPanel();
      elements.watchlistFilterButton.focus();
    }
  });
}

async function refreshSummaries(forceRefresh = false) {
  if (!state.watchlist.length) {
    renderWatchlist();
    clearDetail();
    return;
  }

  setRefreshLoading(true);
  try {
    const query = encodeURIComponent(state.watchlist.join(","));
    const payload = await fetchJson(`/api/watchlist/summary?symbols=${query}&refresh=${forceRefresh ? "1" : "0"}`);
    updateAlertsFromSummary(payload.items || []);
    state.summaries.clear();
    for (const item of payload.items || []) {
      state.summaries.set(item.symbol, item);
    }
    hideMessage();
    renderWatchlist();

    const visibleSymbols = getVisibleWatchlistSymbols();
    if (hasActiveWatchlistFilters() && (!state.selectedSymbol || !visibleSymbols.includes(state.selectedSymbol))) {
      state.selectedSymbol = visibleSymbols[0] || null;
    }

    const selectionPool = hasActiveWatchlistFilters() ? visibleSymbols : state.watchlist;
    if (!state.selectedSymbol || !selectionPool.includes(state.selectedSymbol)) {
      const firstAvailable = selectionPool.find((symbol) => state.summaries.get(symbol)?.data);
      state.selectedSymbol = firstAvailable || selectionPool[0] || null;
    }

    if (state.selectedSymbol && state.summaries.get(state.selectedSymbol)?.data) {
      await loadDetail(state.selectedSymbol, false);
    } else {
      clearDetail();
    }
  } catch (error) {
    showMessage(error.message || String(error), true);
  } finally {
    setRefreshLoading(false);
  }
}

async function refreshSingleSymbol(symbol, forceRefresh = false) {
  setRefreshLoading(true);
  try {
    const summaryPayload = await fetchJson(
      `/api/watchlist/summary?symbols=${encodeURIComponent(symbol)}&refresh=${forceRefresh ? "1" : "0"}`,
    );
    const summaryItem = summaryPayload.items?.[0] || null;
    if (summaryItem) {
      state.summaries.set(symbol, summaryItem);
      updateAlertsFromSummary([summaryItem]);
    }

    if (summaryItem?.data) {
      const detail = await fetchJson(`/api/symbol/${encodeURIComponent(symbol)}?refresh=${forceRefresh ? "1" : "0"}`);
      state.details.set(symbol, detail);
      hideMessage();
      renderWatchlist();
      renderSelectedDetail();
      return;
    }

    renderWatchlist();
    if (summaryItem?.error) {
      throw new Error(summaryItem.error);
    }
  } catch (error) {
    showMessage(error.message || String(error), true);
  } finally {
    setRefreshLoading(false);
  }
}

function renderWatchlist() {
  elements.watchlistBoard.innerHTML = "";

  if (!state.watchlist.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "当前没有自选股，请先添加股票。";
    elements.watchlistBoard.appendChild(empty);
    return;
  }

  const sections = buildWatchlistSections();
  if (!sections.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = getWatchlistEmptyMessage();
    elements.watchlistBoard.appendChild(empty);
    return;
  }

  renderWatchlistMasonry(sections);
}

function renderWatchlistMasonry(sections) {
  const columnCount = getWatchlistColumnCount(sections.length);
  const columns = Array.from({ length: columnCount }, () => {
    const column = document.createElement("div");
    column.className = "watchlist-column";
    elements.watchlistBoard.appendChild(column);
    return column;
  });
  const columnHeights = new Array(columnCount).fill(0);

  for (const section of sections) {
    const panel = renderWatchlistSection(section);
    const targetIndex = getShortestColumnIndex(columnHeights);
    columns[targetIndex].appendChild(panel);
    columnHeights[targetIndex] = columns[targetIndex].scrollHeight;
  }

  for (const column of columns) {
    const dropzone = document.createElement("div");
    dropzone.className = "watchlist-column-dropzone";
    bindColumnDropzone(dropzone, column);
    column.appendChild(dropzone);
  }
}

function getWatchlistColumnCount(sectionCount) {
  const boardWidth =
    elements.watchlistBoard.clientWidth
    || elements.watchlistBoard.parentElement?.clientWidth
    || WATCHLIST_COLUMN_MIN_WIDTH;
  const count = Math.max(
    1,
    Math.floor((boardWidth + WATCHLIST_COLUMN_GAP) / (WATCHLIST_COLUMN_MIN_WIDTH + WATCHLIST_COLUMN_GAP)),
  );
  return Math.max(1, Math.min(sectionCount || 1, count));
}

function getShortestColumnIndex(columnHeights) {
  let shortestIndex = 0;
  for (let index = 1; index < columnHeights.length; index += 1) {
    if (columnHeights[index] < columnHeights[shortestIndex]) {
      shortestIndex = index;
    }
  }
  return shortestIndex;
}

function queueWatchlistLayoutRefresh() {
  if (!state.watchlist.length) {
    return;
  }
  if (state.watchlistResizeTimer) {
    clearTimeout(state.watchlistResizeTimer);
  }
  state.watchlistResizeTimer = setTimeout(() => {
    state.watchlistResizeTimer = null;
    renderWatchlist();
  }, 120);
}

function updateWatchlistFilter(stateKey, enabled) {
  state[stateKey] = enabled;
  persistTrendFilter();
  persistHoldingFilter();
  persistVolumeBelowMA50Filter();
  syncWatchlistFilters();
  renderWatchlist();
  syncSelectionWithFilter().catch((error) => {
    showMessage(error.message || String(error), true);
  });
}

function closeWatchlistFilterPanel() {
  elements.watchlistFilterPanel.hidden = true;
  elements.watchlistFilterButton.setAttribute("aria-expanded", "false");
}

async function loadDetail(symbol, forceRefresh = false) {
  if (!symbol) {
    clearDetail();
    return;
  }

  try {
    const [detail, tradesPayload] = await Promise.all([
      !forceRefresh && state.details.get(symbol)
        ? state.details.get(symbol)
        : fetchJson(`/api/symbol/${encodeURIComponent(symbol)}?refresh=${forceRefresh ? "1" : "0"}`),
      fetchJson(`/api/trades/${encodeURIComponent(symbol)}`),
    ]);
    state.details.set(symbol, detail);
    state.tradeReviews.set(symbol, tradesPayload.reviews || []);
    hideMessage();
    renderSelectedDetail();
  } catch (error) {
    showMessage(error.message || String(error), true);
  }
}

function renderSelectedDetail() {
  const detail = state.details.get(state.selectedSymbol);
  if (!detail) {
    clearDetail();
    return;
  }

  elements.detailSection.hidden = false;
  elements.chartTitle.textContent = detail.symbol;
  renderChartHeadlineStats(detail);
  renderChartStatus(detail);
  renderChecks(elements.trendChecks, detail.trendChecks);
  renderBuyIndicatorChecks();
  renderChecks(elements.patternRiskChecks, detail.patternRiskChecks, {
    ruleTooltips: PATTERN_RISK_RULES,
  });
  renderChecks(elements.tempAdvancedTrendChecks, detail.tempAdvancedTrendChecks, {
    trimPrefix: true,
    ruleTooltips: ADVANCED_CHECK_RULES,
    subduedNames: SUBDUED_CHECK_NAMES,
  });
  logDetailMessages(detail);
  renderMainChart(detail);
  syncMaToggleInputs();
}

function renderBuyIndicatorChecks() {
  const detail = state.details.get(state.selectedSymbol);
  if (!detail) {
    elements.advancedTrendChecks.innerHTML = "";
    return;
  }
  const windowKey = String(state.buyIndicatorWindow);
  const groups = detail.buyIndicatorGroupsByWindow?.[windowKey] || [];
  for (const button of elements.buyIndicatorWindow.querySelectorAll("[data-buy-window]")) {
    button.classList.toggle(
      "active",
      Number(button.dataset.buyWindow) === state.buyIndicatorWindow,
    );
  }
  renderBuyIndicatorGroups(groups);
}

function renderBuyIndicatorGroups(groups) {
  elements.advancedTrendChecks.innerHTML = groups.map(renderBuyIndicatorGroup).join("");
}

function renderBuyIndicatorGroup(group) {
  return `
    <section class="buy-indicator-group${group.observational ? " observational" : ""}${group.key === "common_volume" ? " common-condition" : ""}">
      <header>
        <span>
          <strong>${escapeHtml(group.title)}</strong>
          ${group.subtitle ? `<small>${escapeHtml(group.subtitle)}</small>` : ""}
        </span>
        ${group.observational ? "" : renderCompactCheckState(group.passed)}
      </header>
      <div class="buy-indicator-items">
        ${(group.items || []).map((item) => `
          <div class="buy-indicator-item">
            <div>
              <div class="buy-indicator-item-title">
                <strong>${escapeHtml(item.name)}</strong>
                ${BUY_CHECK_RULES[item.name] ? `
                  <button
                    type="button"
                    class="check-rule-button"
                    title="${escapeHtml(BUY_CHECK_RULES[item.name])}"
                    aria-label="${escapeHtml(item.name)}计算规则"
                  >i</button>
                ` : ""}
              </div>
              <p>${escapeHtml(item.detail || "")}</p>
            </div>
            ${renderCompactCheckState(item.passed)}
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function renderCompactCheckState(passed) {
  const label = passed === true ? "通过" : passed === false ? "未通过" : "待确认";
  const icon = passed === true ? "✓" : passed === false ? "✕" : "?";
  const stateClass = passed === true ? "pass" : passed === false ? "fail" : "pending";
  return `<span class="check-state ${stateClass}" title="${label}" aria-label="${label}">${icon}</span>`;
}

function normalizeWatchlistGroups(groups) {
  if (!Array.isArray(groups)) {
    return [];
  }
  const seen = new Set();
  return groups
    .map((group) => {
      const id = normalizeSymbol(group?.id || "");
      const name = String(group?.name || "").trim();
      const symbols = Array.isArray(group?.symbols)
        ? group.symbols.map(normalizeSymbol).filter(Boolean)
        : [];
      if (!id || !name || seen.has(id)) {
        return null;
      }
      seen.add(id);
      return { id, name, symbols };
    })
    .filter(Boolean);
}

function createGroupId() {
  return `group_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function openGroupEditor() {
  elements.groupEditorList.innerHTML = "";
  if (!state.watchlistGroups.length) {
    appendGroupEditorRow();
  } else {
    state.watchlistGroups.forEach((group) => appendGroupEditorRow(group));
  }
  elements.groupEditorDialog.showModal();
}

function appendGroupEditorRow(group = null) {
  const row = document.createElement("div");
  row.className = "group-editor-row";
  row.dataset.groupId = group?.id || createGroupId();
  row.innerHTML = `
    <label>
      <span>分组名</span>
      <input type="text" data-field="name" placeholder="例如 AI 区" value="${escapeHtml(group?.name || "")}" />
    </label>
    <label>
      <span>股票代码</span>
      <input type="text" data-field="symbols" placeholder="例如 NVDA, MSFT, MU" value="${escapeHtml(getAssignedSymbolsForGroup(group).join(", "))}" />
    </label>
    <div class="group-editor-row-actions">
      <button type="button" class="secondary group-editor-up">上移</button>
      <button type="button" class="secondary group-editor-down">下移</button>
      <button type="button" class="secondary group-editor-remove">删除</button>
    </div>
  `;
  row.querySelector(".group-editor-up").addEventListener("click", () => {
    const previous = row.previousElementSibling;
    if (previous) {
      elements.groupEditorList.insertBefore(row, previous);
    }
  });
  row.querySelector(".group-editor-down").addEventListener("click", () => {
    const next = row.nextElementSibling;
    if (next) {
      elements.groupEditorList.insertBefore(next, row);
    }
  });
  row.querySelector(".group-editor-remove").addEventListener("click", () => {
    row.remove();
  });
  elements.groupEditorList.appendChild(row);
}

async function saveGroupEditor() {
  const nextGroups = [];
  const seenNames = new Set();
  for (const row of elements.groupEditorList.querySelectorAll(".group-editor-row")) {
    const nameInput = row.querySelector("[data-field='name']");
    const symbolsInput = row.querySelector("[data-field='symbols']");
    const name = String(nameInput?.value || "").trim();
    const symbols = String(symbolsInput?.value || "")
      .split(",")
      .map(normalizeSymbol)
      .filter(Boolean);
    if (!name || seenNames.has(name)) {
      continue;
    }
    seenNames.add(name);
    nextGroups.push({
      id: row.dataset.groupId || createGroupId(),
      name,
      symbols,
    });
  }

  const nextWatchlist = [...state.watchlist];
  const newSymbols = [];
  for (const group of nextGroups) {
    for (const symbol of group.symbols) {
      if (!nextWatchlist.includes(symbol)) {
        nextWatchlist.push(symbol);
        newSymbols.push(symbol);
      }
    }
  }

  state.watchlist = nextWatchlist;
  state.watchlistGroups = normalizeWatchlistGroups(nextGroups);
  if (newSymbols.length) {
    state.selectedSymbol = newSymbols[0];
  }
  persistWatchlist();
  persistWatchlistGroups();
  elements.groupEditorDialog.close();
  if (newSymbols.length) {
    renderWatchlist();
    for (const symbol of newSymbols) {
      await refreshSingleSymbol(symbol, true);
    }
    showToast(`已新增并拉新 ${newSymbols.join(", ")}。`);
    return;
  }
  renderWatchlist();
  showToast("分组已保存。");
}

function getAssignedSymbolsForGroup(group) {
  if (!group) {
    return [];
  }
  const watchlistSet = new Set(state.watchlist);
  return (group.symbols || []).filter((symbol) => watchlistSet.has(symbol));
}

function removeSymbolFromGroups(symbol) {
  state.watchlistGroups = state.watchlistGroups.map((group) => ({
    ...group,
    symbols: (group.symbols || []).filter((item) => item !== symbol),
  }));
}

function findGroupForSymbol(symbol) {
  return state.watchlistGroups.find((group) => group.symbols.includes(symbol)) || null;
}

function buildWatchlistSections() {
  const sections = state.watchlistGroups
    .map((group) => ({
      id: group.id,
      name: group.name,
      symbols: filterWatchlistSymbols(getAssignedSymbolsForGroup(group)),
      emptyText: "这个分组还没有股票。",
      draggable: true,
    }))
    .filter((section) => section.symbols.length || !hasActiveWatchlistFilters());

  const ungroupedSymbols = filterWatchlistSymbols(
    state.watchlist.filter((symbol) => !findGroupForSymbol(symbol)),
  );
  if (ungroupedSymbols.length) {
    sections.push({
      id: "ungrouped",
      name: "未分组",
      symbols: ungroupedSymbols,
      emptyText: "新添加但尚未分组的股票会先放在这里。",
      draggable: false,
    });
  }

  return sections;
}

function renderWatchlistSection(section) {
  const panel = document.createElement("section");
  panel.className = "watchlist-group";
  panel.dataset.sectionId = section.id;
  if (section.draggable) {
    panel.classList.add("draggable");
    panel.dataset.groupId = section.id;
  }

  const header = document.createElement("header");
  header.className = "watchlist-group-header";
  header.innerHTML = `<h3>${section.name}</h3>`;
  if (section.draggable) {
    header.classList.add("watchlist-group-drag-handle");
    header.draggable = true;
    bindGroupDrag(header, panel, section.id);
  }

  const body = document.createElement("div");
  body.className = "watchlist-group-body";
  body.dataset.sectionId = section.id;
  bindSymbolContainerDrop(body, section.id);

  panel.appendChild(header);

  if (!section.symbols.length) {
    const empty = document.createElement("div");
    empty.className = "watchlist-group-empty";
    empty.textContent = section.emptyText;
    body.appendChild(empty);
    body.appendChild(createSymbolDropzone(section.id));
    panel.appendChild(body);
    return panel;
  }

  for (const symbol of section.symbols) {
    body.appendChild(renderWatchlistItem(symbol, section.id));
  }
  body.appendChild(createSymbolDropzone(section.id));

  panel.appendChild(body);
  return panel;
}

function bindGroupDrag(handle, panel, groupId) {
  handle.addEventListener("dragstart", (event) => {
    if (state.draggingSymbol) {
      event.preventDefault();
      return;
    }
    state.draggingGroupId = groupId;
    elements.watchlistBoard.classList.add("dragging-groups");
    panel.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", groupId);
  });

  handle.addEventListener("dragend", () => {
    state.draggingGroupId = null;
    elements.watchlistBoard.classList.remove("dragging-groups");
    panel.classList.remove("dragging");
    clearGroupDragOverStates();
  });

  panel.addEventListener("dragover", (event) => {
    if (!state.draggingGroupId || state.draggingGroupId === groupId || state.draggingSymbol) {
      return;
    }
    event.preventDefault();
    clearGroupDragOverStates(panel);
    panel.classList.add("drag-over");
  });

  panel.addEventListener("dragleave", () => {
    panel.classList.remove("drag-over");
  });

  panel.addEventListener("drop", (event) => {
    event.preventDefault();
    clearGroupDragOverStates();
    const sourceId = state.draggingGroupId;
    if (!sourceId || sourceId === groupId) {
      return;
    }
    moveGroupBefore(sourceId, groupId);
  });
}

function bindColumnDropzone(dropzone, column) {
  dropzone.addEventListener("dragover", (event) => {
    if (!state.draggingGroupId || state.draggingSymbol) {
      return;
    }
    event.preventDefault();
    clearGroupDragOverStates(dropzone);
    dropzone.classList.add("drag-over");
  });

  dropzone.addEventListener("dragleave", () => {
    dropzone.classList.remove("drag-over");
  });

  dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    clearGroupDragOverStates();
    const sourceId = state.draggingGroupId;
    if (!sourceId) {
      return;
    }
    const lastDraggableGroup = [...column.querySelectorAll(".watchlist-group[data-group-id]")]
      .map((node) => node.dataset.groupId)
      .filter(Boolean)
      .filter((groupId) => groupId !== sourceId)
      .at(-1);
    if (!lastDraggableGroup) {
      moveGroupToEnd(sourceId);
      return;
    }
    moveGroupAfter(sourceId, lastDraggableGroup);
  });
}

function clearGroupDragOverStates(exceptNode = null) {
  for (const node of elements.watchlistBoard.querySelectorAll(".watchlist-group.drag-over, .watchlist-column-dropzone.drag-over")) {
    if (node !== exceptNode) {
      node.classList.remove("drag-over");
    }
  }
}

function commitGroupOrder(nextGroups) {
  state.watchlistGroups = nextGroups;
  persistWatchlistGroups();
  renderWatchlist();
  showToast("分组顺序已更新。");
}

function createSymbolDropzone(sectionId) {
  const dropzone = document.createElement("div");
  dropzone.className = "watchlist-item-dropzone";
  dropzone.dataset.sectionId = sectionId;
  bindSymbolDropzone(dropzone, sectionId);
  return dropzone;
}

function bindSymbolContainerDrop(container, sectionId) {
  container.addEventListener("dragover", (event) => {
    if (!state.draggingSymbol) {
      return;
    }
    if (event.target.closest(".watchlist-item") || event.target.closest(".watchlist-item-dropzone")) {
      return;
    }
    event.preventDefault();
    clearSymbolDragOverStates(container);
    container.classList.add("drag-over-end");
  });

  container.addEventListener("dragleave", (event) => {
    if (event.currentTarget === event.target) {
      container.classList.remove("drag-over-end");
    }
  });

  container.addEventListener("drop", (event) => {
    if (!state.draggingSymbol) {
      return;
    }
    if (event.target.closest(".watchlist-item") || event.target.closest(".watchlist-item-dropzone")) {
      return;
    }
    event.preventDefault();
    clearSymbolDragOverStates();
    moveSymbolToSection(state.draggingSymbol, sectionId, null, "end");
  });
}

function bindSymbolDropzone(dropzone, sectionId) {
  dropzone.addEventListener("dragover", (event) => {
    if (!state.draggingSymbol) {
      return;
    }
    event.preventDefault();
    clearSymbolDragOverStates(dropzone);
    dropzone.classList.add("drag-over");
  });

  dropzone.addEventListener("dragleave", () => {
    dropzone.classList.remove("drag-over");
  });

  dropzone.addEventListener("drop", (event) => {
    if (!state.draggingSymbol) {
      return;
    }
    event.preventDefault();
    clearSymbolDragOverStates();
    moveSymbolToSection(state.draggingSymbol, sectionId, null, "end");
  });
}

function clearSymbolDragOverStates(exceptNode = null) {
  for (const node of elements.watchlistBoard.querySelectorAll(".watchlist-item.drag-over-before, .watchlist-item.drag-over-after, .watchlist-item-dropzone.drag-over, .watchlist-group-body.drag-over-end")) {
    if (node !== exceptNode) {
      node.classList.remove("drag-over-before", "drag-over-after", "drag-over", "drag-over-end");
    }
  }
}

function moveGroupBefore(sourceId, targetId) {
  const groups = [...state.watchlistGroups];
  const sourceIndex = groups.findIndex((group) => group.id === sourceId);
  const targetIndex = groups.findIndex((group) => group.id === targetId);
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) {
    return;
  }
  const [moved] = groups.splice(sourceIndex, 1);
  const nextTargetIndex = groups.findIndex((group) => group.id === targetId);
  groups.splice(nextTargetIndex, 0, moved);
  commitGroupOrder(groups);
}

function moveGroupAfter(sourceId, targetId) {
  const groups = [...state.watchlistGroups];
  const sourceIndex = groups.findIndex((group) => group.id === sourceId);
  const targetIndex = groups.findIndex((group) => group.id === targetId);
  if (sourceIndex < 0 || targetIndex < 0 || sourceId === targetId) {
    return;
  }
  const [moved] = groups.splice(sourceIndex, 1);
  const nextTargetIndex = groups.findIndex((group) => group.id === targetId);
  groups.splice(nextTargetIndex + 1, 0, moved);
  commitGroupOrder(groups);
}

function moveGroupToEnd(sourceId) {
  const groups = [...state.watchlistGroups];
  const sourceIndex = groups.findIndex((group) => group.id === sourceId);
  if (sourceIndex < 0 || sourceIndex === groups.length - 1) {
    return;
  }
  const [moved] = groups.splice(sourceIndex, 1);
  groups.push(moved);
  commitGroupOrder(groups);
}

function renderWatchlistItem(symbol, sectionId) {
  const item = state.summaries.get(symbol);
  const card = document.createElement("article");
  card.className = `watchlist-item${symbol === state.selectedSymbol ? " selected" : ""}${getHoldingForSymbol(symbol) ? " holding" : ""}`;
  card.tabIndex = 0;
  card.draggable = true;
  card.dataset.symbol = symbol;
  card.dataset.sectionId = sectionId;
  bindWatchlistItemDrag(card, symbol, sectionId);

  const openDetail = async () => {
    if (state.selectedSymbol === symbol) {
      return;
    }
    if (state.tradeReviewActive) {
      exitTradeReview();
    }
    state.selectedSymbol = symbol;
    renderWatchlist();
    await loadDetail(symbol, false);
  };

  card.addEventListener("click", () => {
    openDetail();
  });
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openDetail();
    }
  });

  if (item?.data) {
    const { data } = item;
    const direction = data.trendSparklineDirection || "flat";
    const sparkline = buildSparklineSvg(data.trendSparklineValues || [], direction);
    card.innerHTML = `
      <div class="watchlist-item-line">
        <div class="watchlist-trend-block">
          <strong class="watchlist-symbol">${symbol}</strong>
          <span class="watchlist-template-chip">${data.trendPassCount}/${data.trendTotal}</span>
        </div>
        <div class="watchlist-sparkline-shell" aria-hidden="true">
          ${sparkline}
        </div>
        ${renderNoteButton(symbol)}
      </div>
      ${renderHoldingInline(symbol)}
    `;
    bindNoteButton(card, symbol);
    return card;
  }

  card.innerHTML = `
    <div class="watchlist-item-line">
      <div class="watchlist-trend-block">
        <strong class="watchlist-symbol">${symbol}</strong>
        <span class="watchlist-inline-metric error">${item?.error || "加载失败"}</span>
      </div>
      ${renderNoteButton(symbol)}
    </div>
  `;
  bindNoteButton(card, symbol);
  return card;
}

function bindWatchlistItemDrag(card, symbol, sectionId) {
  card.addEventListener("dragstart", (event) => {
    state.draggingSymbol = symbol;
    elements.watchlistBoard.classList.add("dragging-symbols");
    card.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", symbol);
    event.stopPropagation();
  });

  card.addEventListener("dragend", () => {
    state.draggingSymbol = null;
    elements.watchlistBoard.classList.remove("dragging-symbols");
    card.classList.remove("dragging");
    clearSymbolDragOverStates();
  });

  card.addEventListener("dragover", (event) => {
    if (!state.draggingSymbol || state.draggingSymbol === symbol) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const placement = getSymbolDropPlacement(card, event.clientY);
    clearSymbolDragOverStates(card);
    card.classList.add(placement === "before" ? "drag-over-before" : "drag-over-after");
  });

  card.addEventListener("dragleave", () => {
    card.classList.remove("drag-over-before", "drag-over-after");
  });

  card.addEventListener("drop", (event) => {
    if (!state.draggingSymbol || state.draggingSymbol === symbol) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const placement = getSymbolDropPlacement(card, event.clientY);
    clearSymbolDragOverStates();
    moveSymbolToSection(state.draggingSymbol, sectionId, symbol, placement);
  });
}

function getSymbolDropPlacement(card, clientY) {
  const rect = card.getBoundingClientRect();
  return clientY < rect.top + rect.height / 2 ? "before" : "after";
}

function moveSymbolToSection(symbol, targetSectionId, targetSymbol = null, placement = "end") {
  const normalizedSymbol = normalizeSymbol(symbol);
  if (!normalizedSymbol || !state.watchlist.includes(normalizedSymbol)) {
    return;
  }

  const nextGroups = state.watchlistGroups.map((group) => ({
    ...group,
    symbols: (group.symbols || []).filter((item) => item !== normalizedSymbol),
  }));

  if (targetSectionId !== "ungrouped") {
    const targetGroup = nextGroups.find((group) => group.id === targetSectionId);
    if (!targetGroup) {
      return;
    }
    insertSymbolIntoList(targetGroup.symbols, normalizedSymbol, targetSymbol, placement);
  }

  const nextWatchlist = buildWatchlistOrder(nextGroups, normalizedSymbol, targetSectionId, targetSymbol, placement);
  if (targetSectionId === "ungrouped" && !nextWatchlist.includes(normalizedSymbol)) {
    nextWatchlist.push(normalizedSymbol);
  }

  commitWatchlistArrangement(nextGroups, nextWatchlist);
}

function commitWatchlistArrangement(nextGroups, nextWatchlist) {
  state.watchlistGroups = nextGroups;
  state.watchlist = nextWatchlist;
  persistWatchlistGroups();
  persistWatchlist();
  renderWatchlist();
}

function buildWatchlistOrder(groups, movingSymbol, targetSectionId, targetSymbol, placement) {
  const assignedSymbols = new Set(groups.flatMap((group) => group.symbols));
  const ungrouped = state.watchlist.filter((symbol) => symbol !== movingSymbol && !assignedSymbols.has(symbol));

  if (targetSectionId === "ungrouped") {
    insertSymbolIntoList(ungrouped, movingSymbol, targetSymbol, placement);
  }

  const ordered = [];
  const seen = new Set();
  for (const group of groups) {
    for (const symbol of group.symbols) {
      if (!seen.has(symbol) && state.watchlist.includes(symbol)) {
        ordered.push(symbol);
        seen.add(symbol);
      }
    }
  }
  for (const symbol of ungrouped) {
    if (!seen.has(symbol)) {
      ordered.push(symbol);
      seen.add(symbol);
    }
  }
  return ordered;
}

function insertSymbolIntoList(list, symbol, targetSymbol, placement) {
  const normalizedTarget = normalizeSymbol(targetSymbol || "");
  const nextIndex = normalizedTarget ? list.indexOf(normalizedTarget) : -1;
  if (nextIndex < 0 || placement === "end") {
    list.push(symbol);
    return;
  }
  const insertIndex = placement === "after" ? nextIndex + 1 : nextIndex;
  list.splice(insertIndex, 0, symbol);
}

function renderAlerts() {
  elements.alertsList.innerHTML = "";

  if (!state.alerts.length) {
    const empty = document.createElement("div");
    empty.className = "alerts-empty";
    empty.textContent = "还没有提醒。会根据本地缓存变化和拉新结果记录重要变化。";
    elements.alertsList.appendChild(empty);
    return;
  }

  for (const alert of state.alerts) {
    const node = document.createElement("article");
    node.className = "alert-item";
    node.innerHTML = `
      <div class="alert-item-head">
        <strong>${escapeHtml(alert.symbol)}</strong>
        <span class="alert-item-time">${escapeHtml(alert.timeLabel)}</span>
      </div>
      <p>${escapeHtml(alert.message)}</p>
    `;
    elements.alertsList.appendChild(node);
  }
}

function logDetailMessages(detail) {
  for (const note of detail.sourceNotes || []) {
    console.info(`[${detail.symbol}] ${note}`);
  }
  for (const warning of detail.warnings || []) {
    console.warn(`[${detail.symbol}] ${warning}`);
  }
}

function renderChecks(container, checks, options = {}) {
  container.innerHTML = "";
  for (const item of checks || []) {
    const node = document.createElement("article");
    node.className = "check-item";
    const stateLabel = item.passed === true ? "通过" : item.passed === false ? "未通过" : "待确认";
    const stateIcon = item.passed === true ? "✓" : item.passed === false ? "✕" : "?";
    const stateClass = item.passed === true ? "pass" : item.passed === false ? "fail" : "pending";
    const displayName = options.trimPrefix ? stripCheckNamePrefix(item.name) : item.name;
    const isSubdued = options.subduedNames?.has(displayName);
    if (isSubdued) {
      node.classList.add("subdued-check");
    }
    const header = document.createElement("strong");

    const title = document.createElement("span");
    title.className = "check-title";
    title.textContent = displayName;
    header.appendChild(title);

    const ruleText = options.ruleTooltips?.[displayName];
    if (ruleText) {
      const info = document.createElement("button");
      info.type = "button";
      info.className = "check-rule-button";
      info.textContent = "i";
      info.title = ruleText;
      info.setAttribute("aria-label", `${displayName} 计算规则`);
      header.appendChild(info);
    }

    const state = document.createElement("span");
    state.className = `check-state ${stateClass}`;
    if (isSubdued) {
      state.classList.add("subdued");
    }
    state.textContent = stateIcon;
    state.title = stateLabel;
    state.setAttribute("aria-label", stateLabel);
    header.appendChild(state);

    const detail = document.createElement("p");
    detail.textContent = item.detail || "";

    node.appendChild(header);
    node.appendChild(detail);
    container.appendChild(node);
  }
}

function renderChartHeadlineStats(detail) {
  const stats = [
    { label: "最新收盘", value: detail.latestCloseText || "-" },
    {
      label: "较前收盘",
      value: detail.dailyChangePctText || "-",
      tone: classifyChangeTone(detail.dailyChangePct),
    },
    { label: "收盘日成交量", value: detail.latestVolumeText || "-" },
  ];
  elements.chartHeadlineStats.innerHTML = stats
    .map(
      (item) => `
        <span class="chart-stat-pill${item.tone ? ` ${item.tone}` : ""}">
          <b>${item.label}</b>
          <span>${item.value}</span>
        </span>
      `,
    )
    .join("");
  renderChartHoldingStats(detail.symbol);
}

function renderChartHoldingStats(symbol) {
  const holding = getHoldingSnapshot(symbol);
  if (!holding) {
    elements.chartHoldingStats.innerHTML = "";
    return;
  }
  const tone = classifyChangeTone(holding.pnlPct);
  const holdingParts = [`浮盈亏 ${fmtPct(holding.pnlPct)}`, `成本 ${fmtPrice(holding.costBasis)}`];
  if (holding.pnlValue != null) {
    holdingParts.push(`金额 ${fmtSignedPrice(holding.pnlValue)}`);
  }
  if (holding.shares != null) {
    holdingParts.push(`股数 ${holding.shares}`);
  }
  elements.chartHoldingStats.innerHTML = `
    <span class="chart-holding-pill${tone ? ` ${tone}` : ""}">
      <b>持仓</b>
      <span>${holdingParts.join(" / ")}</span>
    </span>
  `;
}

function getChartLib() {
  return globalThis.LightweightCharts || null;
}

class TradeMarkerPrimitive {
  constructor() {
    this.records = [];
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
    this.view = {
      zOrder: () => "top",
      renderer: () => ({
        draw: (target) => this.draw(target),
      }),
    };
  }

  attached({ chart, series, requestUpdate }) {
    this.chart = chart;
    this.series = series;
    this.requestUpdate = requestUpdate;
  }

  detached() {
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
  }

  paneViews() {
    return [this.view];
  }

  updateAllViews() {}

  setRecords(records) {
    this.records = records;
    this.requestUpdate?.();
  }

  draw(target) {
    if (!this.chart || !this.series || !this.records.length) {
      return;
    }
    target.useBitmapCoordinateSpace((scope) => {
      const context = scope.context;
      const horizontalRatio = scope.horizontalPixelRatio;
      const verticalRatio = scope.verticalPixelRatio;
      const width = scope.bitmapSize.width;
      const height = scope.bitmapSize.height;
      const lineLength = 62 * verticalRatio;
      const labelGap = 18 * verticalRatio;
      const fontSize = 15 * verticalRatio;

      context.save();
      context.font = `700 ${fontSize}px "Segoe UI", "PingFang SC", sans-serif`;
      context.textBaseline = "middle";

      for (const record of this.records) {
        const mediaX = this.chart.timeScale().timeToCoordinate(record.date);
        const mediaY = this.series.priceToCoordinate(Number(record.price));
        if (!Number.isFinite(mediaX) || !Number.isFinite(mediaY)) {
          continue;
        }
        const x = mediaX * horizontalRatio;
        const y = mediaY * verticalRatio;
        if (x < 0 || x > width || y < 0 || y > height) {
          continue;
        }

        const isBuy = record.side === "buy";
        const color = isBuy ? "#166534" : "#b42318";
        const direction = isBuy ? 1 : -1;
        const lineStart = y + direction * 11 * verticalRatio;
        const lineEnd = y + direction * lineLength;
        const quantity = record.quantity == null
          ? ""
          : ` ${fmtTradeQuantity(record.quantity)}股`;
        const text = `${isBuy ? "买入" : "卖出"}${quantity} @ ${fmtPrice(record.price)}`;

        context.strokeStyle = color;
        context.lineWidth = Math.max(1, 1.5 * horizontalRatio);
        context.beginPath();
        context.moveTo(x, lineStart);
        context.lineTo(x, lineEnd);
        context.stroke();

        context.fillStyle = color;
        context.beginPath();
        if (isBuy) {
          context.moveTo(x, y);
          context.lineTo(x - 6 * horizontalRatio, y + 11 * verticalRatio);
          context.lineTo(x + 6 * horizontalRatio, y + 11 * verticalRatio);
        } else {
          context.moveTo(x, y);
          context.lineTo(x - 6 * horizontalRatio, y - 11 * verticalRatio);
          context.lineTo(x + 6 * horizontalRatio, y - 11 * verticalRatio);
        }
        context.closePath();
        context.fill();

        const textWidth = context.measureText(text).width;
        const paddingX = 7 * horizontalRatio;
        const labelHeight = 24 * verticalRatio;
        const labelCenterY = lineEnd + direction * labelGap;
        const labelX = Math.max(
          4 * horizontalRatio,
          Math.min(width - textWidth - paddingX * 2 - 4 * horizontalRatio, x - textWidth / 2 - paddingX),
        );
        const labelY = labelCenterY - labelHeight / 2;

        context.fillStyle = "rgba(255,255,255,0.94)";
        context.fillRect(labelX, labelY, textWidth + paddingX * 2, labelHeight);
        context.strokeStyle = color;
        context.lineWidth = Math.max(1, horizontalRatio);
        context.strokeRect(labelX, labelY, textWidth + paddingX * 2, labelHeight);
        context.fillStyle = color;
        context.fillText(text, labelX + paddingX, labelCenterY);
      }
      context.restore();
    });
  }
}

function renderChartUnavailable(message) {
  destroyCharts();
  hideHoverCard();
  const placeholder = document.createElement("div");
  placeholder.className = "chart-unavailable";
  placeholder.textContent = message;
  elements.priceChartContainer.innerHTML = "";
  elements.priceChartContainer.appendChild(placeholder);
  elements.benchmarkChartContainer.innerHTML = "";
  elements.volumeChartContainer.innerHTML = "";
}

function renderMainChart(detail) {
  const chartData = detail.history || [];
  state.currentChartData = chartData;
  state.currentBenchmarkData = detail.benchmarkHistory || [];
  state.currentBenchmarkSymbol = detail.benchmarkSymbol || "SPY";
  const comparisonAvailable =
    state.currentBenchmarkData.length > 0 && detail.symbol !== state.currentBenchmarkSymbol;
  elements.compareBenchmarkToggle.disabled = !comparisonAvailable;
  elements.compareBenchmarkToggle.checked = state.compareBenchmark && comparisonAvailable;
  elements.benchmarkChartLabel.textContent = state.currentBenchmarkSymbol;
  elements.stockChartLabel.textContent = detail.symbol;
  if (!getChartLib()) {
    renderChartUnavailable("图表库未加载，当前无法显示图表。");
    return;
  }
  if (!state.priceChart) {
    if (!createMainChart()) {
      return;
    }
  }

  state.candleSeries.setData(
    chartData.map((row) => ({
      time: row.Date,
      open: row.Open,
      high: row.High,
      low: row.Low,
      close: row.Close,
    })),
  );

  state.closeLineSeries.setData(
    chartData
      .filter((row) => row.Close != null)
      .map((row) => ({
        time: row.Date,
        value: row.Close,
      })),
  );

  const benchmarkByDate = new Map(
    state.currentBenchmarkData
      .filter((row) => row.Date)
      .map((row) => [row.Date, row]),
  );
  const alignedBenchmarkData = chartData.map((row) => benchmarkByDate.get(row.Date) || { Date: row.Date });
  state.benchmarkCandleSeries.setData(
    alignedBenchmarkData.map((row) =>
      row.Open == null || row.High == null || row.Low == null || row.Close == null
        ? { time: row.Date }
        : {
            time: row.Date,
            open: row.Open,
            high: row.High,
            low: row.Low,
            close: row.Close,
          },
    ),
  );
  state.benchmarkLineSeries.setData(
    alignedBenchmarkData.map((row) =>
      row.Close == null ? { time: row.Date } : { time: row.Date, value: row.Close },
    ),
  );

  state.volumeSeries.setData(
    chartData.map((row) => ({
      time: row.Date,
      value: row.Volume,
      color: row.Close >= row.Open ? "#0f9d58aa" : "#db4437aa",
    })),
  );
  state.volumeMa50Series.setData(buildVolumeMa50Data(chartData));

  applyTradeMarkers(detail.symbol);
  const maFields = [
    ["MA20", "#d48a00"],
    ["MA50", "#0077b6"],
    ["MA150", "#6d597a"],
    ["MA200", "#1d3557"],
  ];
  maFields.forEach(([field], index) => {
    state.maSeries[index].setData(
      chartData
        .filter((row) => row[field] != null)
        .map((row) => ({
          time: row.Date,
          value: row[field],
        })),
    );
  });

  applyMaVisibility();
  applyChartMode();
  applyVisibleWindow(chartData);
  clampVisibleRange();
  hideHoverCard();
  resizeCharts();
}

function createMainChart() {
  const chartLib = getChartLib();
  if (!chartLib) {
    renderChartUnavailable("图表库未加载，当前无法显示图表。");
    return false;
  }

  elements.priceChartContainer.innerHTML = "";
  elements.benchmarkChartContainer.innerHTML = "";
  elements.volumeChartContainer.innerHTML = "";

  const dashedLineStyle = chartLib.LineStyle?.Dashed ?? 2;
  const dottedLineStyle = chartLib.LineStyle?.Dotted ?? 1;

  state.benchmarkChart = chartLib.createChart(elements.benchmarkChartContainer, {
    layout: {
      background: { color: "rgba(255,255,255,0)" },
      textColor: "#617286",
      attributionLogo: false,
    },
    rightPriceScale: {
      borderColor: "rgba(15, 23, 42, 0.12)",
      scaleMargins: { top: 0.12, bottom: 0.12 },
    },
    timeScale: {
      visible: false,
      borderVisible: false,
      rightOffset: 0,
      fixRightEdge: true,
      rightBarStaysOnScroll: true,
      lockVisibleTimeRangeOnResize: true,
    },
    grid: {
      vertLines: { color: "rgba(15, 23, 42, 0.04)" },
      horzLines: { color: "rgba(15, 23, 42, 0.05)" },
    },
    crosshair: {
      vertLine: { color: "rgba(180, 83, 9, 0.3)" },
      horzLine: { color: "rgba(180, 83, 9, 0.2)" },
    },
    handleScroll: false,
    handleScale: false,
  });

  state.benchmarkCandleSeries = state.benchmarkChart.addSeries(chartLib.CandlestickSeries, {
    upColor: "#0f9d58",
    downColor: "#db4437",
    borderVisible: false,
    wickUpColor: "#0f9d58",
    wickDownColor: "#db4437",
    lastValueVisible: true,
    priceLineVisible: false,
  });

  state.benchmarkLineSeries = state.benchmarkChart.addSeries(chartLib.LineSeries, {
    color: "#b45309",
    lineWidth: 2,
    title: "",
    crosshairMarkerVisible: true,
    lastValueVisible: true,
    priceLineVisible: false,
  });

  state.priceChart = chartLib.createChart(elements.priceChartContainer, {
    layout: {
      background: { color: "rgba(255,255,255,0)" },
      textColor: "#102033",
      attributionLogo: false,
    },
    rightPriceScale: {
      borderColor: "rgba(15, 23, 42, 0.12)",
    },
    timeScale: {
      visible: false,
      borderVisible: false,
      ticksVisible: false,
      rightOffset: 0,
      fixRightEdge: true,
      rightBarStaysOnScroll: true,
      lockVisibleTimeRangeOnResize: true,
    },
    grid: {
      vertLines: { color: "rgba(15, 23, 42, 0.06)" },
      horzLines: { color: "rgba(15, 23, 42, 0.06)" },
    },
    crosshair: {
      vertLine: { color: "rgba(14, 116, 144, 0.35)" },
      horzLine: { color: "rgba(14, 116, 144, 0.35)" },
    },
    handleScroll: {
      mouseWheel: false,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: false,
    },
    handleScale: {
      mouseWheel: false,
      pinch: false,
      axisPressedMouseMove: { time: true, price: false },
      axisDoubleClickReset: { time: true, price: false },
    },
  });

  state.candleSeries = state.priceChart.addSeries(chartLib.CandlestickSeries, {
    upColor: "#0f9d58",
    downColor: "#db4437",
    borderVisible: false,
    wickUpColor: "#0f9d58",
    wickDownColor: "#db4437",
    lastValueVisible: false,
    priceLineVisible: false,
  });

  state.closeLineSeries = state.priceChart.addSeries(chartLib.LineSeries, {
    color: "#0e7490",
    lineWidth: 3,
    title: "",
    crosshairMarkerVisible: true,
    lineStyle: chartLib.LineStyle?.Solid ?? 0,
    lastValueVisible: false,
    priceLineVisible: false,
  });
  state.candleTradePrimitive = new TradeMarkerPrimitive();
  state.closeLineTradePrimitive = new TradeMarkerPrimitive();
  state.candleSeries.attachPrimitive(state.candleTradePrimitive);
  state.closeLineSeries.attachPrimitive(state.closeLineTradePrimitive);
  state.maSeries = [
    state.priceChart.addSeries(chartLib.LineSeries, {
      color: "#d48a00cc",
      lineWidth: 1.5,
      lineStyle: dottedLineStyle,
      title: "",
      lastValueVisible: false,
      priceLineVisible: false,
    }),
    state.priceChart.addSeries(chartLib.LineSeries, {
      color: "#0077b6bb",
      lineWidth: 1.5,
      lineStyle: dashedLineStyle,
      title: "",
      lastValueVisible: false,
      priceLineVisible: false,
    }),
    state.priceChart.addSeries(chartLib.LineSeries, {
      color: "#6d597aaa",
      lineWidth: 1.5,
      lineStyle: dashedLineStyle,
      title: "",
      lastValueVisible: false,
      priceLineVisible: false,
    }),
    state.priceChart.addSeries(chartLib.LineSeries, {
      color: "#1d3557aa",
      lineWidth: 1.5,
      lineStyle: dottedLineStyle,
      title: "",
      lastValueVisible: false,
      priceLineVisible: false,
    }),
  ];

  state.closeLineSeries.applyOptions({
    title: "",
    lastValueVisible: false,
    priceLineVisible: false,
  });
  state.maSeries.forEach((series) => {
    series.applyOptions({
      title: "",
      lastValueVisible: false,
      priceLineVisible: false,
    });
  });

  state.volumeChart = chartLib.createChart(elements.volumeChartContainer, {
    layout: {
      background: { color: "rgba(255,255,255,0)" },
      textColor: "#617286",
      attributionLogo: false,
    },
    rightPriceScale: {
      borderColor: "rgba(15, 23, 42, 0.12)",
      scaleMargins: { top: 0.16, bottom: 0.06 },
    },
    timeScale: {
      borderColor: "rgba(15, 23, 42, 0.12)",
      visible: true,
      ticksVisible: true,
      rightOffset: 0,
      fixRightEdge: true,
      rightBarStaysOnScroll: true,
      lockVisibleTimeRangeOnResize: true,
    },
    grid: {
      vertLines: { color: "rgba(15, 23, 42, 0.04)" },
      horzLines: { color: "rgba(15, 23, 42, 0.04)" },
    },
    handleScroll: false,
    handleScale: false,
    crosshair: {
      vertLine: { color: "rgba(14, 116, 144, 0.25)" },
      horzLine: { visible: false },
    },
  });

  state.volumeSeries = state.volumeChart.addSeries(chartLib.HistogramSeries, {
    priceFormat: { type: "volume" },
    priceScaleId: "right",
    lastValueVisible: false,
    priceLineVisible: false,
  });
  state.volumeMa50Series = state.volumeChart.addSeries(chartLib.LineSeries, {
    color: "#0077b6",
    lineWidth: 1.5,
    lineStyle: dashedLineStyle,
    priceFormat: { type: "volume" },
    priceScaleId: "right",
    crosshairMarkerVisible: false,
    lastValueVisible: false,
    priceLineVisible: false,
  });

  state.priceChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (range) {
      const clamped = clampLogicalRange(range, state.currentChartData.length);
      if (hasMeaningfulRangeChange(range, clamped)) {
        state.priceChart.timeScale().setVisibleLogicalRange(clamped);
        return;
      }
      syncChartRanges(clamped);
    }
  });

  state.priceChart.subscribeCrosshairMove((param) => {
    if (state.crosshairSyncing) {
      return;
    }
    if (!param.time || !param.point) {
      hideHoverCard();
      clearSyncedCrosshairs("price");
      return;
    }
    const row = state.currentChartData.find((item) => item.Date === param.time);
    updateHoverCard(row || state.currentChartData.at(-1), param.point);
    syncCrosshairs(param.time, "price");
  });

  state.benchmarkChart.subscribeCrosshairMove((param) => {
    if (state.crosshairSyncing) {
      return;
    }
    if (!param.time || !param.point) {
      clearSyncedCrosshairs("benchmark");
      return;
    }
    syncCrosshairs(param.time, "benchmark");
  });

  state.volumeChart.subscribeCrosshairMove((param) => {
    if (state.crosshairSyncing) {
      return;
    }
    if (!param.time || !param.point) {
      clearSyncedCrosshairs("volume");
      return;
    }
    syncCrosshairs(param.time, "volume");
  });

  bindChartWheelZoom();
  return true;
}

function applyVisibleWindow(history) {
  if (!history.length || !state.priceChart || !state.volumeChart) {
    return;
  }

  const visibleBars = Math.min(DEFAULT_VISIBLE_BARS, history.length);
  const range = buildRightAnchoredRange(visibleBars, history.length);
  state.priceChart.timeScale().setVisibleLogicalRange(range);
  syncChartRanges(range);
}

function applyTradeMarkers(symbol = state.selectedSymbol) {
  const markersVisible =
    state.tradeReviewActive &&
    state.tradeReviewSymbol === symbol &&
    state.selectedSymbol === symbol;
  const activeReview = markersVisible
    ? getTradeReview(symbol, state.tradeReviewId)
    : null;
  const records = activeReview?.trades || [];
  state.candleTradePrimitive?.setRecords(
    state.chartMode === "candles" ? records : [],
  );
  state.closeLineTradePrimitive?.setRecords(
    state.chartMode === "close" ? records : [],
  );
}

function syncChartRanges(range) {
  if (!range) {
    return;
  }
  state.volumeChart?.timeScale().setVisibleLogicalRange(range);
  if (state.compareBenchmark && !elements.compareBenchmarkToggle.disabled) {
    state.benchmarkChart?.timeScale().setVisibleLogicalRange(range);
  }
}

function syncCrosshairs(time, source) {
  const stockRow = state.currentChartData.find((row) => row.Date === time);
  if (!stockRow) {
    return;
  }
  const benchmarkRow = state.currentBenchmarkData.find((row) => row.Date === time);
  const stockSeries = state.chartMode === "candles" ? state.candleSeries : state.closeLineSeries;

  state.crosshairSyncing = true;
  try {
    if (source !== "price" && state.priceChart && stockSeries) {
      state.priceChart.setCrosshairPosition(stockRow.Close, time, stockSeries);
    }
    if (source !== "volume" && state.volumeChart && state.volumeSeries) {
      state.volumeChart.setCrosshairPosition(stockRow.Volume, time, state.volumeSeries);
    }
    if (
      source !== "benchmark" &&
      state.compareBenchmark &&
      !elements.compareBenchmarkToggle.disabled &&
      state.benchmarkChart &&
      (state.benchmarkCandleSeries || state.benchmarkLineSeries) &&
      benchmarkRow
    ) {
      const benchmarkSeries =
        state.chartMode === "candles"
          ? state.benchmarkCandleSeries
          : state.benchmarkLineSeries;
      state.benchmarkChart.setCrosshairPosition(
        benchmarkRow.Close,
        time,
        benchmarkSeries,
      );
    }
  } finally {
    state.crosshairSyncing = false;
  }
}

function clearSyncedCrosshairs(source) {
  state.crosshairSyncing = true;
  try {
    if (source !== "price") {
      state.priceChart?.clearCrosshairPosition();
    }
    if (source !== "volume") {
      state.volumeChart?.clearCrosshairPosition();
    }
    if (source !== "benchmark") {
      state.benchmarkChart?.clearCrosshairPosition();
    }
  } finally {
    state.crosshairSyncing = false;
  }
}

function bindChartWheelZoom() {
  if (state.chartWheelBound) {
    return;
  }

  const handleWheel = (event) => {
    if (!state.priceChart || !state.volumeChart || !state.currentChartData.length) {
      return;
    }
    event.preventDefault();

    const dataLength = state.currentChartData.length;
    const currentRange = state.priceChart.timeScale().getVisibleLogicalRange();
    const fallbackWidth = Math.min(
      DEFAULT_VISIBLE_BARS,
      Math.max(12, dataLength),
    );
    const currentWidth = currentRange ? currentRange.to - currentRange.from : fallbackWidth;
    const zoomFactor = event.deltaY > 0 ? 1.16 : 0.86;
    const nextWidth = Math.min(
      dataLength,
      Math.max(12, currentWidth * zoomFactor),
    );
    if (Math.abs(nextWidth - currentWidth) < 0.05) {
      return;
    }

    const range = buildRightAnchoredRange(nextWidth, dataLength);
    state.priceChart.timeScale().setVisibleLogicalRange(range);
    syncChartRanges(range);
  };

  elements.benchmarkChartContainer.addEventListener("wheel", handleWheel, { passive: false });
  elements.priceChartContainer.addEventListener("wheel", handleWheel, { passive: false });
  elements.volumeChartContainer.addEventListener("wheel", handleWheel, { passive: false });
  state.chartWheelBound = true;
}

function clampLogicalRange(range, dataLength) {
  if (!range || !dataLength) {
    return range;
  }

  const minLogical = -0.5;
  const lastLogical = dataLength - 0.5;
  let from = Number(range.from);
  let to = Number(range.to);
  if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) {
    return { from: minLogical, to: lastLogical };
  }

  const maxWidth = lastLogical - minLogical;
  const width = Math.min(to - from, maxWidth);
  to = lastLogical;
  from = to - width;

  if (from < minLogical) {
    from = minLogical;
    to = Math.min(lastLogical, from + width);
  }

  return { from, to };
}

function hasMeaningfulRangeChange(original, next) {
  if (!original || !next) {
    return false;
  }
  return Math.abs(original.from - next.from) > 0.01 || Math.abs(original.to - next.to) > 0.01;
}

function clampVisibleRange() {
  if (!state.priceChart || !state.volumeChart || !state.currentChartData.length) {
    return;
  }
  const range = state.priceChart.timeScale().getVisibleLogicalRange();
  if (!range) {
    return;
  }
  const clamped = clampLogicalRange(range, state.currentChartData.length);
  state.priceChart.timeScale().setVisibleLogicalRange(clamped);
  syncChartRanges(clamped);
}

function buildRightAnchoredRange(width, dataLength) {
  const lastLogical = dataLength - 0.5;
  const from = Math.max(-0.5, lastLogical - width);
  return { from, to: lastLogical };
}

function clearDetail() {
  if (state.tradeReviewActive) {
    exitTradeReview();
  }
  elements.detailSection.hidden = true;
  elements.chartTitle.textContent = "选择一只股票";
  elements.chartHeadlineStats.innerHTML = "";
  elements.chartHoldingStats.innerHTML = "";
  elements.benchmarkChartPanel.hidden = true;
  elements.benchmarkChartLabel.textContent = "SPY";
  elements.stockChartLabel.hidden = true;
  elements.stockChartLabel.textContent = "";
  elements.compareBenchmarkToggle.disabled = true;
  elements.compareBenchmarkToggle.checked = false;
  state.currentChartData = [];
  state.currentBenchmarkData = [];
  hideChartStatus();
  elements.trendChecks.innerHTML = "";
  elements.advancedTrendChecks.innerHTML = "";
  elements.patternRiskChecks.innerHTML = "";
  elements.tempAdvancedTrendChecks.innerHTML = "";
  hideHoverCard();
  destroyCharts();
}

function destroyCharts() {
  if (state.benchmarkChart) {
    state.benchmarkChart.remove();
    state.benchmarkChart = null;
    state.benchmarkCandleSeries = null;
    state.benchmarkLineSeries = null;
  }
  if (state.priceChart) {
    state.priceChart.remove();
    state.priceChart = null;
    state.candleSeries = null;
    state.closeLineSeries = null;
    state.candleTradePrimitive = null;
    state.closeLineTradePrimitive = null;
    state.maSeries = [];
  }
  if (state.volumeChart) {
    state.volumeChart.remove();
    state.volumeChart = null;
    state.volumeSeries = null;
    state.volumeMa50Series = null;
  }
  elements.benchmarkChartContainer.innerHTML = "";
  elements.priceChartContainer.innerHTML = "";
  elements.volumeChartContainer.innerHTML = "";
}

function resizeCharts() {
  if (state.benchmarkChart) {
    state.benchmarkChart.resize(
      elements.benchmarkChartContainer.clientWidth,
      elements.benchmarkChartContainer.clientHeight,
    );
  }
  if (state.priceChart) {
    state.priceChart.resize(elements.priceChartContainer.clientWidth, elements.priceChartContainer.clientHeight);
  }
  if (state.volumeChart) {
    state.volumeChart.resize(elements.volumeChartContainer.clientWidth, elements.volumeChartContainer.clientHeight);
  }
}

function syncMaToggleInputs() {
  for (const input of elements.maToggleGroup.querySelectorAll("input[type='checkbox']")) {
    if (input instanceof HTMLInputElement) {
      input.checked = !!state.maVisibility[input.dataset.ma];
    }
  }
}

function applyMaVisibility() {
  const maFields = ["MA20", "MA50", "MA150", "MA200"];
  maFields.forEach((field, index) => {
    if (state.maSeries[index]) {
      state.maSeries[index].applyOptions({ visible: !!state.maVisibility[field] });
    }
  });
  state.volumeMa50Series?.applyOptions({
    visible: !!state.maVisibility.VolumeMA50,
  });
}

function buildVolumeMa50Data(chartData) {
  const window = [];
  let total = 0;
  const result = [];

  for (const row of chartData) {
    const volume = Number(row.Volume);
    if (!Number.isFinite(volume)) {
      window.length = 0;
      total = 0;
      continue;
    }
    window.push(volume);
    total += volume;
    if (window.length > 50) {
      total -= window.shift();
    }
    if (window.length === 50) {
      result.push({
        time: row.Date,
        value: total / 50,
      });
    }
  }
  return result;
}

function applyChartMode() {
  if (!state.candleSeries || !state.closeLineSeries) {
    return;
  }
  const comparisonAvailable =
    !elements.compareBenchmarkToggle.disabled && state.currentBenchmarkData.length > 0;
  const comparisonMode = state.compareBenchmark && comparisonAvailable;
  const closeMode = state.chartMode === "close";
  state.candleSeries.applyOptions({ visible: !closeMode });
  state.closeLineSeries.applyOptions({ visible: closeMode });
  state.benchmarkCandleSeries?.applyOptions({ visible: !closeMode });
  state.benchmarkLineSeries?.applyOptions({ visible: closeMode });
  applyMaVisibility();
  elements.chartMode.disabled = false;
  elements.maToggleGroup.hidden = false;
  elements.benchmarkChartPanel.hidden = !comparisonMode;
  elements.stockChartLabel.hidden = !comparisonMode;
  applyTradeMarkers();
}

function updateHoverCard(row, point, pinned = false) {
  if (!row) {
    hideHoverCard();
    return;
  }

  const parts = [
    `<strong>${row.Date}</strong>`,
    `开盘 ${fmtPrice(row.Open)}`,
    `最高 ${fmtPrice(row.High)}`,
    `最低 ${fmtPrice(row.Low)}`,
    `收盘 ${fmtPrice(row.Close)}`,
    `成交量 ${fmtVolume(row.Volume)}`,
  ];

  for (const field of ["MA20", "MA50", "MA150", "MA200"]) {
    if (state.maVisibility[field]) {
      parts.push(`${field} ${fmtPrice(row[field])}`);
    }
  }
  const activeReview =
    state.tradeReviewActive && state.tradeReviewSymbol === state.selectedSymbol
      ? getTradeReview(state.selectedSymbol, state.tradeReviewId)
      : null;
  const dayTrades = (activeReview?.trades || []).filter(
    (record) => record.date === row.Date,
  );
  for (const record of dayTrades) {
    const action = record.side === "buy" ? "买入" : "卖出";
    const note = String(record.note || "").trim();
    const quantity = record.quantity == null ? "" : ` ${fmtTradeQuantity(record.quantity)} 股`;
    parts.push(
      `${action}${quantity} @ ${fmtPrice(record.price)}${note ? ` · ${escapeHtml(note)}` : ""}`,
    );
  }

  elements.chartHoverCard.innerHTML = parts.join("<br />");
  elements.chartHoverCard.hidden = false;

  if (!point || pinned) {
    elements.chartHoverCard.style.left = "14px";
    elements.chartHoverCard.style.top = "14px";
    return;
  }

  const containerWidth = elements.priceChartContainer.clientWidth;
  const containerHeight = elements.priceChartContainer.clientHeight;
  const cardWidth = Math.min(260, Math.max(190, elements.chartHoverCard.offsetWidth || 220));
  const cardHeight = elements.chartHoverCard.offsetHeight || 150;
  const offsetX = 16;
  const offsetY = 16;

  let left = point.x + offsetX;
  let top = point.y + offsetY;

  if (left + cardWidth > containerWidth - 8) {
    left = point.x - cardWidth - offsetX;
  }
  if (top + cardHeight > containerHeight - 8) {
    top = point.y - cardHeight - offsetY;
  }

  left = Math.max(8, left);
  top = Math.max(8, top);
  elements.chartHoverCard.style.left = `${left}px`;
  elements.chartHoverCard.style.top = `${top}px`;
}

function hideHoverCard() {
  elements.chartHoverCard.hidden = true;
}

function renderChartStatus(detail) {
  const status = deriveChartStatus(detail);
  if (!status) {
    hideChartStatus();
    return;
  }
  elements.chartStatusNote.textContent = status;
  elements.chartStatusNote.hidden = false;
}

function hideChartStatus() {
  elements.chartStatusNote.hidden = true;
  elements.chartStatusNote.textContent = "";
}

function persistWatchlist() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state.watchlist));
}

function persistWatchlistGroups() {
  localStorage.setItem(GROUPS_STORAGE_KEY, JSON.stringify(state.watchlistGroups));
}

function persistNotes() {
  localStorage.setItem(NOTES_STORAGE_KEY, JSON.stringify(state.notes));
}

function persistTrendFilter() {
  localStorage.setItem(TREND_FILTER_STORAGE_KEY, state.filterTrendTemplateOnly ? "1" : "0");
}

function persistHoldingFilter() {
  localStorage.setItem(HOLDING_FILTER_STORAGE_KEY, state.filterHoldingOnly ? "1" : "0");
}

function persistVolumeBelowMA50Filter() {
  localStorage.setItem(
    VOLUME_BELOW_MA50_FILTER_STORAGE_KEY,
    state.filterVolumeBelowMA50Only ? "1" : "0",
  );
}

function persistAlerts() {
  localStorage.setItem(ALERTS_STORAGE_KEY, JSON.stringify(state.alerts));
}

function persistAlertsSnapshot() {
  localStorage.setItem(ALERTS_SNAPSHOT_STORAGE_KEY, JSON.stringify(state.alertsSnapshot));
}

function persistChartPrefs() {
  localStorage.setItem(
    CHART_PREFS_KEY,
    JSON.stringify({
      chartMode: state.chartMode,
      compareBenchmark: state.compareBenchmark,
      maVisibility: state.maVisibility,
    }),
  );
}

function loadChartPrefs() {
  try {
    const raw = localStorage.getItem(CHART_PREFS_KEY);
    if (!raw) {
      return;
    }
    const parsed = JSON.parse(raw);
    if (parsed.chartMode === "candles" || parsed.chartMode === "close") {
      state.chartMode = parsed.chartMode;
    }
    if (typeof parsed.compareBenchmark === "boolean") {
      state.compareBenchmark = parsed.compareBenchmark;
    }
    if (parsed.maVisibility && typeof parsed.maVisibility === "object") {
      for (const key of ["MA20", "MA50", "MA150", "MA200", "VolumeMA50"]) {
        if (typeof parsed.maVisibility[key] === "boolean") {
          state.maVisibility[key] = parsed.maVisibility[key];
        }
      }
    }
  } catch {
    // Ignore malformed local preferences.
  }
}

function loadStoredWatchlist() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(normalizeSymbol).filter(Boolean) : [];
  } catch {
    return [];
  }
}

function loadStoredWatchlistGroups(fallback) {
  try {
    const raw = localStorage.getItem(GROUPS_STORAGE_KEY);
    if (!raw) {
      return fallback;
    }
    const parsed = JSON.parse(raw);
    const groups = normalizeWatchlistGroups(parsed);
    return groups.length ? groups : fallback;
  } catch {
    return fallback;
  }
}

function loadStoredNotes() {
  try {
    const raw = localStorage.getItem(NOTES_STORAGE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(parsed)
        .map(([symbol, note]) => {
          const normalizedSymbol = normalizeSymbol(symbol);
          if (!normalizedSymbol) {
            return null;
          }
          if (typeof note === "string") {
            const text = note.trim();
            return text ? [normalizedSymbol, { text, isHolding: false, costBasis: null, shares: null }] : null;
          }
          if (!note || typeof note !== "object" || Array.isArray(note)) {
            return null;
          }
          const costBasis = parsePositiveNumber(note.costBasis);
          const shares = parsePositiveNumber(note.shares);
          const text = String(note.text || "").trim();
          const isHolding = !!note.isHolding || costBasis != null || shares != null;
          if (!text && !isHolding && costBasis == null && shares == null) {
            return null;
          }
          return [normalizedSymbol, { text, isHolding, costBasis, shares }];
        })
        .filter(Boolean),
    );
  } catch {
    return {};
  }
}

function loadStoredTrendFilter() {
  try {
    return localStorage.getItem(TREND_FILTER_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function loadStoredHoldingFilter() {
  try {
    return localStorage.getItem(HOLDING_FILTER_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function loadStoredVolumeBelowMA50Filter() {
  try {
    return localStorage.getItem(VOLUME_BELOW_MA50_FILTER_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function loadStoredAlerts() {
  try {
    const raw = localStorage.getItem(ALERTS_STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed
      .filter((item) => !String(item?.message || "").includes("较上次快照"))
      .slice(0, 20);
  } catch {
    return [];
  }
}

function loadStoredAlertsSnapshot() {
  try {
    const raw = localStorage.getItem(ALERTS_SNAPSHOT_STORAGE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("\"", "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function normalizeSymbol(value) {
  return (value || "")
    .trim()
    .toUpperCase()
    .replace(/[\s.]+/g, "-")
    .replace(/-{2,}/g, "-");
}

function fmtPrice(value) {
  return value == null ? "-" : Number(value).toFixed(2);
}

function fmtTradeQuantity(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "-";
  }
  return Number.isInteger(numeric)
    ? numeric.toLocaleString("zh-CN")
    : numeric.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
}

function fmtSignedPrice(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "-";
  }
  const numeric = Number(value);
  const sign = numeric > 0 ? "+" : "";
  return `${sign}${numeric.toFixed(2)}`;
}

function fmtVolume(value) {
  if (value == null) {
    return "-";
  }
  const absolute = Math.abs(Number(value));
  if (absolute >= 1_000_000_000) {
    return `${(value / 1_000_000_000).toFixed(2)}B`;
  }
  if (absolute >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(2)}M`;
  }
  if (absolute >= 1_000) {
    return `${(value / 1_000).toFixed(1)}K`;
  }
  return String(Math.round(value));
}

function parsePositiveNumber(value) {
  if (value == null || value === "") {
    return null;
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
}

function stripCheckNamePrefix(value) {
  const text = String(value || "").trim();
  for (const separator of ["：", ":"]) {
    const index = text.indexOf(separator);
    if (index >= 0) {
      return text.slice(index + 1).trim() || text;
    }
  }
  return text;
}

function getNoteForSymbol(symbol) {
  return String(state.notes[normalizeSymbol(symbol)]?.text || "").trim();
}

function getCostBasisForSymbol(symbol) {
  const value = state.notes[normalizeSymbol(symbol)]?.costBasis;
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

function getSharesForSymbol(symbol) {
  const value = state.notes[normalizeSymbol(symbol)]?.shares;
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

function getHoldingForSymbol(symbol) {
  return !!state.notes[normalizeSymbol(symbol)]?.isHolding;
}

function getHoldingSnapshot(symbol) {
  const normalizedSymbol = normalizeSymbol(symbol);
  const latestClose = state.summaries.get(normalizedSymbol)?.data?.latestClose;
  const costBasis = getCostBasisForSymbol(normalizedSymbol);
  const shares = getSharesForSymbol(normalizedSymbol);
  if (!(typeof latestClose === "number" && Number.isFinite(latestClose)) || !(typeof costBasis === "number" && costBasis > 0)) {
    return null;
  }
  const pnlPct = latestClose / costBasis - 1;
  const pnlValue = typeof shares === "number" && shares > 0 ? (latestClose - costBasis) * shares : null;
  return { latestClose, costBasis, shares, pnlPct, pnlValue };
}

function renderHoldingInline(symbol) {
  const holding = getHoldingSnapshot(symbol);
  if (!holding) {
    return "";
  }
  const tone = classifyChangeTone(holding.pnlPct);
  return `
    <div class="watchlist-holding-line${tone ? ` ${tone}` : ""}">
      <span>浮盈亏 ${fmtPct(holding.pnlPct)}</span>
      ${holding.pnlValue != null ? `<span>${fmtSignedPrice(holding.pnlValue)}</span>` : ""}
    </div>
  `;
}

function renderNoteButton(symbol) {
  const noteText = getNoteForSymbol(symbol);
  const isHolding = getHoldingForSymbol(symbol);
  const costBasis = getCostBasisForSymbol(symbol);
  const shares = getSharesForSymbol(symbol);
  const hasMeta = !!noteText || isHolding || costBasis != null || shares != null;
  const activeClass = hasMeta ? " has-note" : "";
  const parts = [];
  if (isHolding) {
    parts.push("持仓股");
  }
  if (costBasis != null) {
    parts.push(`成本 ${fmtPrice(costBasis)}`);
  }
  if (shares != null) {
    parts.push(`股数 ${shares}`);
  }
  if (noteText) {
    parts.push(noteText);
  }
  const title = parts.join(" · ") || "查看或编辑笔记";
  return `
    <button type="button" class="watchlist-note-button${activeClass}" data-note-symbol="${symbol}" aria-label="${escapeHtml(title)}" title="${escapeHtml(title)}">
      i
    </button>
  `;
}

function bindNoteButton(card, symbol) {
  const button = card.querySelector(`[data-note-symbol="${symbol}"]`);
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    openNoteEditor(symbol);
  });
}

async function openTradeReview() {
  const storedPayload = await fetchJson("/api/trades");
  state.allTradeReviews = storedPayload.reviews || [];
  const candidates = Array.from(
    new Set([
      ...state.watchlist,
      ...(storedPayload.symbols || []),
      state.tradeReviewSymbol,
      state.selectedSymbol,
    ].filter(Boolean)),
  );
  if (!candidates.length) {
    showToast("请先添加一只股票。", true);
    return;
  }
  elements.tradeSymbolSelect.innerHTML = candidates
    .map((symbol) => `<option value="${escapeHtml(symbol)}">${escapeHtml(symbol)}</option>`)
    .join("");
  const symbol = normalizeSymbol(
    state.tradeReviewSymbol || state.selectedSymbol || candidates[0],
  );
  elements.tradeSymbolSelect.value = symbol;
  populateGlobalTradeReviewSelect(state.tradeReviewId);
  showTradeReviewChooser();
  syncTradeReviewButton();
  elements.tradeReviewDialog.showModal();
}

function showTradeReviewChooser() {
  elements.tradeReviewChooser.hidden = false;
  elements.tradeReviewEditor.hidden = true;
  elements.tradeReviewTitle.textContent = "交易复盘";
}

function showTradeReviewEditor(review) {
  elements.tradeReviewChooser.hidden = true;
  elements.tradeReviewEditor.hidden = false;
  elements.tradeReviewTitle.textContent = "管理交易记录";
  elements.tradeReviewEditorTitle.textContent =
    `复盘 #${review.number} · ${review.symbol}`;
}

async function openTradeReviewEditor(reviewId) {
  const review = getGlobalTradeReview(reviewId);
  if (!review) {
    throw new Error("请先新建一次复盘。");
  }
  await selectGlobalTradeReview(review.id);
  showTradeReviewEditor(review);
}

async function selectTradeReviewSymbol(rawSymbol) {
  const symbol = normalizeSymbol(rawSymbol);
  if (!symbol) {
    return;
  }
  state.selectedSymbol = symbol;
  renderWatchlist();
  await loadDetail(symbol, false);
  elements.tradeReviewTitle.textContent = `${symbol} 交易复盘`;
  const latestRow = state.currentChartData.at(-1);
  elements.tradeDateInput.value = latestRow?.Date || "";
  elements.tradePriceInput.value =
    latestRow?.Close == null ? "" : Number(latestRow.Close).toFixed(4);
  elements.tradeQuantityInput.value = "";
  elements.tradeNoteInput.value = "";
  const buyInput = elements.tradeReviewForm.querySelector('input[name="tradeSide"][value="buy"]');
  if (buyInput instanceof HTMLInputElement) {
    buyInput.checked = true;
  }
}

function populateGlobalTradeReviewSelect(preferredReviewId = null) {
  const reviews = state.allTradeReviews;
  elements.tradeReviewSelect.innerHTML = reviews.length
    ? reviews
        .map(
          (review) =>
            `<option value="${escapeHtml(review.id)}">#${review.number} · ${escapeHtml(review.symbol)}</option>`,
        )
        .join("")
    : '<option value="">暂无复盘</option>';
  elements.tradeReviewSelect.disabled = !reviews.length;
  elements.manageTradeReviewButton.disabled = !reviews.length;
  elements.activateTradeReviewButton.disabled = !reviews.length;
  const selectedId =
    preferredReviewId && reviews.some((review) => review.id === preferredReviewId)
      ? preferredReviewId
      : reviews.at(-1)?.id || "";
  elements.tradeReviewSelect.value = selectedId;
}

function getGlobalTradeReview(reviewId) {
  return state.allTradeReviews.find((review) => review.id === reviewId) || null;
}

function getTradeReview(symbol, reviewId) {
  return (state.tradeReviews.get(symbol) || []).find((review) => review.id === reviewId) || null;
}

async function selectGlobalTradeReview(reviewId) {
  const review = getGlobalTradeReview(reviewId);
  if (!review) {
    renderTradeRecords("", "");
    return;
  }
  elements.tradeSymbolSelect.value = review.symbol;
  await selectTradeReviewSymbol(review.symbol);
  elements.tradeReviewSelect.value = review.id;
  renderTradeRecords(review.symbol, review.id);
}

async function createTradeReview() {
  const symbol = normalizeSymbol(elements.tradeSymbolSelect.value);
  if (!symbol) {
    throw new Error("请选择复盘股票。");
  }
  const review = await fetchJson(`/api/trades/${encodeURIComponent(symbol)}/reviews`, {
    method: "POST",
  });
  const reviews = [...(state.tradeReviews.get(symbol) || []), review];
  state.tradeReviews.set(symbol, reviews);
  state.allTradeReviews = [...state.allTradeReviews, review].sort(
    (left, right) => Number(left.number) - Number(right.number),
  );
  populateGlobalTradeReviewSelect(review.id);
  await selectGlobalTradeReview(review.id);
  showTradeReviewEditor(review);
  showToast(`${symbol} 复盘 #${review.number} 已创建。`);
}

async function activateTradeReview() {
  const reviewId = elements.tradeReviewSelect.value;
  const review = getGlobalTradeReview(reviewId);
  if (!review) {
    throw new Error("请先新建一次复盘。");
  }
  const symbol = review.symbol;
  await selectGlobalTradeReview(reviewId);
  state.tradeReviewActive = true;
  state.tradeReviewSymbol = symbol;
  state.tradeReviewId = reviewId;
  applyTradeMarkers(symbol);
  syncTradeReviewButton();
  elements.tradeReviewDialog.close();
  elements.detailSection.scrollIntoView({ behavior: "smooth", block: "start" });
  showToast(`${symbol} 已进入复盘模式。`);
}

function exitTradeReview() {
  const previousSymbol = state.tradeReviewSymbol;
  state.tradeReviewActive = false;
  state.tradeReviewSymbol = null;
  state.tradeReviewId = null;
  if (previousSymbol) {
    applyTradeMarkers(previousSymbol);
  } else {
    state.candleTradePrimitive?.setRecords([]);
    state.closeLineTradePrimitive?.setRecords([]);
  }
  syncTradeReviewButton();
  hideHoverCard();
}

function syncTradeReviewButton() {
  elements.tradeReviewButton.classList.toggle("active-toggle", state.tradeReviewActive);
  elements.tradeReviewButton.textContent = state.tradeReviewActive
    ? `退出复盘 · #${
        getTradeReview(state.tradeReviewSymbol, state.tradeReviewId)?.number || ""
      } ${state.tradeReviewSymbol}`
    : "交易复盘";
}

async function saveTradeRecord() {
  const reviewId = elements.tradeReviewSelect.value;
  const selectedReview = getGlobalTradeReview(reviewId);
  if (!selectedReview) {
    throw new Error("请先新建一次复盘。");
  }
  const symbol = selectedReview.symbol;
  const tradeDate = elements.tradeDateInput.value;
  if (!state.currentChartData.some((row) => row.Date === tradeDate)) {
    throw new Error("该日期不是当前图表中的交易日。");
  }
  const selectedSide = elements.tradeReviewForm.querySelector('input[name="tradeSide"]:checked');
  const record = await fetchJson(
    `/api/trades/${encodeURIComponent(symbol)}/reviews/${encodeURIComponent(reviewId)}`,
    {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      date: tradeDate,
      side: selectedSide?.value || "buy",
      price: Number(elements.tradePriceInput.value),
      quantity: Number(elements.tradeQuantityInput.value),
      note: String(elements.tradeNoteInput.value || "").trim(),
    }),
  });
  const review = getTradeReview(symbol, reviewId);
  const records = [...(review?.trades || []), record].sort(
    (left, right) =>
      String(left.date).localeCompare(String(right.date)) ||
      String(left.createdAt).localeCompare(String(right.createdAt)),
  );
  if (review) {
    review.trades = records;
  }
  elements.tradeNoteInput.value = "";
  elements.tradeQuantityInput.value = "";
  renderTradeRecords(symbol, reviewId);
  applyTradeMarkers(symbol);
  showToast(`${symbol} 交易记录已添加。`);
}

async function deleteTradeRecord(tradeId) {
  const reviewId = elements.tradeReviewSelect.value;
  const selectedReview = getGlobalTradeReview(reviewId);
  if (!selectedReview || !tradeId) {
    return;
  }
  const symbol = selectedReview.symbol;
  await fetchJson(
    `/api/trades/${encodeURIComponent(symbol)}/reviews/${encodeURIComponent(reviewId)}/${encodeURIComponent(tradeId)}`,
    { method: "DELETE" },
  );
  const review = getTradeReview(symbol, reviewId);
  if (review) {
    review.trades = (review.trades || []).filter((record) => record.id !== tradeId);
  }
  renderTradeRecords(symbol, reviewId);
  applyTradeMarkers(symbol);
  showToast("交易记录已删除。");
}

function renderTradeRecords(symbol, reviewId) {
  const review = getTradeReview(symbol, reviewId);
  const records = [...(review?.trades || [])].sort(
    (left, right) =>
      String(right.date).localeCompare(String(left.date)) ||
      String(right.createdAt).localeCompare(String(left.createdAt)),
  );
  elements.tradeRecordCount.textContent = review
    ? `复盘 #${review.number} · ${records.length} 条`
    : "尚未新建复盘";
  if (!records.length) {
    elements.tradeRecordList.innerHTML = '<div class="trade-record-empty">还没有复盘记录。</div>';
    return;
  }
  elements.tradeRecordList.innerHTML = records
    .map((record) => {
      const isBuy = record.side === "buy";
      const note = String(record.note || "").trim();
      return `
        <article class="trade-record-item">
          <span class="trade-record-side ${isBuy ? "buy" : "sell"}">${isBuy ? "买入" : "卖出"}</span>
          <div class="trade-record-main">
            <strong>${escapeHtml(record.date)} · ${
              record.quantity == null ? "" : `${fmtTradeQuantity(record.quantity)} 股 @ `
            }${fmtPrice(record.price)}</strong>
            ${note ? `<p>${escapeHtml(note)}</p>` : ""}
          </div>
          <button
            type="button"
            class="secondary trade-record-delete"
            data-trade-delete="${escapeHtml(record.id)}"
            aria-label="删除该交易记录"
            title="删除"
          >删除</button>
        </article>
      `;
    })
    .join("");
}

function openNoteEditor(symbol) {
  state.activeNoteSymbol = symbol;
  elements.noteDialogTitle.textContent = `${symbol} 笔记`;
  elements.noteTextarea.value = getNoteForSymbol(symbol);
  elements.noteHoldingCheckbox.checked = getHoldingForSymbol(symbol);
  elements.noteCostBasisInput.value = getCostBasisForSymbol(symbol) ?? "";
  elements.noteSharesInput.value = getSharesForSymbol(symbol) ?? "";
  elements.deleteSymbolButton.hidden = !state.watchlist.includes(symbol);
  elements.noteDialog.showModal();
  elements.noteTextarea.focus();
}

function saveNote() {
  if (!state.activeNoteSymbol) {
    return;
  }
  const symbol = normalizeSymbol(state.activeNoteSymbol);
  const value = String(elements.noteTextarea.value || "").trim();
  const costBasis = parsePositiveNumber(elements.noteCostBasisInput.value);
  const shares = parsePositiveNumber(elements.noteSharesInput.value);
  const isHolding = !!elements.noteHoldingCheckbox.checked || costBasis != null || shares != null;
  if (value || isHolding || costBasis != null || shares != null) {
    state.notes[symbol] = {
      text: value,
      isHolding,
      costBasis,
      shares,
    };
  } else {
    delete state.notes[symbol];
  }
  persistNotes();
  renderWatchlist();
  elements.noteDialog.close();
}

async function deleteActiveSymbol() {
  if (!state.activeNoteSymbol) {
    return;
  }
  const symbol = normalizeSymbol(state.activeNoteSymbol);
  state.watchlist = state.watchlist.filter((item) => item !== symbol);
  delete state.notes[symbol];
  delete state.alertsSnapshot[symbol];
  state.alerts = state.alerts.filter((alert) => alert.symbol !== symbol);
  removeSymbolFromGroups(symbol);
  state.summaries.delete(symbol);
  state.details.delete(symbol);
  state.tradeReviews.delete(symbol);
  if (state.selectedSymbol === symbol) {
    state.selectedSymbol = state.watchlist[0] || null;
  }
  persistWatchlist();
  persistWatchlistGroups();
  persistNotes();
  persistAlerts();
  persistAlertsSnapshot();
  elements.noteDialog.close();
  renderAlerts();
  renderWatchlist();
  showToast(`${symbol} 已删除。`);
  if (state.selectedSymbol && state.summaries.get(state.selectedSymbol)?.data) {
    await loadDetail(state.selectedSymbol, false);
  } else {
    clearDetail();
  }
}

async function copyPromptForActiveSymbol() {
  const symbol = normalizeSymbol(state.activeNoteSymbol || state.selectedSymbol || "");
  if (!symbol) {
    return;
  }
  const liveNote = state.activeNoteSymbol === symbol ? String(elements.noteTextarea.value || "").trim() : "";
  const note = liveNote || getNoteForSymbol(symbol);
  const holding = buildPromptHoldingPayload(symbol);
  const payload = await fetchJson(`/api/prompt/${encodeURIComponent(symbol)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ note, holding }),
  });
  await copyTextToClipboard(String(payload.prompt || ""));
  showToast(`${symbol} 的分析 Prompt 已复制。`);
}

function buildPromptHoldingPayload(symbol) {
  const normalizedSymbol = normalizeSymbol(symbol);
  const isHolding = getHoldingForSymbol(normalizedSymbol);
  const costBasis = getCostBasisForSymbol(normalizedSymbol);
  const shares = getSharesForSymbol(normalizedSymbol);
  const holding = getHoldingSnapshot(normalizedSymbol);
  if (!isHolding && costBasis == null && shares == null && !holding) {
    return null;
  }
  return {
    isHolding: true,
    costBasis,
    shares,
    latestClose: holding?.latestClose ?? state.summaries.get(normalizedSymbol)?.data?.latestClose ?? null,
    pnlPct: holding?.pnlPct ?? null,
    pnlValue: holding?.pnlValue ?? null,
  };
}

function filterWatchlistSymbols(symbols) {
  return symbols.filter((symbol) => {
    const data = state.summaries.get(symbol)?.data;
    if (state.filterTrendTemplateOnly && !isTrendTemplateMatch(data)) {
      return false;
    }
    if (
      state.filterHoldingOnly
      && !getHoldingForSymbol(symbol)
      && !hasTradeReviewForSymbol(symbol)
    ) {
      return false;
    }
    if (state.filterVolumeBelowMA50Only && !data?.latestVolumeBelowMA50) {
      return false;
    }
    return true;
  });
}

function hasTradeReviewForSymbol(symbol) {
  const normalizedSymbol = normalizeSymbol(symbol);
  return state.allTradeReviews.some(
    (review) => normalizeSymbol(review?.symbol) === normalizedSymbol,
  );
}

function isTrendTemplateMatch(data) {
  return !!data && data.trendPassCount === data.trendTotal && data.trendTotal > 0;
}

function getVisibleWatchlistSymbols() {
  return filterWatchlistSymbols([...state.watchlist]);
}

async function syncSelectionWithFilter() {
  const visibleSymbols = getVisibleWatchlistSymbols();
  if (!visibleSymbols.length) {
    state.selectedSymbol = null;
    clearDetail();
    return;
  }
  if (state.selectedSymbol && visibleSymbols.includes(state.selectedSymbol)) {
    return;
  }
  state.selectedSymbol = visibleSymbols[0];
  renderWatchlist();
  await loadDetail(state.selectedSymbol, false);
}

function hasActiveWatchlistFilters() {
  return (
    state.filterTrendTemplateOnly
    || state.filterHoldingOnly
    || state.filterVolumeBelowMA50Only
  );
}

function syncWatchlistFilters() {
  elements.trendFilterInput.checked = state.filterTrendTemplateOnly;
  elements.holdingFilterInput.checked = state.filterHoldingOnly;
  elements.volumeBelowMA50FilterInput.checked = state.filterVolumeBelowMA50Only;
  const count =
    Number(state.filterTrendTemplateOnly) +
    Number(state.filterHoldingOnly) +
    Number(state.filterVolumeBelowMA50Only);
  elements.watchlistFilterCount.hidden = count === 0;
  elements.watchlistFilterCount.textContent = String(count);
  elements.watchlistFilterButton.classList.toggle("active-toggle", count > 0);
  elements.clearWatchlistFiltersButton.disabled = count === 0;
}

function getWatchlistEmptyMessage() {
  if (state.filterVolumeBelowMA50Only) {
    return "当前没有满足全部筛选条件的股票；成交量条件按前一交易日与50日均量比较。";
  }
  if (state.filterTrendTemplateOnly && state.filterHoldingOnly) {
    return "当前没有同时满足趋势模板且属于当前或曾经持仓的股票。";
  }
  if (state.filterTrendTemplateOnly) {
    return "当前没有满足 8 个趋势模板条件的股票。";
  }
  if (state.filterHoldingOnly) {
    return "当前没有标记为持仓或出现在交易复盘中的股票。";
  }
  return "当前没有自选股，请先添加股票。";
}

function updateAlertsFromSummary(items) {
  const nextSnapshot = {};
  const freshAlerts = [];

  for (const item of items) {
    if (!item?.data) {
      continue;
    }
    const data = item.data;
    const symbol = normalizeSymbol(data.symbol || item.symbol || "");
    if (!symbol) {
      continue;
    }

    const current = {
      latestClose: data.latestClose,
      latestDate: data.latestDate,
      dailyChangePct: data.dailyChangePct,
      fiveDayChangePct: data.fiveDayChangePct,
      trendPassCount: data.trendPassCount,
      trendTotal: data.trendTotal,
      isSixMonthHigh: !!data.isSixMonthHigh,
      isSixMonthLow: !!data.isSixMonthLow,
      sixMonthHighText: data.sixMonthHighText || "-",
      sixMonthLowText: data.sixMonthLowText || "-",
    };
    nextSnapshot[symbol] = current;

    const previous = state.alertsSnapshot[symbol];
    if (!previous) {
      continue;
    }

    const wasTemplate = previous.trendTotal > 0 && previous.trendPassCount === previous.trendTotal;
    const isTemplate = current.trendTotal > 0 && current.trendPassCount === current.trendTotal;
    if (!wasTemplate && isTemplate) {
      freshAlerts.push(createAlert(symbol, "刚刚满足趋势模板。"));
    } else if (wasTemplate && !isTemplate) {
      freshAlerts.push(createAlert(symbol, "已不再满足趋势模板。"));
    }

    if (!previous.isSixMonthHigh && current.isSixMonthHigh) {
      freshAlerts.push(createAlert(symbol, `创近 6 个月新高（${current.sixMonthHighText}）。`));
    }
    if (!previous.isSixMonthLow && current.isSixMonthLow) {
      freshAlerts.push(createAlert(symbol, `创近 6 个月新低（${current.sixMonthLowText}）。`));
    }

    if (
      current.latestDate
      && current.latestDate !== previous.latestDate
      && typeof current.dailyChangePct === "number"
      && Math.abs(current.dailyChangePct) >= 0.05
    ) {
      const direction = current.dailyChangePct > 0 ? "上涨" : "下跌";
      freshAlerts.push(createAlert(symbol, `较前一交易日${direction} ${fmtPct(current.dailyChangePct)}。`));
    }

    if (
      current.latestDate
      && current.latestDate !== previous.latestDate
      && typeof current.fiveDayChangePct === "number"
      && Math.abs(current.fiveDayChangePct) >= 0.08
    ) {
      const direction = current.fiveDayChangePct > 0 ? "上涨" : "下跌";
      freshAlerts.push(createAlert(symbol, `近 5 个交易日累计${direction} ${fmtPct(current.fiveDayChangePct)}。`));
    }
  }

  state.alertsSnapshot = nextSnapshot;
  persistAlertsSnapshot();
  if (!freshAlerts.length) {
    return;
  }
  state.alerts = [...freshAlerts, ...state.alerts].slice(0, 20);
  persistAlerts();
  renderAlerts();
}

function createAlert(symbol, message) {
  const createdAt = new Date().toISOString();
  return {
    symbol,
    message,
    createdAt,
    timeLabel: formatAlertTime(new Date(createdAt)),
  };
}

function formatAlertTime(date) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function fmtPct(value) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(1)}%`;
}

function showToast(message, isError = false, durationMs = 2200) {
  if (toastTimer) {
    clearTimeout(toastTimer);
  }
  elements.toast.hidden = false;
  elements.toast.textContent = message;
  elements.toast.classList.toggle("error", !!isError);
  toastTimer = setTimeout(() => {
    elements.toast.hidden = true;
    elements.toast.textContent = "";
    elements.toast.classList.remove("error");
    toastTimer = null;
  }, durationMs);
}

function setRefreshLoading(isLoading) {
  elements.refreshButton.disabled = isLoading;
  elements.refreshButton.classList.toggle("button-loading", isLoading);
  elements.refreshButton.setAttribute("aria-busy", isLoading ? "true" : "false");
}

function classifyChangeTone(value) {
  if (typeof value !== "number" || Number.isNaN(value) || value === 0) {
    return "";
  }
  return value > 0 ? "up" : "down";
}

function deriveChartStatus(detail) {
  const latestDate = detail?.history?.at(-1)?.Date || detail?.latestDate;
  const notes = [...(detail?.sourceNotes || []), ...(detail?.warnings || [])];
  const isStale = notes.some((note) => /可能不是最新交易日|离线|回退/.test(String(note)));
  if (!isStale || !latestDate) {
    return "";
  }
  return `最新到 ${latestDate}`;
}

function buildSparklineSvg(values, direction = "flat") {
  if (!Array.isArray(values) || values.length < 2) {
    return `<div class="watchlist-sparkline empty"></div>`;
  }

  const width = 112;
  const height = 28;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const pointsArray = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * width;
      const y = height - ((value - min) / range) * height;
      return { x, y };
    });
  const points = pointsArray.map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ");
  const endPoint = pointsArray.at(-1);

  return `
    <svg class="watchlist-sparkline ${direction}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
      <polyline points="${points}" fill="none" vector-effect="non-scaling-stroke"></polyline>
      <circle cx="${endPoint.x.toFixed(2)}" cy="${endPoint.y.toFixed(2)}" r="2.4"></circle>
    </svg>
  `;
}

function showMessage(message, isError) {
  elements.messageBar.hidden = false;
  elements.messageBar.textContent = message;
  elements.messageBar.style.background = isError ? "rgba(180, 35, 24, 0.12)" : "rgba(15, 23, 42, 0.06)";
  elements.messageBar.style.color = isError ? "#7f1d1d" : "#102033";
}

function hideMessage() {
  elements.messageBar.hidden = true;
  elements.messageBar.textContent = "";
}

async function fetchJson(url, options = undefined) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || "请求失败");
  }
  return payload;
}

async function copyTextToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "readonly");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}
