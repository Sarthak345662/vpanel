"""Vizion Panel - full Discord command pack.

Implements the user-facing VPS commands, invite/boost commands, admin commands,
main-admin/protection commands, port commands and system commands advertised in
``/help``.  Every command is available both as a slash command and through the
configured text prefix (``!`` by default).

The module is self-contained on purpose: it only talks to the panel through the
helpers handed over by ``register_bot_commands`` so it can be unit tested without
a Discord connection or a running panel.

Database objects used here are created lazily by :func:`ensure_bot_schema`, so
an old panel database keeps working after an update.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import time
from datetime import datetime, timedelta

MAX_TEXT = 1900
DEFAULT_PLANS = [
    {"id": "free", "name": "Free", "vcpus": 1, "memory_mb": 1024, "disk_gb": 10,
     "vps_type": "lxc", "days": 7, "price": "0", "free": 1},
    {"id": "starter", "name": "Starter", "vcpus": 2, "memory_mb": 2048, "disk_gb": 25,
     "vps_type": "lxc", "days": 30, "price": "2", "free": 0},
    {"id": "pro", "name": "Pro", "vcpus": 4, "memory_mb": 8192, "disk_gb": 60,
     "vps_type": "kvm", "days": 30, "price": "8", "free": 0},
]


def _clip(text, limit=MAX_TEXT):
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fmt_uptime(seconds):
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    bits = []
    if days:
        bits.append(f"{days}d")
    if hours or days:
        bits.append(f"{hours}h")
    bits.append(f"{minutes}m")
    bits.append(f"{secs}s")
    return " ".join(bits)


def _fmt_bytes(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def _bar(percent, width=12):
    try:
        percent = max(0.0, min(100.0, float(percent)))
    except (TypeError, ValueError):
        percent = 0.0
    filled = int(round(percent / 100 * width))
    return "█" * filled + "░" * (width - filled) + f" {percent:.0f}%"


def register_bot_commands(*, client, tree, discord, app_commands, helpers):
    """Register every panel command on ``client`` / ``tree``.

    ``helpers`` must provide: get_db, get_settings, add_log, log_activity,
    create_vps_for_user, get_node_for_vps, lxc_start, lxc_stop, lxc_restart,
    lxc_destroy, lxc_attach, call_node_api, panel_url, load_bot_admins,
    vps_types (module), bot_uptime.
    """
    get_db = helpers["get_db"]
    get_settings = helpers.get("get_settings")
    add_log = helpers.get("add_log") or (lambda *a, **k: None)
    log_activity = helpers.get("log_activity") or (lambda *a, **k: None)
    create_vps_for_user = helpers.get("create_vps_for_user")
    get_node_for_vps = helpers["get_node_for_vps"]
    lxc_start = helpers.get("lxc_start")
    lxc_stop = helpers.get("lxc_stop")
    lxc_restart = helpers.get("lxc_restart")
    lxc_destroy = helpers.get("lxc_destroy")
    lxc_attach = helpers.get("lxc_attach")
    call_node_api = helpers.get("call_node_api")
    panel_url = helpers.get("panel_url") or (lambda: "")
    load_bot_admins = helpers.get("load_bot_admins") or (lambda: [])
    vps_types = helpers.get("vps_types")
    bot_uptime = helpers.get("bot_uptime") or (lambda: 0)
    started_at = time.time()

    schema_ready = {"done": False}

    # ------------------------------------------------------------- utilities
    def _conn():
        return get_db()

    def _columns(conn, table):
        try:
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            return set()

    def ensure_bot_schema():
        if schema_ready["done"]:
            return
        conn = _conn()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bot_invites (
                    discord_id TEXT PRIMARY KEY, invites INTEGER NOT NULL DEFAULT 0,
                    boosts INTEGER NOT NULL DEFAULT 0, updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS bot_invite_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, discord_id TEXT NOT NULL,
                    action TEXT NOT NULL, amount INTEGER DEFAULT 0, actor TEXT,
                    note TEXT, created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS bot_plans (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, vcpus INTEGER NOT NULL,
                    memory_mb INTEGER NOT NULL, disk_gb INTEGER NOT NULL,
                    vps_type TEXT NOT NULL DEFAULT 'lxc', days INTEGER DEFAULT 30,
                    price TEXT DEFAULT '0', free INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS bot_suspensions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, vps_id INTEGER NOT NULL,
                    reason TEXT, actor TEXT, suspended_at TEXT, resumed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS bot_port_perms (
                    user_id INTEGER PRIMARY KEY, ports_max INTEGER DEFAULT 0, updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS bot_whitelist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, vps_id INTEGER NOT NULL,
                    discord_id TEXT NOT NULL, created_at TEXT,
                    UNIQUE(vps_id, discord_id)
                );
                CREATE TABLE IF NOT EXISTS bot_protected (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, vps_id INTEGER NOT NULL UNIQUE,
                    note TEXT, created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY, value TEXT
                );
                """
            )
            for column, definition in (
                ("vps_type", "TEXT DEFAULT 'lxc'"), ("nesting", "INTEGER DEFAULT 0"),
                ("server_type", "TEXT DEFAULT 'shared'"), ("suspended", "INTEGER DEFAULT 0"),
                ("expires_at", "TEXT"), ("ssh_password", "TEXT"), ("max_backups", "INTEGER DEFAULT 0"),
            ):
                if column not in _columns(conn, "vps"):
                    try:
                        conn.execute(f"ALTER TABLE vps ADD COLUMN {column} {definition}")
                    except Exception:
                        pass
            if "discord_id" not in _columns(conn, "users"):
                try:
                    conn.execute("ALTER TABLE users ADD COLUMN discord_id TEXT")
                except Exception:
                    pass
            conn.commit()
            for plan in DEFAULT_PLANS:
                conn.execute(
                    "INSERT OR IGNORE INTO bot_plans(id,name,vcpus,memory_mb,disk_gb,vps_type,days,price,free)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (plan["id"], plan["name"], plan["vcpus"], plan["memory_mb"], plan["disk_gb"],
                     plan["vps_type"], plan["days"], plan["price"], plan["free"]),
                )
            conn.commit()
            schema_ready["done"] = True
        finally:
            conn.close()

    def _setting(key, default=""):
        try:
            settings = get_settings() if get_settings else None
            if settings is None:
                return default
            value = settings[key] if key in settings.keys() else default
            return value if value not in (None, "") else default
        except Exception:
            return default

    def _owner_ids():
        ids = set()
        for key in ("bot_owner_id", "main_admin_id", "health_alert_discord_id"):
            value = str(_setting(key, "") or "").strip()
            if value.isdigit():
                ids.add(value)
        for value in str(os.getenv("MAIN_ADMIN_ID", "")).split(","):
            value = value.strip()
            if value.isdigit():
                ids.add(value)
        for value in str(os.getenv("BOT_OWNER_ID", "")).split(","):
            value = value.strip()
            if value.isdigit():
                ids.add(value)
        try:
            for value in load_bot_admins() or []:
                value = str(value).strip()
                if value.isdigit():
                    ids.add(value)
        except Exception:
            pass
        return ids

    def _linked_user(conn, discord_id):
        try:
            return conn.execute("SELECT * FROM users WHERE discord_id=?", (str(discord_id),)).fetchone()
        except Exception:
            return None

    def _actor(discord_id, username=""):
        discord_id = str(discord_id)
        conn = _conn()
        try:
            user = _linked_user(conn, discord_id)
            admin = discord_id in _owner_ids() or bool(user and int(user["is_admin"] or 0))
            return {
                "discord_id": discord_id,
                "username": username or (user["username"] if user else f"user-{discord_id[-4:]}"),
                "user": user,
                "admin": admin,
            }
        finally:
            conn.close()

    def _vps_rows(conn, user_id):
        return conn.execute(
            "SELECT * FROM vps WHERE user_id=? ORDER BY id DESC", (int(user_id),)
        ).fetchall()

    def _all_vps(conn):
        return conn.execute(
            "SELECT v.*, u.username FROM vps v LEFT JOIN users u ON u.id=v.user_id ORDER BY v.id DESC"
        ).fetchall()

    def _vps(conn, vps_id):
        return conn.execute("SELECT * FROM vps WHERE id=?", (int(vps_id),)).fetchone()

    def _type_of(row):
        try:
            return str(row["vps_type"] or "lxc")
        except Exception:
            return "lxc"

    def _suspended(row):
        try:
            return bool(int(row["suspended"] or 0))
        except Exception:
            return False

    def _name_of(row):
        try:
            return row["container_name"] or row["container_id"] or f"vps-{row['id']}"
        except Exception:
            return f"vps-{row['id']}"

    def _vps_line(row):
        state = "suspended" if _suspended(row) else str(row["status"] or "unknown")
        return (f"`#{row['id']}` **{_name_of(row)}** · {_type_of(row)} · {state} · "
                f"{row['ram']} / {row['cpu']} / {row['disk']}")

    def _require_vps(actor, vps_id, allow_admin=True):
        conn = _conn()
        try:
            row = _vps(conn, vps_id)
            if not row:
                raise ValueError(f"VPS `#{vps_id}` was not found.")
            owner = False
            try:
                owner = int(row["user_id"]) == int(actor["user"]["id"])
            except Exception:
                owner = False
            shared = conn.execute(
                "SELECT 1 FROM vps_shares WHERE vps_id=? AND shared_with=?",
                (int(vps_id), int(actor["user"]["id"]) if actor["user"] else -1),
            ).fetchone()
            if not (owner or shared or (allow_admin and actor["admin"])):
                raise PermissionError("You do not own this VPS.")
            if _suspended(row) and not actor["admin"]:
                raise PermissionError("This VPS is suspended. Contact an administrator.")
            return row
        finally:
            conn.close()

    def _linked_required(actor):
        if not actor["user"]:
            raise PermissionError(
                "Link your Discord account in the panel profile first (Profile → Bot token / Discord link)."
            )
        return actor["user"]

    def _node_for(vps_id):
        row, node = get_node_for_vps(int(vps_id))
        if not row or not node:
            raise RuntimeError("VPS node is unavailable.")
        return row, node

    def _action(vps_id, action):
        row, node = _node_for(vps_id)
        name = row["container_name"] or row["container_id"]
        if _type_of(row) == "nonkvm" and vps_types is not None:
            ok, message = vps_types.action(name, "nonkvm", action)
            if not ok:
                raise RuntimeError(message or "Action failed")
        elif node["is_local"]:
            {"start": lxc_start, "stop": lxc_stop, "restart": lxc_restart}[action](row["container_id"] or name)
        else:
            response, code = call_node_api(
                node["node_url"], node["api_key"],
                f"/api/container/{row['container_id'] or name}/{action}", method="POST",
            )
            if code not in (200, 201, 202) or (isinstance(response, dict) and response.get("error")):
                raise RuntimeError(
                    response.get("error", "Node operation failed") if isinstance(response, dict)
                    else f"Node operation failed (HTTP {code})"
                )
        state = "running" if action in ("start", "restart") else "stopped"
        conn = _conn()
        try:
            conn.execute("UPDATE vps SET status=? WHERE id=?", (state, int(vps_id)))
            conn.commit()
        finally:
            conn.close()
        return state

    def _agent_exec(vps_id, command, timeout=60):
        row, node = _node_for(vps_id)
        name = row["container_name"] or row["container_id"]
        if node["is_local"]:
            code, output = lxc_attach(row["container_id"] or name, command, timeout=timeout)
            if isinstance(output, bytes):
                output = output.decode("utf-8", "ignore")
            return code, str(output or "")
        response, code = call_node_api(
            node["node_url"], node["api_key"],
            f"/api/container/{row['container_id'] or name}/exec", method="POST",
            data={"command": command}, timeout=timeout,
        )
        if isinstance(response, dict):
            return (0 if code == 200 and not response.get("error") else 1,
                    str(response.get("output") or response.get("error") or ""))
        return 1, str(response)

    def _stats(vps_id):
        row, node = _node_for(vps_id)
        name = row["container_name"] or row["container_id"]
        if node["is_local"]:
            if vps_types is not None and _type_of(row) == "nonkvm":
                return vps_types.stats(name, "nonkvm")
            response, code = call_node_api(
                "http://127.0.0.1:5001", _node_token(), f"/api/container/{row['container_id'] or name}/stats",
                timeout=8,
            )
            if code == 200 and isinstance(response, dict) and not response.get("error"):
                return response
            return {}
        response, code = call_node_api(
            node["node_url"], node["api_key"],
            f"/api/container/{row['container_id'] or name}/stats", timeout=8,
        )
        return response if code == 200 and isinstance(response, dict) else {}

    def _node_token():
        try:
            with open("/etc/omega-panel/node-token", "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return ""

    def _set_state(key, value):
        ensure_bot_schema()
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO bot_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
            conn.commit()
        finally:
            conn.close()

    def _get_state(key, default=""):
        ensure_bot_schema()
        conn = _conn()
        try:
            row = conn.execute("SELECT value FROM bot_state WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default
        finally:
            conn.close()

    def _log_action(actor, action, amount=1, note=""):
        ensure_bot_schema()
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO bot_invite_logs(discord_id,action,amount,actor,note,created_at)"
                " VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)",
                (actor["discord_id"], action, int(amount), actor["username"], note),
            )
            conn.commit()
        finally:
            conn.close()

    def _bump_invite(actor, field, amount=1):
        ensure_bot_schema()
        amount = int(amount)
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO bot_invites(discord_id,invites,boosts,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP)"
                " ON CONFLICT(discord_id) DO UPDATE SET "
                + ("invites=invites+?, " if field == "invites" else "boosts=boosts+?, ")
                + "updated_at=CURRENT_TIMESTAMP",
                (actor["discord_id"], amount if field == "invites" else 0,
                 amount if field == "boosts" else 0, amount),
            )
            conn.commit()
        finally:
            conn.close()

    def _plan_list():
        ensure_bot_schema()
        conn = _conn()
        try:
            return conn.execute("SELECT * FROM bot_plans ORDER BY free DESC, memory_mb").fetchall()
        finally:
            conn.close()

    def _plan(plan_id):
        ensure_bot_schema()
        conn = _conn()
        try:
            return conn.execute("SELECT * FROM bot_plans WHERE id=?", (str(plan_id),)).fetchone()
        finally:
            conn.close()

    def _create_with_plan(user, plan):
        if not create_vps_for_user:
            raise RuntimeError("Provisioning helper is unavailable.")
        creds = create_vps_for_user(user, {
            "os_type": "ubuntu-22.04",
            "ram_mb": int(plan["memory_mb"]),
            "cpu": int(plan["vcpus"]),
            "disk_gb": int(plan["disk_gb"]),
            "vps_type": plan["vps_type"],
            "server_type": "shared",
        })
        if plan["days"]:
            conn = _conn()
            try:
                conn.execute(
                    "UPDATE vps SET expires_at=? WHERE id=?",
                    ((datetime.now() + timedelta(days=int(plan["days"]))).strftime("%Y-%m-%d %H:%M:%S"),
                     int(creds["vps_id"])),
                )
                conn.commit()
            finally:
                conn.close()
        return creds

    # -------------------------------------------------------------- dispatch
    async def run(name, actor, args):
        name = name.lower()
        handler = HANDLERS.get(name)
        if not handler:
            return f"Unknown command `{name}`. Try `/help`."
        try:
            result = handler(actor, args or {})
            if hasattr(result, "__await__"):
                result = await result
            return _clip(result if result is not None else "Done.")
        except PermissionError as exc:
            return f"⛔ {exc}"
        except ValueError as exc:
            return f"⚠️ {exc}"
        except Exception as exc:  # keep Discord replies readable
            try:
                app_log = helpers.get("logger")
                if app_log:
                    app_log.exception("Discord command %s failed", name)
            except Exception:
                pass
            return f"❌ `{name}` failed: `{str(exc)[:300]}`"

    # --------------------------------------------------------------- commands
    HANDLERS = {}

    def handler(*names):
        def decorator(func):
            for name in names:
                HANDLERS[name] = func
            return func
        return decorator

    @handler("ping")
    def _ping(actor, args):
        latency = 0
        try:
            latency = int(getattr(client, "latency", 0) * 1000)
        except Exception:
            latency = 0
        return (f"🏓 **Pong!**\nGateway latency: `{latency} ms`\n"
                f"Bot uptime: `{_fmt_uptime(time.time() - started_at)}`\n"
                f"Panel uptime: `{_fmt_uptime(bot_uptime())}`")

    @handler("uptime")
    def _uptime(actor, args):
        return (f"⏱️ **Uptime**\nBot: `{_fmt_uptime(time.time() - started_at)}`\n"
                f"Panel: `{_fmt_uptime(bot_uptime())}`\nBot owner: <@{next(iter(_owner_ids()), '0')}>")

    @handler("stats", "serverstats", "machine-status")
    def _stats_cmd(actor, args):
        import psutil
        cpu = psutil.cpu_percent(interval=0.2)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        conn = _conn()
        try:
            users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
            vps_rows = _all_vps(conn)
            nodes = conn.execute("SELECT COUNT(*) AS n FROM nodes").fetchone()["n"]
        finally:
            conn.close()
        running = sum(1 for v in vps_rows if str(v["status"]).lower() == "running")
        return (
            "📊 **Panel statistics**\n"
            f"CPU `{_bar(cpu)}`\n"
            f"RAM `{_bar(mem.percent)}` ({_fmt_bytes(mem.used)} / {_fmt_bytes(mem.total)})\n"
            f"Disk `{_bar(disk.percent)}` ({_fmt_bytes(disk.used)} / {_fmt_bytes(disk.total)})\n"
            f"Users: `{users}` · Nodes: `{nodes}` · VPS: `{len(vps_rows)}` (`{running}` running)"
        )

    @handler("vps-stats", "vpsinfo")
    def _vps_info(actor, args):
        vps_id = int(args.get("vps_id") or 0)
        if not vps_id:
            return "Usage: `/vps-stats <vps_id>` (see `/myvps`)."
        row = _require_vps(actor, vps_id)
        node = _node_for(vps_id)[1]
        stats = _stats(vps_id)
        expires = ""
        try:
            expires = row["expires_at"] or ""
        except Exception:
            expires = ""
        lines = [
            f"🖥️ **VPS #{row['id']} · {_name_of(row)}**",
            f"Type: `{_type_of(row)}` · Status: `{row['status']}`",
            f"Plan: `{row['cpu']}` CPU · `{row['ram']}` RAM · `{row['disk']}` disk",
            f"OS: `{row['os_type']}` · Node: `{node['name']}`",
        ]
        if expires:
            lines.append(f"Expires: `{expires}`")
        if stats:
            memory_percent = stats.get("memory_percent") or 0
            lines.append(f"CPU `{_bar(stats.get('cpu_percent') or 0)}`")
            lines.append(f"RAM `{_bar(memory_percent)}`")
            if stats.get("net_rx") is not None:
                lines.append(f"Net RX `{_fmt_bytes(stats.get('net_rx'))}` · TX `{_fmt_bytes(stats.get('net_tx'))}`")
        else:
            lines.append("Live metrics are unavailable (node agent did not answer).")
        return "\n".join(lines)

    @handler("myvps", "list")
    def _myvps(actor, args):
        user = _linked_required(actor)
        conn = _conn()
        try:
            rows = _vps_rows(conn, user["id"])
            shared = conn.execute(
                "SELECT v.* FROM vps v JOIN vps_shares s ON s.vps_id=v.id WHERE s.shared_with=?",
                (int(user["id"]),),
            ).fetchall()
        finally:
            conn.close()
        if not rows and not shared:
            return "You do not have a VPS yet. Use `/claimfree` for a free plan."
        lines = [f"🗂️ **Your VPS ({len(rows)})**"]
        lines += [_vps_line(row) for row in rows[:20]]
        if shared:
            lines.append(f"\n🤝 **Shared with you ({len(shared)})**")
            lines += [f"{_vps_line(row)} · from <@{row['user_id']}>" for row in shared[:10]]
        return "\n".join(lines)

    @handler("manage")
    def _manage(actor, args):
        vps_id = int(args.get("vps_id") or 0)
        action = str(args.get("action") or "").lower()
        if not vps_id:
            return ("Usage: `/manage <vps_id> <start|stop|restart|delete|info>`\n"
                    "Alias: `/mangage`.")
        row = _require_vps(actor, vps_id)
        if action == "info" or not action:
            return _vps_info(actor, {"vps_id": vps_id})
        if action == "delete":
            if int(row["user_id"]) != int(actor["user"]["id"]) and not actor["admin"]:
                raise PermissionError("Only the owner or an administrator can delete this VPS.")
            destroy = helpers.get("destroy_vps")
            if destroy:
                destroy(int(vps_id))
            else:
                _action(vps_id, "stop")
                conn = _conn()
                try:
                    conn.execute("DELETE FROM vps WHERE id=?", (int(vps_id),))
                    conn.commit()
                finally:
                    conn.close()
            return f"🗑️ VPS `#{vps_id}` deleted."
        if action not in ("start", "stop", "restart"):
            return "Action must be `start`, `stop`, `restart`, `delete` or `info`."
        state = _action(vps_id, action)
        return f"✅ VPS `#{vps_id}` is now **{state}**."

    @handler("restart-vps")
    def _restart_vps(actor, args):
        vps_id = int(args.get("vps_id") or 0)
        if not vps_id:
            return "Usage: `/restart-vps <vps_id>`."
        _require_vps(actor, vps_id)
        return f"✅ VPS `#{vps_id}` is now **{_action(vps_id, 'restart')}**."

    @handler("stop-vps-all")
    def _stop_all(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        conn = _conn()
        try:
            ids = [row["id"] for row in _all_vps(conn) if str(row["status"]).lower() == "running"]
        finally:
            conn.close()
        stopped, failed = 0, []
        for vps_id in ids:
            try:
                _action(int(vps_id), "stop")
                stopped += 1
            except Exception as exc:
                failed.append(f"#{vps_id}: {str(exc)[:80]}")
        text = f"⏹️ Stopped `{stopped}` VPS."
        if failed:
            text += "\nFailed: " + ", ".join(failed[:10])
        return text

    @handler("renew-vps")
    def _renew(actor, args):
        vps_id = int(args.get("vps_id") or 0)
        days = int(args.get("days") or 30)
        if not vps_id:
            return "Usage: `/renew-vps <vps_id> [days]`."
        _require_vps(actor, vps_id)
        conn = _conn()
        try:
            row = _vps(conn, vps_id)
            base = datetime.now()
            current = row["expires_at"] if "expires_at" in row.keys() else None
            if current:
                try:
                    parsed = datetime.strptime(str(current)[:19], "%Y-%m-%d %H:%M:%S")
                    if parsed > base:
                        base = parsed
                except ValueError:
                    pass
            new_expiry = (base + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE vps SET expires_at=?, status=CASE WHEN status='suspended' THEN 'created' ELSE status END, suspended=0 WHERE id=?",
                         (new_expiry, vps_id))
            conn.commit()
        finally:
            conn.close()
        add_log("info", f"VPS #{vps_id} renewed by {actor['username']} (+{days}d)", "vps")
        return f"🔄 VPS `#{vps_id}` renewed for `{days}` days — expires `{new_expiry}`."

    @handler("clone-vps")
    def _clone(actor, args):
        vps_id = int(args.get("vps_id") or 0)
        if not vps_id:
            return "Usage: `/clone-vps <vps_id>`."
        row = _require_vps(actor, vps_id)
        user = actor["user"]
        plan = {"memory_mb": _mb(row["ram"]), "vcpus": _cpu(row["cpu"]), "disk_gb": _gb(row["disk"]),
                "vps_type": _type_of(row), "days": 0, "id": "clone"}
        creds = _create_with_plan(user, plan)
        return (f"🧬 Cloned VPS `#{vps_id}` → `#{creds['vps_id']}` **{creds['name']}**.\n"
                "Credentials are delivered by DM.")

    def _mb(value):
        raw = str(value or "").lower().replace(" ", "")
        try:
            if "gb" in raw:
                return int(float(raw.replace("gb", "")) * 1024)
            return int(float(raw.replace("mb", "")))
        except ValueError:
            return 2048

    def _cpu(value):
        digits = "".join(ch for ch in str(value or "1") if ch.isdigit())
        return int(digits or 1)

    def _gb(value):
        digits = str(value or "20").lower().replace("gb", "").strip()
        try:
            return int(float(digits))
        except ValueError:
            return 20

    @handler("snapshot")
    def _snapshot(actor, args):
        vps_id = int(args.get("vps_id") or 0)
        label = str(args.get("name") or f"snap-{int(time.time())}")
        if not vps_id:
            return "Usage: `/snapshot <vps_id> [name]`."
        row = _require_vps(actor, vps_id)
        name = row["container_name"] or row["container_id"]
        node = _node_for(vps_id)[1]
        if node["is_local"]:
            code, output = subprocess.getstatusoutput(f"incus snapshot create {name} {label} --reuse")
            if code != 0:
                raise RuntimeError(output[-300:])
        else:
            response, code = call_node_api(
                node["node_url"], node["api_key"], f"/api/container/{name}/backup",
                method="POST", data={"name": label}, timeout=600,
            )
            if code not in (200, 201, 202):
                raise RuntimeError(str(response)[:300])
        return f"📦 Snapshot `{label}` created for VPS `#{vps_id}`."

    @handler("restore-backup")
    def _restore(actor, args):
        vps_id = int(args.get("vps_id") or 0)
        label = str(args.get("name") or "").strip()
        if not vps_id or not label:
            return "Usage: `/restore-backup <vps_id> <snapshot-name>`."
        row = _require_vps(actor, vps_id)
        name = row["container_name"] or row["container_id"]
        node = _node_for(vps_id)[1]
        if node["is_local"]:
            code, output = subprocess.getstatusoutput(f"incus snapshot restore {name} {label}")
            if code != 0:
                raise RuntimeError(output[-300:])
        else:
            response, code = call_node_api(
                node["node_url"], node["api_key"], f"/api/container/{name}/restore",
                method="POST", data={"name": label}, timeout=900,
            )
            if code not in (200, 201, 202):
                raise RuntimeError(str(response)[:300])
        return f"♻️ Snapshot `{label}` restored on VPS `#{vps_id}`."

    @handler("plans")
    def _plans(actor, args):
        lines = ["💠 **Hosting plans**"]
        for plan in _plan_list():
            free = " · FREE" if int(plan["free"] or 0) else f" · ${plan['price']}/mo"
            lines.append(
                f"`{plan['id']}` **{plan['name']}** — {plan['vcpus']} vCPU · "
                f"{plan['memory_mb']} MB RAM · {plan['disk_gb']} GB · "
                f"{plan['vps_type'].upper()} · {plan['days']} days{free}"
            )
        lines.append("\nClaim the free plan with `/claimfree`.")
        return "\n".join(lines)

    @handler("freeplans")
    def _freeplans(actor, args):
        plans = [plan for plan in _plan_list() if int(plan["free"] or 0)]
        if not plans:
            return "No free plan is configured right now."
        lines = ["🎁 **Free plans**"]
        for plan in plans:
            lines.append(
                f"`{plan['id']}` — {plan['vcpus']} vCPU · {plan['memory_mb']} MB · "
                f"{plan['disk_gb']} GB · {plan['days']} days"
            )
        lines.append("Use `/claimfree` to create one.")
        return "\n".join(lines)

    @handler("claimfree")
    def _claimfree(actor, args):
        user = _linked_required(actor)
        plans = [plan for plan in _plan_list() if int(plan["free"] or 0)]
        if not plans:
            return "No free plan is available."
        conn = _conn()
        try:
            claimed = conn.execute(
                "SELECT value FROM bot_state WHERE key=?", (f"free_claim:{user['id']}",)
            ).fetchone()
            if claimed:
                return "You already claimed your free VPS. Contact an administrator for more resources."
            owned = conn.execute(
                "SELECT COUNT(*) AS n FROM vps WHERE user_id=?", (int(user["id"]),)
            ).fetchone()["n"]
            limit = int(user["max_vps"] or 1) if "max_vps" in user.keys() else 1
            if owned >= max(1, limit):
                raise ValueError(f"You reached your VPS limit ({owned}/{limit}).")
        finally:
            conn.close()
        creds = _create_with_plan(user, plans[0])
        _set_state(f"free_claim:{user['id']}", creds["vps_id"])
        add_log("info", f"Free VPS claimed by {user['username']}", "vps")
        return (f"🎉 Free VPS created: `#{creds['vps_id']}` **{creds['name']}** "
                f"({plans[0]['vcpus']} vCPU · {plans[0]['memory_mb']} MB · {plans[0]['disk_gb']} GB).\n"
                "Credentials are delivered by DM.")

    @handler("share-user", "share-ruser")
    def _share_user(actor, args):
        vps_id = int(args.get("vps_id") or 0)
        target = str(args.get("discord_id") or "").strip("<@!>")
        if not vps_id or not target.isdigit():
            return "Usage: `/share-user <vps_id> <discord_id>`."
        row = _require_vps(actor, vps_id)
        conn = _conn()
        try:
            target_user = _linked_user(conn, target)
            if not target_user:
                raise ValueError("That Discord account is not linked to a panel user.")
            if int(target_user["id"]) == int(row["user_id"]):
                raise ValueError("This user already owns the VPS.")
            conn.execute(
                "INSERT INTO vps_shares(vps_id,shared_with,shared_by) VALUES(?,?,?)",
                (int(vps_id), int(target_user["id"]), int(row["user_id"])),
            )
            conn.commit()
        finally:
            conn.close()
        return f"🤝 VPS `#{vps_id}` shared with <@{target}>."

    @handler("manage-shared")
    def _manage_shared(actor, args):
        user = _linked_required(actor)
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT v.*, u.username AS owner FROM vps v JOIN vps_shares s ON s.vps_id=v.id "
                "JOIN users u ON u.id=v.user_id WHERE s.shared_with=? ORDER BY v.id DESC",
                (int(user["id"]),),
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return "No VPS has been shared with you."
        lines = ["🤝 **VPS shared with you**"]
        lines += [f"{_vps_line(row)} · owner `{row['owner']}`" for row in rows[:20]]
        return "\n".join(lines)

    # ---------------------------------------------------------- invite/boost
    @handler("addinv", "removeinv")
    def _invites(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        target = str(args.get("discord_id") or "").strip("<@!>")
        amount = int(args.get("amount") or 1)
        if not target.isdigit():
            return "Usage: `/addinv <discord_id> [amount]`."
        sign = 1 if args.get("_command") == "addinv" else -1
        _bump_invite({"discord_id": target, "username": actor["username"]}, "invites", sign * amount)
        _log_action(actor, "invite", sign * amount, f"target={target}")
        return f"{'➕' if sign > 0 else '➖'} Invites for <@{target}>: `{sign * amount:+d}`."

    @handler("addboost", "removeboost")
    def _boosts(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        target = str(args.get("discord_id") or "").strip("<@!>")
        amount = int(args.get("amount") or 1)
        if not target.isdigit():
            return "Usage: `/addboost <discord_id> [amount]`."
        sign = 1 if args.get("_command") == "addboost" else -1
        _bump_invite({"discord_id": target, "username": actor["username"]}, "boosts", sign * amount)
        _log_action(actor, "boost", sign * amount, f"target={target}")
        return f"{'➕' if sign > 0 else '➖'} Boosts for <@{target}>: `{sign * amount:+d}`."

    @handler("user-stats")
    def _user_stats(actor, args):
        target = str(args.get("discord_id") or actor["discord_id"]).strip("<@!>")
        ensure_bot_schema()
        conn = _conn()
        try:
            row = conn.execute("SELECT * FROM bot_invites WHERE discord_id=?", (target,)).fetchone()
            user = _linked_user(conn, target)
            vps_count = conn.execute(
                "SELECT COUNT(*) AS n FROM vps WHERE user_id=?", (int(user["id"]),)
            ).fetchone()["n"] if user else 0
        finally:
            conn.close()
        invites = row["invites"] if row else 0
        boosts = row["boosts"] if row else 0
        name = user["username"] if user else "not linked"
        return (f"📈 **Stats for <@{target}>**\nPanel user: `{name}`\n"
                f"Invites: `{invites}` · Boosts: `{boosts}` · VPS: `{vps_count}`")

    @handler("invite-logs", "boost-logs")
    def _invite_logs(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        ensure_bot_schema()
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT * FROM bot_invite_logs ORDER BY id DESC LIMIT 15"
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return "No invite/boost activity logged yet."
        return "📜 **Recent invite / boost activity**\n" + "\n".join(
            f"`{row['created_at']}` <@{row['discord_id']}> {row['action']} {row['amount']:+d} by `{row['actor']}`"
            for row in rows
        )

    # ------------------------------------------------------------ admin cmds
    def _pick_user(conn, raw):
        raw = str(raw or "").strip()
        if not raw:
            raise ValueError("A user id or username is required.")
        row = None
        if raw.isdigit():
            row = conn.execute("SELECT * FROM users WHERE id=?", (int(raw),)).fetchone()
        if row is None:
            row = conn.execute("SELECT * FROM users WHERE username=?", (raw,)).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM users WHERE discord_id=?", (raw.strip("<@!>"),)
            ).fetchone()
        if row is None:
            raise ValueError(f"User `{raw}` was not found.")
        return row

    @handler("create")
    def _create(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        conn = _conn()
        try:
            user = _pick_user(conn, args.get("user") or actor["discord_id"])
        finally:
            conn.close()
        plan = _plan(args.get("plan") or "free")
        if plan is None:
            plan = {"vcpus": int(args.get("cpu") or 1), "memory_mb": int(args.get("ram") or 1024),
                    "disk_gb": int(args.get("disk") or 10), "vps_type": args.get("type") or "lxc",
                    "days": 30, "id": "custom"}
        creds = _create_with_plan(user, plan)
        add_log("info", f"Admin {actor['username']} created VPS #{creds['vps_id']}", "vps")
        return (f"✅ VPS `#{creds['vps_id']}` **{creds['name']}** created for `{user['username']}`.\n"
                "Credentials are delivered by DM.")

    @handler("delete-vps")
    def _delete_vps(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        vps_id = int(args.get("vps_id") or 0)
        if not vps_id:
            return "Usage: `/delete-vps <vps_id>`."
        destroy = helpers.get("destroy_vps")
        if destroy:
            destroy(vps_id)
        else:
            _action(vps_id, "stop")
            conn = _conn()
            try:
                conn.execute("DELETE FROM vps WHERE id=?", (vps_id,))
                conn.commit()
            finally:
                conn.close()
        return f"🗑️ VPS `#{vps_id}` deleted."

    @handler("add-resources", "resize-vps")
    def _resize(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        vps_id = int(args.get("vps_id") or 0)
        if not vps_id:
            return "Usage: `/resize-vps <vps_id> [cpu] [ram_mb] [disk_gb]`."
        row = _require_vps(actor, vps_id)
        cpu = int(args.get("cpu") or _cpu(row["cpu"]))
        ram = int(args.get("ram") or _mb(row["ram"]))
        disk = int(args.get("disk") or _gb(row["disk"]))
        conn = _conn()
        try:
            conn.execute(
                "UPDATE vps SET cpu=?, ram=?, disk=? WHERE id=?",
                (f"{cpu} vCPU", f"{ram}MB", f"{disk}GB", vps_id),
            )
            conn.commit()
        finally:
            conn.close()
        name = row["container_name"] or row["container_id"]
        node = _node_for(vps_id)[1]
        applied = False
        try:
            if node["is_local"]:
                for cmd in (
                    f"incus config set {name} limits.cpu {cpu}",
                    f"incus config set {name} limits.memory {ram}MiB",
                    f"incus config device override {name} root size={disk}GiB",
                ):
                    code, output = subprocess.getstatusoutput(cmd)
                    applied = applied or code == 0
            else:
                response, code = call_node_api(
                    node["node_url"], node["api_key"], f"/api/container/{name}/update",
                    method="POST", data={"cpu": f"{cpu} vCPU", "ram": f"{ram}MB", "disk": f"{disk}GB"},
                )
                applied = code == 200
        except Exception as exc:
            return (f"Record updated to `{cpu} vCPU / {ram} MB / {disk} GB`, "
                    f"but the node rejected the resize: `{str(exc)[:200]}`")
        return (f"📈 VPS `#{vps_id}` resized to `{cpu} vCPU / {ram} MB / {disk} GB`"
                f" ({'applied on node' if applied else 'panel record only'}).")

    @handler("suspend-vps", "unsuspend-vps")
    def _suspend(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        vps_id = int(args.get("vps_id") or 0)
        if not vps_id:
            return "Usage: `/suspend-vps <vps_id> [reason]`."
        suspend = args.get("_command") == "suspend-vps"
        row = _require_vps(actor, vps_id)
        conn = _conn()
        try:
            ensure_bot_schema()
            conn.execute("UPDATE vps SET suspended=? WHERE id=?", (1 if suspend else 0, vps_id))
            if suspend:
                conn.execute(
                    "INSERT INTO bot_suspensions(vps_id,reason,actor,suspended_at) VALUES(?,?,?,CURRENT_TIMESTAMP)",
                    (vps_id, str(args.get("reason") or "")[:300], actor["username"]),
                )
            else:
                conn.execute(
                    "UPDATE bot_suspensions SET resumed_at=CURRENT_TIMESTAMP WHERE vps_id=? AND resumed_at IS NULL",
                    (vps_id,),
                )
            conn.commit()
        finally:
            conn.close()
        try:
            _action(vps_id, "stop" if suspend else "start")
        except Exception:
            pass
        return f"{'⏸️ Suspended' if suspend else '▶️ Unsuspended'} VPS `#{vps_id}` ({_name_of(row)})."

    @handler("suspension-logs")
    def _suspension_logs(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        ensure_bot_schema()
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT * FROM bot_suspensions ORDER BY id DESC LIMIT 15"
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return "No suspensions recorded."
        return "⏸️ **Suspension log**\n" + "\n".join(
            f"`{row['suspended_at']}` `#{row['vps_id']}` by `{row['actor']}` — {row['reason'] or 'no reason'}"
            + (f" · resumed `{row['resumed_at']}`" if row["resumed_at"] else "")
            for row in rows
        )

    @handler("whitelist-vps")
    def _whitelist(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        vps_id = int(args.get("vps_id") or 0)
        target = str(args.get("discord_id") or "").strip("<@!>")
        if not vps_id or not target.isdigit():
            return "Usage: `/whitelist-vps <vps_id> <discord_id>`."
        ensure_bot_schema()
        conn = _conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO bot_whitelist(vps_id,discord_id,created_at) VALUES(?,?,CURRENT_TIMESTAMP)",
                (vps_id, target),
            )
            conn.commit()
        finally:
            conn.close()
        return f"✅ <@{target}> whitelisted for VPS `#{vps_id}`."

    @handler("userinfo")
    def _userinfo(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        conn = _conn()
        try:
            user = _pick_user(conn, args.get("user"))
            vps_count = conn.execute(
                "SELECT COUNT(*) AS n FROM vps WHERE user_id=?", (int(user["id"]),)
            ).fetchone()["n"]
        finally:
            conn.close()
        return (f"👤 **{user['username']}** (`#{user['id']}`)\n"
                f"Email: `{user['email']}`\nRole: `{'admin' if int(user['is_admin'] or 0) else 'user'}`\n"
                f"Discord: <@{user['discord_id'] or '0'}>\nVPS: `{vps_count}`")

    @handler("list-all")
    def _list_all(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        conn = _conn()
        try:
            rows = _all_vps(conn)
        finally:
            conn.close()
        if not rows:
            return "No VPS in the panel."
        lines = [f"🌐 **All VPS ({len(rows)})**"]
        lines += [f"{_vps_line(row)} · `{row['username']}`" for row in rows[:35]]
        if len(rows) > 35:
            lines.append(f"… and {len(rows) - 35} more.")
        return "\n".join(lines)

    @handler("migrate-vps")
    def _migrate(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        vps_id = int(args.get("vps_id") or 0)
        target = str(args.get("node") or "").strip()
        if not vps_id or not target:
            return "Usage: `/migrate-vps <vps_id> <node_id>`."
        conn = _conn()
        try:
            node = conn.execute(
                "SELECT * FROM nodes WHERE id=? OR name=?", (target if target.isdigit() else -1, target)
            ).fetchone()
            if not node:
                raise ValueError(f"Node `{target}` not found.")
            conn.execute("UPDATE vps SET node_id=? WHERE id=?", (int(node["id"]), vps_id))
            conn.commit()
        finally:
            conn.close()
        return f"🚚 VPS `#{vps_id}` reassigned to node `{node['name']}`."

    @handler("vps-network")
    def _vps_network(actor, args):
        vps_id = int(args.get("vps_id") or 0)
        if not vps_id:
            return "Usage: `/vps-network <vps_id>`."
        _require_vps(actor, vps_id)
        code, output = _agent_exec(vps_id, "ip -br addr 2>/dev/null || ifconfig 2>/dev/null", timeout=25)
        return f"🌐 **Network for VPS `#{vps_id}`**\n```\n{output.strip()[:1200] or 'no output'}\n```"

    @handler("fix-internet")
    def _fix_internet(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        vps_id = int(args.get("vps_id") or 0)
        if not vps_id:
            return "Usage: `/fix-internet <vps_id>`."
        _require_vps(actor, vps_id)
        script = ("printf 'nameserver 1.1.1.1\\nnameserver 8.8.8.8\\n' > /etc/resolv.conf; "
                  "ip link set eth0 up 2>/dev/null; (dhclient eth0 || udhcpc -i eth0) >/dev/null 2>&1; "
                  "ping -c1 -W2 1.1.1.1 >/dev/null 2>&1 && echo NETWORK_OK || echo NETWORK_FAIL")
        _, output = _agent_exec(vps_id, script, timeout=90)
        return f"🔧 Network repair for VPS `#{vps_id}`:\n```\n{output.strip()[-800:] or 'no output'}\n```"

    @handler("apply-permissions")
    def _apply_permissions(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        vps_id = int(args.get("vps_id") or 0)
        if not vps_id:
            return "Usage: `/apply-permissions <vps_id>`."
        _require_vps(actor, vps_id)
        _, output = _agent_exec(vps_id, "chown -R root:root /root 2>/dev/null; chmod 755 /root; echo PERMISSIONS_OK", timeout=30)
        return f"🔐 Permissions applied on VPS `#{vps_id}`: `{output.strip()[-200:] or 'ok'}`"

    @handler("lxc-list")
    def _lxc_list(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        local = None
        try:
            code, output = 0, subprocess.getoutput("incus list --format csv -c n,s,t 2>/dev/null")
        except Exception:
            code, output = 1, ""
        conn = _conn()
        try:
            nodes = conn.execute("SELECT * FROM nodes ORDER BY id").fetchall()
        finally:
            conn.close()
        lines = [f"🧱 **Incus / LXC inventory on this host**", "```", (output or "incus is not installed").strip()[:1200], "```"]
        for node in nodes:
            if node["is_local"] or not node["node_url"]:
                continue
            response, code = call_node_api(node["node_url"], node["api_key"], "/api/containers", timeout=8)
            items = response.get("containers") if isinstance(response, dict) else None
            lines.append(f"**{node['name']}**: `{len(items) if items else (code if code != 200 else 0)}` instance(s)")
        return "\n".join(lines)

    # ------------------------------------------------------ main admin cmds
    @handler("admin-add", "admin-remove")
    def _admin_toggle(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        target = str(args.get("discord_id") or "").strip("<@!>")
        if not target.isdigit():
            return "Usage: `/admin-add <discord_id>`."
        try:
            current = [str(x) for x in (load_bot_admins() or [])]
        except Exception:
            current = []
        add = args.get("_command") == "admin-add"
        if add and target not in current:
            current.append(target)
        if not add:
            current = [x for x in current if x != target]
        save = helpers.get("save_bot_admins")
        if save:
            save(current)
        else:
            raise RuntimeError("The bot admin list is read-only in this build.")
        return f"{'✅ Added' if add else '➖ Removed'} <@{target}> {'to' if add else 'from'} the bot admin list."

    @handler("admin-list")
    def _admin_list(actor, args):
        ids = _owner_ids()
        if not ids:
            return "No bot administrator configured."
        return "🛡️ **Bot administrators**\n" + "\n".join(f"<@{value}> (`{value}`)" for value in sorted(ids))

    @handler("purge-prot", "purge-remove-prot")
    def _purge_prot(actor, args):
        if not str(_get_state("mode", "normal")) == "normal" and not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        ensure_bot_schema()
        vps_id = int(args.get("vps_id") or 0)
        if not vps_id:
            return "Usage: `/purge-prot <vps_id>`."
        conn = _conn()
        try:
            if args.get("_command") == "purge-prot":
                conn.execute(
                    "INSERT OR IGNORE INTO bot_protected(vps_id,note,created_at) VALUES(?,?,CURRENT_TIMESTAMP)",
                    (vps_id, str(args.get("note") or "")[:200]),
                )
                message = f"🛡️ VPS `#{vps_id}` is now purge-protected."
            else:
                conn.execute("DELETE FROM bot_protected WHERE vps_id=?", (vps_id,))
                message = f"🔓 VPS `#{vps_id}` is no longer purge-protected."
            conn.commit()
        finally:
            conn.close()
        return message

    @handler("purge-list-protected")
    def _purge_list(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        ensure_bot_schema()
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT p.vps_id, p.note, p.created_at, v.container_name FROM bot_protected p "
                "LEFT JOIN vps v ON v.id=p.vps_id ORDER BY p.id DESC"
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return "No protected VPS."
        return "🛡️ **Protected VPS**\n" + "\n".join(
            f"`#{row['vps_id']}` {row['container_name'] or 'deleted'} — {row['note'] or 'no note'}"
            for row in rows
        )

    @handler("purge-vm-all")
    def _purge_all(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        ensure_bot_schema()
        conn = _conn()
        try:
            protected = {row["vps_id"] for row in conn.execute("SELECT vps_id FROM bot_protected").fetchall()}
            targets = [row["id"] for row in _all_vps(conn) if int(row["id"]) not in protected]
        finally:
            conn.close()
        _set_state("pending_purge", json.dumps(targets))
        return (f"⚠️ **Purge prepared:** `{len(targets)}` VPS will be destroyed "
                f"({len(protected)} protected skipped).\n"
                "Run `/confirm-purge-all` within 5 minutes to proceed.")

    @handler("confirm-purge-all")
    def _confirm_purge(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        raw = _get_state("pending_purge", "")
        if not raw:
            return "Nothing to purge. Run `/purge-vm-all` first."
        try:
            targets = json.loads(raw)
        except Exception:
            targets = []
        destroy = helpers.get("destroy_vps")
        done, failed = 0, 0
        for vps_id in targets:
            try:
                if destroy:
                    destroy(int(vps_id))
                else:
                    conn = _conn()
                    try:
                        conn.execute("DELETE FROM vps WHERE id=?", (int(vps_id),))
                        conn.commit()
                    finally:
                        conn.close()
                done += 1
            except Exception:
                failed += 1
        _set_state("pending_purge", "")
        add_log("warning", f"Purge-all executed by {actor['username']}: {done} deleted", "vps")
        return f"🧹 Purged `{done}` VPS. Failures: `{failed}`."

    @handler("purge-data")
    def _purge_data(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        scope = str(args.get("scope") or "invites").lower()
        ensure_bot_schema()
        tables = {
            "invites": ["bot_invites", "bot_invite_logs"],
            "logs": ["bot_invite_logs", "activity_log", "system_logs"],
            "suspensions": ["bot_suspensions"],
            "whitelist": ["bot_whitelist"],
            "protection": ["bot_protected"],
        }
        if scope not in tables:
            return "Scope must be one of: " + ", ".join(tables)
        _set_state("pending_data_purge", scope)
        return f"⚠️ Will clear `{scope}` data. Confirm with `/confirm-purge`."

    @handler("confirm-purge")
    def _confirm_data(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        scope = _get_state("pending_data_purge", "")
        if not scope:
            return "Nothing to purge. Run `/purge-data <scope>` first."
        tables = {
            "invites": ["bot_invites", "bot_invite_logs"],
            "logs": ["bot_invite_logs"],
            "suspensions": ["bot_suspensions"],
            "whitelist": ["bot_whitelist"],
            "protection": ["bot_protected"],
        }.get(scope, [])
        conn = _conn()
        try:
            for table in tables:
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    pass
            conn.commit()
        finally:
            conn.close()
        _set_state("pending_data_purge", "")
        return f"🧹 Cleared `{scope}` data."

    @handler("exec")
    def _exec(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        vps_id = int(args.get("vps_id") or 0)
        command = str(args.get("command") or "").strip()
        if not command:
            return "Usage: `/exec <vps_id> <command>`."
        if len(command) > 500:
            raise ValueError("Command is too long (max 500 characters).")
        _require_vps(actor, vps_id)
        code, output = _agent_exec(vps_id, command, timeout=90)
        add_log("warning", f"exec on VPS #{vps_id} by {actor['username']}: {command[:120]}", "vps")
        return (f"💻 **exec on VPS `#{vps_id}`** (exit `{code}`)\n"
                f"```\n{(output or 'no output').strip()[:1500]}\n```")

    # ------------------------------------------------------------ port cmds
    @handler("ports", "ports-list")
    def _ports(actor, args):
        conn = _conn()
        try:
            if actor["admin"]:
                rows = conn.execute(
                    "SELECT p.*, u.username FROM port_forwards p LEFT JOIN users u ON u.id=p.user_id ORDER BY p.id DESC LIMIT 30"
                ).fetchall()
            else:
                user = _linked_required(actor)
                rows = conn.execute(
                    "SELECT p.*, ? AS username FROM port_forwards p WHERE user_id=? ORDER BY p.id DESC LIMIT 30",
                    (user["username"], int(user["id"])),
                ).fetchall()
        finally:
            conn.close()
        if not rows:
            return "No port forwards found."
        return "🔌 **Port forwards**\n" + "\n".join(
            f"`#{row['id']}` `{row['remote_port']}` → `{row['local_port']}` · "
            f"VPS `#{row['vps_id']}` · `{row['status']}` · `{row['username']}`"
            for row in rows
        )

    @handler("ports add", "ports-add")
    def _ports_add(actor, args):
        user = _linked_required(actor)
        vps_id = int(args.get("vps_id") or 0)
        local_port = int(args.get("local_port") or 0)
        remote_port = int(args.get("remote_port") or 0)
        if not (vps_id and local_port and remote_port):
            return "Usage: `/ports add <vps_id> <local_port> <remote_port>`."
        _require_vps(actor, vps_id)
        conn = _conn()
        try:
            max_ports = int(_setting("max_ports_per_user", 20) or 20)
            if actor["admin"]:
                perm = conn.execute(
                    "SELECT ports_max FROM bot_port_perms WHERE user_id=?", (int(user["id"]),)
                ).fetchone()
                if perm:
                    max_ports = int(perm["ports_max"] or max_ports)
            used = conn.execute(
                "SELECT COUNT(*) AS n FROM port_forwards WHERE user_id=?", (int(user["id"]),)
            ).fetchone()["n"]
            if used >= max_ports:
                raise ValueError(f"Port limit reached ({used}/{max_ports}).")
            conn.execute(
                "INSERT INTO port_forwards(user_id,vps_id,local_port,remote_port,status) VALUES(?,?,?,?,'active')",
                (int(user["id"]), vps_id, local_port, remote_port),
            )
            conn.commit()
        finally:
            conn.close()
        return f"🔌 Port forward requested: `{remote_port}` → `{local_port}` on VPS `#{vps_id}`."

    @handler("ports remove", "ports-remove")
    def _ports_remove(actor, args):
        port_id = int(args.get("port_id") or 0)
        if not port_id:
            return "Usage: `/ports remove <port_id>`."
        conn = _conn()
        try:
            row = conn.execute("SELECT * FROM port_forwards WHERE id=?", (port_id,)).fetchone()
            if not row:
                raise ValueError("Port forward not found.")
            if not actor["admin"] and (not actor["user"] or int(row["user_id"]) != int(actor["user"]["id"])):
                raise PermissionError("You cannot remove this port forward.")
            conn.execute("DELETE FROM port_forwards WHERE id=?", (port_id,))
            conn.commit()
        finally:
            conn.close()
        return f"🔌 Port forward `#{port_id}` removed."

    @handler("ports-add-user", "ports-remove-user", "ports-revoke")
    def _ports_user(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        command = args.get("_command")
        target = str(args.get("user") or "").strip()
        amount = int(args.get("amount") or 0)
        ensure_bot_schema()
        conn = _conn()
        try:
            user = _pick_user(conn, target)
            if command == "ports-add-user":
                conn.execute(
                    "INSERT INTO bot_port_perms(user_id,ports_max,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)"
                    " ON CONFLICT(user_id) DO UPDATE SET ports_max=ports_max+excluded.ports_max, updated_at=CURRENT_TIMESTAMP",
                    (int(user["id"]), max(1, amount)),
                )
            elif command == "ports-remove-user":
                conn.execute(
                    "INSERT INTO bot_port_perms(user_id,ports_max,updated_at) VALUES(?,0,CURRENT_TIMESTAMP)"
                    " ON CONFLICT(user_id) DO UPDATE SET ports_max=MAX(0, ports_max-?), updated_at=CURRENT_TIMESTAMP",
                    (int(user["id"]), max(1, amount)),
                )
            else:
                conn.execute("DELETE FROM port_forwards WHERE user_id=?", (int(user["id"]),))
                conn.execute("DELETE FROM bot_port_perms WHERE user_id=?", (int(user["id"]),))
            conn.commit()
        finally:
            conn.close()
        return f"🔌 Port permission updated for `{user['username']}`."

    # ----------------------------------------------------------- system cmds
    @handler("set-status")
    async def _set_status(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        text = str(args.get("text") or "").strip()
        if not text:
            return "Usage: `/set-status <text>`."
        _set_state("status_text", text[:120])
        try:
            await client.change_presence(activity=discord.Game(name=text[:120]))
            return f"✅ Bot status set to `{text[:120]}`."
        except Exception as exc:
            return f"Status stored, but Discord rejected it: `{exc}`. It applies on the next restart."

    @handler("change-mode")
    def _change_mode(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        mode = str(args.get("mode") or "").lower()
        if mode not in ("normal", "maintenance", "readonly"):
            return "Usage: `/change-mode <normal|maintenance|readonly>`."
        _set_state("mode", mode)
        add_log("warning", f"Panel bot mode changed to {mode} by {actor['username']}", "bot")
        return f"⚙️ Bot mode is now **{mode}**."

    @handler("maintenance")
    def _maintenance(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        current = _get_state("mode", "normal")
        target = "normal" if current == "maintenance" else "maintenance"
        _set_state("mode", target)
        return f"🛠️ Maintenance mode **{'ON' if target == 'maintenance' else 'OFF'}**."

    @handler("thresholds")
    def _thresholds(actor, args):
        cpu = _get_state("threshold_cpu", _setting("cpu_threshold", 90))
        ram = _get_state("threshold_ram", _setting("ram_threshold", 90))
        disk = _get_state("threshold_disk", 90)
        return (f"📐 **Alert thresholds**\nCPU: `{cpu}%` · RAM: `{ram}%` · Disk: `{disk}%`\n"
                "Change with `/set-threshold <cpu|ram|disk> <percent>`.")

    @handler("set-threshold")
    def _set_threshold(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        metric = str(args.get("metric") or "").lower()
        value = int(args.get("value") or 0)
        if metric not in ("cpu", "ram", "disk") or not 1 <= value <= 100:
            return "Usage: `/set-threshold <cpu|ram|disk> <1-100>`."
        _set_state(f"threshold_{metric}", value)
        return f"📐 `{metric.upper()}` alert threshold set to `{value}%`."

    @handler("check-resources")
    def _check_resources(actor, args):
        if not actor["admin"]:
            raise PermissionError("Bot administrator only.")
        conn = _conn()
        try:
            nodes = conn.execute("SELECT * FROM nodes ORDER BY id").fetchall()
        finally:
            conn.close()
        cpu_limit = int(_get_state("threshold_cpu", _setting("cpu_threshold", 90)) or 90)
        ram_limit = int(_get_state("threshold_ram", _setting("ram_threshold", 90)) or 90)
        lines = ["🩺 **Resource check**"]
        alerts = 0
        for node in nodes:
            cpu = float(node["cpu_usage"] or 0) if "cpu_usage" in node.keys() else 0
            ram = float(node["ram_usage"] or 0) if "ram_usage" in node.keys() else 0
            flag = ""
            if cpu >= cpu_limit or ram >= ram_limit:
                flag = " ⚠️"
                alerts += 1
            lines.append(f"`{node['name']}` CPU `{cpu:.0f}%` · RAM `{ram:.0f}%` · `{node['status']}`{flag}")
        lines.append(f"\nAlerts: `{alerts}` · thresholds CPU `{cpu_limit}%` / RAM `{ram_limit}%`")
        return "\n".join(lines)

    @handler("help", "commands")
    def _help(actor, args):
        admin_extra = (
            "\n**Admin** `/create` `/delete-vps` `/add-resources` `/resize-vps` `/suspend-vps` "
            "`/unsuspend-vps` `/suspension-logs` `/whitelist-vps` `/userinfo` `/list-all` "
            "`/serverstats` `/stop-vps-all` `/migrate-vps` `/vps-network` `/apply-permissions` "
            "`/fix-internet` `/lxc-list`\n"
            "\n**Main admin** `/admin-add` `/admin-remove` `/admin-list` `/purge-prot` "
            "`/purge-remove-prot` `/purge-list-protected` `/purge-vm-all` `/confirm-purge-all` "
            "`/purge-data` `/confirm-purge` `/exec`\n"
        )
        return (
            "**Vizion Panel commands**\n\n"
            "**General** `/ping` `/uptime` `/plans` `/freeplans` `/stats` `/claimfree` "
            "`/myvps` `/list` `/manage` `/mangage` `/share-user` `/share-ruser` `/manage-shared`\n"
            "\n**VPS** `/vpsinfo` `/vps-stats` `/restart-vps` `/renew-vps` `/clone-vps` "
            "`/snapshot` `/restore-backup`\n"
            "\n**Invites** `/addinv` `/removeinv` `/addboost` `/removeboost` `/user-stats` "
            "`/invite-logs` `/boost-logs`\n"
            "\n**Ports** `/ports` `/ports add` `/ports list` `/ports remove` `/ports-add-user` "
            "`/ports-remove-user` `/ports-revoke`\n"
            "\n**System** `/set-status` `/change-mode` `/maintenance` `/thresholds` "
            "`/set-threshold` `/check-resources` `/machine-status` `/help` `/commands`"
            + (admin_extra if actor["admin"] else "")
        )

    # -------------------------------------------------------- slash commands
    def slash_command(name, description):
        """Create an app_commands.Command from a dispatcher handler."""
        async def callback(interaction, vps_id: int = None, target: str = None,
                           value: str = None, extra: str = None):
            await interaction.response.defer(ephemeral=True, thinking=True)
            actor = _actor(interaction.user.id, getattr(interaction.user, "display_name", ""))
            argv = {"vps_id": vps_id, "discord_id": target, "user": target, "value": value,
                    "amount": value, "name": extra, "text": extra, "command": extra,
                    "reason": extra, "node": extra, "plan": extra, "type": extra,
                    "scope": extra, "mode": value, "metric": value, "_command": name}
            text = await run(name, actor, argv)
            await interaction.followup.send(text, ephemeral=True)

        callback.__name__ = f"cmd_{name.replace('-', '_').replace(' ', '_')}"
    # The dynamically created callback keeps a stable signature so discord.py can
    # introspect parameters.

        return app_commands.Command(name=name, description=description[:100], callback=callback)

    def add_slash(name, description):
        try:
            tree.add_command(slash_command(name, description))
        except Exception as exc:
            helpers.get("logger") and helpers["logger"].warning("Could not register /%s: %s", name, exc)

    COMMANDS = [
        ("ping", "Bot and panel latency"),
        ("uptime", "Bot / panel uptime"),
        ("plans", "Show hosting plans"),
        ("freeplans", "Show free plans"),
        ("stats", "Panel resource statistics"),
        ("claimfree", "Claim your free VPS"),
        ("myvps", "List your VPS"),
        ("list", "List your VPS"),
        ("manage", "Start/stop/restart/delete your VPS"),
        ("mangage", "Alias of /manage"),
        ("share-user", "Share a VPS with a Discord user"),
        ("share-ruser", "Share a VPS with a Discord user"),
        ("manage-shared", "List VPS shared with you"),
        ("vpsinfo", "VPS details"),
        ("vps-stats", "Live VPS resource usage"),
        ("restart-vps", "Restart one of your VPS"),
        ("renew-vps", "Renew a VPS expiry date"),
        ("clone-vps", "Clone a VPS"),
        ("snapshot", "Create an Incus snapshot"),
        ("restore-backup", "Restore an Incus snapshot"),
        ("addinv", "Add invites to a user"),
        ("removeinv", "Remove invites from a user"),
        ("addboost", "Add boosts to a user"),
        ("removeboost", "Remove boosts from a user"),
        ("user-stats", "Invite / boost stats for a user"),
        ("invite-logs", "Recent invite activity"),
        ("boost-logs", "Recent boost activity"),
        ("create", "Create a VPS for a user"),
        ("delete-vps", "Delete a VPS"),
        ("add-resources", "Increase VPS resources"),
        ("resize-vps", "Resize a VPS"),
        ("suspend-vps", "Suspend a VPS"),
        ("unsuspend-vps", "Unsuspend a VPS"),
        ("suspension-logs", "Suspension history"),
        ("whitelist-vps", "Whitelist a Discord user for a VPS"),
        ("userinfo", "Panel user details"),
        ("list-all", "List every VPS"),
        ("serverstats", "Panel statistics"),
        ("stop-vps-all", "Stop every running VPS"),
        ("migrate-vps", "Move a VPS to another node"),
        ("vps-network", "Show VPS network interfaces"),
        ("apply-permissions", "Reapply guest file permissions"),
        ("fix-internet", "Repair VPS DNS / DHCP"),
        ("lxc-list", "List Incus/LXC instances per node"),
        ("admin-add", "Add a bot administrator"),
        ("admin-remove", "Remove a bot administrator"),
        ("admin-list", "List bot administrators"),
        ("purge-prot", "Protect a VPS from purge"),
        ("purge-remove-prot", "Remove purge protection"),
        ("purge-list-protected", "List protected VPS"),
        ("purge-vm-all", "Prepare a purge of every VPS"),
        ("confirm-purge-all", "Confirm the VPS purge"),
        ("purge-data", "Prepare a bot-data purge"),
        ("confirm-purge", "Confirm the bot-data purge"),
        ("exec", "Run a command inside a VPS"),
        ("ports-list", "List port forwards"),
        ("ports-add-user", "Grant port quota to a user"),
        ("ports-remove-user", "Reduce a user port quota"),
        ("ports-revoke", "Revoke all ports for a user"),
        ("set-status", "Set the bot status text"),
        ("change-mode", "Switch bot mode"),
        ("maintenance", "Toggle maintenance mode"),
        ("thresholds", "Show alert thresholds"),
        ("set-threshold", "Set an alert threshold"),
        ("check-resources", "Check node resource alerts"),
        ("machine-status", "Panel resource statistics"),
        ("help", "Show every command"),
        ("commands", "Show every command"),
    ]
    for name, description in COMMANDS:
        add_slash(name, description)

    # /ports group: add, list, remove
    ports_group = app_commands.Group(name="ports", description="Port forwarding commands")

    async def _ports_callback(interaction, sub, vps_id=None, local_port=None, remote_port=None, port_id=None):
        await interaction.response.defer(ephemeral=True, thinking=True)
        actor = _actor(interaction.user.id, getattr(interaction.user, "display_name", ""))
        text = await run(sub, actor, {"vps_id": vps_id, "local_port": local_port,
                                      "remote_port": remote_port, "port_id": port_id,
                                      "_command": sub})
        await interaction.followup.send(text, ephemeral=True)

    @ports_group.command(name="list", description="List port forwards")
    async def ports_list(interaction):
        await _ports_callback(interaction, "ports")

    @ports_group.command(name="add", description="Add a port forward")
    async def ports_add(interaction, vps_id: int, local_port: int, remote_port: int):
        await _ports_callback(interaction, "ports add", vps_id, local_port, remote_port)

    @ports_group.command(name="remove", description="Remove a port forward")
    async def ports_remove_it(interaction, port_id: int):
        await _ports_callback(interaction, "ports remove", port_id=port_id)

    try:
        tree.add_command(ports_group)
    except Exception as exc:
        helpers.get("logger") and helpers["logger"].warning("Could not register /ports: %s", exc)

    # ---------------------------------------------------------- prefix mirror
    async def prefix_dispatch(message, command, args):
        actor = _actor(message.author.id, getattr(message.author, "display_name", ""))
        argv = {"_command": command}
        words = list(args)
        if words and words[0].isdigit():
            argv["vps_id"] = int(words[0])
            words = words[1:]
        if command in ("ports",):
            sub = words[0].lower() if words else "list"
            words = words[1:]
            if sub == "add" and len(words) >= 3:
                argv.update({"local_port": int(words[0]) if words[0].isdigit() else 0,
                             "remote_port": int(words[1]) if words[1].isdigit() else 0})
            command = f"ports {sub}" if sub in ("add", "list", "remove") else "ports"
        if words:
            argv.setdefault("extra", " ".join(words))
            argv.setdefault("text", " ".join(words))
            argv.setdefault("name", words[0])
            argv.setdefault("value", words[0])
            argv.setdefault("user", words[0])
            argv.setdefault("discord_id", words[0].strip("<@!>"))
            argv.setdefault("amount", words[0] if words[0].lstrip("-").isdigit() else 1)
            argv.setdefault("reason", " ".join(words))
            argv.setdefault("command", " ".join(words))
            argv.setdefault("plan", words[0])
            argv.setdefault("node", words[0])
            argv.setdefault("scope", words[0])
            argv.setdefault("metric", words[0])
        return await run(command, actor, argv)

    @client.event
    async def on_message(message):
        if getattr(message.author, "bot", True) or not message.guild:
            return
        if not helpers.get("prefix_enabled", lambda: True)():
            return
        prefix = str(_setting("bot_prefix", "!") or "!")
        if not message.content.startswith(prefix):
            return
        parts = message.content[len(prefix):].strip().split()
        if not parts:
            return
        command, args = parts[0].lower(), parts[1:]
        try:
            text = await prefix_dispatch(message, command, args)
            await message.reply(text, mention_author=False)
        except Exception:
            try:
                await message.reply("❌ Command failed. Try `/help`.", mention_author=False)
            except Exception:
                pass

    @client.event
    async def on_ready():
        try:
            status = _get_state("status_text", "")
            if status:
                await client.change_presence(activity=discord.Game(name=status))
            mode = _get_state("mode", "normal")
            if mode != "normal":
                helpers.get("logger") and helpers["logger"].warning("Bot started in %s mode", mode)
        except Exception:
            pass

    helpers.get("logger") and helpers["logger"].info(
        "Registered %s slash commands and the prefix command mirror", len(COMMANDS) + 3
    )
    return {"handlers": HANDLERS, "run": run}
