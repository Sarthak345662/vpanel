#!/usr/bin/env bash
# Vizion Panel - install the node stack AND the node agent on this host.
#
#   sudo bash scripts/omega-node-install.sh                     # auto-detect stack
#   sudo bash scripts/omega-node-install.sh --type=lxc          # containers only
#   sudo bash scripts/omega-node-install.sh --type=kvm          # QEMU/KVM + libvirt
#   sudo bash scripts/omega-node-install.sh --type=incus        # containers + VMs
#   sudo bash scripts/omega-node-install.sh --check             # status only
#
# LXC, KVM and Incus are different stacks: this script installs exactly the one
# you ask for and never purges an existing LXD/Incus installation.
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${OMEGA_INSTALL_DIR:-/opt/omega-panel}"
SERVICE_USER="${OMEGA_SERVICE_USER:-omega-panel}"
NODE_PORT="${OMEGA_NODE_PORT:-5001}"
NODE_BIND="${OMEGA_NODE_BIND:-127.0.0.1}"
NODE_TYPE="${OMEGA_NODE_TYPE:-auto}"
UNIT="omega-node.service"
MODE="install"

for arg in "$@"; do
  case "$arg" in
    --check|--status) MODE="check" ;;
    --bind=*) NODE_BIND="${arg#*=}" ;;
    --type=*) NODE_TYPE="${arg#*=}" ;;
    --help)
      cat <<EOF
Vizion node installer

Usage: sudo bash scripts/omega-node-install.sh [--check] [--type=lxc|kvm|incus]
                                                [--bind=ADDR]

  --type=lxc     plain LXC userland + Incus container backend (no QEMU/libvirt)
  --type=kvm     QEMU/KVM + libvirt + cpu-checker, /dev/kvm verified
  --type=incus   Incus: system containers + QEMU virtual machines
  --type=auto    detect an existing stack, otherwise install Incus (default)
  --type=none    install only the agent (stack already present)
  --bind=ADDR    agent listen address (default 127.0.0.1, 0.0.0.0 for remote panel)
  --check        print stack + agent status, install nothing

Environment: OMEGA_INSTALL_DIR, OMEGA_NODE_PORT, OMEGA_NODE_BIND, OMEGA_NODE_TYPE
EOF
      exit 0 ;;
  esac
done

have() { command -v "$1" >/dev/null 2>&1; }

stack_summary() {
  local incus="missing" kvm="missing" qemu="missing" lxc="missing"
  have incus && incus="$(incus --version 2>/dev/null || echo present)"
  [[ -e /dev/kvm ]] && kvm="available" || kvm="unavailable"
  have qemu-system-x86_64 && qemu="$(qemu-system-x86_64 --version 2>/dev/null | head -n1)"
  have lxc-create && lxc="$(lxc-create --version 2>/dev/null || echo present)"
  echo "STACK_INCUS=${incus}"
  echo "STACK_LXC=${lxc}"
  echo "STACK_QEMU=${qemu}"
  echo "STACK_KVM=${kvm}"
  if have incus; then
    if incus info >/dev/null 2>&1; then echo "STACK_INCUS_READY=yes"; else echo "STACK_INCUS_READY=no"; fi
  fi
}

if [[ "$MODE" == "check" ]]; then
  state="$(systemctl is-active "$UNIT" 2>/dev/null || true)"
  echo "NODE_TYPE=${NODE_TYPE}"
  echo "NODE_SERVICE=${state:-unknown}"
  if have curl; then
    health="$(curl -fsS --max-time 4 "http://127.0.0.1:${NODE_PORT}/api/health" 2>/dev/null || true)"
    if [[ -n "$health" ]]; then echo "NODE_HEALTH=${health}"; else echo "NODE_HEALTH=unreachable"; fi
  fi
  if [[ -s /etc/omega-panel/node-token ]]; then echo "NODE_TOKEN=present"; else echo "NODE_TOKEN=missing"; fi
  if [[ -f "/etc/systemd/system/${UNIT}" ]]; then
    echo "NODE_BIND=$(grep -oE -- '--bind [^ ]+' "/etc/systemd/system/${UNIT}" | awk '{print $2}' | cut -d: -f1)"
  fi
  stack_summary
  exit 0
fi

die() { echo "ERROR: $*" >&2; exit 1; }
[[ "$(id -u)" -eq 0 ]] || die "Run as root: sudo bash scripts/omega-node-install.sh"
have systemctl || die "systemd is required"

case "$NODE_TYPE" in
  lxc|kvm|incus|none) ;;
  auto)
    if have incus; then
      NODE_TYPE="incus"
    else
      NODE_TYPE="incus"
    fi
    ;;
  *) die "Unknown --type '$NODE_TYPE' (use lxc, kvm, incus, none or auto)" ;;
esac

# ---------------------------------------------------------------------------
# Package helpers
# ---------------------------------------------------------------------------
APT_UPDATED=0
apt_install() {
  if ! have apt-get; then return 1; fi
  if [[ "$APT_UPDATED" -eq 0 ]]; then
    DEBIAN_FRONTEND=noninteractive apt-get update -y >/dev/null 2>&1 || true
    APT_UPDATED=1
  fi
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@" >/dev/null 2>&1
}
dnf_install() {
  if ! have dnf; then return 1; fi
  dnf -y install "$@" >/dev/null 2>&1
}
pkg_install() {
  if have apt-get; then apt_install "$@"; else dnf_install "$@"; fi
}
pkg_install_optional() {
  pkg_install "$@" || echo "[node-install] optional package(s) skipped: $*"
}

install_incus() {
  if have incus; then
    echo "[node-install] Incus already installed"
  else
    echo "[node-install] installing Incus"
    if have apt-get; then
      if ! apt_install incus; then
        install -d -m 0755 /etc/apt/keyrings
        curl -fsSL https://pkgs.zabbly.com/key.asc -o /etc/apt/keyrings/zabbly.asc
        CODENAME="${VERSION_CODENAME:-$(. /etc/os-release 2>/dev/null; echo "${VERSION_CODENAME:-noble}")}"
        printf 'deb [signed-by=/etc/apt/keyrings/zabbly.asc] https://pkgs.zabbly.com/incus/stable %s main\n' "$CODENAME" \
          > /etc/apt/sources.list.d/incus.list
        APT_UPDATED=0
        apt_install incus || die "Could not install Incus from apt or the Zabbly repository"
      fi
    elif have dnf; then
      dnf_install incus || die "Could not install Incus (enable the Zabbly COPR/EPEL repository first)"
    else
      die "Unsupported package manager: install Incus manually, then rerun with --type=none"
    fi
  fi
  systemctl enable --now incus 2>/dev/null || systemctl enable --now incus.service 2>/dev/null || true
  if ! incus info >/dev/null 2>&1; then
    echo "[node-install] initialising Incus storage + network"
    incus admin init --auto >/dev/null 2>&1 || incus admin init --minimal >/dev/null 2>&1 || \
      echo "[node-install] Incus init needs manual attention: incus admin init"
  fi
  incus config set images.auto_update_cached true >/dev/null 2>&1 || true
  incus config set images.auto_update_interval 6 >/dev/null 2>&1 || true
}

install_lxc_type() {
  echo "[node-install] LXC node: container backend only (no QEMU/libvirt packages)"
  pkg_install_optional lxc lxc-templates bridge-utils uidmap libpam-cgfs
  install_incus
  # Containers must be able to run apparmor/lxcfs; never block on missing bits.
  systemctl enable --now lxcfs 2>/dev/null || true
}

install_kvm_type() {
  echo "[node-install] KVM node: QEMU/KVM + libvirt"
  pkg_install_optional qemu-kvm qemu-system-x86 qemu-utils libvirt-daemon-system \
    libvirt-clients bridge-utils virtinst virt-manager cpu-checker cloud-image-utils
  systemctl enable --now libvirtd 2>/dev/null || systemctl enable --now virtqemud 2>/dev/null || true
  if have kvm-ok; then kvm-ok 2>&1 | sed 's/^/[node-install] /' || true; fi
  if [[ -e /dev/kvm ]]; then
    echo "[node-install] /dev/kvm is available: hardware acceleration enabled"
  else
    echo "[node-install] WARNING: /dev/kvm is missing. KVM guests will refuse to start;" \
         "nested virtualisation is probably disabled on the host. Use --type=incus and a non-KVM guest instead."
  fi
  install_incus
}

install_incus_type() {
  echo "[node-install] Incus node: system containers + QEMU virtual machines"
  pkg_install_optional qemu-kvm qemu-utils cloud-image-utils cpu-checker
  install_incus
}

case "$NODE_TYPE" in
  lxc) install_lxc_type ;;
  kvm) install_kvm_type ;;
  incus) install_incus_type ;;
  none) echo "[node-install] --type=none: keeping the existing virtualisation stack" ;;
esac

echo "[node-install] stack summary:"
stack_summary | sed 's/^/[node-install]   /'

# ---------------------------------------------------------------------------
# Panel agent (unchanged behaviour: venv, token, systemd unit)
# ---------------------------------------------------------------------------
echo "[node-install] install directory: $INSTALL_DIR"
id "$SERVICE_USER" >/dev/null 2>&1 || \
  useradd --system --create-home --home-dir /var/lib/omega-panel --shell /usr/sbin/nologin "$SERVICE_USER"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 /var/lib/omega-panel /etc/omega-panel "$INSTALL_DIR"

if [[ "$(readlink -f "$APP_DIR")" != "$(readlink -f "$INSTALL_DIR")" ]]; then
  cp -a "$APP_DIR/node.py" "$APP_DIR/node_requirements.txt" "$INSTALL_DIR/"
  rm -rf "$INSTALL_DIR/core"
  cp -a "$APP_DIR/core" "$INSTALL_DIR/core"
  install -d "$INSTALL_DIR/scripts" "$INSTALL_DIR/systemd"
  cp -a "$APP_DIR/scripts/." "$INSTALL_DIR/scripts/"
  cp -a "$APP_DIR/systemd/omega-node.service" "$INSTALL_DIR/systemd/omega-node.service"
fi

# Give the agent access to the virtualisation sockets.
for group in kvm libvirt lxd incus incus-admin; do
  getent group "$group" >/dev/null 2>&1 && usermod -aG "$group" "$SERVICE_USER" 2>/dev/null || true
done

PYTHON_BIN="$(command -v python3)" || die "python3 is required"
if [[ ! -x "$INSTALL_DIR/venv/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$INSTALL_DIR/venv"
  "$INSTALL_DIR/venv/bin/python" -m pip install --upgrade pip wheel >/dev/null
fi
"$INSTALL_DIR/venv/bin/pip" install --no-cache-dir -r "$INSTALL_DIR/node_requirements.txt" >/dev/null
echo "[node-install] python dependencies ready"

if [[ ! -s /etc/omega-panel/node-token ]]; then
  umask 077
  openssl rand -hex 32 > /etc/omega-panel/node-token
  echo "[node-install] generated a new node token"
fi
chown root:"$SERVICE_USER" /etc/omega-panel/node-token 2>/dev/null || true
chmod 0640 /etc/omega-panel/node-token

install -m 0644 "$INSTALL_DIR/systemd/omega-node.service" "/etc/systemd/system/${UNIT}"
# A node on another VPS must listen beyond loopback for the panel to reach it.
if [[ "$NODE_BIND" != "127.0.0.1" ]]; then
  sed -i -E "s#--bind [0-9.]+:[0-9]+#--bind ${NODE_BIND}:${NODE_PORT}#g" "/etc/systemd/system/${UNIT}"
fi
systemctl daemon-reload
systemctl enable "$UNIT" >/dev/null 2>&1 || true
systemctl restart "$UNIT"
echo "[node-install] waiting for the agent to answer on port ${NODE_PORT}"
status="unknown"
for _ in $(seq 1 20); do
  sleep 1
  if have curl && curl -fsS --max-time 3 "http://127.0.0.1:${NODE_PORT}/api/health" >/dev/null 2>&1; then
    status="healthy"; break
  fi
  status="$(systemctl is-active "$UNIT" 2>/dev/null || true)"
done

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" >/dev/null 2>&1 || true
echo "NODE_TYPE=${NODE_TYPE}"
echo "NODE_SERVICE=$(systemctl is-active "$UNIT" 2>/dev/null || echo unknown)"
echo "NODE_HEALTH=${status}"
echo "NODE_PORT=${NODE_PORT}"
echo "NODE_BIND=${NODE_BIND}"
echo "NODE_TOKEN_FILE=/etc/omega-panel/node-token"
if [[ "$NODE_BIND" != "127.0.0.1" ]]; then
  echo "NODE_FIREWALL=allow ${NODE_PORT}/tcp only from the panel IP (ufw allow from PANEL_IP to any port ${NODE_PORT} proto tcp)"
fi
[[ "$status" == "healthy" ]] || {
  echo "The agent did not become healthy. Inspect: journalctl -u ${UNIT} -n 50 --no-pager" >&2
  exit 1
}
echo "[node-install] done (${NODE_TYPE} node ready)"
