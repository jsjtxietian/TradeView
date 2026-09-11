#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

OLD_HEAD="$(git rev-parse HEAD)"
git pull --ff-only
NEW_HEAD="$(git rev-parse HEAD)"

if [ "$OLD_HEAD" != "$NEW_HEAD" ]; then
    if git diff --name-only "$OLD_HEAD" "$NEW_HEAD" | grep -qv '^data/'; then
        if git diff --name-only "$OLD_HEAD" "$NEW_HEAD" | grep -Eq '^(requirements/|requirements(-dev)?\.txt$)'; then
            . .venv/bin/activate
            pip install -r requirements/runtime.txt
            deactivate
        fi
        sudo systemctl stop trenddeck.service
        if ! .venv/bin/python scripts/migrate-data.py; then
            sudo systemctl start trenddeck.service
            exit 1
        fi
        sudo systemctl restart trenddeck.service
    fi
fi

. .venv/bin/activate
python - <<'PY'
import time
from urllib.request import urlopen

for attempt in range(30):
    try:
        with urlopen("http://127.0.0.1:8000/api/config", timeout=2):
            break
    except Exception:
        if attempt == 29:
            raise
        time.sleep(1)
PY

python scripts/refresh-cache.py

git add -- data/stock data/trade/alerts.json data/trade/alerts_snapshot.json data/trade/watchlist.json data/trade/notes.json

if git diff --cached --quiet; then
    echo "No market-data, alert, watchlist, or note changes to commit."
    exit 0
fi

git -c user.name="TrendDeck Bot" \
    -c user.email="trenddeck-bot@users.noreply.github.com" \
    commit -m "Update market data $(date +%F)"

git push
