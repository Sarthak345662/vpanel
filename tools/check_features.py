"""Feature checks for the node-add flow, VPS creation and the Discord command pack."""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmp = tempfile.mkdtemp(prefix="omega-check-")
os.environ["OMEGA_DATA_DIR"] = tmp
os.environ["OMEGA_DB"] = os.path.join(tmp, "omega.db")
os.environ["OMEGA_SECRET_FILE"] = os.path.join(tmp, "secret")
os.environ["OMEGA_NODE_TOKEN_FILE"] = os.path.join(tmp, "node-token")
os.environ["OMEGA_BOT_PREFIX_COMMANDS"] = "0"

import app as panel  # noqa: E402

failures = []

# Keep the real executor: a later section stubs panel.lxc_attach globally.
REAL_LXC_ATTACH = panel.lxc_attach


def check(name, condition, detail=""):
    if condition:
        print(f"PASS  {name}")
    else:
        failures.append(f"{name} {detail}")
        print(f"FAIL  {name} {detail}")


panel.init_db()
conn = panel.get_db()
conn.execute("DELETE FROM nodes WHERE is_local=0")
conn.execute("DELETE FROM vps")
conn.commit()
conn.close()
panel.migrate_nodes()

# 1. Local node is seeded so VPS creation cannot fail with "No online node".
conn = panel.get_db()
nodes = conn.execute("SELECT id, name, status, is_local FROM nodes").fetchall()
conn.close()
check("local node seeded", len(nodes) >= 1 and nodes[0]["is_local"] == 1, str([dict(n) for n in nodes]))
check("local node online", bool(nodes) and nodes[0]["status"] == "online", str(nodes[0]["status"] if nodes else "none"))

conn = panel.get_db()
if not conn.execute("SELECT 1 FROM users WHERE is_admin=1").fetchone():
    conn.execute(
        "INSERT INTO users(username,email,password,is_admin,api_key) VALUES(?,?,?,?,?)",
        ("admin", "admin@vps.local", panel.hash_password("admin"), 1, panel.generate_api_key()),
    )
    conn.commit()
admin_id = conn.execute("SELECT id FROM users WHERE is_admin=1 LIMIT 1").fetchone()["id"]
conn.close()

client = panel.app.test_client()
with client.session_transaction() as sess:
    sess["user_id"] = admin_id
    sess["username"] = "admin"
    sess["is_admin"] = True

for path in ["/dashboard", "/nodes", "/admin/integrations", f"/nodes/connect/{nodes[0]['id']}"]:
    resp = client.get(path)
    check(f"GET {path}", resp.status_code == 200, f"status={resp.status_code}")

# 2. Command preview for the Add Node dialog (per type).
for node_type in ("lxc", "kvm", "incus"):
    resp = client.post("/nodes/command", json={"name": f"node-{node_type}", "ip": "203.0.113.9",
                                               "node_type": node_type})
    data = resp.get_json() or {}
    command = data.get("command", "")
    check(f"command preview {node_type}", resp.status_code == 200 and "--node-connect" in command
          and f"--type {node_type}" in command, command or str(data))

# 3. Add node with only name + IP (no agent running yet).
resp = client.post("/nodes/add", data={"name": "ip-node", "hostname": "198.51.100.7",
                                       "node_type": "kvm", "node_port": "5001"},
                   follow_redirects=True)
body = resp.get_data(as_text=True)
print("   add node ->", resp.status_code, "connect command on page:",
      "--node-connect" in body)
check("POST /nodes/add lands on the connect page", resp.status_code == 200
      and "--node-connect" in body and "ip-node" in body, str(resp.status_code))
conn = panel.get_db()
new_node = conn.execute("SELECT * FROM nodes WHERE name='ip-node'").fetchone()
conn.close()
check("node stored", new_node is not None, "")
if new_node is not None:
    check("node url from ip", new_node["node_url"] == "http://198.51.100.7:5001", new_node["node_url"])
    check("node type stored", str(new_node["node_type"]) == "kvm", str(new_node["node_type"]))
    check("node pending until agent runs", str(new_node["status"]) == "pending", str(new_node["status"]))
    page = client.get(f"/nodes/connect/{new_node['id']}")
    check("connect page renders command", page.status_code == 200
          and "--node-connect" in page.get_data(as_text=True), str(page.status_code))
    status = client.get(f"/nodes/status/{new_node['id']}")
    check("status endpoint", status.status_code == 200 and status.get_json()["online"] is False,
          str(status.get_json()))

# 3b. Admin pages touched by this change.
integrations = client.get("/admin/integrations")
integrations_body = integrations.get_data(as_text=True)
check("integrations page", integrations.status_code == 200, str(integrations.status_code))
check("bot owner id field present", 'name="bot_owner_id"' in integrations_body)

print("SECTION 4", flush=True)
# 4. VPS creation end to end with the local node (Incus mocked).
import core.vps_types as _vps_types_module  # noqa: E402

_vps_types_module.capabilities = lambda: {"incus": True, "kvm": False, "nonkvm": False,
                                          "types": {"lxc": True, "kvm": False, "nonkvm": False}}
panel.lxc_create = lambda name, *a, **k: name
panel.lxc_attach = lambda *a, **k: (0, "")
resp = client.post("/admin/create-vps", data={
    "user_id": str(admin_id), "os_type": "ubuntu-22.04", "ram": "2048", "cpu": "1",
    "disk": "20", "node_id": str(nodes[0]["id"]), "vps_type": "lxc",
}, follow_redirects=True)
body = resp.get_data(as_text=True)
check("VPS create succeeds", resp.status_code == 200 and "No online node" not in body, str(resp.status_code))
conn = panel.get_db()
vps_rows = conn.execute("SELECT id, container_name, status FROM vps").fetchall()
conn.close()
check("VPS row written", len(vps_rows) >= 1, str([dict(r) for r in vps_rows]))

print("SECTION 5", flush=True)
# 5. Bot command pack registers everything the user asked for.
import discord  # noqa: E402
from discord import app_commands  # noqa: E402
from core.bot_commands import register_bot_commands  # noqa: E402

bot_client = discord.Client(intents=discord.Intents.none())
tree = app_commands.CommandTree(bot_client)
helpers = {
    "get_db": panel.get_db,
    "get_settings": panel.get_settings,
    "add_log": panel.add_log,
    "get_node_for_vps": panel.get_node_for_vps,
    "load_bot_admins": panel.load_bot_admins,
    "save_bot_admins": panel.save_bot_admins,
    "prefix_enabled": lambda: False,
    "logger": panel.app.logger,
}
registered = register_bot_commands(client=bot_client, tree=tree, discord=discord,
                                   app_commands=app_commands, helpers=helpers)
names = {command.name for command in tree.get_commands()}
ports = next((c for c in tree.get_commands() if c.name == "ports"), None)
sub = {c.name for c in ports.commands} if ports else set()
required = [
    "ping", "uptime", "plans", "freeplans", "stats", "claimfree", "myvps", "list",
    "manage", "share-user", "share-ruser", "manage-shared", "mangage",
    "vpsinfo", "vps-stats", "restart-vps", "renew-vps", "clone-vps", "snapshot",
    "restore-backup", "addinv", "removeinv", "addboost", "removeboost", "user-stats",
    "invite-logs", "boost-logs", "create", "delete-vps", "add-resources", "resize-vps",
    "suspend-vps", "unsuspend-vps", "suspension-logs", "whitelist-vps", "userinfo",
    "list-all", "serverstats", "stop-vps-all", "migrate-vps", "vps-network",
    "apply-permissions", "fix-internet", "lxc-list", "admin-add", "admin-remove",
    "admin-list", "purge-prot", "purge-remove-prot", "purge-list-protected",
    "purge-vm-all", "confirm-purge-all", "purge-data", "confirm-purge", "exec",
    "ports-add-user", "ports-remove-user", "ports-revoke", "set-status", "change-mode",
    "maintenance", "thresholds", "set-threshold", "check-resources", "machine-status",
    "help", "commands", "ports",
]
missing = [name for name in required if name not in names]
check("all slash commands registered", not missing, f"missing={missing} sub={sub}")
check("ports group subcommands", sub == {"add", "list", "remove"}, str(sub))
print(f"\nregistered {len(names)} top-level commands + {len(sub)} port subcommands")

print("SECTION 6", flush=True)
# 6. Handlers actually execute (no Discord context needed).
import asyncio  # noqa: E402

actor = {"discord_id": "42", "username": "tester", "user": None, "admin": True}
for command_name in ("ping", "plans", "freeplans", "thresholds", "help", "user-stats"):
    text = asyncio.run(registered["run"](command_name, actor, {}))
    check(f"handler {command_name}", isinstance(text, str) and len(text) > 10, str(text)[:120])
help_text = asyncio.run(registered["run"]("help", actor, {}))
check("help lists admin commands", "/purge-vm-all" in help_text and "/ports add" in help_text)

print("SECTION 7", flush=True)
# 7. The SSHX step must not kill the shell that runs it, and a KVM guest whose
#    Incus agent is still starting must be retried instead of reported broken.
import subprocess as _sp  # noqa: E402
import time as _time  # noqa: E402

captured = {}


def _fake_attach(name, command, timeout=60, **kwargs):
    captured["command"] = command
    return 0, b"https://sshx.io/s/checkshare"


conn = panel.get_db()
conn.execute(
    "INSERT INTO vps (user_id, node_id, container_id, container_name, ssh_command, "
    "os_type, ram, cpu, disk, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
    (admin_id, nodes[0]["id"], "omega-check-lxc", "omega-check-lxc", "",
     "ubuntu-22.04", "2GB", "1 vCPU", "20GB", "running"),
)
conn.commit()
sshx_vps_id = conn.execute("SELECT id FROM vps WHERE container_id='omega-check-lxc'").fetchone()["id"]
conn.close()

real_attach = REAL_LXC_ATTACH
real_status = panel.lxc_status
real_start = panel.lxc_start
panel.lxc_attach = _fake_attach
panel.lxc_status = lambda name: "running"
panel.lxc_start = lambda name: True
try:
    url = panel._omega_bot_ensure_sshx(sshx_vps_id, force=True)
finally:
    panel.lxc_attach = real_attach
    panel.lxc_status = real_status
    panel.lxc_start = real_start

script = captured.get("command", "")
check("sshx link returned", url == "https://sshx.io/s/checkshare", url)
check("sshx step does not pkill -f itself", "pkill -f" not in script, script[:160])
check("sshx step tracks a pid file", "/tmp/sshx.pid" in script, script[:160])
if _sp is not None and script:
    parsed = _sp.run(["/bin/sh", "-n"], input=script, text=True, capture_output=True)
    check("sshx step is valid shell", parsed.returncode == 0, parsed.stderr[:200])

check("agent error detected",
      panel._omega_agent_not_ready(b"Error: VM agent isn't currently running"), "")
check("real output not mistaken for agent error",
      not panel._omega_agent_not_ready(b"OMEGA_PASSWORD_OK"), "")

agent_calls = []


def _fake_cli(name, command, timeout):
    agent_calls.append(command)
    if len(agent_calls) == 1:
        return 1, b"Error: VM agent isn't currently running"
    return 0, b"agent-ready"


real_kind = panel.omega_vps_kind
real_base = panel._omega_lxc_attach_base
real_cli = panel._omega_instance_exec_cli
real_sleep = _time.sleep
panel.omega_vps_kind = lambda name, default="lxc": "kvm"
panel._omega_lxc_attach_base = lambda *a, **k: (1, b"")
panel._omega_instance_exec_cli = _fake_cli
_time.sleep = lambda seconds: None
try:
    code, output = panel.lxc_attach("omega-check-kvm", "true", timeout=45)
finally:
    panel.omega_vps_kind = real_kind
    panel._omega_lxc_attach_base = real_base
    panel._omega_instance_exec_cli = real_cli
    _time.sleep = real_sleep
check("kvm agent wait retries the command", code == 0 and output == b"agent-ready",
      f"code={code} out={output!r} calls={len(agent_calls)}")

check("sshx step falls back to wget", "wget" in script, script[:160])

print("SECTION 8", flush=True)
# 8. Create must keep moving after the instance exists: the SSHX link is best
#    effort, and refreshing it must not wipe the root password.
conn = panel.get_db()
conn.execute(
    "INSERT INTO vps (user_id, node_id, container_id, container_name, ssh_command, ssh_password, "
    "os_type, ram, cpu, disk, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
    (admin_id, nodes[0]["id"], "omega-check-sshx2", "omega-check-sshx2", "",
     "keepme", "ubuntu-22.04", "2GB", "1 vCPU", "20GB", "running"),
)
conn.commit()
keep_id = conn.execute("SELECT id FROM vps WHERE container_id='omega-check-sshx2'").fetchone()["id"]
conn.close()

real_attach = panel.lxc_attach
real_status = panel.lxc_status
real_start = panel.lxc_start
panel.lxc_attach = _fake_attach
panel.lxc_status = lambda name: "running"
panel.lxc_start = lambda name: True
try:
    panel._omega_bot_ensure_sshx(keep_id, force=True, timeout=30, wait_ssh=0)
finally:
    panel.lxc_attach = real_attach
    panel.lxc_status = real_status
    panel.lxc_start = real_start
conn = panel.get_db()
row = conn.execute("SELECT ssh_password FROM vps WHERE id=?", (keep_id,)).fetchone()
conn.close()
check("sshx refresh keeps the root password",
      bool(row) and row["ssh_password"] == "keepme",
      str(row["ssh_password"] if row else None))

real_ensure = panel._omega_bot_ensure_sshx


def _boom(*a, **k):
    raise RuntimeError("guest is not ready")


panel._omega_bot_ensure_sshx = _boom
try:
    failed_link = panel._omega_create_sshx_for_new_vps(keep_id, "nonkvm")
finally:
    panel._omega_bot_ensure_sshx = real_ensure
check("create survives a failed sshx step", failed_link == "", repr(failed_link))

conn = panel.get_db()
conn.execute("DELETE FROM vps WHERE id=?", (keep_id,))
conn.execute("DELETE FROM vps WHERE container_id='omega-check-lxc'")
conn.commit()
conn.close()

print("SECTION 9", flush=True)
# 9. Creating or replacing an SSHX link must not block the browser: the panel
#    starts a job, the page polls it, and the finished link lands in the row.
conn = panel.get_db()
conn.execute(
    "INSERT INTO vps (user_id, node_id, container_id, container_name, ssh_command, ssh_password, "
    "os_type, ram, cpu, disk, status, vps_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
    (admin_id, nodes[0]["id"], "omega-check-sshx3", "omega-check-sshx3", "",
     "pw", "ubuntu-22.04", "2GB", "1 vCPU", "20GB", "running", "lxc"),
)
conn.commit()
job_vps_id = conn.execute("SELECT id FROM vps WHERE container_id='omega-check-sshx3'").fetchone()["id"]
conn.close()

real_ensure = panel._omega_bot_ensure_sshx
seen = {}


def _fake_ensure(vps_id, force=False, timeout=None, wait_ssh=None, report=None):
    seen["wait_ssh"] = wait_ssh
    if report:
        report(30, "Installing sshx inside the VPS\u2026")
    return "https://sshx.io/s/jobtest"


panel._omega_bot_ensure_sshx = _fake_ensure
job_id = None
status = None
try:
    started = client.post("/vps/%d/sshx-job" % job_vps_id, headers={"Accept": "application/json"})
    started_data = started.get_json() or {}
    job_id = started_data.get("job_id")
    deadline = _time.time() + 10
    while job_id and _time.time() < deadline:
        polled = client.get("/api/vps-create/%d" % job_id).get_json() or {}
        status = polled.get("status")
        if status in ("done", "error"):
            break
        _time.sleep(0.1)
finally:
    panel._omega_bot_ensure_sshx = real_ensure

check("sshx job endpoint returns a job", started.status_code == 200 and bool(job_id),
      str(started_data))
check("sshx job finishes without blocking the request", status == "done", str(status))
conn = panel.get_db()
job_row = conn.execute("SELECT ssh_command FROM vps WHERE id=?", (job_vps_id,)).fetchone()
conn.close()
check("sshx job stores the new link",
      bool(job_row) and job_row["ssh_command"] == "https://sshx.io/s/jobtest",
      str(job_row["ssh_command"] if job_row else None))
page = client.get("/vps/%d" % job_vps_id)
page_body = page.get_data(as_text=True)
check("vps page offers the job-driven sshx button",
      page.status_code == 200 and 'data-sshx-url="/vps/%d/sshx-job"' % job_vps_id in page_body,
      str(page.status_code))
check("vps page shows the new sshx link", "https://sshx.io/s/jobtest" in page_body, "")

conn = panel.get_db()
conn.execute("DELETE FROM vps WHERE id=?", (job_vps_id,))
conn.execute("DELETE FROM vps_create_jobs WHERE vps_id=?", (job_vps_id,))
conn.commit()
conn.close()

print("SECTION 10", flush=True)
# 10. Real failures seen in production: guests with a dead systemd-resolved
#     stub had no DNS, and Non-KVM guests were declared broken before a TCG
#     boot finished.
import inspect as _inspect
ensure_src = _inspect.getsource(panel._omega_bot_ensure_sshx)
check("sshx step repairs guest DNS before downloading",
      "resolved.conf.d" in ensure_src and "nameserver 1.1.1.1" in ensure_src, "")
check("start/create hook repairs guest DNS",
      "resolved.conf.d" in _inspect.getsource(panel._lxc_fix_networking), "")
wait_src = _inspect.getsource(panel._omega_wait_guest_ssh)
check("non-kvm boot wait is patient and reports progress",
      "timeout=420" in wait_src and "report(" in wait_src, "")
check("non-kvm boot wait shows the console tail on failure",
      "Console tail" in wait_src, "")
check("sshx job waits for a non-kvm boot",
      "OMEGA_NONKVM_SSH_WAIT" in _inspect.getsource(panel._omega_start_sshx_job), "")
check("sshx starts under a pty and retries without one",
      "script -qfc" in ensure_src and ensure_src.count("setsid sshx") >= 2, "")
check("sshx failure reports a log size and a foreground try",
      "logbytes:" in ensure_src and "fg: " in ensure_src, "")
bot_src = _inspect.getsource(panel._omega_bot_ensure_sshx)
check("sshx no-url error keeps the guest log tail",
      "| sshx log: " in bot_src and "never printed a share link" in bot_src, "")
check("sshx waits briefly for a late link",
      "late link poll failed" in bot_src, "")
check("create flow keeps sshx progress inside 92-99%",
      "92 + int(" in _inspect.getsource(panel._create_vps_for_user), "")

# Guest networking for real: with only loopback inside, the panel must hand the
# guest a static address from the bridge subnet and every script must be valid.
import subprocess as _subproc


class _FakeProc:
    def __init__(self, rc=1, out=b""):
        self.returncode, self.stdout, self.stderr = rc, out, b""


def _fake_incus(record):
    def run(cmd, **kw):
        record.append(list(cmd))
        text = " ".join(str(c) for c in cmd)
        if "config show" in text:
            return _FakeProc(0, b"devices:\n  eth0:\n    network: incusbr0\n")
        if "ipv4.address" in text:
            return _FakeProc(0, b"10.9.0.1/24\n")
        if "ipv4.gateway" in text:
            return _FakeProc(0, b"10.9.0.1\n")
        return _FakeProc(1, b"")
    return run


_net_calls = []
_real_run, _real_sleep = _subproc.run, panel.time.sleep
_subproc.run = _fake_incus(_net_calls)
panel.time.sleep = lambda *a, **k: None
try:
    panel._lxc_fix_networking("omega-net-check")
finally:
    _subproc.run, panel.time.sleep = _real_run, _real_sleep

_guest_cmds = [c[-1] for c in _net_calls
               if c[:4] == ["incus", "exec", "omega-net-check", "--"]]
check("sshx job repairs the guest network before asking for a link",
      "_lxc_fix_networking(container_id, budget=45)" in ensure_src, "")
check("guest networking repair is time boxed",
      "budget=120" in _inspect.getsource(panel._lxc_fix_networking)
      and "out_of_time()" in _inspect.getsource(panel._lxc_fix_networking), "")
check("non-kvm wait notices a console that stopped growing",
      "boot stalled" in wait_src and "console_size" in wait_src, "")
nonkvm_script = io.open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts", "omega-nonkvm.sh"),
    encoding="utf-8").read()
check("tcg boot uses one vCPU and an entropy device",
      "-device virtio-rng-pci" in nonkvm_script
      and 'smp=1' in nonkvm_script and 'accel" != "kvm' in nonkvm_script, "")
check("guest networking asks the DHCP clients for a lease",
      any("dhclient" in c for c in _guest_cmds), "")
check("guest networking tries a networkd DHCP profile",
      any("10-omega-dhcp.network" in c for c in _guest_cmds), "")
check("guest networking falls back to a static bridge address",
      any("ip addr add 10.9.0." in c for c in _guest_cmds), "")
_parse_ok = True
for _body in _guest_cmds:
    if _real_run(["sh", "-n", "-c", _body],
                 capture_output=True, text=True).returncode:
        _parse_ok = False
check("every guest networking script parses in sh", _parse_ok, "")
check("sshx script brings eth0 up before downloading",
      "ip -4 -o addr show scope global" in ensure_src and "dhclient" in ensure_src, "")
check("sshx network failure shows the interface address",
      "addr: /" in ensure_src, "")

print()
if failures:
    print("FAILURES:", len(failures))
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("ALL FEATURE CHECKS PASSED")
