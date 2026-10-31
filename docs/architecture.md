# 从哪里开始看

根目录 `app.py` 是兼容入口，实际装配位于 `trenddeck/api/app.py`。保持一个 FastAPI 进程，网页通过 HTTP API 取数据，MCP 使用同一组服务函数；前端不依赖 Python 模块，后端不依赖浏览器。

## 代码地图

| 要改什么 | 从这里开始 |
| --- | --- |
| HTTP 路由、应用生命周期 | `trenddeck/api/app.py`、`trenddeck/api/routes/` |
| MCP 工具、鉴权、图片返回 | `trenddeck/api/mcp.py` |
| 网页、图表与复盘页面 | `trenddeck/web/`，仍通过 `/static/` 提供资源 |
| 形态接口的数据读取与组装 | `trenddeck/setups/queries.py` |
| 基底确认、冻结边界与生命周期 | `trenddeck/setups/bases.py` |
| VCP 标签的证据要求 | `trenddeck/setups/vcp.py` |
| 综合评分、结构损伤、失效说明 | `trenddeck/setups/engine.py`、`risk.py`、`validity.py` |
| 深度回调后的修复基底 | `trenddeck/setups/pullback.py` |
| 中文理由与评分说明 | `trenddeck/setups/explanations.py` |
| 模型参数与提示词 | `trenddeck/resources/` |
| 供图、研究与历史事件 | `trenddeck/setups/chart.py`、`research.py`、`events.py` |
| 行情提供商、缓存、指标和分析 | `trenddeck/market.py`、`indicators.py`、`analysis.py` |
| 自选、持仓、笔记和提醒 | `trenddeck/storage.py`、`alerts.py` |
| IBKR 转换 | `trenddeck/importers/ibkr.py` |
| 部署、迁移、研究命令 | `scripts/` |

依赖方向：HTTP/MCP → 查询服务 → 模型/行情/存储。形态计算模块不知道 FastAPI 或 MCP；绘图消费同一次报告的证据，不重新识别基底。

## 本机内容与依赖

- `data/`：保留原数据布局及自动刷新规则。
- `local/ref/books/`：书籍与阅读材料，只保留本地，不打包、不提交。
- `local/.env`：MCP token、Host 设置和可选环境配置。
- `local/secrets.toml`：原 `.streamlit/secrets.toml`，保留内容与 Tiingo 双 key 行为；也支持环境变量提供 key。
- `local/sshpassword`：原部署凭据；应用不读取这个文件。
- `local/todo.md`：本地连接、运维备忘和工作状态，不进入 Git。
- `local/cache/`：pytest、Ruff 的可再生缓存。
- `local/artifacts/`：图、研究结果和原 `tmp/` 的内容。

Python 依赖统一声明在 `pyproject.toml`，开发工具放在 `dev` extra。开发入口 `python -B scripts/dev.py ...` 和部署服务不写 `__pycache__`；直接运行其他 Python 命令仍可能生成缓存，它们始终被 Git 忽略。

## 迁移与兼容

运行 `python -B scripts/migrate-local.py --dry-run` 查看计划，去掉 `--dry-run` 执行。迁移前检查全部目标；不同内容冲突就停止，相同内容合并，成功后可以重复执行，不打印密钥内容。

新配置优先，未迁移的根目录 `.env` 与 `.streamlit/secrets.toml` 仅作为升级兼容来源。迁移完成后无需旧目录。显式进程环境变量优先于文件。

HTTP URL、MCP 工具名与参数、`uvicorn app:app` 和 `scripts/import-ibkr.py` 保持兼容。内部 Python 导入路径已调整，自己的研究脚本应改用 `trenddeck.setups.*`；例如 `trenddeck.setups.queries.get_setup_report`。

评分接口仍只暴露当前模型。VCP 标签要求价格与量能证据同时成立，但一般基底不因此失去评分资格。默认理由固定保留失效说明，详细证据在 `details.geometry.vcp_evidence` 与 `details.invalidation`。
