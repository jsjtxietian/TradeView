const STORAGE_KEY = "trenddeck_watchlist";
const GROUPS_STORAGE_KEY = "trenddeck_watchlist_groups";
const CHART_PREFS_KEY = "trenddeck_chart_prefs";
const NOTES_STORAGE_KEY = "trenddeck_symbol_notes";
const TREND_FILTER_STORAGE_KEY = "trenddeck_watchlist_filter_template";
const HOLDING_FILTER_STORAGE_KEY = "trenddeck_watchlist_filter_holding";
const ALERTS_STORAGE_KEY = "trenddeck_watchlist_alerts";
const ALERTS_SNAPSHOT_STORAGE_KEY = "trenddeck_watchlist_alerts_snapshot";
const DEFAULT_VISIBLE_BARS = 126;
const WATCHLIST_COLUMN_MIN_WIDTH = 280;
const WATCHLIST_COLUMN_GAP = 10;

let toastTimer = null;

const state = {
  watchlist: [],
  watchlistGroups: [],
  notes: {},
  alerts: [],
  alertsSnapshot: {},
  alertsOpen: false,
  filterTrendTemplateOnly: false,
  filterHoldingOnly: false,
  activeNoteSymbol: null,
  draggingGroupId: null,
  draggingSymbol: null,
  selectedSymbol: null,
  chartMode: "close",
  currentChartData: [],
  summaries: new Map(),
  details: new Map(),
  priceChart: null,
  volumeChart: null,
  candleSeries: null,
  closeLineSeries: null,
  volumeSeries: null,
  maSeries: [],
  maVisibility: { MA20: true, MA50: true, MA150: true, MA200: true },
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
  trendFilterButton: document.getElementById("trendFilterButton"),
  holdingFilterButton: document.getElementById("holdingFilterButton"),
  editGroupsButton: document.getElementById("editGroupsButton"),
  refreshButton: document.getElementById("refreshButton"),
  chartMode: document.getElementById("chartMode"),
  maToggleGroup: document.getElementById("maToggleGroup"),
  chartHoverCard: document.getElementById("chartHoverCard"),
  messageBar: document.getElementById("messageBar"),
  detailSection: document.getElementById("detailSection"),
  detailTitle: document.getElementById("detailTitle"),
  chartTitle: document.getElementById("chartTitle"),
  chartHeadlineStats: document.getElementById("chartHeadlineStats"),
  trendChecks: document.getElementById("trendChecks"),
  advancedTrendChecks: document.getElementById("advancedTrendChecks"),
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
  deleteSymbolButton: document.getElementById("deleteSymbolButton"),
  clearNoteButton: document.getElementById("clearNoteButton"),
  cancelNoteButton: document.getElementById("cancelNoteButton"),
  saveNoteButton: document.getElementById("saveNoteButton"),
  toast: document.getElementById("toast"),
};

init().catch((error) => {
  showMessage(error.message || String(error), true);
});

async function init() {
  bindEvents();
  const config = await fetchJson("/api/config");
  loadChartPrefs();
  state.notes = loadStoredNotes();
  state.alerts = loadStoredAlerts();
  state.alertsSnapshot = loadStoredAlertsSnapshot();
  state.filterTrendTemplateOnly = loadStoredTrendFilter();
  state.filterHoldingOnly = loadStoredHoldingFilter();
  state.watchlistGroups = loadStoredWatchlistGroups(
    normalizeWatchlistGroups(config.watchlistGroups || []),
  );
  elements.chartMode.value = state.chartMode;
  syncTrendFilterButton();
  syncHoldingFilterButton();
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
    await refreshSummaries();
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
    state.alertsOpen = true;
    elements.alertsDialog.showModal();
  });

  elements.closeAlertsButton.addEventListener("click", () => {
    state.alertsOpen = false;
    renderAlerts();
    elements.alertsDialog.close();
  });

  elements.trendFilterButton.addEventListener("click", () => {
    state.filterTrendTemplateOnly = !state.filterTrendTemplateOnly;
    persistTrendFilter();
    syncTrendFilterButton();
    renderWatchlist();
    syncSelectionWithFilter().catch((error) => {
      showMessage(error.message || String(error), true);
    });
  });

  elements.holdingFilterButton.addEventListener("click", () => {
    state.filterHoldingOnly = !state.filterHoldingOnly;
    persistHoldingFilter();
    syncHoldingFilterButton();
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

  elements.clearNoteButton.addEventListener("click", () => {
    if (!state.activeNoteSymbol) {
      return;
    }
    elements.noteTextarea.value = "";
  });

  elements.saveNoteButton.addEventListener("click", () => {
    saveNote();
  });

  elements.noteDialog.addEventListener("close", () => {
    state.activeNoteSymbol = null;
  });

  elements.alertsDialog.addEventListener("close", () => {
    state.alertsOpen = false;
  });

  elements.chartMode.addEventListener("change", () => {
    state.chartMode = elements.chartMode.value;
    persistChartPrefs();
    applyChartMode();
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

  window.addEventListener("resize", () => {
    resizeCharts();
    queueWatchlistLayoutRefresh();
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
    if (state.filterTrendTemplateOnly && (!state.selectedSymbol || !visibleSymbols.includes(state.selectedSymbol))) {
      state.selectedSymbol = visibleSymbols[0] || null;
    }

    const selectionPool = state.filterTrendTemplateOnly ? visibleSymbols : state.watchlist;
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

async function loadDetail(symbol, forceRefresh = false) {
  if (!symbol) {
    clearDetail();
    return;
  }

  try {
    const detail =
      !forceRefresh && state.details.get(symbol)
        ? state.details.get(symbol)
        : await fetchJson(`/api/symbol/${encodeURIComponent(symbol)}?refresh=${forceRefresh ? "1" : "0"}`);
    state.details.set(symbol, detail);
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
  elements.detailTitle.textContent = detail.symbol;
  elements.chartTitle.textContent = detail.symbol;
  renderChartHeadlineStats(detail);
  renderChartStatus(detail);
  renderChecks(elements.trendChecks, detail.trendChecks);
  renderChecks(elements.advancedTrendChecks, detail.advancedTrendChecks, { trimPrefix: true });
  logDetailMessages(detail);
  renderMainChart(detail);
  syncMaToggleInputs();
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
    await refreshSummaries(true);
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
    .filter((section) => section.symbols.length || !state.filterTrendTemplateOnly);

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
    moveSymbolToSection(state.draggingSymbol.symbol, sectionId, null, "end");
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
    moveSymbolToSection(state.draggingSymbol.symbol, sectionId, null, "end");
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
  state.watchlistGroups = groups;
  persistWatchlistGroups();
  renderWatchlist();
  showToast("分组顺序已更新。");
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
  state.watchlistGroups = groups;
  persistWatchlistGroups();
  renderWatchlist();
  showToast("分组顺序已更新。");
}

function moveGroupToEnd(sourceId) {
  const groups = [...state.watchlistGroups];
  const sourceIndex = groups.findIndex((group) => group.id === sourceId);
  if (sourceIndex < 0 || sourceIndex === groups.length - 1) {
    return;
  }
  const [moved] = groups.splice(sourceIndex, 1);
  groups.push(moved);
  state.watchlistGroups = groups;
  persistWatchlistGroups();
  renderWatchlist();
  showToast("分组顺序已更新。");
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
    state.draggingSymbol = { symbol, sectionId };
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
    if (!state.draggingSymbol || state.draggingSymbol.symbol === symbol) {
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
    if (!state.draggingSymbol || state.draggingSymbol.symbol === symbol) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const placement = getSymbolDropPlacement(card, event.clientY);
    clearSymbolDragOverStates();
    moveSymbolToSection(state.draggingSymbol.symbol, sectionId, symbol, placement);
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
    const stateClass = item.passed === true ? "pass" : item.passed === false ? "fail" : "pending";
    const displayName = options.trimPrefix ? stripCheckNamePrefix(item.name) : item.name;
    node.innerHTML = `
      <strong>
        ${displayName}
        <span class="check-state ${stateClass}">${stateLabel}</span>
      </strong>
      <p>${item.detail}</p>
    `;
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
    { label: "当日成交量", value: detail.latestVolumeText || "-" },
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
}

function renderMainChart(detail) {
  const chartData = detail.history || [];
  state.currentChartData = chartData;
  if (!state.priceChart) {
    createMainChart();
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

  state.volumeSeries.setData(
    chartData.map((row) => ({
      time: row.Date,
      value: row.Volume,
      color: row.Close >= row.Open ? "#0f9d58aa" : "#db4437aa",
    })),
  );

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
  const dashedLineStyle = LightweightCharts.LineStyle?.Dashed ?? 2;
  const dottedLineStyle = LightweightCharts.LineStyle?.Dotted ?? 1;

  state.priceChart = LightweightCharts.createChart(elements.priceChartContainer, {
    layout: {
      background: { color: "rgba(255,255,255,0)" },
      textColor: "#102033",
    },
    rightPriceScale: {
      borderColor: "rgba(15, 23, 42, 0.12)",
    },
    timeScale: {
      borderColor: "rgba(15, 23, 42, 0.12)",
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

  state.candleSeries = state.priceChart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: "#0f9d58",
    downColor: "#db4437",
    borderVisible: false,
    wickUpColor: "#0f9d58",
    wickDownColor: "#db4437",
    lastValueVisible: false,
    priceLineVisible: false,
  });

  state.closeLineSeries = state.priceChart.addSeries(LightweightCharts.LineSeries, {
    color: "#0e7490",
    lineWidth: 3,
    title: "",
    crosshairMarkerVisible: true,
    lineStyle: LightweightCharts.LineStyle?.Solid ?? 0,
    lastValueVisible: false,
    priceLineVisible: false,
  });

  state.maSeries = [
    state.priceChart.addSeries(LightweightCharts.LineSeries, {
      color: "#d48a00cc",
      lineWidth: 1.5,
      lineStyle: dottedLineStyle,
      title: "",
      lastValueVisible: false,
      priceLineVisible: false,
    }),
    state.priceChart.addSeries(LightweightCharts.LineSeries, {
      color: "#0077b6bb",
      lineWidth: 1.5,
      lineStyle: dashedLineStyle,
      title: "",
      lastValueVisible: false,
      priceLineVisible: false,
    }),
    state.priceChart.addSeries(LightweightCharts.LineSeries, {
      color: "#6d597aaa",
      lineWidth: 1.5,
      lineStyle: dashedLineStyle,
      title: "",
      lastValueVisible: false,
      priceLineVisible: false,
    }),
    state.priceChart.addSeries(LightweightCharts.LineSeries, {
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

  state.volumeChart = LightweightCharts.createChart(elements.volumeChartContainer, {
    layout: {
      background: { color: "rgba(255,255,255,0)" },
      textColor: "#617286",
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

  state.volumeSeries = state.volumeChart.addSeries(LightweightCharts.HistogramSeries, {
    priceFormat: { type: "volume" },
    priceScaleId: "right",
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
      state.volumeChart.timeScale().setVisibleLogicalRange(clamped);
    }
  });

  state.priceChart.subscribeCrosshairMove((param) => {
    if (!param.time || !param.point) {
      hideHoverCard();
      return;
    }
    const row = state.currentChartData.find((item) => item.Date === param.time);
    updateHoverCard(row || state.currentChartData.at(-1), param.point);
  });

  bindChartWheelZoom();
}

function applyVisibleWindow(history) {
  if (!history.length || !state.priceChart || !state.volumeChart) {
    return;
  }

  const visibleBars = Math.min(DEFAULT_VISIBLE_BARS, history.length);
  const range = buildRightAnchoredRange(visibleBars, history.length);
  state.priceChart.timeScale().setVisibleLogicalRange(range);
  state.volumeChart.timeScale().setVisibleLogicalRange(range);
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
    state.volumeChart.timeScale().setVisibleLogicalRange(range);
  };

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
  state.volumeChart.timeScale().setVisibleLogicalRange(clamped);
}

function buildRightAnchoredRange(width, dataLength) {
  const lastLogical = dataLength - 0.5;
  const from = Math.max(-0.5, lastLogical - width);
  return { from, to: lastLogical };
}

function clearDetail() {
  elements.detailSection.hidden = true;
  elements.detailTitle.textContent = "选择一只股票";
  elements.chartTitle.textContent = "选择一只股票";
  elements.chartHeadlineStats.innerHTML = "";
  hideChartStatus();
  elements.trendChecks.innerHTML = "";
  elements.advancedTrendChecks.innerHTML = "";
  hideHoverCard();
  destroyCharts();
}

function destroyCharts() {
  if (state.priceChart) {
    state.priceChart.remove();
    state.priceChart = null;
    state.candleSeries = null;
    state.closeLineSeries = null;
    state.maSeries = [];
  }
  if (state.volumeChart) {
    state.volumeChart.remove();
    state.volumeChart = null;
    state.volumeSeries = null;
  }
  elements.priceChartContainer.innerHTML = "";
  elements.volumeChartContainer.innerHTML = "";
}

function resizeCharts() {
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
}

function applyChartMode() {
  if (!state.candleSeries || !state.closeLineSeries) {
    return;
  }
  const closeMode = state.chartMode === "close";
  state.candleSeries.applyOptions({ visible: !closeMode });
  state.closeLineSeries.applyOptions({ visible: closeMode });
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
    if (parsed.maVisibility && typeof parsed.maVisibility === "object") {
      for (const key of ["MA20", "MA50", "MA150", "MA200"]) {
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
            return text ? [normalizedSymbol, { text, isHolding: false }] : null;
          }
          if (!note || typeof note !== "object" || Array.isArray(note)) {
            return null;
          }
          const text = String(note.text || "").trim();
          const isHolding = !!note.isHolding;
          if (!text && !isHolding) {
            return null;
          }
          return [normalizedSymbol, { text, isHolding }];
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

function loadStoredAlerts() {
  try {
    const raw = localStorage.getItem(ALERTS_STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.slice(0, 20) : [];
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

function getHoldingForSymbol(symbol) {
  return !!state.notes[normalizeSymbol(symbol)]?.isHolding;
}

function renderNoteButton(symbol) {
  const noteText = getNoteForSymbol(symbol);
  const isHolding = getHoldingForSymbol(symbol);
  const hasMeta = !!noteText || isHolding;
  const activeClass = hasMeta ? " has-note" : "";
  const title = noteText
    ? (isHolding ? `持仓股 · ${noteText}` : noteText)
    : (isHolding ? "持仓股" : "查看或编辑笔记");
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

function openNoteEditor(symbol) {
  state.activeNoteSymbol = symbol;
  elements.noteDialogTitle.textContent = `${symbol} 笔记`;
  elements.noteTextarea.value = getNoteForSymbol(symbol);
  elements.noteHoldingCheckbox.checked = getHoldingForSymbol(symbol);
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
  const isHolding = !!elements.noteHoldingCheckbox.checked;
  if (value || isHolding) {
    state.notes[symbol] = {
      text: value,
      isHolding,
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

function filterWatchlistSymbols(symbols) {
  return symbols.filter((symbol) => {
    const data = state.summaries.get(symbol)?.data;
    if (state.filterTrendTemplateOnly && !(!!data && data.trendPassCount === data.trendTotal && data.trendTotal > 0)) {
      return false;
    }
    if (state.filterHoldingOnly && !getHoldingForSymbol(symbol)) {
      return false;
    }
    return true;
  });
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

function syncTrendFilterButton() {
  elements.trendFilterButton.classList.toggle("active-toggle", state.filterTrendTemplateOnly);
}

function syncHoldingFilterButton() {
  elements.holdingFilterButton.classList.toggle("active-toggle", state.filterHoldingOnly);
}

function getWatchlistEmptyMessage() {
  if (state.filterTrendTemplateOnly && state.filterHoldingOnly) {
    return "当前没有同时满足趋势模板且标记为持仓的股票。";
  }
  if (state.filterTrendTemplateOnly) {
    return "当前没有满足 8 个趋势模板条件的股票。";
  }
  if (state.filterHoldingOnly) {
    return "当前没有标记为持仓的股票。";
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
      typeof previous.latestClose === "number"
      && typeof current.latestClose === "number"
      && previous.latestClose > 0
    ) {
      const move = current.latestClose / previous.latestClose - 1;
      if (Math.abs(move) >= 0.05) {
        const direction = move > 0 ? "上涨" : "下跌";
        freshAlerts.push(createAlert(symbol, `较上次快照${direction} ${fmtPct(move)}。`));
      }
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
  return `缓存数据，最新到 ${latestDate}`;
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

async function fetchJson(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || "请求失败");
  }
  return payload;
}
