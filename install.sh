#!/usr/bin/env bash
# Vizion Panel - one-line bootstrap installer.
#
#   curl -fsSL https://raw.githubusercontent.com/Sarthak345662/vpanel/main/install.sh | sudo bash
#   curl -fsSL .../install.sh | sudo bash -s -- --node-connect --panel URL --key KEY --name N --url http://IP:5001 --type lxc
#
# Every argument is forwarded to omega.sh, so this file is only a shortcut:
#   * no arguments   -> interactive menu
#   * --install      -> install the panel
#   * --node-connect -> install this host as a node and connect it
set -Eeuo pipefail

REPO_SLUG="${OMEGA_REPO_SLUG:-Sarthak345662/vpanel}"
REPO_REF="${OMEGA_REPO_REF:-main}"
RAW_BASE="https://raw.githubusercontent.com/${REPO_SLUG}/${REPO_REF}"

command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 1; }

if [[ "$(id -u)" -ne 0 && "${1:-}" != "--help" ]]; then
    echo "Run as root: curl -fsSL ${RAW_BASE}/install.sh | sudo bash -s -- $*" >&2
    exit 1
fi

echo "→ Vizion Panel bootstrap (${REPO_SLUG}@${REPO_REF})"
exec bash <(curl -fsSL "${RAW_BASE}/omega.sh") "$@"
