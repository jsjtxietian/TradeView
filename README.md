# TrendDeck

美股趋势看板：读取本地行情，展示技术指标、持仓笔记和交易复盘，并通过 HTTP MCP 向 agent 提供每日变化与单股分析。

## 本机启动

Python 3.11+，在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements/runtime.txt
.\.venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

已有 `.venv` 时不用重复创建。网页地址：http://127.0.0.1:8000

MCP 地址为 `http://127.0.0.1:8000/mcp`，使用项目固定的 Bearer token。显示可复制的 agent 配置：

```powershell
.\.venv\Scripts\python -m trenddeck.mcp_server
```

## 目录

```text
app.py                  网页和 MCP 服务入口（启动命令不变）
trenddeck/              行情、指标、存储、分析和 MCP 代码
static/                 网页资源及本地图表库
scripts/                部署、刷新、迁移、IBKR 导入命令
requirements/           runtime.txt / dev.txt 依赖清单
templates/              analysis_prompt.md 分析 Prompt 模板
data/
  stock/                历史行情 CSV
  trade/                交易、账务、自选股、笔记、提醒和提醒快照
  imports/              原始账单及导入中间结果（不进入 Git）
docs/                   设计、部署、导入和 MCP 说明
tests/                  自动测试
```

所有默认数据路径均从项目位置解析，不依赖启动时的工作目录。
可选的 `TRENDDECK_DATA_DIR` 指向数据根目录，其中直接包含 `stock/`、`trade/`；
相对路径从项目根目录解析。服务器和各命令脚本也会读取项目的可选 `.env`，进程环境变量优先。

## 常用命令

```powershell
# 刷新行情，需要服务正在运行
.\.venv\Scripts\python scripts/refresh-cache.py

# 导入交易：先转换到 data/imports/，检查后再追加
.\.venv\Scripts\python scripts/import-ibkr.py convert your-ibkr-statement.csv
.\.venv\Scripts\python scripts/import-ibkr.py append data/imports/ibkr-trades.json

# 安装开发依赖并测试
.\.venv\Scripts\python -m pip install -r requirements/dev.txt
.\.venv\Scripts\python -m pytest -q
```

## 详细说明

- [HTTP MCP 与 Hermes 接入](docs/mcp.md)
- [服务器部署、每日刷新及旧目录迁移](docs/deployment.md)
- [IBKR 交易与账务导入](docs/ibkr-import.md)
- [指标、提醒与界面设计](docs/design.md)

从旧版本升级时，Git 会迁移已跟踪的数据文件；服务器安装脚本会检查并迁移剩余的
`.cache/`、`.trade/` 本机文件。同名不同内容会停止迁移，绝不覆盖。
