#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

git pull --ff-only

. .venv/bin/activate
python scripts/refresh-cache.py

git add .cache

if git diff --cached --quiet; then
    echo "No cache changes to commit."
    exit 0
fi

git -c user.name="TrendDeck Bot" \
    -c user.email="trenddeck-bot@users.noreply.github.com" \
    commit -m "Update market data $(date +%F)"

git push
