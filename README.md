# Trend Deck

本地趋势交易看盘工具，使用 `Python + FastAPI` 提供数据接口，前端为原生 `HTML / JS` 单页。

## 功能

- 首页展示自选股概览，可添加股票并切换查看明细
- 优先用 `Tiingo` 抓取日线行情，失败时回退到 `yfinance`
- 使用 `yfinance` 抓取季度财报
- 展示 K 线、20/50/150/200 日均线、成交量
- 检查 8 条趋势模板条件
- 检查 `Code 33`:
  - EPS 同比增速是否连续三季加速
  - 营收同比增速是否连续三季加速
  - 净利率是否连续三季抬升
- 内部固定用 `SPY` 计算一个 RS 代理分数

## 启动

```powershell
pip install -r requirements.txt
python -m uvicorn app:app --reload
```

如需启用 Tiingo 价格源，请先设置环境变量：

```powershell
setx TIINGO_API_KEY "你的key"
```

## 备注

- 默认优先读取本地缓存；点击页面上的“拉新”后，会对价格和财报执行增量更新并回写本地缓存。
- Yahoo Finance 偶尔会限流，因此工具保留了本地缓存和备用抓取逻辑。
- 如果设置了 `TIINGO_API_KEY`，价格数据会优先走 Tiingo。
- 行情、季度利润表和财报日期都会写入 `.cache/`。如果外部接口临时不可用，已拉取过的股票仍可离线展示。
- 自选股列表当前保存在浏览器 `localStorage`。
- RS 这里是本地近似分数，不是 IBD 官方评级。
