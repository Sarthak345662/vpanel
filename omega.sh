#!/usr/bin/env bash
# VIZION PANEL - one command menu
#
# No clone needed:
#   curl -fsSL https://raw.githubusercontent.com/Sarthak345662/vpanel/main/omega.sh | sudo bash
#
# Or from a checkout:
#   sudo bash omega.sh
#
# Credit: sartahk34 · SKa
set -Eeuo pipefail

REPO_SLUG="${OMEGA_REPO_SLUG:-Sarthak345662/vpanel}"
REPO_REF="${OMEGA_REPO_REF:-main}"
ARCHIVE_URL="${OMEGA_REPO_ARCHIVE:-https://codeload.github.com/${REPO_SLUG}/tar.gz/refs/heads/${REPO_REF}}"
RAW_BASE="https://raw.githubusercontent.com/${REPO_SLUG}/${REPO_REF}"

clear_screen() {
    if [[ -t 1 ]]; then
        clear 2>/dev/null || printf '\033[H\033[2J'
    fi
}

# When started as `bash <(curl ...)` there is no checkout; fetch one into tmp.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-}")" 2>/dev/null && pwd || echo /)"
if [[ ! -f "$ROOT_DIR/setup.sh" ]]; then
    command -v curl >/dev/null 2>&1 || { echo "curl is required for the one-line installer." >&2; exit 1; }
    command -v tar >/dev/null 2>&1 || { echo "tar is required for the one-line installer." >&2; exit 1; }
    BOOT_DIR="$(mktemp -d)"
    echo "Downloading Vizion Panel (${REPO_SLUG}@${REPO_REF}) ..."
    if ! curl -fsSL "$ARCHIVE_URL" | tar -xz -C "$BOOT_DIR" --strip-components=1; then
        echo "Download failed. Check the network or clone the repo manually." >&2
        exit 1
    fi
    [[ -f "$BOOT_DIR/setup.sh" ]] || { echo "Downloaded archive is missing setup.sh" >&2; exit 1; }
    exec bash "$BOOT_DIR/omega.sh" "$@"
fi

INSTALL_DIR="${OMEGA_INSTALL_DIR:-/opt/omega-panel}"
PANEL_PORT="${OMEGA_PORT:-5000}"
NODE_PORT="${OMEGA_NODE_PORT:-5001}"
LOG_FILE="${OMEGA_LOG_FILE:-/tmp/omega-install.log}"

say()  { printf '\033[36m>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# Make box alignment reliable (counts characters, not bytes)
if locale -a 2>/dev/null | grep -qi '^c\.utf-\?8$'; then export LC_ALL=C.UTF-8; fi

BRAND_NAME="${OMEGA_BRAND:-VIZION  PANEL}"
BRAND_SUB="VPS · LXC · KVM control panel"
BRAND_CREDIT="Credit: sartahk34 · SKa"
BOX_W=46
ANIM_ON=1
[[ "${OMEGA_NO_ANIM:-0}" == "1" ]] && ANIM_ON=0
[[ -t 1 ]] || ANIM_ON=0

if [[ -t 1 ]]; then
    C_BORDER=$'\033[36m'; C_TITLE=$'\033[1;97m'; C_SUB=$'\033[96m'; C_DIM=$'\033[2;37m'; C_RST=$'\033[0m'
    C_OK=$'\033[32m'; C_BAD=$'\033[31m'; C_WARN=$'\033[33m'
else
    C_BORDER=""; C_TITLE=""; C_SUB=""; C_DIM=""; C_RST=""; C_OK=""; C_BAD=""; C_WARN=""
fi

# ---------------------------------------------------------------------------
# Animation helpers: a live spinner for long steps and staged progress lines.
# ---------------------------------------------------------------------------
SPIN_PID=""
spin_start() {
    local msg="${1:-working}"
    [[ "$ANIM_ON" == "1" ]] || { printf '  %s ...\n' "$msg"; return 0; }
    (
        local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
        while :; do
            for f in "${frames[@]}"; do
                printf '\r  \033[36m%s\033[0m %s' "$f" "$msg"
                sleep 0.08
            done
        done
    ) &
    SPIN_PID=$!
}
spin_stop() {
    if [[ -n "$SPIN_PID" ]]; then
        kill "$SPIN_PID" 2>/dev/null || true
        wait "$SPIN_PID" 2>/dev/null || true
        SPIN_PID=""
        [[ "$ANIM_ON" == "1" ]] && printf '\r\033[K'
    fi
}
trap 'spin_stop' EXIT

# run_animated "label" cmd args...  -> runs the command, spinner + log on failure
run_animated() {
    local label="$1"; shift
    mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
    spin_start "$label"
    if "$@" >>"$LOG_FILE" 2>&1; then
        spin_stop
        ok "$label"
        return 0
    fi
    spin_stop
    warn "$label failed — last lines from $LOG_FILE:"
    tail -n 8 "$LOG_FILE" 2>/dev/null | sed 's/^/    /' || true
    return 1
}

# Cosmetic pipeline animation: draw(checkpoints) — purely visual, no side effects.
draw_steps() {
    [[ "$ANIM_ON" == "1" ]] || return 0
    local steps=("$@") i=0 total=${#steps[@]}
    for step in "${steps[@]}"; do
        i=$((i + 1))
        printf '  \033[2;37m[%d/%d]\033[0m %s\n' "$i" "$total" "$step"
        sleep 0.12
    done
}

box_line() {   # box_line "text" "color"
    local text="$1" color="${2:-}" len l r
    len=${#text}
    l=$(( (BOX_W - len) / 2 )); r=$(( BOX_W - len - l ))
    printf '  %s║%s%*s%s%s%s%*s%s║%s\n' "$C_BORDER" "$C_RST" "$l" "" "$color" "$text" "$C_RST" "$r" "" "$C_BORDER" "$C_RST"
}

print_logo() {
    local bar
    bar="$(printf '═%.0s' $(seq 1 "$BOX_W"))"
    printf '\n  %s╔%s╗%s\n' "$C_BORDER" "$bar" "$C_RST"
    box_line "" ""
    box_line "$BRAND_NAME" "$C_TITLE"
    box_line "$BRAND_SUB" "$C_SUB"
    box_line "" ""
    box_line "$BRAND_CREDIT" "$C_DIM"
    printf '  %s╚%s╝%s\n' "$C_BORDER" "$bar" "$C_RST"
    print_state_line
    printf '\n'
}

print_state_line() {
    local state="not installed" color="$C_BAD"
    if command -v systemctl >/dev/null 2>&1 && [[ -f /etc/systemd/system/omega-panel.service ]]; then
        state="$(systemctl is-active omega-panel.service 2>/dev/null || true)"
        if [[ "$state" == "active" ]]; then color="$C_OK"; else color="$C_WARN"; fi
    fi
    printf '  Panel: %s%s%s   Port: %s   Dir: %s\n' "$color" "${state:-unknown}" "$C_RST" "$PANEL_PORT" "$INSTALL_DIR"
}

need_root() {
    [[ "$(id -u)" -eq 0 ]] || die "Run as root: sudo bash omega.sh $*"
}

need_systemd() {
    command -v systemctl >/dev/null 2>&1 || die "systemd is required (Ubuntu/Debian server)."
}

# ---------------------------------------------------------------------------
# Panel install / update / repair / doctor
# ---------------------------------------------------------------------------
install_panel() {
    need_root
    need_systemd
    echo
    say "Installing Vizion Panel from ${ROOT_DIR} ..."
    draw_steps "Checking systemd and Python" "Installing LXC / KVM / Incus stack" \
               "Creating the panel virtualenv" "Seeding the local node" "Starting 24/7 services"
    run_animated "Installing the panel (setup.sh)" bash "$ROOT_DIR/setup.sh" "$@" || die "Install failed. See $LOG_FILE"
    ok "Install finished."
    echo
    echo "  Panel URL : http://$(hostname -I 2>/dev/null | awk '{print $1}'):${PANEL_PORT}"
    echo "  Login     : admin / admin   (change it after first login)"
    echo "  Password  : /var/lib/omega-panel/first_admin_credentials.txt"
    echo "  Status    : sudo bash omega.sh --status"
    echo "  Bot owner : Admin -> Settings -> Integrations -> Bot owner Discord ID"
}

uninstall_panel() {
    need_root
    need_systemd
    echo "This stops and removes the panel services."
    read -rp "Also delete the database and panel code in ${INSTALL_DIR}? type 'yes' to delete: " purge || true
    if [[ "${purge:-}" == "yes" ]]; then
        warn "Full purge selected: code, database and config will be deleted."
        bash "$ROOT_DIR/scripts/omega-panel-uninstall.sh" --purge
    else
        bash "$ROOT_DIR/scripts/omega-panel-uninstall.sh"
    fi
}

# ---------------------------------------------------------------------------
# Node installation: LXC / KVM / Incus are three different stacks.
# ---------------------------------------------------------------------------
node_type_menu() {
    clear_screen
    print_logo
    printf '  %s[ NODE INSTALL ]%s\n' "$C_SUB" "$C_RST"
    echo "    1) LXC node    containers only (no QEMU/libvirt)"
    echo "    2) KVM node    QEMU/KVM + libvirt + /dev/kvm check"
    echo "    3) Incus node  containers + QEMU virtual machines"
    echo "    4) Connect an installed node to a panel (SSH command)"
    echo "    5) Node status"
    echo "    0) Back"
    echo
    read -rp "  Choose [0-5]: " pick || return 0
    case "${pick:-}" in
        1) install_node_type lxc ;;
        2) install_node_type kvm ;;
        3) install_node_type incus ;;
        4) node_connect ;;
        5) node_status ;;
        0|q|Q) return 0 ;;
        *) warn "Please choose 0-5." ;;
    esac
}

install_node_type() {
    local type="$1"
    need_root
    need_systemd
    case "$type" in
        lxc)   say "Installing an LXC node (plain LXC + Incus container backend, no KVM packages)" ;;
        kvm)   say "Installing a KVM node (QEMU/KVM + libvirt, /dev/kvm verified)" ;;
        incus) say "Installing an Incus node (system containers + QEMU virtual machines)" ;;
        *) die "Unknown node type: $type" ;;
    esac
    echo
    if [[ "$type" == "kvm" ]]; then
        draw_steps "Installing QEMU/KVM + libvirt" "Checking /dev/kvm" "Installing the node agent" "Waiting for the agent health endpoint"
    elif [[ "$type" == "lxc" ]]; then
        draw_steps "Installing LXC userland" "Installing Incus (container backend)" "Installing the node agent" "Waiting for the agent health endpoint"
    else
        draw_steps "Installing Incus + QEMU" "Initialising Incus storage/network" "Installing the node agent" "Waiting for the agent health endpoint"
    fi
    run_animated "Node stack (${type})" bash "$ROOT_DIR/scripts/omega-node-install.sh" "--type=${type}" \
        || die "Node install failed. See $LOG_FILE"
    echo
    ok "Node stack ready: ${type}"
    echo "  Agent health : http://127.0.0.1:${NODE_PORT}/api/health"
    echo "  Now connect it to the panel: menu 6 -> 4, or:"
    echo "  sudo bash omega.sh --node-connect --panel http://PANEL_IP:${PANEL_PORT} --key KEY --name NAME --url http://THIS_IP:${NODE_PORT} --type ${type}"
}

node_status() {
    echo
    echo "Node status"
    echo "------------------"
    bash "$ROOT_DIR/scripts/omega-node-install.sh" --check || true
    echo
    if command -v incus >/dev/null 2>&1; then
        echo "  incus      : $(incus --version 2>/dev/null || echo present)"
    else
        echo "  incus      : not installed"
    fi
    if [[ -e /dev/kvm ]]; then echo "  /dev/kvm   : available"; else echo "  /dev/kvm   : unavailable (no hardware acceleration)"; fi
    if command -v qemu-system-x86_64 >/dev/null 2>&1; then
        echo "  qemu       : $(qemu-system-x86_64 --version 2>/dev/null | head -n1)"
    else
        echo "  qemu       : not installed"
    fi
    if command -v lxc-create >/dev/null 2>&1; then echo "  lxc        : $(lxc-create --version 2>/dev/null || echo present)"; else echo "  lxc        : not installed"; fi
}

node_connect() {
    need_systemd
    local panel="" enroll_key="" node_name="" node_url="" node_bind="" node_type="lxc" panel_host

    # Non-interactive flags (used by the panel's generated one-line command).
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --panel) panel="${2:-}"; shift 2 ;;
            --key) enroll_key="${2:-}"; shift 2 ;;
            --name) node_name="${2:-}"; shift 2 ;;
            --url) node_url="${2:-}"; shift 2 ;;
            --type) node_type="${2:-}"; shift 2 ;;
            --bind) node_bind="${2:-}"; shift 2 ;;
            *) shift ;;
        esac
    done

    if [[ -z "$panel" ]]; then
        echo
        echo "Connect this server as a node of a Vizion panel."
        echo "The enrollment key is on the panel: Admin -> Nodes -> Add Node."
        echo
        read -rp "Panel URL [http://127.0.0.1:${PANEL_PORT}]: " panel || true
        panel="${panel:-http://127.0.0.1:${PANEL_PORT}}"
        read -rp "Enrollment key: " enroll_key || true
        read -rp "Node name [$(hostname -s 2>/dev/null || hostname)]: " node_name || true
        node_name="${node_name:-$(hostname -s 2>/dev/null || hostname)}"
        read -rp "Node type (lxc/kvm/incus) [lxc]: " node_type || true
        node_type="${node_type:-lxc}"
    fi
    [[ -n "${enroll_key:-}" ]] || die "Enrollment key is required (Admin -> Nodes -> Add Node)."
    node_name="${node_name:-$(hostname -s 2>/dev/null || hostname)}"
    case "$node_type" in lxc|kvm|incus) ;; *) warn "Unknown type '$node_type', using lxc"; node_type=lxc ;; esac

    panel_host="$(printf '%s' "$panel" | sed -E 's#^https?://([^/:]+).*#\1#')"
    if [[ -z "${node_bind:-}" ]]; then
        if [[ "$panel_host" == "127.0.0.1" || "$panel_host" == "localhost" ]]; then
            node_bind="127.0.0.1"
        else
            node_bind="0.0.0.0"
        fi
    fi
    if [[ -z "${node_url:-}" ]]; then
        if [[ "$node_bind" == "127.0.0.1" ]]; then
            node_url="http://127.0.0.1:${NODE_PORT}"
        else
            node_url="http://$(hostname -I 2>/dev/null | awk '{print $1}'):${NODE_PORT}"
        fi
    fi

    if [[ "$(id -u)" -eq 0 ]]; then
        run_animated "Installing the ${node_type} node agent (bind ${node_bind})" \
            bash "$ROOT_DIR/scripts/omega-node-install.sh" "--bind=${node_bind}" "--type=${node_type}" \
            || warn "Agent install reported a problem; continuing with the connect step."
    else
        warn "Not root: skipping agent install. Run 'sudo bash scripts/omega-node-install.sh --bind=${node_bind} --type=${node_type}' first."
    fi

    spin_start "Registering ${node_name} with ${panel}"
    if bash "$ROOT_DIR/scripts/omega-node-connect.sh" \
        --panel "$panel" --key "$enroll_key" --name "$node_name" --url "$node_url" >>"$LOG_FILE" 2>&1; then
        spin_stop
        ok "Node '${node_name}' connected to ${panel} (${node_type})"
    else
        spin_stop
        warn "Could not register the node. Last log lines:"
        tail -n 8 "$LOG_FILE" 2>/dev/null | sed 's/^/    /' || true
        die "Check the panel URL, enrollment key and that port ${NODE_PORT} is reachable."
    fi
    if [[ "$node_bind" != "127.0.0.1" ]] && command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q active; then
        say "Allowing node agent port ${NODE_PORT} from ${panel_host} only"
        ufw allow from "$panel_host" to any port "$NODE_PORT" proto tcp >/dev/null 2>&1 || true
    fi
}

# ---------------------------------------------------------------------------
# Panel user creation (CLI) - creates a login without the web UI.
# ---------------------------------------------------------------------------
create_panel_user() {
    need_root
    echo
    echo "Create a panel user"
    echo "-------------------"
    local username email password role max_vps bot_token
    read -rp "Username: " username || true
    [[ -n "${username:-}" ]] || die "Username is required."
    read -rp "Email [${username}@vps.local]: " email || true
    email="${email:-${username}@vps.local}"
    read -rp "Password (blank = generate): " password || true
    if [[ -z "${password:-}" ]]; then
        password="$(openssl rand -base64 12 2>/dev/null || head -c 12 /dev/urandom | base64)"
    fi
    read -rp "Max VPS [1]: " max_vps || true
    max_vps="${max_vps:-1}"
    read -rp "Admin user? [y/N]: " role || true
    local is_admin=0
    [[ "${role:-}" =~ ^[Yy] ]] && is_admin=1

    local py="${INSTALL_DIR}/venv/bin/python"
    [[ -x "$py" ]] || die "Panel is not installed (missing ${py}). Choose Install first."
    [[ -f "${INSTALL_DIR}/app.py" ]] || die "Panel code is missing in ${INSTALL_DIR}."

    local out
    out="$(cd "$INSTALL_DIR" && OMEGA_DATA_DIR="${OMEGA_DATA_DIR:-/var/lib/omega-panel}" "$py" - "$username" "$email" "$password" "$is_admin" "$max_vps" <<'PY'
import json
import secrets
import sys

username, email, password, is_admin, max_vps = sys.argv[1:6]
is_admin = int(is_admin)
max_vps = int(max_vps)

import app as panel

panel.init_db()
try:
    panel.migrate_nodes()
except Exception:
    pass
conn = panel.get_db()
columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
    print(json.dumps({"error": f"user '{username}' already exists"}))
    raise SystemExit(1)
fields = ["username", "email", "password", "is_admin", "api_key"]
values = [username, email, panel.hash_password(password), is_admin, panel.generate_api_key()]
if "role" in columns:
    fields.append("role")
    values.append("admin" if is_admin else "user")
if "max_vps" in columns:
    fields.append("max_vps")
    values.append(max_vps)
bot_token = None
if "bot_api_token_hash" in columns:
    import hashlib
    bot_token = "omega_bot_" + secrets.token_urlsafe(32)
    fields.append("bot_api_token_hash")
    values.append(hashlib.sha256(bot_token.encode()).hexdigest())
placeholders = ",".join("?" for _ in fields)
conn.execute(f"INSERT INTO users ({','.join(fields)}) VALUES ({placeholders})", values)
conn.commit()
row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
conn.close()
print(json.dumps({"id": row["id"], "username": username, "admin": bool(is_admin),
                  "max_vps": max_vps, "bot_token": bot_token}))
PY
)" || { echo "$out"; die "User creation failed."; }

    echo
    ok "Panel user created"
    "$py" - "$out" <<'PY'
import json
import sys

data = json.loads(sys.argv[1])
print(f"  username : {data['username']}")
print(f"  user id  : #{data['id']}")
print(f"  admin    : {'yes' if data['admin'] else 'no'}")
print(f"  max vps  : {data['max_vps']}")
if data.get("bot_token"):
    print(f"  bot token: {data['bot_token']}")
    print("             (use it for /api/bot/* and the Discord account link)")
PY
    echo "  login at : http://$(hostname -I 2>/dev/null | awk '{print $1}'):${PANEL_PORT}/login"
    echo "  password : (the one you typed or the generated value above)"
}

# ---------------------------------------------------------------------------
# Port forwarding / status / update / repair / doctor
# ---------------------------------------------------------------------------
port_forward() {
    clear_screen
    bash "$ROOT_DIR/scripts/omega-portforward.sh" --interactive
}

panel_status() {
    echo
    echo "Panel status"
    echo "------------------"
    if command -v systemctl >/dev/null 2>&1; then
        for unit in omega-panel.service omega-bot.service omega-node.service omega-jobs.service omega-watchdog.timer omega-image-refresh.timer; do
            state="$(systemctl is-active "$unit" 2>/dev/null || true)"
            printf '  %-28s %s\n' "$unit" "${state:-not-installed}"
        done
    else
        warn "systemctl not available"
    fi
    local health
    health="$(curl -fsS --max-time 4 "http://127.0.0.1:${NODE_PORT}/api/health" 2>/dev/null || true)"
    echo "  node agent health            ${health:-unreachable}"
    if [[ -r /var/lib/omega-panel/first_admin_credentials.txt ]]; then
        echo "  credentials file             /var/lib/omega-panel/first_admin_credentials.txt"
    fi
    echo
}

fetch_latest() {   # downloads latest code into $1
    local dest="$1"
    command -v curl >/dev/null 2>&1 || die "curl is required for update."
    command -v tar  >/dev/null 2>&1 || die "tar is required for update."
    say "Downloading latest code (${REPO_SLUG}@${REPO_REF}) ..."
    curl -fsSL "$ARCHIVE_URL" | tar -xz -C "$dest" --strip-components=1 \
        || die "Download failed. Check internet / repo access."
    [[ -f "$dest/setup.sh" && -f "$dest/app.py" ]] || die "Downloaded archive looks incomplete."
}

restart_services() {
    # Make sure the Discord bot / background worker unit exists (added in newer versions).
    if [[ -f "$INSTALL_DIR/bot_runner.py" && ! -f /etc/systemd/system/omega-bot.service ]]; then
        local svc_user="omega-panel"; id "$svc_user" >/dev/null 2>&1 || svc_user="root"
        cat > /etc/systemd/system/omega-bot.service <<UNIT
[Unit]
Description=Vizion Panel Discord bot and background workers
After=network-online.target incus.service omega-panel.service
Wants=network-online.target

[Service]
Type=simple
User=${svc_user}
Group=${svc_user}
WorkingDirectory=${INSTALL_DIR}
Environment=PYTHONPATH=${INSTALL_DIR}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/etc/omega-panel/omega.env
ExecStart=${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/bot_runner.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/var/lib/omega-panel /etc/omega-panel

[Install]
WantedBy=multi-user.target
UNIT
        systemctl daemon-reload 2>/dev/null || true
        systemctl enable omega-bot.service >/dev/null 2>&1 || true
    fi
    systemctl daemon-reload 2>/dev/null || true
    for unit in omega-panel.service omega-bot.service omega-jobs.service omega-node.service; do
        if [[ -f "/etc/systemd/system/$unit" ]]; then
            systemctl restart "$unit" 2>/dev/null && ok "restarted $unit" || warn "could not restart $unit"
        fi
    done
}

update_panel() {
    need_root
    need_systemd
    local tmp
    if git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        say "Git checkout found - pulling the latest code ..."
        git -C "$ROOT_DIR" pull --ff-only || warn "git pull failed, continuing with local code."
        bash "$ROOT_DIR/setup.sh" --noninteractive
    else
        tmp="$(mktemp -d)"
        fetch_latest "$tmp"
        say "Applying update to ${INSTALL_DIR} (database and config are kept) ..."
        bash "$tmp/setup.sh" --noninteractive
        rm -rf "$tmp"
    fi
    restart_services
    ok "Update finished."
    doctor_panel quiet || true
}

fix_panel() {
    need_root
    need_systemd
    say "Repairing ${INSTALL_DIR} ..."
    [[ -d "$INSTALL_DIR" ]] || die "Panel is not installed. Choose Install first."
    if [[ "$ROOT_DIR" != "$INSTALL_DIR" && -f "$ROOT_DIR/app.py" ]]; then
        say "Syncing code from ${ROOT_DIR} ..."
        cp -a "$ROOT_DIR"/. "$INSTALL_DIR"/
    fi
    if [[ -x "$INSTALL_DIR/venv/bin/pip" ]]; then
        say "Re-installing Python requirements ..."
        "$INSTALL_DIR/venv/bin/pip" install --no-cache-dir -q -r "$INSTALL_DIR/requirements.txt" \
            && ok "requirements ok" || warn "pip install had errors"
    else
        warn "venv missing - run Update (it rebuilds everything)."
    fi
    chmod +x "$INSTALL_DIR"/scripts/*.sh "$INSTALL_DIR/omega.sh" 2>/dev/null || true
    if id omega-panel >/dev/null 2>&1; then chown -R omega-panel:omega-panel "$INSTALL_DIR" /var/lib/omega-panel 2>/dev/null || true; fi
    restart_services
    sleep 2
    doctor_panel quiet || true
    echo
    echo "  If an error still shows, run:  sudo bash ${INSTALL_DIR}/omega.sh --update"
}

doctor_panel() {
    local quiet="${1:-}" bad=0 py="python3"
    [[ "$quiet" == "quiet" ]] || { echo; echo "Panel doctor"; echo "------------"; }
    [[ -x "$INSTALL_DIR/venv/bin/python" ]] && py="$INSTALL_DIR/venv/bin/python"

    if [[ -f "$INSTALL_DIR/app.py" ]]; then
        if "$py" -m py_compile "$INSTALL_DIR/app.py" 2>/dev/null; then ok "app.py syntax ok"; else printf '%s✗%s app.py has a syntax error\n' "$C_BAD" "$C_RST"; bad=1; fi
        if out="$(cd "$INSTALL_DIR" && "$py" - <<'PY'
import ast, builtins, sys
src = open("app.py").read(); t = ast.parse(src)
d = set(dir(builtins))
for n in ast.walk(t):
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        d.add(n.name)
        if not isinstance(n, ast.ClassDef):
            a = n.args
            for x in a.args + a.kwonlyargs + a.posonlyargs: d.add(x.arg)
            if a.vararg: d.add(a.vararg.arg)
            if a.kwarg: d.add(a.kwarg.arg)
    elif isinstance(n, ast.Lambda):
        for x in n.args.args + n.args.kwonlyargs: d.add(x.arg)
        if n.args.vararg: d.add(n.args.vararg.arg)
        if n.args.kwarg: d.add(n.args.kwarg.arg)
    elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)): d.add(n.id)
    elif isinstance(n, (ast.Import, ast.ImportFrom)):
        for a in n.names: d.add((a.asname or a.name).split(".")[0])
    elif isinstance(n, ast.ExceptHandler) and n.name: d.add(n.name)
    elif isinstance(n, (ast.Global, ast.Nonlocal)): d.update(n.names)
miss = sorted({(n.id, n.lineno) for n in ast.walk(t) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in d and n.id != "__file__"}, key=lambda x: x[1])
for name, line in miss: print(f"undefined name '{name}' at app.py line {line}")
sys.exit(1 if miss else 0)
PY
        )"; then ok "no undefined names in app.py"; else printf '%s✗%s %s\n' "$C_BAD" "$C_RST" "$out"; bad=1; fi
    else
        printf '%s✗%s %s/app.py not found (not installed?)\n' "$C_BAD" "$C_RST" "$INSTALL_DIR"; bad=1
    fi

    if command -v systemctl >/dev/null 2>&1; then
        for unit in omega-panel.service omega-bot.service omega-jobs.service omega-node.service; do
            [[ -f "/etc/systemd/system/$unit" ]] || continue
            if [[ "$(systemctl is-active "$unit" 2>/dev/null)" == "active" ]]; then ok "$unit running"; else printf '%s✗%s %s is not running\n' "$C_BAD" "$C_RST" "$unit"; bad=1; fi
        done
    fi

    if curl -fsS -o /dev/null --max-time 5 "http://127.0.0.1:${PANEL_PORT}/" 2>/dev/null; then ok "panel answers on port ${PANEL_PORT}"; else printf '%s✗%s panel does not answer on port %s\n' "$C_BAD" "$C_RST" "$PANEL_PORT"; bad=1; fi

    if [[ "$bad" -ne 0 ]] && command -v journalctl >/dev/null 2>&1; then
        echo; warn "Last panel log lines:"
        journalctl -u omega-panel.service -n 15 --no-pager 2>/dev/null | sed 's/^/    /' || true
        if [[ -f /etc/systemd/system/omega-bot.service ]]; then
            echo; warn "Last bot log lines:"
            journalctl -u omega-bot.service -n 15 --no-pager 2>/dev/null | sed 's/^/    /' || true
        fi
    fi
    return "$bad"
}

# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------
main_menu() {
    while true; do
        clear_screen
        print_logo
        printf '  %s[ SETUP ]%s\n' "$C_SUB" "$C_RST"
        echo "    1) Install panel"
        echo "    2) Update panel          (latest code from GitHub)"
        echo "    3) Fix / repair panel    (use when 'Something went wrong')"
        echo "    4) Doctor                (check errors + logs)"
        printf '\n  %s[ MANAGE ]%s\n' "$C_SUB" "$C_RST"
        echo "    5) Panel status"
        echo "    6) Nodes                 (install LXC / KVM / Incus, connect by IP)"
        echo "    7) Create panel user"
        echo "    8) Port forwarding"
        printf '\n  %s[ DANGER ]%s\n' "$C_SUB" "$C_RST"
        echo "    9) Uninstall panel"
        echo "    0) Exit"
        echo
        read -rp "  Choose [0-9]: " choice || exit 0
        case "${choice:-}" in
            1) install_panel ;;
            2) update_panel ;;
            3) fix_panel ;;
            4) doctor_panel || true ;;
            5) panel_status ;;
            6) node_type_menu ;;
            7) create_panel_user ;;
            8) port_forward ;;
            9) uninstall_panel ;;
            0|q|Q) exit 0 ;;
            *) warn "Please choose 0-9." ;;
        esac
        echo
        read -rp "Press Enter to return to the menu..." _ || exit 0
    done
}

# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------
NODE_TYPE_ARG=""
NODE_CONNECT_ARGS=()
POSITIONAL=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --type=*) NODE_TYPE_ARG="${1#*=}"; shift ;;
        --type) NODE_TYPE_ARG="${2:-}"; shift 2 ;;
        --node-connect) NODE_CONNECT_ARGS+=("--node-connect"); shift ;;
        --panel|--key|--name|--url|--bind)
            NODE_CONNECT_ARGS+=("$1" "${2:-}"); shift 2 ;;
        *) POSITIONAL+=("$1"); shift ;;
    esac
done
set -- "${POSITIONAL[@]+"${POSITIONAL[@]}"}"

case "${1:-}" in
    --install) shift; install_panel "$@" ;;
    --uninstall) uninstall_panel ;;
    --node) node_connect ;;
    --node-connect)
        shift
        node_connect "${NODE_CONNECT_ARGS[@]+"${NODE_CONNECT_ARGS[@]}"}" ;;
    --node-install) install_node_type "${NODE_TYPE_ARG:-lxc}" ;;
    --node-types|--nodes) node_type_menu ;;
    --node-status) node_status ;;
    --create-user|--user-create|--adduser) create_panel_user ;;
    --port-forward) port_forward ;;
    --status) panel_status ;;
    --update) update_panel ;;
    --fix|--repair) fix_panel ;;
    --doctor) doctor_panel ;;
    --help|-h)
        clear_screen
        print_logo
        cat <<EOF
Usage: sudo bash omega.sh [option]

  (no option)        interactive menu
  --install          install the panel (runs setup.sh)
  --uninstall        stop and remove the panel services
  --node             install this host as a node and connect it (guided)
  --node-connect     non-interactive connect, needs:
                       --panel URL --key KEY --name NAME --url NODE_URL [--type lxc|kvm|incus]
  --node-install     install only the node stack: --type lxc | kvm | incus
  --node-types       interactive node install menu
  --node-status      show incus / qemu / lxc / kvm capabilities
  --create-user      create a panel user from the CLI
  --port-forward     create/list/remove socat port forwards
  --status           service and node agent health
  --update           download latest code + re-run the installer
  --fix              repair deps/permissions and restart services
  --doctor           check code, services, port and show logs

Node types (each installs a different stack):
  lxc    plain LXC containers only (no QEMU/libvirt packages)
  kvm    QEMU/KVM + libvirt, /dev/kvm verified before the first VM
  incus  Incus containers + QEMU virtual machines

One line (no clone):
  curl -fsSL ${RAW_BASE}/omega.sh | sudo bash
  curl -fsSL ${RAW_BASE}/omega.sh | sudo bash -s -- --install

Default login after install: admin / admin
Credit: sartahk34 · SKa
EOF
        ;;
    "") main_menu ;;
    *) die "Unknown option: $1 (try --help)" ;;
esac
