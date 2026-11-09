#!/usr/bin/env bash
# One-shot fix for "SSHX link failed" (no IP/DNS in LXC VPS, Non-KVM SSH timeout).
# Usage: put this file, app.py and omega-nonkvm.sh in the SAME folder, then:
#   sudo bash fix-omega.sh
set -u
[[ $EUID -eq 0 ]] || { echo "Run as root: sudo bash $0"; exit 1; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIR=/opt/omega-panel
say() { echo -e "\n\033[1;36m==> $*\033[0m"; }

# ---------------------------------------------------------------- 1. files
say "Installing patched panel files"
[[ -d $DIR ]] || { echo "$DIR not found - is the panel installed?"; exit 1; }
STAMP=$(date +%s)
for pair in "app.py:$DIR/app.py" "omega-nonkvm.sh:$DIR/scripts/omega-nonkvm.sh"; do
  f="${pair%%:*}"; dst="${pair#*:}"
  src="$HERE/$f"; [[ -f $src ]] || src="$HERE/scripts/$f"
  if [[ -f $src ]]; then
    cp -a "$dst" "$dst.bak.$STAMP" 2>/dev/null || true
    cp "$src" "$dst" && echo "updated $dst"
  else
    echo "WARNING: $src missing, skipped"
  fi
done
chmod +x "$DIR/scripts/omega-nonkvm.sh" 2>/dev/null || true
chown omega-panel:omega-panel "$DIR/app.py" "$DIR/scripts/omega-nonkvm.sh" 2>/dev/null || true

# ---------------------------------------------------------------- 2. bridges
say "Fixing Incus/LXD bridge (DHCP + NAT)"
BRIDGES=$(incus network list --format csv 2>/dev/null | awk -F, '$2=="bridge" && toupper($3)=="YES"{print $1}')
[[ -n $BRIDGES ]] || BRIDGES="incusbr0 lxdbr0"
for b in $BRIDGES; do
  ip link show "$b" >/dev/null 2>&1 || continue
  echo "bridge: $b"
  for k in ipv4.dhcp ipv4.nat; do
    [[ "$(incus network get "$b" $k 2>/dev/null)" == "false" ]] && incus network set "$b" $k true && echo "  enabled $k"
  done
  if [[ "$(incus network get "$b" ipv4.address 2>/dev/null)" =~ ^(none|)$ ]]; then
    incus network set "$b" ipv4.address auto 2>/dev/null && echo "  set ipv4.address auto"
  fi
done

# ---------------------------------------------------------------- 3. forwarding + firewall
say "Enabling IP forwarding"
sysctl -w net.ipv4.ip_forward=1 >/dev/null
echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-omega-forward.conf

say "Opening firewall for VM bridges"
for b in $BRIDGES; do
  ip link show "$b" >/dev/null 2>&1 || continue
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow in on "$b" >/dev/null
    ufw route allow in on "$b" >/dev/null
    ufw route allow out on "$b" >/dev/null
    echo "ufw rules added for $b"
  fi
  if command -v iptables >/dev/null 2>&1; then
    iptables -C INPUT   -i "$b" -j ACCEPT 2>/dev/null || iptables -I INPUT   -i "$b" -j ACCEPT
    iptables -C FORWARD -i "$b" -j ACCEPT 2>/dev/null || iptables -I FORWARD -i "$b" -j ACCEPT
    iptables -C FORWARD -o "$b" -j ACCEPT 2>/dev/null || iptables -I FORWARD -o "$b" -j ACCEPT
    echo "iptables rules added for $b"
  fi
done
# keep rules after reboot when a persistence tool exists
command -v netfilter-persistent >/dev/null 2>&1 && netfilter-persistent save >/dev/null 2>&1 || true
# restart Incus' own DHCP/NAT rules so they are re-applied after the above
systemctl reload incus 2>/dev/null || true

# ---------------------------------------------------------------- 4. KVM
say "Checking KVM"
modprobe kvm 2>/dev/null; modprobe kvm_intel 2>/dev/null || modprobe kvm_amd 2>/dev/null
if [[ -e /dev/kvm ]]; then
  chmod 666 /dev/kvm 2>/dev/null || true
  usermod -aG kvm omega-panel 2>/dev/null || true
  echo "/dev/kvm present - Non-KVM guests will boot fast"
else
  echo "No /dev/kvm: Non-KVM VPS will use slow emulation (needs a few minutes to boot)."
  echo "Ask your host provider to enable nested virtualization for real speed."
fi

# ---------------------------------------------------------------- 5. restart
say "Restarting panel"
python3 -m py_compile "$DIR/app.py" && echo "app.py syntax OK" || { echo "app.py has a syntax error!"; exit 1; }
systemctl daemon-reload
systemctl restart omega-panel
for s in omega-jobs omega-bot omega-watchdog.timer; do
  systemctl is-enabled "$s" >/dev/null 2>&1 && systemctl restart "$s" 2>/dev/null
done
sleep 2
systemctl is-active omega-panel >/dev/null && echo -e "\n\033[1;32mDONE - panel is running. Now click SSHX on your VPS.\033[0m" \
  || { echo "Panel failed to start:"; journalctl -u omega-panel -n 20 --no-pager; exit 1; }
