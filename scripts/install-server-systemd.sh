#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${USER:-ubuntu}"

cd "$APP_DIR"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

. .venv/bin/activate
pip install -r requirements.txt

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
ExecStart=/usr/bin/bash scripts/refresh-and-push.sh
SERVICE

sudo tee /etc/systemd/system/trenddeck-refresh.timer >/dev/null <<'TIMER'
[Unit]
Description=Run TrendDeck market-data refresh after US market close

[Timer]
OnCalendar=Tue..Sat *-*-* 07:00:00
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
