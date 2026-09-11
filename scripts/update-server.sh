#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Git may relocate tracked data files. Stop the app before pulling, and restore
# it on failure as well as success if it was running before this update.
WAS_ACTIVE=false
if systemctl is-active --quiet trenddeck.service; then
    WAS_ACTIVE=true
    sudo systemctl stop trenddeck.service
fi
restore_service() {
    if [ "$WAS_ACTIVE" = true ]; then
        sudo systemctl start trenddeck.service
    fi
}
trap restore_service EXIT

git pull --ff-only

bash scripts/install-server-systemd.sh
