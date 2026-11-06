"""Billing for the Vizion panel.

Admins create and edit plans, users see them on /billing, and pressing "Buy"
opens the Discord invite that is configured in the admin billing page.
"""
from __future__ import annotations

from flask import flash, redirect, render_template, request, session, url_for

SETTINGS_COLUMNS = [
    ("billing_enabled", "INTEGER DEFAULT 1"),
    ("billing_discord_link", "TEXT DEFAULT ''"),
    ("billing_discord_webhook", "TEXT DEFAULT ''"),
    ("billing_currency", "TEXT DEFAULT 'USD'"),
    ("billing_note", "TEXT DEFAULT ''"),
]

PLANS_TABLE = """
CREATE TABLE IF NOT EXISTS billing_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    price REAL DEFAULT 0,
    cpu INTEGER DEFAULT 1,
    ram INTEGER DEFAULT 1024,
    disk INTEGER DEFAULT 20,
    vps_limit INTEGER DEFAULT 1,
    duration_days INTEGER DEFAULT 30,
    features TEXT DEFAULT '',
    is_active INTEGER DEFAULT 1,
    sort_order INTEGER DEFAULT 0,
    bg_image TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

ORDERS_TABLE = """
CREATE TABLE IF NOT EXISTS billing_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username TEXT DEFAULT '',
    plan_id INTEGER,
    plan_name TEXT DEFAULT '',
    price REAL DEFAULT 0,
    status TEXT DEFAULT 'pending',
    note TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

ORDERS_TABLE_SQL = ORDERS_TABLE

BILLING_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Billing — {{ settings.site_name if settings else 'Vizion Panel' }}</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <link rel="icon" href="/logo">
    <style>
        .plans-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 18px;
        }
        .plan-card {
            position: relative;
            border-radius: 18px;
            overflow: hidden;
            min-height: 320px;
            background-size: cover;
            background-position: center;
            background-repeat: no-repeat;
            background-image: linear-gradient(135deg, #1b2436, #0c1119);
            border: 1px solid rgba(255,255,255,.07);
            box-shadow: 0 14px 36px -18px rgba(0,0,0,.85);
            transition: transform .18s ease, box-shadow .18s ease;
        }
        .plan-card:hover { transform: translateY(-3px); box-shadow: 0 20px 44px -18px rgba(0,0,0,.9); }
        .plan-card-overlay {
            position: absolute;
            inset: 0;
            background: linear-gradient(180deg, rgba(8,10,16,.10) 0%, rgba(8,10,16,.72) 55%, rgba(8,10,16,.96) 100%);
            padding: 22px 20px;
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            gap: 8px;
        }
        .plan-card-price {
            font-size: 27px;
            font-weight: 800;
            color: #fff;
            letter-spacing: -0.6px;
            line-height: 1.1;
            text-shadow: 0 2px 14px rgba(0,0,0,.55);
        }
        .plan-card-period { font-size: 13px; font-weight: 500; color: rgba(255,255,255,.68); margin-left: 4px; }
        .plan-card-name {
            font-size: 19px;
            font-weight: 700;
            color: #fff;
            margin: 0;
            text-shadow: 0 2px 14px rgba(0,0,0,.55);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .plan-card-specs {
            margin: 0;
            font-size: 13px;
            color: rgba(255,255,255,.82);
            line-height: 1.45;
            text-shadow: 0 1px 10px rgba(0,0,0,.5);
        }
        .plan-card-features {
            margin: 2px 0 4px;
            font-size: 12.5px;
            color: rgba(255,255,255,.66);
            white-space: pre-line;
        }
        .plan-card-buy { margin-top: 8px; }
        .page-section-head { margin-bottom: 18px; }
        .page-section-head h2 { font-size: 18px; margin: 0 0 6px; }
        .page-section-head .text-dim { color: var(--text-dim); }
    </style>
</head>
<body>
{% include 'icons.html' %}
{% from 'components.html' import sidebar, topbar, overlay %}
<div class="app-layout">
    {{ sidebar('billing', settings, custom_icon) }}
    <div class="main-content">
        {{ topbar('Billing', 'billing') }}
        <div class="page">
            <div class="page-header">
                <div>
                    <h1>Billing &amp; Plans</h1>
                    <p class="page-header-sub">Pick a plan, then finish the payment with our team on Discord.</p>
                </div>
            </div>
            {% if not enabled %}
            <div class="card"><p class="text-dim">Billing is currently disabled by the administrator.</p></div>
            {% else %}
            <div class="page-section-head">
                <div>
                    <h2 class="text-xl" style="font-weight:700;color:var(--text);">Available plans</h2>
                    <p class="text-dim" style="font-size:13px;">Choose a plan. Your order is created instantly and paid on Discord.</p>
                </div>
            </div>
            <div class="plans-grid">
                {% for plan in plans %}
                <div class="plan-card" style="{% if plan['bg_image'] %}background-image: url('{{ plan['bg_image'] }}');{% endif %}">
                    <div class="plan-card-overlay">
                        <div class="plan-card-price">{{ currency }} {{ '%.2f'|format(plan['price'] or 0) }}<span class="plan-card-period">/ {{ plan['duration_days'] }} days</span></div>
                        <h3 class="plan-card-name">{{ plan['name'] }}</h3>
                        <p class="plan-card-specs">{{ plan['cpu'] }} vCPU &middot; {{ plan['ram'] }} MiB RAM &middot; {{ plan['disk'] }} GiB Disk &middot; {{ plan['vps_limit'] }} VPS</p>
                        {% if plan['features'] %}<p class="plan-card-features">{{ plan['features'] }}</p>{% endif %}
                        <form method="post" action="{{ url_for('billing_buy', plan_id=plan['id']) }}">
                            <button class="btn primary full plan-card-buy" type="submit">Buy this plan</button>
                        </form>
                    </div>
                </div>
                {% else %}
                <div class="empty">No plans are available right now.</div>
                {% endfor %}
            </div>
            <div class="card" style="margin-top:18px;">
                <div class="card-head"><div><h2>My orders</h2><p>New orders wait for confirmation on Discord.</p></div></div>
                <table class="table">
                    <thead><tr><th>#</th><th>Plan</th><th>Price</th><th>Status</th><th>Date</th><th></th></tr></thead>
                    <tbody>
                    {% for order in orders %}
                    <tr>
                        <td class="text-dim">#{{ order['id'] }}</td>
                        <td>{{ order['plan_name'] }}</td>
                        <td>{{ currency }} {{ '%.2f'|format(order['price'] or 0) }}</td>
                        <td><span class="status status-{{ 'running' if order['status'] == 'paid' else ('stopped' if order['status'] == 'cancelled' else 'creating') }}">{{ order['status'] }}</span></td>
                        <td class="text-dim">{{ (order['created_at'] or '')[:16] }}</td>
                        <td>{% if discord_link and order['status'] == 'pending' %}<a class="btn btn-ghost btn-sm" href="{{ discord_link }}" target="_blank" rel="noopener">Open ticket</a>{% endif %}</td>
                    </tr>
                    {% else %}
                    <tr><td colspan="6" class="text-dim">No orders yet.</td></tr>
                    {% endfor %}
                    </tbody>
                </table>
            </div>
            {% if note %}
            <div class="card" style="margin-top:18px;">
                <h2 class="card-title">How payment works</h2>
                <p class="text-dim" style="font-size:13px;white-space:pre-line;">{{ note }}</p>
            </div>
            {% endif %}
            {% if discord_link %}
            <div class="card" style="margin-top:18px;">
                <h2 class="card-title">Questions or payment</h2>
                <p class="text-dim" style="font-size:13px;margin-bottom:10px;">Join our Discord and open a ticket with your order id.</p>
                <a class="btn primary" href="{{ discord_link }}" target="_blank" rel="noopener">Open our Discord</a>
            </div>
            {% endif %}
            {% endif %}
        </div>
    </div>
</div>
{{ overlay() }}
<script src="{{ url_for('static', filename='js/main.js') }}"></script>
</body>
</html>"""

ADMIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Billing admin — {{ settings.site_name if settings else 'Vizion Panel' }}</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <link rel="icon" href="/logo">
    <style>
        .plan-editor { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 22px; align-items: start; }
        @media (max-width: 860px) { .plan-editor { grid-template-columns: 1fr; } }
        .plan-preview-wrap { position: sticky; top: 16px; }
        .preview-label { font-size: 11px; text-transform: uppercase; letter-spacing: .12em; color: var(--text-dim); margin: 0 0 8px; }
        .plan-card {
            position: relative; border-radius: 18px; overflow: hidden; min-height: 300px;
            background-size: cover; background-position: center; background-repeat: no-repeat;
            background-image: linear-gradient(135deg, #1b2436, #0c1119);
            border: 1px solid rgba(255,255,255,.07); box-shadow: 0 14px 36px -18px rgba(0,0,0,.85);
        }
        .plan-card-overlay {
            position: absolute; inset: 0; padding: 22px 20px;
            background: linear-gradient(180deg, rgba(8,10,16,.10) 0%, rgba(8,10,16,.72) 55%, rgba(8,10,16,.96) 100%);
            display: flex; flex-direction: column; justify-content: flex-end; gap: 8px;
        }
        .plan-card-price { font-size: 27px; font-weight: 800; color: #fff; letter-spacing: -.6px; line-height: 1.1; }
        .plan-card-period { font-size: 13px; font-weight: 500; color: rgba(255,255,255,.68); margin-left: 4px; }
        .plan-card-name { font-size: 19px; font-weight: 700; color: #fff; margin: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .plan-card-specs { margin: 0; font-size: 13px; color: rgba(255,255,255,.82); line-height: 1.45; }
        .plan-card-features { margin: 2px 0 0; font-size: 12.5px; color: rgba(255,255,255,.66); white-space: pre-line; }
        .editor-hint { font-size: 12px; color: var(--text-dim); margin: 10px 0 0; line-height: 1.5; }
    </style>
</head>
<body>
{% include 'icons.html' %}
{% from 'components.html' import sidebar, topbar, overlay %}
<div class="app-layout">
    {{ sidebar('adminbilling', settings, custom_icon) }}
    <div class="main-content">
        {{ topbar('Billing admin', 'adminbilling') }}
        <div class="page">
            {% with messages = get_flashed_messages(with_categories=true) %}
            {% for cat, msg in messages %}<div class="card" style="margin-bottom:12px;border-color:{{ 'var(--green)' if cat == 'success' else 'var(--red)' }};">{{ msg }}</div>{% endfor %}
            {% endwith %}
            <div class="page-header">
                <div>
                    <h1>Billing &amp; plans</h1>
                    <p class="page-header-sub">Create or edit plans and set the Discord link that opens after a purchase.</p>
                </div>
            </div>

            <div class="card" style="margin-bottom:16px;">
                <h2 class="card-title">Billing settings</h2>
                <form method="post" action="{{ url_for('billing_admin_settings') }}" class="form-grid">
                    <label>Discord invite link<input name="billing_discord_link" value="{{ settings['billing_discord_link'] }}" placeholder="https://discord.gg/..."></label>
                    <label>Currency<input name="billing_currency" value="{{ settings['billing_currency'] or 'USD' }}" placeholder="USD"></label>
                    <label>Payment note<textarea name="billing_note" rows="3" placeholder="How users should pay">{{ settings['billing_note'] }}</textarea></label>
                    <label>Ticket webhook (optional)<input name="billing_discord_webhook" value="{{ settings['billing_discord_webhook'] if 'billing_discord_webhook' in settings.keys() else '' }}" placeholder="https://discord.com/api/webhooks/..."><span class="editor-hint">Every new order posts a ticket message here, so the admin can reply in Discord.</span></label>
                    <label>Billing enabled<select name="billing_enabled"><option value="1" {% if settings['billing_enabled'] %}selected{% endif %}>Yes</option><option value="0" {% if not settings['billing_enabled'] %}selected{% endif %}>No</option></select></label>
                    <button class="btn primary full" type="submit">Save billing settings</button>
                </form>
            </div>

            <div class="card" style="margin-bottom:16px;" id="planEditor">
                <h2 class="card-title">{{ 'Edit plan — ' + edit_plan['name'] if edit_plan else 'Add plan' }}</h2>
                <div class="plan-editor">
                    <form method="post" action="{{ url_for('billing_admin_plan_save') }}" class="form-grid" id="planForm">
                        <input type="hidden" name="plan_id" value="{{ edit_plan['id'] if edit_plan else '' }}">
                        <label>Plan name<input id="pf-name" name="name" required value="{{ edit_plan['name'] if edit_plan else '' }}" placeholder="Starter"></label>
                        <label>Price<input id="pf-price" name="price" type="number" step="0.01" min="0" value="{{ edit_plan['price'] if edit_plan else 0 }}"></label>
                        <label>CPU cores<input id="pf-cpu" name="cpu" type="number" min="0" value="{{ edit_plan['cpu'] if edit_plan else 1 }}"></label>
                        <label>RAM (MiB)<input id="pf-ram" name="ram" type="number" min="0" value="{{ edit_plan['ram'] if edit_plan else 1024 }}"></label>
                        <label>Disk (GiB)<input id="pf-disk" name="disk" type="number" min="0" value="{{ edit_plan['disk'] if edit_plan else 20 }}"></label>
                        <label>VPS limit<input id="pf-vps" name="vps_limit" type="number" min="0" value="{{ edit_plan['vps_limit'] if edit_plan else 1 }}"></label>
                        <label>Duration (days)<input id="pf-days" name="duration_days" type="number" min="1" value="{{ edit_plan['duration_days'] if edit_plan else 30 }}"></label>
                        <label>Sort order<input name="sort_order" type="number" value="{{ edit_plan['sort_order'] if edit_plan else 0 }}"></label>
                        <label>Card text (features, one per line)<textarea id="pf-features" name="features" rows="3" placeholder="Instant setup&#10;Full root access">{{ edit_plan['features'] if edit_plan else '' }}</textarea></label>
                        <label>Background image URL<input id="pf-bg" name="bg_image" value="{{ edit_plan['bg_image'] if edit_plan else '' }}" placeholder="https://images.unsplash.com/photo-..."><span class="editor-hint">Only this plan's card background changes. Leave empty for the default dark gradient.</span></label>
                        <label>Visible<select name="is_active"><option value="1" {% if not edit_plan or edit_plan['is_active'] %}selected{% endif %}>Yes</option><option value="0" {% if edit_plan and not edit_plan['is_active'] %}selected{% endif %}>No</option></select></label>
                        <button class="btn primary full" type="submit">{{ 'Save changes' if edit_plan else 'Add plan' }}</button>
                    </form>
                    <div class="plan-preview-wrap">
                        <p class="preview-label">Live card preview</p>
                        <div class="plan-card" id="planPreview">
                            <div class="plan-card-overlay">
                                <div class="plan-card-price"><span id="pv-price">{{ currency }} 0.00</span><span class="plan-card-period">/ <span id="pv-days">30</span> days</span></div>
                                <h3 class="plan-card-name" id="pv-name">Plan name</h3>
                                <p class="plan-card-specs" id="pv-specs">1 vCPU · 1024 MiB RAM · 20 GiB Disk · 1 VPS</p>
                                <p class="plan-card-features" id="pv-features"></p>
                            </div>
                        </div>
                        <p class="editor-hint">This is exactly how the plan card looks on the billing page. Text only, over the plan background.</p>
                    </div>
                </div>
                {% if edit_plan %}<p style="margin-top:10px;"><a class="btn btn-ghost" href="{{ url_for('billing_admin') }}">Cancel editing</a></p>{% endif %}
                <script>
                (function () {
                    const currency = {{ currency|tojson }};
                    const card = document.getElementById('planPreview');
                    const bind = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener('input', fn); };
                    function draw() {
                        const val = id => (document.getElementById(id) || {}).value || '';
                        const name = val('pf-name') || 'Plan name';
                        const price = parseFloat(val('pf-price') || '0') || 0;
                        const cpu = val('pf-cpu') || '0', ram = val('pf-ram') || '0', disk = val('pf-disk') || '0', vps = val('pf-vps') || '0';
                        document.getElementById('pv-name').textContent = name;
                        document.getElementById('pv-price').textContent = currency + ' ' + price.toFixed(2);
                        document.getElementById('pv-days').textContent = val('pf-days') || '30';
                        document.getElementById('pv-specs').textContent = cpu + ' vCPU · ' + ram + ' MiB RAM · ' + disk + ' GiB Disk · ' + vps + ' VPS';
                        document.getElementById('pv-features').textContent = val('pf-features');
                        const bg = val('pf-bg').trim();
                        card.style.backgroundImage = bg ? "url('" + bg.replace(/'/g, '%27') + "')" : '';
                    }
                    ['pf-name', 'pf-price', 'pf-cpu', 'pf-ram', 'pf-disk', 'pf-vps', 'pf-days', 'pf-features', 'pf-bg'].forEach(id => bind(id, draw));
                    draw();
                })();
                </script>
            </div>

            <div class="card" style="margin-bottom:16px;">
                <h2 class="card-title">Plans</h2>
                <table class="table">
                    <thead><tr><th>Name</th><th>Price</th><th>Specs</th><th>VPS</th><th>Days</th><th>Visible</th><th></th></tr></thead>
                    <tbody>
                    {% for plan in plans %}
                    <tr>
                        <td>{{ plan['name'] }}</td>
                        <td>{{ currency }} {{ '%.2f'|format(plan['price'] or 0) }}</td>
                        <td class="text-dim">{{ plan['cpu'] }} CPU · {{ plan['ram'] }} MiB · {{ plan['disk'] }} GiB</td>
                        <td>{{ plan['vps_limit'] }}</td>
                        <td>{{ plan['duration_days'] }}</td>
                        <td>{{ 'yes' if plan['is_active'] else 'no' }}</td>
                        <td style="white-space:nowrap;">
                            <a class="btn btn-ghost btn-sm" href="{{ url_for('billing_admin', edit=plan['id']) }}">Edit</a>
                            <form method="post" action="{{ url_for('billing_admin_plan_delete', plan_id=plan['id']) }}" style="display:inline;" onsubmit="return confirm('Delete this plan?');">
                                        <button class="btn btn-ghost btn-sm" type="submit">Delete</button>
                            </form>
                        </td>
                    </tr>
                    {% else %}
                    <tr><td colspan="7" class="text-dim">No plans yet. Add one above.</td></tr>
                    {% endfor %}
                    </tbody>
                </table>
            </div>

            <div class="card">
                <h2 class="card-title">Orders</h2>
                <table class="table">
                    <thead><tr><th>#</th><th>User</th><th>Plan</th><th>Price</th><th>Status</th><th>Date</th><th></th></tr></thead>
                    <tbody>
                    {% for order in orders %}
                    <tr>
                        <td>{{ order['id'] }}</td>
                        <td>{{ order['username'] or order['user_id'] }}</td>
                        <td>{{ order['plan_name'] }}</td>
                        <td>{{ currency }} {{ '%.2f'|format(order['price'] or 0) }}</td>
                        <td><span class="status status-{{ 'running' if order['status'] == 'paid' else ('stopped' if order['status'] == 'cancelled' else 'creating') }}">{{ order['status'] }}</span></td>
                        <td class="text-dim">{{ (order['created_at'] or '')[:16] }}</td>
                        <td style="white-space:nowrap;">
                            <form method="post" action="{{ url_for('billing_admin_order_status', order_id=order['id'], status='paid') }}" style="display:inline;"><button class="btn btn-ghost btn-sm" type="submit">Mark paid</button></form>
                            <form method="post" action="{{ url_for('billing_admin_order_status', order_id=order['id'], status='cancelled') }}" style="display:inline;"><button class="btn btn-ghost btn-sm" type="submit">Cancel</button></form>
                        </td>
                    </tr>
                    {% else %}
                    <tr><td colspan="7" class="text-dim">No orders yet.</td></tr>
                    {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>
{{ overlay() }}
<script src="{{ url_for('static', filename='js/main.js') }}"></script>
</body>
</html>"""


def register_billing(app, get_db, login_required, admin_required, get_settings, templates, add_log=None):
    """Attach /billing and /admin/billing to the panel app."""
    state = {"ready": False}

    def ensure_schema():
        if state["ready"]:
            return
        conn = get_db()
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(settings)").fetchall()}
            for name, definition in SETTINGS_COLUMNS:
                if name not in columns:
                    conn.execute(f"ALTER TABLE settings ADD COLUMN {name} {definition}")
            conn.execute(PLANS_TABLE)
            conn.execute(ORDERS_TABLE)
            conn.commit()
            state["ready"] = True
        finally:
            conn.close()

    try:
        ensure_schema()
    except Exception:
        app.logger.exception("Billing schema could not be prepared yet")

    @app.before_request
    def _billing_ready():
        try:
            ensure_schema()
        except Exception:
            app.logger.exception("Billing schema check failed")

    templates["billing.html"] = BILLING_PAGE
    templates["admin_billing.html"] = ADMIN_PAGE

    def _settings_dict():
        row = get_settings()
        return dict(row) if row else {}

    def billing_currency(data):
        return (data.get("billing_currency") or "USD").strip() or "USD"

    @app.route("/billing")
    @login_required
    def billing_home():
        ensure_schema()
        data = _settings_dict()
        enabled = str(data.get("billing_enabled", 1)).strip().lower() not in {"0", "false", "off", "no", ""}
        conn = get_db()
        try:
            plans = conn.execute(
                "SELECT * FROM billing_plans WHERE COALESCE(is_active,1)=1 ORDER BY COALESCE(sort_order,0), COALESCE(price,0), id"
            ).fetchall()
            orders = conn.execute(
                "SELECT * FROM billing_orders WHERE user_id=? ORDER BY id DESC LIMIT 30",
                (session.get("user_id"),),
            ).fetchall()
        finally:
            conn.close()
        return render_template(
            "billing.html", settings=data, plans=plans, orders=orders,
            enabled=enabled, note=(data.get("billing_note") or "").strip(),
            discord_link=(data.get("billing_discord_link") or "").strip(),
            currency=billing_currency(data),
        )

    def _open_ticket(order_id, plan_name, price, username, currency, discord_link, webhook):
        """Post the new order to the ticket webhook so an admin can handle it."""
        if not webhook:
            return False
        try:
            import requests

            content = (
                f"**New order #{order_id}** — {plan_name}\n"
                f"User: `{username}` · Price: {currency} {price:.2f}\n"
                + (f"Ticket: {discord_link}" if discord_link else "")
            )
            response = requests.post(
                webhook,
                json={
                    "content": content[:1900],
                    "username": "Vizion Billing",
                    "embeds": [{
                        "title": f"Order #{order_id} · {plan_name}",
                        "description": "A customer opened this order from the billing page. Reply here to handle it.",
                        "color": 0x39A9F0,
                        "fields": [
                            {"name": "Customer", "value": username or "unknown", "inline": True},
                            {"name": "Price", "value": f"{currency} {price:.2f}", "inline": True},
                        ],
                    }],
                },
                timeout=8,
            )
            return response.status_code in (200, 204)
        except Exception:
            app.logger.exception("Billing ticket webhook failed")
            return False

    @app.route("/billing/buy/<int:plan_id>", methods=["POST"])
    @login_required
    def billing_buy(plan_id):
        ensure_schema()
        data = _settings_dict()
        discord_link = (data.get("billing_discord_link") or "").strip()
        webhook = (data.get("billing_discord_webhook") or "").strip()
        currency = billing_currency(data)
        conn = get_db()
        try:
            plan = conn.execute("SELECT * FROM billing_plans WHERE id=?", (plan_id,)).fetchone()
            if not plan:
                flash("That plan no longer exists.", "error")
                return redirect(url_for("billing_home"))
            cur = conn.execute(
                "INSERT INTO billing_orders (user_id, username, plan_id, plan_name, price, status) VALUES (?,?,?,?,?, 'pending')",
                (session.get("user_id"), session.get("username") or "", plan["id"], plan["name"], plan["price"] or 0),
            )
            conn.commit()
            order_id = cur.lastrowid
        finally:
            conn.close()
        ticket = _open_ticket(order_id, plan["name"], float(plan["price"] or 0),
                              session.get("username") or "", currency, discord_link, webhook)
        if add_log:
            add_log("info", f"Plan '{plan['name']}' requested from billing (order #{order_id}, ticket {'opened' if ticket else 'not posted'})", "billing")
        if discord_link:
            message = f"Order #{order_id} created for {plan['name']}."
            message += " A ticket was opened on Discord." if ticket else " Finish the payment on Discord."
            flash(message, "success")
            return redirect(discord_link)
        flash(f"Order #{order_id} created for {plan['name']}. The administrator has not set a Discord link yet.", "success")
        return redirect(url_for("billing_home"))

    @app.route("/admin/billing", methods=["GET"])
    @admin_required
    def billing_admin():
        ensure_schema()
        data = _settings_dict()
        edit_id = request.args.get("edit", type=int)
        conn = get_db()
        try:
            edit_plan = conn.execute("SELECT * FROM billing_plans WHERE id=?", (edit_id,)).fetchone() if edit_id else None
            plans = conn.execute("SELECT * FROM billing_plans ORDER BY COALESCE(sort_order,0), id").fetchall()
            orders = conn.execute("SELECT * FROM billing_orders ORDER BY id DESC LIMIT 100").fetchall()
        finally:
            conn.close()
        return render_template(
            "admin_billing.html", settings=data, plans=plans, orders=orders,
            edit_plan=edit_plan, currency=billing_currency(data),
        )

    @app.route("/admin/billing/settings", methods=["POST"])
    @admin_required
    def billing_admin_settings():
        ensure_schema()
        conn = get_db()
        try:
            row = conn.execute("SELECT id FROM settings LIMIT 1").fetchone()
            settings_id = row["id"] if row else 1
            conn.execute(
                "UPDATE settings SET billing_enabled=?, billing_discord_link=?, billing_discord_webhook=?,"
                " billing_currency=?, billing_note=? WHERE id=?",
                (1 if request.form.get("billing_enabled") == "1" else 0,
                 (request.form.get("billing_discord_link") or "").strip()[:400],
                 (request.form.get("billing_discord_webhook") or "").strip()[:400],
                 (request.form.get("billing_currency") or "USD").strip()[:12] or "USD",
                 (request.form.get("billing_note") or "").strip()[:1000],
                 settings_id),
            )
            conn.commit()
        finally:
            conn.close()
        if add_log:
            add_log("info", "Billing settings updated", "billing")
        flash("Billing settings saved.", "success")
        return redirect(url_for("billing_admin"))

    @app.route("/admin/billing/plans/save", methods=["POST"])
    @admin_required
    def billing_admin_plan_save():
        ensure_schema()

        def number(field, default=0, cast=float):
            try:
                return cast(float(request.form.get(field) or default))
            except (TypeError, ValueError):
                return default

        name = (request.form.get("name") or "").strip()[:80]
        if not name:
            flash("Plan name is required.", "error")
            return redirect(url_for("billing_admin"))
        plan_id = request.form.get("plan_id", type=int)
        # The background image is set per plan and must be read before the row
        # values are built, otherwise saving a plan raised a NameError and the
        # background image was never stored.
        bg_image = (request.form.get("bg_image") or "").strip()[:1000]
        values = (
            name, number("price", 0.0), number("cpu", 1, int), number("ram", 1024, int),
            number("disk", 20, int), number("vps_limit", 1, int), max(1, number("duration_days", 30, int)),
            (request.form.get("features") or "").strip()[:1000], 1 if request.form.get("is_active") == "1" else 0,
            number("sort_order", 0, int), bg_image,
        )
        conn = get_db()
        try:
            if plan_id:
                conn.execute(
                    """UPDATE billing_plans SET name=?, price=?, cpu=?, ram=?, disk=?, vps_limit=?,
                              duration_days=?, features=?, is_active=?, sort_order=?, bg_image=? WHERE id=?""",
                    values + (plan_id,),
                )
            else:
                conn.execute(
                    """INSERT INTO billing_plans (name, price, cpu, ram, disk, vps_limit, duration_days,
                              features, is_active, sort_order, bg_image) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    values,
                )
            conn.commit()
        finally:
            conn.close()
        if add_log:
            add_log("info", f"Billing plan '{name}' saved", "billing")
        flash("Plan saved.", "success")
        return redirect(url_for("billing_admin"))

    @app.route("/admin/billing/plans/<int:plan_id>/delete", methods=["POST"])
    @admin_required
    def billing_admin_plan_delete(plan_id):
        ensure_schema()
        conn = get_db()
        try:
            conn.execute("DELETE FROM billing_plans WHERE id=?", (plan_id,))
            conn.commit()
        finally:
            conn.close()
        flash("Plan deleted.", "success")
        return redirect(url_for("billing_admin"))

    @app.route("/admin/billing/orders/<int:order_id>/<status>", methods=["POST"])
    @admin_required
    def billing_admin_order_status(order_id, status):
        ensure_schema()
        if status not in {"pending", "paid", "cancelled"}:
            flash("Unknown order status.", "error")
            return redirect(url_for("billing_admin"))
        conn = get_db()
        try:
            conn.execute("UPDATE billing_orders SET status=? WHERE id=?", (status, order_id))
            conn.commit()
        finally:
            conn.close()
        flash(f"Order #{order_id} marked {status}.", "success")
        return redirect(url_for("billing_admin"))
