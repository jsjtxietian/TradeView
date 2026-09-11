# Deployment and Operations

Production runs as a single FastAPI service behind Nginx.

Runtime layout:

- app directory: `/home/ubuntu/trenddeck`
- service: `trenddeck.service`
- refresh timer: `trenddeck-refresh.timer`
- refresh job: `trenddeck-refresh.service`
- reverse proxy: Nginx port 80 to `127.0.0.1:8000`

Data layout:

- `data/stock/` is committed to Git and is automatically updated by the scheduled refresh job.
- `data/trade/alerts.json`, `data/trade/alerts_snapshot.json`, `data/trade/watchlist.json`, and `data/trade/notes.json` are staged by the scheduled refresh job.
- MCP credentials live only in the Git-ignored local `.env` (mode 600 on Linux). Installation creates a random token when missing and preserves it on subsequent updates; see `.env.example`.
- `.streamlit/` stays local and contains secrets such as market-data keys.
- `.venv/` is generated on each machine and is ignored by Git.

Install or update the server units:

```bash
cd /home/ubuntu/trenddeck
bash scripts/update-server.sh
```

`scripts/update-server.sh` runs `git pull --ff-only`, installs or updates Python dependencies, writes the systemd unit files, restarts `trenddeck.service`, and enables `trenddeck-refresh.timer`.

Scheduled refresh:

- timer: `trenddeck-refresh.timer`
- schedule: `Tue..Sat 07:00:00` in the server local timezone
- script: `scripts/refresh-and-push.sh`

The refresh script:

- pulls the latest Git commit with `git pull --ff-only`
- restarts `trenddeck.service` if files outside `data/` changed
- reinstalls dependencies first if any file under `requirements/` changed
- calls `scripts/refresh-cache.py`
- refreshes through `POST /api/watchlist/refresh`, which shares the same backend refresh path as the UI refresh button
- stages `data/stock`, `data/trade/alerts.json`, `data/trade/alerts_snapshot.json`, `data/trade/watchlist.json`, and `data/trade/notes.json`
- commits changed market-data, alert snapshot, alert-history, server watchlist, and symbol-note files as `Update market data YYYY-MM-DD`
- pushes back to GitHub

Manual refresh:

```bash
sudo systemctl start trenddeck-refresh.service
journalctl -u trenddeck-refresh.service -n 120 --no-pager
```

Status checks:

```bash
systemctl status trenddeck.service --no-pager
journalctl -u trenddeck.service -n 120 --no-pager
systemctl list-timers trenddeck-refresh.timer --no-pager
systemctl status trenddeck-refresh.timer --no-pager
```

GitHub write access is provided by a server-side SSH deploy key with write access enabled. The remote should use SSH:

```bash
git remote get-url origin
ssh -T git@github.com
```

## 从旧目录升级

首次升级到 `data/` 布局时，先停止旧服务和刷新定时器，再拉取代码，避免旧进程在 Git
移动文件期间写回 `.cache/` 或 `.trade/`：

```bash
cd /home/ubuntu/trenddeck
sudo systemctl stop trenddeck-refresh.timer trenddeck.service
git pull --ff-only
bash scripts/install-server-systemd.sh
```

安装脚本会安装 `requirements/runtime.txt`，检查并迁移遗留的本机文件，再启动服务和定时器。
已被 Git 跟踪的文件会随提交迁移；迁移脚本处理剩余文件：

- `.cache/*_history.csv` → `data/stock/*_history.csv`
- `.cache/alerts_snapshot.json` → `data/trade/alerts_snapshot.json`
- `.trade/*` → `data/trade/*`

可以先单独预览迁移，不写任何文件：

```bash
.venv/bin/python scripts/migrate-data.py --dry-run
```

确认服务停止后，去掉 `--dry-run` 即可执行。相同内容会合并；同名不同内容会在迁移前
报错并停止，保留两边文件。重复运行不会重复搬运。Git 拉取若提示本机有未提交数据，
先正常提交或备份并解决冲突，不要用强制覆盖来跳过。

后续可直接使用 `bash scripts/update-server.sh`，它会在拉取前停止服务，完成后恢复。

## 自定义数据目录

默认数据根目录是项目的 `data/`。可在项目 `.env` 或进程环境中设置
`TRENDDECK_DATA_DIR=/srv/trenddeck-data`，其中直接包含 `stock/`、`trade/`，
应用、导入、迁移和刷新脚本使用同一配置。相对路径相对于项目根目录解析。
显式的进程环境变量优先于 `.env`；旧版本若设置过该变量，需要按新的目录含义检查路径。

Git 自动提交只覆盖项目内的 `data/stock` 和选定的 `data/trade` 文件。
使用项目外的数据目录时，应单独安排数据备份。
