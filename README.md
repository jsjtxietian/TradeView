# TrendDeck

美股趋势看板：网页、交易复盘、基底/VCP 与回调修复观察，以及 HTTP MCP 接口。

## 启动

Python 3.11+，在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -B scripts/migrate-local.py
.\.venv\Scripts\python -B scripts/configure-mcp.py
.\.venv\Scripts\python -B scripts/dev.py serve
```

网页：http://127.0.0.1:8000，MCP：http://127.0.0.1:8000/mcp。
已有虚拟环境不必重建。生产环境使用 `pip install -e .`，无需开发依赖。旧的 `python -m uvicorn app:app` 仍可使用。

## 从哪里看

```text
trenddeck/              全部应用代码与资源
  api/                  HTTP 路由、MCP 和应用装配
  setups/               基底、VCP、pullback、评分、绘图与研究
  web/                  现有 HTML / JS / CSS / 图表库
  resources/            模型参数、提示词模板
  importers/            IBKR 转换
  config.py             路径与本地配置入口
  market.py             行情与缓存
  storage.py            自选、持仓、笔记和交易存储
scripts/                启动、部署、迁移、刷新和研究命令
tests/                  自动测试
docs/                   架构、接口与模型规则
data/                   行情及交易数据（保持原布局）
local/                  本地密钥、参考资料、缓存及研究产物，不提交
app.py                  兼容启动入口
pyproject.toml          依赖、打包、测试和代码检查配置
```

详细代码导航见 [架构与目录说明](docs/architecture.md)。修改页面看 `trenddeck/web/`；修改形态算法看 `trenddeck/setups/`；修改接口看 `trenddeck/api/`。

## 配置

本机配置放在 `local/.env`，示例为 [docs/config.env.example](docs/config.env.example)。原 Streamlit 密钥迁移到 `local/secrets.toml`；不再需要 Streamlit 目录。

Tiingo key 可通过 `TIINGO_API_KEY`、`TIINGO_API_KEY_2` 环境变量或本地配置提供。进程环境变量优先。MCP token 首次运行配置脚本时生成，后续保留。查看 MCP 客户端配置（包含 token，请勿提交输出）：

```powershell
.\.venv\Scripts\python -B -m trenddeck.api.mcp
```

默认数据路径不依赖启动时的工作目录。`TRENDDECK_DATA_DIR` 可以覆盖 `data/`，内部仍使用 `stock/` 和 `trade/`。

## 常用命令

```powershell
python -B scripts/dev.py test
python -B scripts/dev.py check
python -B scripts/refresh-cache.py
python -B scripts/import-ibkr.py convert your-ibkr-statement.csv
python -B scripts/import-ibkr.py append data/imports/ibkr-trades.json
```

使用虚拟环境时，将 `python` 换成 `.\.venv\Scripts\python`。测试和检查缓存放在 `local/cache/`，这些命令不产生散落的 Python 字节码缓存。

## 说明

- [架构与目录导航](docs/architecture.md)
- [HTTP MCP 与 Hermes](docs/mcp.md)
- [部署与刷新](docs/deployment.md)
- [形态模型与验证](docs/setups.md)
- [IBKR 导入](docs/ibkr-import.md)
- [指标与界面设计](docs/design.md)

迁移本地资料可先执行 `python -B scripts/migrate-local.py --dry-run`。发现目标文件内容冲突时停止，不覆盖密钥或资料。书籍放在 `local/ref/books/`；之前的图和研究结果在 `local/artifacts/`。
