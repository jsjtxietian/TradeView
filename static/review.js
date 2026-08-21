const elements = {
  range: document.getElementById("reviewRange"),
  coverage: document.getElementById("reviewCoverage"),
  summary: document.getElementById("reviewSummary"),
  distribution: document.getElementById("reviewDistribution"),
  largestWins: document.getElementById("reviewLargestWins"),
  largestLosses: document.getElementById("reviewLargestLosses"),
  ledgerCoverage: document.getElementById("reviewLedgerCoverage"),
  cashSummary: document.getElementById("reviewCashSummary"),
  dividends: document.getElementById("reviewDividends"),
  interest: document.getElementById("reviewInterest"),
  misc: document.getElementById("reviewMisc"),
};

let allResults = [];
let ledger = null;
let extremeMode = "percent";

init().catch((error) => {
  elements.coverage.textContent = error.message || String(error);
});

async function init() {
  const [tradeResponse, ledgerResponse] = await Promise.all([
    fetch("/api/trades"),
    fetch("/api/review/ledger"),
  ]);
  if (!tradeResponse.ok) {
    throw new Error(`交易数据读取失败（${tradeResponse.status}）`);
  }
  const payload = await tradeResponse.json();
  ledger = ledgerResponse.ok ? await ledgerResponse.json() : null;
  allResults = (payload.trades || [])
    .map(calculateTradeResult)
    .filter(Boolean)
    .sort((left, right) => left.exitDate.localeCompare(right.exitDate) || left.id - right.id);
  elements.range.value = "ytd";
  elements.range.addEventListener("change", render);
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-extreme-mode]");
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    extremeMode = button.dataset.extremeMode === "amount" ? "amount" : "percent";
    for (const candidate of document.querySelectorAll("[data-extreme-mode]")) {
      candidate.classList.toggle("active", candidate.dataset.extremeMode === extremeMode);
    }
    render();
  });
  render();
}

function calculateTradeResult(trade) {
  const direction = trade.direction === "short" ? "short" : "long";
  const transactions = trade.transactions || [];
  const openActions = direction === "short" ? new Set(["short", "add_short"]) : new Set(["buy", "add"]);
  const closeAction = direction === "short" ? "cover" : "sell";
  const opening = transactions.filter((transaction) => openActions.has(transaction.action));
  const closing = transactions.filter((transaction) => transaction.action === closeAction);
  if (!opening.length || !closing.length) {
    return null;
  }
  const openingAmount = sumAmounts(opening);
  const closingAmount = sumAmounts(closing);
  if (!(openingAmount > 0)) {
    return null;
  }
  const pnl = direction === "short"
    ? openingAmount - closingAmount
    : closingAmount - openingAmount;
  const entryDate = opening.map((item) => item.date).sort()[0];
  const exitDate = closing.map((item) => item.date).sort().at(-1);
  return {
    id: Number(trade.id),
    symbol: trade.symbol,
    currency: String(trade.currency || "USD").toUpperCase(),
    direction,
    entryDate,
    exitDate,
    holdDays: Math.max(0, Math.round((parseIsoDate(exitDate) - parseIsoDate(entryDate)) / 86_400_000)),
    basis: openingAmount,
    pnl,
    returnPct: pnl / openingAmount,
  };
}

function sumAmounts(transactions) {
  return transactions.reduce(
    (total, item) => total + Number(item.quantity || 0) * Number(item.price || 0),
    0,
  );
}

function parseIsoDate(value) {
  const [year, month, day] = String(value).split("-").map(Number);
  return new Date(year, month - 1, day);
}

function formatLocalIsoDate(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function getStartDate(range) {
  if (range === "all") {
    return null;
  }
  const anchor = new Date();
  if (range === "ytd") {
    return new Date(anchor.getFullYear(), 0, 1);
  }
  const months = { "1m": 1, "3m": 3, "6m": 6, "12m": 12 }[range];
  const start = new Date(anchor);
  start.setMonth(start.getMonth() - months);
  return start;
}

function render() {
  const startDate = getStartDate(elements.range.value);
  const rangeResults = startDate
    ? allResults.filter((result) => parseIsoDate(result.exitDate) >= startDate)
    : allResults;
  const boxxResults = rangeResults.filter((result) => result.symbol === "BOXX");
  const results = rangeResults.filter((result) => result.symbol !== "BOXX");
  const longCount = results.filter((result) => result.direction === "long").length;
  const shortCount = results.length - longCount;
  elements.coverage.textContent = results.length
    ? `${results[0].exitDate} 至 ${results.at(-1).exitDate} · ${results.length} 笔普通完整交易（${longCount} 多 / ${shortCount} 空）· BOXX 归入类现金收益`
    : "所选区间没有完整平仓交易";
  renderSummary(results);
  renderLedger(startDate, boxxResults);
  renderDistribution(results);
  renderExtremeList(elements.largestWins, results, "win", extremeMode);
  renderExtremeList(elements.largestLosses, results, "loss", extremeMode);
}

function renderLedger(startDate, boxxResults) {
  if (!ledger?.available) {
    elements.ledgerCoverage.textContent = "未找到 .trade/ledger.json，请先导入 IBKR 账务数据";
    for (const container of [elements.cashSummary, elements.dividends, elements.interest, elements.misc]) {
      container.innerHTML = '<div class="review-empty compact">暂无账单数据</div>';
    }
    return;
  }
  const entries = (ledger.entries || []).filter(
    (entry) => !startDate || parseIsoDate(entry.date) >= startDate,
  );
  const currency = ledger.baseCurrency || "USD";
  const commissions = sumEntries(entries, "commission");
  const grossDividends = sumEntries(entries, "dividend");
  const withholding = sumEntries(entries, "withholding_tax");
  const netDividends = grossDividends + withholding;
  const brokerInterest = sumEntries(entries, "credit_interest");
  const debitInterest = sumEntries(entries, "debit_interest");
  const boxxPnl = boxxResults.reduce((total, result) => total + result.pnl, 0);
  const miscIncome = sumEntries(entries, "misc_income");
  const miscExpense = sumEntries(entries, "misc_expense");
  const cashIncome = brokerInterest + boxxPnl;
  const selectedStart = startDate ? formatLocalIsoDate(startDate) : ledger.startDate;
  elements.ledgerCoverage.textContent =
    `当前统计 ${selectedStart} 至 ${ledger.endDate} · JSON 数据覆盖 ${ledger.startDate} 至 ${ledger.endDate} · 基础货币 ${currency}`;
  const cards = [
    ["全部佣金", fmtMoney(commissions, currency), "买卖及外汇成交佣金净额", commissions ? "negative" : ""],
    ["股息净收入", fmtMoney(netDividends, currency), `毛额 ${fmtMoney(grossDividends, currency)} · 预扣税 ${fmtMoney(withholding, currency)}`, "positive"],
    ["现金利息 / 类现金", fmtMoney(cashIncome, currency), `IBKR ${fmtMoney(brokerInterest, currency)} · BOXX ${fmtMoney(boxxPnl, currency)}`, cashIncome >= 0 ? "positive" : "negative"],
    ["融资利息", fmtMoney(debitInterest, currency), "借方利息与借贷费用", debitInterest < 0 ? "negative" : ""],
    ["其他收支净额", fmtMoney(miscIncome + miscExpense, currency), `收入 ${fmtMoney(miscIncome, currency)} · 支出 ${fmtMoney(miscExpense, currency)}`, miscIncome + miscExpense >= 0 ? "positive" : "negative"],
  ];
  elements.cashSummary.innerHTML = cards.map(([label, value, detail, tone]) => `
    <article class="review-stat-card ${tone}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(detail)}</small>
    </article>
  `).join("");
  renderDividendList(entries, currency);
  renderInterestList(brokerInterest, boxxPnl, debitInterest, currency);
  renderMiscList(entries, currency);
}

function sumEntries(entries, category) {
  return entries
    .filter((entry) => entry.category === category)
    .reduce((total, entry) => total + Number(entry.amount || 0), 0);
}

function renderDividendList(entries, currency) {
  const grouped = {};
  for (const entry of entries.filter((item) => ["dividend", "withholding_tax"].includes(item.category))) {
    const symbol = entry.symbol || "未分类";
    grouped[symbol] ||= { symbol, gross: 0, tax: 0 };
    if (entry.category === "dividend") grouped[symbol].gross += entry.amount;
    else grouped[symbol].tax += entry.amount;
  }
  const rows = Object.values(grouped)
    .map((item) => ({ ...item, net: item.gross + item.tax }))
    .sort((left, right) => right.net - left.net);
  renderLedgerRows(
    elements.dividends,
    rows,
    (item) => `<b>${escapeHtml(item.symbol)}</b><span>毛额 ${escapeHtml(fmtMoney(item.gross, currency))} · 税 ${escapeHtml(fmtMoney(item.tax, currency))}</span><strong>${escapeHtml(fmtMoney(item.net, currency))}</strong>`,
  );
}

function renderInterestList(brokerInterest, boxxPnl, debitInterest, currency) {
  const rows = [
    { label: "IBKR 贷方利息 / 股票收益提升", detail: "账单贷方利息", amount: brokerInterest },
    { label: "BOXX 已实现价差", detail: "完整平仓收益，未扣佣金", amount: boxxPnl },
    { label: "融资及借贷利息", detail: "账单借方利息", amount: debitInterest },
  ].filter((item) => item.amount);
  renderLedgerRows(
    elements.interest,
    rows,
    (item) => `<b>${escapeHtml(item.label)}</b><span>${escapeHtml(item.detail)}</span><strong class="${item.amount < 0 ? "negative" : "positive"}">${escapeHtml(fmtMoney(item.amount, currency))}</strong>`,
  );
}

function renderMiscList(entries, currency) {
  const grouped = {};
  for (const entry of entries.filter((item) => ["misc_income", "misc_expense"].includes(item.category))) {
    const key = entry.description || "未分类";
    grouped[key] ||= { label: key, amount: 0 };
    grouped[key].amount += entry.amount;
  }
  const rows = Object.values(grouped)
    .filter((item) => Math.abs(item.amount) >= 0.005)
    .sort((left, right) => Math.abs(right.amount) - Math.abs(left.amount));
  renderLedgerRows(
    elements.misc,
    rows,
    (item) => `<b>${escapeHtml(item.label)}</b><span>${item.amount >= 0 ? "其他收入" : "其他支出"}</span><strong class="${item.amount < 0 ? "negative" : "positive"}">${escapeHtml(fmtMoney(item.amount, currency))}</strong>`,
  );
}

function renderLedgerRows(container, rows, formatter) {
  container.innerHTML = rows.length
    ? rows.map((item) => `<div class="review-ledger-item">${formatter(item)}</div>`).join("")
    : '<div class="review-empty compact">所选区间暂无数据</div>';
}

function renderSummary(results) {
  const wins = results.filter((result) => result.returnPct > 0);
  const losses = results.filter((result) => result.returnPct < 0);
  const averageWin = average(wins.map((result) => result.returnPct));
  const averageLoss = average(losses.map((result) => Math.abs(result.returnPct)));
  const battingAverage = results.length ? wins.length / results.length : null;
  const lossRate = results.length ? losses.length / results.length : null;
  const winLossRatio = averageLoss ? averageWin / averageLoss : null;
  const adjustedRatio = winLossRatio != null && lossRate
    ? winLossRatio * battingAverage / lossRate
    : null;
  const currencyStats = buildCurrencyStats(results);
  const primary = Object.values(currencyStats).sort((left, right) => right.count - left.count)[0] || null;
  const primaryResults = primary
    ? results.filter((result) => result.currency === primary.currency)
    : [];
  const primaryWins = primaryResults.filter((result) => result.pnl > 0);
  const primaryLosses = primaryResults.filter((result) => result.pnl < 0);
  const largestMoneyWin = maxBy(primaryWins, (result) => result.pnl);
  const largestMoneyLoss = minBy(primaryLosses, (result) => result.pnl);
  const largestPctWin = maxBy(wins, (result) => result.returnPct);
  const largestPctLoss = minBy(losses, (result) => result.returnPct);
  const otherCurrencies = Object.values(currencyStats)
    .filter((item) => item.currency !== primary?.currency)
    .map((item) => `${item.currency} ${fmtSignedMoney(item.pnl)}`)
    .join(" · ");
  const cards = [
    ["平均盈利", fmtPct(averageWin), `${wins.length} 笔盈利交易`, "positive"],
    ["平均亏损", averageLoss == null ? "-" : fmtPct(-averageLoss), `${losses.length} 笔亏损交易`, "negative"],
    ["胜负比", fmtRatio(winLossRatio), "平均盈利 / 平均亏损"],
    ["胜率", battingAverage == null ? "-" : `${(battingAverage * 100).toFixed(1)}%`, `${wins.length} 胜 · ${losses.length} 负`],
    ["调整后胜负比", fmtRatio(adjustedRatio), "胜负比 × 胜率 / 败率", adjustedRatio >= 1 ? "positive" : "negative"],
    ["最大比例盈利 / 亏损", `${fmtPct(largestPctWin?.returnPct)} / ${fmtPct(largestPctLoss?.returnPct)}`, `${largestPctWin?.symbol || "-"} / ${largestPctLoss?.symbol || "-"}`],
    ["盈利单持有", fmtDays(average(wins.map((result) => result.holdDays))), "盈利交易平均持有天数"],
    ["亏损单持有", fmtDays(average(losses.map((result) => result.holdDays))), "亏损交易平均持有天数"],
    ["平仓价差盈亏", primary ? `${primary.currency} ${fmtSignedMoney(primary.pnl)}` : "-", otherCurrencies || "不含股息、佣金及利息", primary?.pnl >= 0 ? "positive" : "negative"],
    ["Profit Factor", primary ? fmtRatio(primary.profitFactor) : "-", primary ? `${primary.currency} 总盈利 / 总亏损` : "总盈利 / 总亏损", primary?.profitFactor >= 1 ? "positive" : "negative"],
    ["最大金额盈利 / 亏损", primary ? `${fmtSignedMoney(largestMoneyWin?.pnl)} / ${fmtSignedMoney(largestMoneyLoss?.pnl)}` : "-", primary ? `${primary.currency} · ${largestMoneyWin?.symbol || "-"} / ${largestMoneyLoss?.symbol || "-"}` : "按交易金额比较"],
  ];
  elements.summary.innerHTML = cards.map(([label, value, detail, tone = ""]) => `
    <article class="review-stat-card ${tone}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(detail)}</small>
    </article>
  `).join("");
}

function buildCurrencyStats(results) {
  const stats = {};
  for (const result of results) {
    const current = stats[result.currency] || {
      currency: result.currency,
      count: 0,
      pnl: 0,
      grossProfit: 0,
      grossLoss: 0,
      profitFactor: null,
    };
    current.count += 1;
    current.pnl += result.pnl;
    if (result.pnl > 0) {
      current.grossProfit += result.pnl;
    } else if (result.pnl < 0) {
      current.grossLoss += Math.abs(result.pnl);
    }
    stats[result.currency] = current;
  }
  for (const current of Object.values(stats)) {
    current.profitFactor = current.grossLoss
      ? current.grossProfit / current.grossLoss
      : current.grossProfit
        ? Number.POSITIVE_INFINITY
        : null;
  }
  return stats;
}

function renderExtremeList(container, results, direction, mode) {
  const primaryCurrency = getPrimaryCurrency(results);
  const candidates = mode === "amount" && primaryCurrency
    ? results.filter((result) => result.currency === primaryCurrency)
    : results;
  const metric = mode === "amount"
    ? (result) => result.pnl
    : (result) => result.returnPct;
  const ordered = candidates
    .filter((result) => direction === "win" ? result.returnPct > 0 : result.returnPct < 0)
    .sort((left, right) => direction === "win"
      ? metric(right) - metric(left)
      : metric(left) - metric(right))
    .slice(0, 6);
  if (!ordered.length) {
    container.innerHTML = '<div class="review-empty">暂无数据</div>';
    return;
  }
  container.innerHTML = ordered.map((result, index) => `
    <div class="review-extreme-item ${direction}">
      <b>#${index + 1}</b>
      <span>${escapeHtml(result.symbol)} · ${result.direction === "short" ? "空" : "多"} · ${escapeHtml(result.exitDate)} · ${result.holdDays} 天</span>
      <strong>${
        mode === "amount"
          ? `${escapeHtml(result.currency)} ${escapeHtml(fmtSignedMoney(result.pnl))}<br />${escapeHtml(fmtPct(result.returnPct))}`
          : `${escapeHtml(fmtPct(result.returnPct))}<br />${escapeHtml(result.currency)} ${escapeHtml(fmtSignedMoney(result.pnl))}`
      }</strong>
    </div>
  `).join("");
}

function getPrimaryCurrency(results) {
  const counts = {};
  for (const result of results) {
    counts[result.currency] = (counts[result.currency] || 0) + 1;
  }
  return Object.entries(counts)
    .sort((left, right) => right[1] - left[1])[0]?.[0] || null;
}

function renderDistribution(results) {
  if (!results.length) {
    elements.distribution.innerHTML = '<div class="review-empty">所选区间暂无交易</div>';
    return;
  }
  const values = results.map((result) => result.returnPct * 100);
  const binSize = 2;
  const minValue = Math.floor(Math.min(...values, 0) / binSize) * binSize;
  const actualMax = Math.ceil(Math.max(...values, 0) / binSize) * binSize;
  const regularMax = Math.min(30, Math.max(2, actualMax));
  const bins = [];
  for (let lower = minValue; lower < regularMax; lower += binSize) {
    bins.push({
      lower,
      upper: lower + binSize,
      count: 0,
      extreme: false,
    });
  }
  const hasExtreme = Math.max(...values) > regularMax;
  if (hasExtreme) {
    bins.push({
      lower: regularMax,
      upper: Math.max(...values),
      count: 0,
      extreme: true,
    });
  }
  for (const value of values) {
    const index = hasExtreme && value > regularMax
      ? bins.length - 1
      : Math.max(
        0,
        Math.min(
          bins.length - 1 - (hasExtreme ? 1 : 0),
          Math.floor((value - minValue) / binSize),
        ),
      );
    bins[index].count += 1;
  }
  const width = 1120;
  const height = 320;
  const margin = { top: 16, right: 16, bottom: 52, left: 42 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const maxCount = Math.max(...bins.map((bin) => bin.count), 1);
  const yTicks = buildCountTicks(maxCount);
  const ceiling = yTicks.at(-1);
  const extremeGap = hasExtreme ? 26 : 0;
  const slotWidth = (plotWidth - extremeGap) / bins.length;
  const barWidth = Math.max(3, slotWidth * 0.48);
  const grid = yTicks.map((tick) => {
    const y = margin.top + plotHeight - tick / ceiling * plotHeight;
    return `<line class="grid-line" x1="${margin.left}" y1="${y}" x2="${width - margin.right}" y2="${y}" /><text x="${margin.left - 8}" y="${y + 3}" text-anchor="end">${tick}</text>`;
  }).join("");
  const bars = bins.map((bin, index) => {
    const barHeight = bin.count / ceiling * plotHeight;
    const x = margin.left
      + index * slotWidth
      + (bin.extreme ? extremeGap : 0)
      + (slotWidth - barWidth) / 2;
    const y = margin.top + plotHeight - barHeight;
    const tone = bin.extreme || bin.lower >= 0
      ? "bar-win"
      : bin.upper <= 0
        ? "bar-loss"
        : "bar-flat";
    const every = bins.length > 18 ? 2 : 1;
    const lowerLabel = bin.extreme ? "极值" : fmtBin(bin.lower);
    const rangeLabel = bin.extreme
      ? `高于 ${fmtBin(regularMax)}`
      : `${fmtBin(bin.lower)} 至 ${fmtBin(bin.upper)}`;
    const overflowResults = bin.extreme
      ? results
        .filter((result) => result.returnPct * 100 > regularMax)
        .sort((left, right) => right.returnPct - left.returnPct)
      : [];
    const overflowLabel = overflowResults.length
      ? `${overflowResults[0].symbol} ${fmtPct(overflowResults[0].returnPct)}`
      : "";
    const overflowTitle = overflowResults.length
      ? `；${overflowResults
        .slice(0, 5)
        .map((result) => `${result.symbol} ${fmtPct(result.returnPct)}`)
        .join("、")}`
      : "";
    return `
      <rect class="${tone}" x="${x}" y="${y}" width="${barWidth}" height="${barHeight}"><title>${rangeLabel}：${bin.count} 笔${overflowTitle}</title></rect>
      ${bin.count ? `<text x="${x + barWidth / 2}" y="${Math.max(11, y - 5)}" text-anchor="middle">${bin.count}</text>` : ""}
      ${overflowLabel ? `<text class="overflow-label" x="${x + barWidth / 2}" y="${Math.max(22, y - 19)}" text-anchor="end">${escapeHtml(overflowLabel)}</text>` : ""}
      ${bin.extreme || index % every === 0 ? `<text x="${x + barWidth / 2}" y="${height - 25}" text-anchor="end" transform="rotate(-45 ${x + barWidth / 2} ${height - 25})">${lowerLabel}</text>` : ""}
    `;
  }).join("");
  const smoothed = bins.map((_, index) => {
    let total = 0;
    let weights = 0;
    bins.forEach((bin, binIndex) => {
      const distance = binIndex - index;
      const weight = Math.exp(-(distance * distance) / 2);
      total += bin.count * weight;
      weights += weight;
    });
    return total / weights;
  });
  const curve = smoothPath(smoothed
    .map((count, index) => ({ count, index }))
    .filter(({ index }) => !bins[index].extreme)
    .map(({ count, index }) => ({
      x: margin.left + (index + 0.5) * slotWidth,
      y: margin.top + plotHeight - count / ceiling * plotHeight,
    })));
  elements.distribution.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="交易盈亏百分比分布图">
      ${grid}
      <line class="axis-line" x1="${margin.left}" y1="${margin.top + plotHeight}" x2="${width - margin.right}" y2="${margin.top + plotHeight}" />
      ${bars}
      <path class="distribution-curve" d="${curve}" />
      <text x="${width / 2}" y="${height - 2}" text-anchor="middle">单笔收益率</text>
    </svg>
  `;
}

function average(values) {
  return values.length ? values.reduce((total, value) => total + value, 0) / values.length : null;
}

function maxBy(values, getter) {
  return values.length ? values.reduce((best, value) => getter(value) > getter(best) ? value : best) : null;
}

function minBy(values, getter) {
  return values.length ? values.reduce((best, value) => getter(value) < getter(best) ? value : best) : null;
}

function fmtPct(value) {
  if (!Number.isFinite(value)) return "-";
  return `${value > 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;
}

function fmtRatio(value) {
  if (value === Number.POSITIVE_INFINITY) return "∞";
  return Number.isFinite(value) ? `${value.toFixed(2)}x` : "-";
}

function fmtDays(value) {
  return Number.isFinite(value) ? `${value.toFixed(1)} 天` : "-";
}

function fmtSignedMoney(value) {
  if (!Number.isFinite(value)) return "-";
  return `${value > 0 ? "+" : ""}${value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtMoney(value, currency) {
  if (!Number.isFinite(value)) return "-";
  return `${currency} ${fmtSignedMoney(value)}`;
}

function buildCountTicks(maxCount) {
  const step = maxCount <= 5 ? 1 : maxCount <= 15 ? 2 : Math.ceil(maxCount / 5);
  const ceiling = Math.max(step, Math.ceil(maxCount / step) * step);
  const ticks = [];
  for (let value = 0; value <= ceiling; value += step) ticks.push(value);
  return ticks;
}

function fmtBin(value) {
  return `${value > 0 ? "+" : ""}${Number(value.toFixed(1))}%`;
}

function smoothPath(points) {
  if (!points.length) return "";
  let path = `M ${points[0].x} ${points[0].y}`;
  for (let index = 1; index < points.length - 1; index += 1) {
    const current = points[index];
    const next = points[index + 1];
    path += ` Q ${current.x} ${current.y} ${(current.x + next.x) / 2} ${(current.y + next.y) / 2}`;
  }
  const last = points.at(-1);
  return `${path} T ${last.x} ${last.y}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("\"", "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}
