# HTTP MCP 接入

TrendDeck 在原有 FastAPI 服务上提供 **Streamable HTTP MCP**，地址为 `/mcp`。
Hermes 和其他支持 Streamable HTTP、可配置 Bearer header 的 agent 可以使用同一个服务。
无需额外 MCP 进程或 systemd 服务；当前不是旧版 HTTP+SSE `/sse` 协议，也不提供 OAuth 自动登录。

## 启用

Python 3.11+。token 只保存在项目根目录的本地 `.env` 中，该文件被 Git 忽略，
仓库只提供不含真实凭据的 `.env.example`。源码不内置任何默认 token。

服务器沿用原来的部署命令：

```bash
cd /home/ubuntu/trenddeck
bash scripts/update-server.sh
```

安装脚本自动生成缺失的 token，后续部署保留现有值，不会每次换 token。
Linux 上配置文件权限为 600。安装日志不输出 token。

本机首次使用先初始化配置，再启动服务：

```powershell
cd D:\Repos\TradeView
.\.venv\Scripts\python scripts/configure-mcp.py
.\.venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

`TRENDDECK_MCP_TOKEN` 未配置或为空时，MCP 返回 503，网页继续正常运行。
已配置时，未携带或携带错误 token 的请求返回 401。进程环境变量仍优先于 `.env`；
如果终端以前设置过测试 token，请先清除该变量再启动，以免覆盖本地配置。

## Hermes 配置

在服务器项目目录执行以下命令，可按需显示包含实际 token 的完整配置：

```bash
.venv/bin/python -m trenddeck.mcp_server
```

Windows 对应命令：

```powershell
.\.venv\Scripts\python -m trenddeck.mcp_server
```

将输出合并到运行 Hermes 的用户的 `~/.hermes/config.yaml` 中，保留已有服务器条目。
配置示例只使用占位符：

```yaml
mcp_servers:
  trenddeck:
    url: "http://127.0.0.1:8000/mcp"
    headers:
      Authorization: "Bearer <本地配置中的 token>"
```

客户端配置也应保留在本机，不提交真实 token。重新加载 Hermes 的 MCP 配置后，
让它调用 `get_daily_changes` 验证连接。

## 更换 token

在服务器运行：

```bash
.venv/bin/python scripts/configure-mcp.py --rotate
sudo systemctl restart trenddeck.service
```

这只更新 `.env` 中的 token，保留 Host、数据路径等其他设置。
服务重启后旧 token 失效，客户端需要换成新值。查看命令同上，不需要将 token 写入源码。
历史提交里出现过的旧凭据无需继续使用；轮换后它们不再能访问服务。

其他 agent 使用相同 URL 和 `Authorization: Bearer <token>` header，
具体配置键名取决于客户端，不需要修改服务端工具。

参考：[Hermes 官方 MCP 文档](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/mcp.md)。

## 远程 agent

在已有 HTTPS Nginx 站点中添加以下 location（域名、证书沿用你的实际站点配置）：

```nginx
location = /mcp {
    auth_basic off; # MCP 使用自己的 Bearer 验证，网页仍可保留 Basic auth
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Authorization $http_authorization;
    proxy_buffering off;
    proxy_read_timeout 120s;
}
```

在项目 `.env` 中设置实际的域名，例如：

```dotenv
TRENDDECK_MCP_ALLOWED_HOSTS=stocks.example.com
```

重启 TrendDeck 后，客户端使用 `https://stocks.example.com/mcp`。Nginx 配置应先执行
`sudo nginx -t` 再 reload。不要直接向公网开放 Uvicorn 的 8000 端口。
本机 `127.0.0.1`、`localhost` 已允许任意端口；自定义域名需要明确列入允许名单。
Host 不匹配返回 421。普通服务端 agent 不发送 Origin；若客户端确实发送，使用
`TRENDDECK_MCP_ALLOWED_ORIGINS` 配置其精确 Origin，否则返回 403。

## 工具分类与对话方式

MCP 暴露八个工具，按状态影响分类：

| 分类 | 工具 | 行情联网 | 写入内容 |
| --- | --- | --- | --- |
| 纯只读 | `get_watchlist` | 否 | 无 |
| 纯只读 | `get_daily_changes` | 否 | 无 |
| 纯只读 | `get_realtime_quote` | 是 | 无 |
| 纯只读 | `get_setup_report` | 否 | 无 |
| 纯只读 | `get_setup_rankings` | 否 | 无 |
| 可写，默认只读 | `get_symbol_report` | 仅 `refresh=true` | 该股票的日线缓存，仅 `refresh=true` |
| 改变状态 | `add_watchlist_symbol` | 否 | 自选列表及分组 |
| 改变状态 | `remove_watchlist_symbol` | 否 | 自选列表、分组及提醒比较快照 |

“只读”描述是否修改状态，与是否联网无关。MCP 的 `readOnlyHint` 是工具级别，
不随参数变化；因此 `get_symbol_report` 标注为可写，响应的 `read_only` 则反映本次调用。
纯只读工具标注 `readOnlyHint=true`；自选增删标注为写操作且幂等，删除另带 destructive 标注。
服务端注册代码分别位于 `_register_read_tools` 和 `_register_state_tools`，保持同一个 `/mcp` 入口。

原来的 `get_symbol_data`、`get_symbol_analysis`、`get_price_history` 已移除；
升级后重新加载 agent 的 MCP 工具列表，并更新已有定时任务中的工具名。

新增的枢轴模型工具及 HTTP 接口见 [枢轴评分与研究](setups.md)：
`get_setup_rankings` 排行、`get_setup_report` 单股评分。
两者默认返回 schema 2 的分数、中文理由和共享 Markdown 评分条件；
仅在 `include_details=true` 时附加完整诊断 `details`，HTTP 与 MCP 参数一致。
两者只使用当前模型 `base-v4`；兼容字段 `model_version` 可省略，显式传值也只接受 `base-v4`。
当前模型先识别独立基底，再判断内部收缩；详细的基底目录和早晚期背景仅在 `include_details=true` 中返回。
算法和目前验证边界见 [当前基底评分与研究模型](setups.md)。
Markdown 按当前模型实际权重与规则生成，整体形态分与突破时机标记分别返回。
工具只读取本地缓存，不运行 LLM，也不自动调参。
两者新增可选 `setup_type="pullback"`，默认 `"base"`。Pullback 观察深度调整后的第一修复基底，
返回阶段、理由和 `pullback` 结构信息，`score=null`；排行按阶段再按代码排序，不代表质量分高低。
`include_details=true` 可看历史尝试、失败记录与未通过的趋势条件。两类结果分别使用各自的评分／观察条件说明。
回测不注册为 MCP 工具；历史验证保留在本地研究脚本和 HTTP 研究接口中。

单股 `get_setup_report` 支持 `include_chart=true`，默认关闭，适用于 `base` 和 `pullback`：

```json
{"symbol": "META", "setup_type": "pullback", "as_of": "2025-05-12", "include_chart": true}
```

开启后，MCP 的 `content` 同时返回报告文字和原生 `image/png` 图片块（base64 数据）；
`structuredContent` 保留原报告并增加 `chart` 元信息，图片数据不重复放入 JSON。
图像使用本次评分的同一份缓存快照与识别结果，标出 K 线、成交量、MA20/50/200、基底边界、
确认和已记录的突破日期。没有基底或历史不足时明确标注，不推断箱体；无图时报告格式不变。
图采用英文字段以兼容无中文字体的 Linux 服务，中文理由仍在报告中。
默认显示最近 125 根日线，按所选基底／调整周期扩展，最多 504 根；截断基底起点会明确说明。
绘图只在内存中进行，不生成文件或公开 URL，不联网。排行榜不支持附图。
HTTP 单股接口同样支持此参数，在 `chart.data` 中返回 base64 PNG。
客户端是否内联显示图片、是否把图片交给模型，取决于客户端的多模态支持。

### `get_watchlist`

无需参数，传 `{}` 即可。返回 `configured`、`watchlist`、`groups`、`total_symbols`
以及 `items`（每只自选股的 `symbol`、`group_ids`、`isHolding`、`costBasis`、`shares`）。
持仓来源为手工笔记，不是券商实时仓位；自选之外的持仓不会加入列表。
不需要 SPY 或其他行情缓存，不联网、不写文件。未配置时返回默认自选及分组；
已保存的空自选保持为空。分组包含 `id`、`name` 和 `symbols`，可据此指定添加股票的目标分组。

### `get_daily_changes`

默认使用 **SPY 缓存中的最近交易日**，返回当前 watchlist 内股票的当日变化和最新提醒。
只读缓存，不刷新行情、不修改提醒日志。watchlist 外的手工持仓不自动加入日报。

```json
{"limit": 30, "offset": 0}
```

- `session_date` 可省略；指定 YYYY-MM-DD 时必须存在对应的 SPY 日线。
- `symbols` 可选，省略、`null` 或 `[]` 均表示整个 watchlist；非空列表按股票代码筛选当前 watchlist，最多 200 个。
- 默认包含没有触发特殊变化的股票，持仓优先排列；`include_unchanged: false` 仅返回触发条件的股票。
- `limit` 为 1–100，默认 30；使用 `next_offset` 继续读取，直到其为 null。
- 每项包含涨跌幅、量比、趋势状态和 `changes`：趋势模板进出、基础检查变化、MA50 穿越、
  六个月收盘高低点、单日 ±5%、五交易日 ±8%、成交量高于均量 1.5 倍或低于 0.5 倍。
- `latestAlerts` 返回该股票**最近一次提醒时间下的全部消息**，包括 `message`、`createdAt`、
  `timeLabel`；没有提醒时为 `[]`。它可能是以前的提醒，不代表本交易日新触发。
- `coverage` 列出缺失、落后于参考交易日以及中间缺少交易日的股票；缺失/落后的条目也附带
  `latestAlerts`，不把旧行情当成当日变化。
- `snapshot_id` 是结果内容指纹，可以去重；不是服务器持久化的历史快照。

变化从缓存日线重新计算，不用提醒生成时间冒充交易日期。观察条件可以连续多天出现。
历史查询仍使用**当前** watchlist、持仓标记和最新提醒；提醒不按 `session_date` 截断。

### `get_symbol_report`

```json
{"symbol": "NVDA"}
```

返回与网页同源的完整技术分析证据：趋势检查、各观察周期的买入/卖出指标、相对 SPY 表现分、
当前笔记和手工持仓、最新提醒，以及 OHLCV 和 MA20/50/150/200。
这是结构化技术报告，由 agent 据此解读；服务端不调用大模型生成投资结论。

- `refresh` 默认 **false**：仅读磁盘缓存，即使没有缓存也不自动联网，而是提示显式刷新。
- `refresh: true`：实际请求 Tiingo，更新并保存该股票的三年日线缓存，跳过短时间冷却。
  可查询 watchlist 外的股票，不会把它加入自选股，也不改笔记、持仓或 alert。
  SPY 基准仍使用已有缓存，注意检查 `benchmark_session`。
- `as_of` 可指定包含当天的分析截止日期；`as_of_session` 是实际使用的交易日。
  历史截止日期只影响报告，不限制主动刷新到磁盘的数据。
- `history_limit` 指定返回的日线数量，为 1–500，默认 60 根；返回页内按日期正序。
- 将 `history.next_before` 传为 `before`，继续读取更早日线。`before` 不包含当天，
  只影响历史分页，不改变分析截止日。分页时固定 `as_of` 并使用 `refresh: false`。

主动拉新：

```json
{"symbol": "NVDA", "refresh": true}
```

读取更早历史（日期替换为上一页返回的值）：

```json
{"symbol": "NVDA", "as_of": "2026-09-10", "before": "2025-09-11", "history_limit": 252}
```

`cache.status` 为 `cached`、`fetched` 或 `refreshed`；`refresh_status` 为 `not_requested`
或 `succeeded`。刷新成功只表示本次行情请求成功，不保证已包含预期的最新交易日，仍需检查日期。
无 API key、鉴权失败、HTTP 429 或其他刷新错误都会返回 MCP Tool 错误，不会静默返回旧缓存。
429 错误在可用时包含 Tiingo 的 `Retry-After`。返回的是日线数据，不是实时行情。

笔记、持仓和 `latestAlerts` 始终是当前保存的信息；不是历史仓位，也不随本次刷新重新生成提醒。
MCP 将此工具标注为可能写缓存和访问外部行情源，因为 `refresh=true` 会执行这些操作。

### `get_realtime_quote`

```json
{"symbol": "NVDA"}
```

优先查询 Tiingo Consolidated Equity Beta；该接口不可用或无数据时自动回退 IEX。返回参考价、
相对昨收涨跌、日内 OHLC、服务商时段成交量、时间戳和 `age_seconds`。该工具不读写历史行情
缓存，也不修改 watchlist、笔记、持仓或提醒；配置多个 Tiingo Key 时会按顺序尝试。

Consolidated 响应中 `tngoLast` 是衍生参考价，`lq*` 是流动性买卖价估算而非真实 NBBO；
`volume` 可能是当前服务商时段累计量，不保证为正式全日成交量。通过 `feed`、`price_type`、
`volume_scope` 和 `fallback_used` 判断实际口径。仅用于“现在多少钱”等即时问题，日报、历史
分析和正式结论仍以缓存日线收盘价为准。无 API key、鉴权失败、HTTP 429、两个源均无数据或
其他上游错误都会返回 MCP Tool 错误，429 在可用时包含 `Retry-After`。

### `add_watchlist_symbol` / `remove_watchlist_symbol`

仅在用户要求修改自选时调用。例如添加英伟达：

```json
{"symbol": "NVDA"}
```

同样的参数传给 `remove_watchlist_symbol` 可移出自选。代码会规范化为大写，支持 `BRK.B`
转换为 `BRK-B`。`add_watchlist_symbol` 支持可选的 `group_id`，先调用 `get_watchlist`
取得现有分组的 ID，再添加到该组，例如（`AI` 替换为实际 ID）：

```json
{"symbol": "NVDA", "group_id": "AI"}
```

新股票追加到列表尾部；不传 `group_id` 或传 `null` 时清除旧分组引用，归入未分组。
对于已在自选中的股票，不传分组会保留原分组；指定分组则从其他分组移出并加入目标组，
因此同一接口也支持调整分组。目标分组不存在或为空字符串时直接报错，不修改自选，
不自动创建新分组。组 ID 按现有存储规则规范化，大小写不敏感。

删除会移除
列表和所有分组中的引用，并清理不再属于自选的提醒比较快照。笔记、手工持仓、历史提醒、
交易记录和行情缓存保留。自选可以为空，不会回退到默认股票。

两个工具直接落盘，与网页共用 `watchlist.json`；已打开的网页重新加载后可看到更新。
重复添加且分组不变、删除不存在的股票均成功，返回 `changed: false`；实际变更为 `true`。
返回包含规范化的 `symbol`、`action`、`is_in_watchlist`、更新后的 `watchlist` 和 `groups`。
非法代码、文件损坏或写入失败会返回 MCP Tool 错误。

增删操作不调用行情 API；添加后若需要立即获取数据，另调用
`get_symbol_report` 并设置 `refresh: true`。这时行情限流按查询工具的规则返回错误，
不会撤销已完成的自选添加。两个工具均标注为写操作且幂等，删除工具带 destructive 标注。
当前单进程内的自选写入会串行执行，以避免并发增删丢失更新；该锁不跨多个服务进程。

## 每日报告

沿用现有行情刷新任务，在刷新后让 Hermes 执行日报即可。例如当前定时器是服务器本地时间
周二至周六 07:00，加上最多 10 分钟随机延迟，可以将 Hermes 安排在稍后运行。
确认两边的时区；该 MCP 实现本身不创建或修改 Hermes 的定时任务，也不负责发送消息。

可以给 Hermes 这样的任务说明：

> 读取 TrendDeck 的每日变化并生成中文日报。先写清数据对应的交易日，检查 coverage。
> 优先整理持仓风险、趋势模板变化和需要进一步关注的标的；如有 next_offset，继续读取。
> 对最值得关注的少量标的调用 get_symbol_report，默认只读缓存；需要更多日线时使用 before 翻页。
> 结合 latestAlerts 的原始时间解读，不把旧提醒说成今日新变化。
> 涨跌幅 0.05 表示 5%；本地 RS 不是官方 IBD 排名。
> 保存上次报告的交易日和 snapshot_id，避免同一份数据重复报告。
> 如果数据没有更新或缺失，直接说明，不把旧行情描述为今日行情。
> 我随后追问某只股票时，调用 get_symbol_report；明确需要拉新时才设 refresh=true。

当前每日刷新流程没有可靠的持久化成功状态，因此 get_daily_changes 返回 `refresh_status: not_tracked`，
日期仅说明读到了哪一天的缓存。未引入交易所日历，不把周末/节假日粗暴标记为刷新失败。
查询期间应避免同时刷新文件；每次查询直接读磁盘，下一次查询无需等待网页内存缓存过期。
基本面、新闻、自动交易和复盘收益统计不属于这次 MCP 范围。

## 验证

安装开发依赖后执行：

```bash
python -m pip install -r requirements/dev.txt
python -m pytest -q
python -m ruff check app.py trenddeck tests
```

测试使用临时行情文件，并通过真实 HTTP MCP 客户端验证初始化、工具发现、并发查询、
鉴权、Host/Origin 限制、历史截止日期与分页、最新提醒、显式刷新和自选增删持久化行为；
不联网拉行情、不修改真实账户文件。
