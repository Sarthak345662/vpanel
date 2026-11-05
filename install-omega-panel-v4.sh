#!/usr/bin/env bash
# Vizion Panel bootstrap (kept for the old file name).
#
# This file used to clone a third-party V4 repository and install that older
# panel. It now always installs the current Vizion Panel from this repository,
# so running it can never bring back the legacy panel code.
#
#   curl -fsSL https://raw.githubusercontent.com/Sarthak345662/vpanel/main/install-omega-panel-v4.sh | sudo bash
set -Eeuo pipefail

REPO_SLUG="${OMEGA_REPO_SLUG:-Sarthak345662/vpanel}"
REPO_REF="${OMEGA_REPO_REF:-main}"
RAW_BASE="https://raw.githubusercontent.com/${REPO_SLUG}/${REPO_REF}"

command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 1; }
if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run as root: curl -fsSL ${RAW_BASE}/install-omega-panel-v4.sh | sudo bash" >&2
    exit 1
fi

echo "→ Vizion Panel installer (${REPO_SLUG}@${REPO_REF}, latest code)"
exec bash <(curl -fsSL "${RAW_BASE}/install.sh") "$@"
