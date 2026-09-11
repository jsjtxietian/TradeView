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

## 工具与对话方式

### `get_daily_changes`

默认使用 **SPY 缓存中的最近交易日**，比较当前自选股和标记持仓的前后状态。
不读取浏览器 localStorage，不刷新行情，也不修改提醒日志。

```json
{"session_date": "2026-09-10", "limit": 30, "offset": 0}
```

- `session_date` 可省略；指定时必须存在对应的 SPY 日线。
- `symbols` 可选，最多 200 个；省略时读取当前服务器自选股及标记持仓。
- 默认仅返回触发变化/观察条件的标的，持仓优先排列。`include_unchanged: true` 可查看全部。
- `limit` 为 1–100，默认 30；使用 `next_offset` 继续读取。
- 变化包含趋势模板进出、各基础检查状态变化、MA50 穿越、六个月收盘高低点、
  单日 ±5%、五交易日 ±8%、成交量高于均量 1.5 倍或低于 0.5 倍。
- `coverage` 列出缺失、落后于参考交易日以及中间缺少交易日的标的。
- `snapshot_id` 是结果内容指纹，同一份数据重复查询可以去重；不是服务器持久化的历史快照。

变化直接从缓存日线计算，不用提醒日志的生成时间冒充交易日期。
“六个月新高”“放量”等观察条件可以连续多天出现。
历史查询仍使用**当前**自选股与持仓标记；前复权历史也可能随以后刷新而变化。

### `get_symbol_analysis`

```json
{"symbol": "NVDA"}
```

返回与网页同源的趋势检查、买入/卖出观察指标、相对 SPY 表现分、当前保存的笔记和持仓信息。
`as_of: "2026-09-10"` 可限制价格数据截至某一天；返回 `as_of_session` 是实际可用交易日。
默认省略完整历史 K 线及重复的指标窗口，方便“这只怎么样”的追问。
持仓是手工维护的信息，不是券商实时仓位，也不是指定历史日期的持仓。

### `get_price_history`

```json
{"symbol": "NVDA", "limit": 60}
```

返回最近 60 根缓存日线及 MA20/50/150/200，每页 1–500 根，页内按日期正序。
需要更多时，将返回的 `next_before` 传为 `before`；配合原有日期范围继续请求。

```json
{"symbol": "NVDA", "before": "2026-06-18", "limit": 120}
```

`start_date`、`end_date` 为包含边界的日期范围；`before` 不包含当天。
`cached_range` 表示本机全部可用日期，`price_mode` 表示价格口径。
超出缓存范围返回可用部分或空列表，不会自动联网补数据。

## 每日报告

沿用现有行情刷新任务，在刷新后让 Hermes 执行日报即可。例如当前定时器是服务器本地时间
周二至周六 07:00，加上最多 10 分钟随机延迟，可以将 Hermes 安排在稍后运行。
确认两边的时区；该 MCP 实现本身不创建或修改 Hermes 的定时任务，也不负责发送消息。

可以给 Hermes 这样的任务说明：

> 读取 TrendDeck 的每日变化并生成中文日报。先写清数据对应的交易日，检查 coverage。
> 优先整理持仓风险、趋势模板变化和需要进一步关注的标的；如有 next_offset，继续读取。
> 对最值得关注的少量标的调用 get_symbol_analysis，需要量价证据时取日线。
> 涨跌幅 0.05 表示 5%；本地 RS 不是官方 IBD 排名。
> 保存上次报告的交易日和 snapshot_id，避免同一份数据重复报告。
> 如果数据没有更新或缺失，直接说明，不把旧行情描述为今日行情。
> 我随后追问某只股票时，继续调用单股分析和日线工具回答。

当前刷新流程没有可靠的持久化成功状态，因此工具明确返回 `refresh_status: not_tracked`，
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
鉴权、Host/Origin 限制、历史截止日期与分页；不联网拉行情、不修改真实账户文件。
