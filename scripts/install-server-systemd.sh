#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${USER:-ubuntu}"

cd "$APP_DIR"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

. .venv/bin/activate
pip install -e .

# Stop readers/writers before relocating any local-only legacy files.
WAS_ACTIVE=false
if systemctl is-active --quiet trenddeck.service; then
    WAS_ACTIVE=true
    sudo systemctl stop trenddeck.service
fi
if ! python -B scripts/migrate-local.py || ! python -B scripts/configure-mcp.py || ! python -B scripts/migrate-data.py; then
    if [ "$WAS_ACTIVE" = true ]; then
        sudo systemctl start trenddeck.service
    fi
    exit 1
fi

sudo tee /etc/systemd/system/trenddeck.service >/dev/null <<SERVICE
[Unit]
Description=TrendDeck FastAPI service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$APP_DIR
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
EnvironmentFile=-$APP_DIR/local/.env
ExecStart=$APP_DIR/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

sudo tee /etc/systemd/system/trenddeck-refresh.service >/dev/null <<SERVICE
[Unit]
Description=Refresh TrendDeck market-data cache
After=trenddeck.service
Wants=trenddeck.service

[Service]
Type=oneshot
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$APP_DIR
Environment=PYTHONDONTWRITEBYTECODE=1
EnvironmentFile=-$APP_DIR/local/.env
ExecStart=/usr/bin/bash scripts/refresh-and-push.sh
SERVICE

sudo tee /etc/systemd/system/trenddeck-refresh.timer >/dev/null <<'TIMER'
[Unit]
Description=Run TrendDeck market-data refresh after US market close

[Timer]
OnCalendar=Tue..Sat *-*-* 07:00:00 Asia/Shanghai
Persistent=true
RandomizedDelaySec=10m

[Install]
WantedBy=timers.target
TIMER

sudo systemctl daemon-reload
sudo systemctl enable --now trenddeck.service
sudo systemctl enable --now trenddeck-refresh.timer
sudo systemctl restart trenddeck.service

systemctl is-active trenddeck.service
systemctl list-timers trenddeck-refresh.timer --no-pager

echo "Show Hermes MCP configuration: .venv/bin/python -m trenddeck.api.mcp"
