# IBKR 交易导入

建议每次从 IBKR 下载 YTD Transaction History。导出的时间范围可以与之前重叠，
同一份 CSV 用于更新两类本地 JSON：

- `data/trade/trades.json`：完整多头/空头交易周期与手工复盘
- `data/trade/ledger.json`：佣金、股息、预扣税、贷方/借方利息和其他收支

应用运行时只读取这两个 JSON，不直接读取 CSV。

导入脚本统一位于 `scripts/import-ibkr.py`。不指定 `--output` 时，转换中间结果写到
`data/imports/ibkr-trades.json`，追加交易写到 `data/trade/trades.json`，账务写到
`data/trade/ledger.json`。这些默认路径不受当前工作目录影响，也支持 `TRENDDECK_DATA_DIR`。
`data/imports/` 已被 Git 忽略，可用来保存原始账单和导入中间结果。

## 导入交易周期

先将股票买卖转换成独立 JSON 供检查：

```powershell
python scripts/import-ibkr.py convert ibkr-transactions-ytd.csv --output data/imports/ibkr-trades.json
```

确认后追加到交易复盘数据：

```powershell
python scripts/import-ibkr.py append data/imports/ibkr-trades.json --output data/trade/trades.json
```

交易追加阶段会按股票、币种和完整成交明细去重。重复运行不会重复写入已有交易，
也不会覆盖已经填写的整体复盘 `note`。独立转换结果按每笔交易的首次操作日期正序编号；
append 保留已有 ID，并将新识别的完整交易按时间顺序追加到末尾。网页仍按最后卖出日期
倒序展示。

## 导入账户账务

将同一份 Transaction History CSV 中的佣金、股息、预扣税、利息和其他收支写入账务 JSON：

```powershell
python scripts/import-ibkr.py ledger ibkr-transactions-ytd.csv --output data/trade/ledger.json
```

ledger 命令每次完整覆盖 `data/trade/ledger.json`，因此应优先使用覆盖开户至今或完整 YTD
范围的新账单，不能只导入一个很短的增量区间，否则 JSON 中较早的账务记录会消失。
导入完成后，`/review` 的统计区间会同时筛选交易、佣金、股息、利息和其他收支。

交易复盘的数据统计按完整平仓周期计算，时间筛选以最后平仓日期为准。多头单笔收益率为
`(总卖出收入 - 总买入成本) / 总买入成本`；空头单笔收益率为
`(总做空收入 - 总回补成本) / 总做空收入`。当前 JSON 不保存佣金，因此统计结果未计佣金。
调整后胜负比为 `平均盈利 / 平均亏损 × 胜率 / 败率`。
同一币种内另行统计平仓价差盈亏、Profit Factor 和按金额计算的最大单笔盈利/亏损；
不同币种的金额不会直接相加。

导入器同时识别完整多头与空头周期。持仓由负转正或由正转负时，同一笔成交会先按
原持仓数量完成平仓周期，再将剩余数量作为反向新仓。交易数据统计位于 `/review`，
复盘弹窗里的“数据统计”按钮会在新窗口打开该页面。BOXX 的完整平仓价差不进入普通交易
胜率和盈亏分布，而是在账户收入与费用中归入“现金利息 / 类现金”。
