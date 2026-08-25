#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

OLD_HEAD="$(git rev-parse HEAD)"
git pull --ff-only
NEW_HEAD="$(git rev-parse HEAD)"

if [ "$OLD_HEAD" != "$NEW_HEAD" ]; then
    if git diff --name-only "$OLD_HEAD" "$NEW_HEAD" | grep -qv '^\.cache/'; then
        if git diff --name-only "$OLD_HEAD" "$NEW_HEAD" | grep -q '^requirements\.txt$'; then
            . .venv/bin/activate
            pip install -r requirements.txt
            deactivate
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

git add .cache .trade/alerts.json .trade/watchlist.json .trade/notes.json

if git diff --cached --quiet; then
    echo "No market-data, alert, watchlist, or note changes to commit."
    exit 0
fi

git -c user.name="TrendDeck Bot" \
    -c user.email="trenddeck-bot@users.noreply.github.com" \
    commit -m "Update market data $(date +%F)"

git push
