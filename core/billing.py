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
        .billing-wrap { display: flex; flex-direction: column; gap: 20px; }
        .billing-hero {
            position: relative;
            overflow: hidden;
            border-radius: 26px;
            padding: 46px 30px 40px;
            text-align: center;
            background:
                radial-gradient(760px 340px at 50% -20%, rgba(139,92,246,.55), transparent 72%),
                radial-gradient(520px 300px at 92% 112%, rgba(56,189,248,.22), transparent 70%),
                radial-gradient(520px 300px at 6% 108%, rgba(168,85,247,.20), transparent 70%),
                linear-gradient(165deg, #1a1330 0%, #100c1b 46%, #08080f 100%);
            border: 1px solid rgba(255,255,255,.08);
            box-shadow: 0 40px 90px -60px rgba(139,92,246,.95), inset 0 1px 0 rgba(255,255,255,.06);
        }
        .billing-hero::before {
            content: "";
            position: absolute;
            inset: 0;
            background-image: radial-gradient(rgba(255,255,255,.085) 1px, transparent 1px);
            background-size: 24px 24px;
            -webkit-mask-image: radial-gradient(ellipse 70% 70% at 50% 20%, #000 25%, transparent 75%);
            mask-image: radial-gradient(ellipse 70% 70% at 50% 20%, #000 25%, transparent 75%);
            pointer-events: none;
        }
        .billing-hero::after {
            content: "";
            position: absolute;
            left: 50%;
            top: -180px;
            width: 460px;
            height: 460px;
            transform: translateX(-50%);
            background: radial-gradient(circle, rgba(139,92,246,.35), transparent 62%);
            filter: blur(18px);
            pointer-events: none;
        }
        .billing-hero > * { position: relative; z-index: 1; }
        .billing-brand {
            display: inline-flex;
            align-items: center;
            gap: 9px;
            font-size: 15px;
            font-weight: 700;
            color: #f4f1ff;
            letter-spacing: .2px;
        }
        .billing-brand img { width: 22px; height: 22px; border-radius: 7px; object-fit: contain; }
        .billing-brand-dot {
            width: 22px; height: 22px; border-radius: 7px;
            display: inline-flex; align-items: center; justify-content: center;
            font-size: 12px; font-weight: 800; color: #fff;
            background: linear-gradient(135deg, #a855f7, #6d28d9);
            box-shadow: 0 0 18px rgba(168,85,247,.65);
        }
        .billing-pill {
            display: inline-flex;
            align-items: center;
            gap: 7px;
            margin-top: 14px;
            padding: 5px 13px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 600;
            color: rgba(233,225,255,.9);
            background: rgba(255,255,255,.05);
            border: 1px solid rgba(168,85,247,.35);
            box-shadow: inset 0 1px 0 rgba(255,255,255,.06);
        }
        .billing-pill span.dot {
            width: 6px; height: 6px; border-radius: 50%;
            background: #34d399; box-shadow: 0 0 8px #34d399;
        }
        .billing-hero h1 {
            margin: 18px auto 10px;
            font-size: clamp(30px, 4.6vw, 46px);
            line-height: 1.06;
            letter-spacing: -1.6px;
            font-weight: 800;
            color: #fff;
            max-width: 720px;
        }
        .billing-hero h1 em {
            font-style: normal;
            background: linear-gradient(100deg, #c4b5fd 0%, #a855f7 45%, #7c3aed 100%);
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
        }
        .billing-hero p.lead {
            margin: 0 auto;
            max-width: 560px;
            font-size: 15px;
            line-height: 1.6;
            color: rgba(226,220,245,.68);
        }
        .billing-chips {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 10px;
            max-width: 580px;
            margin: 24px auto 0;
        }
        .billing-chip {
            padding: 10px 12px;
            text-align: center;
            border-radius: 14px;
            font-size: 12.5px;
            color: rgba(236,232,248,.82);
            background: rgba(255,255,255,.04);
            border: 1px solid rgba(255,255,255,.08);
            backdrop-filter: blur(6px);
        }
        .billing-chip strong {
            display: block;
            font-size: 15px;
            font-weight: 800;
            color: #c4b5fd;
            letter-spacing: -.2px;
        }
        .billing-cta-row { display: flex; flex-wrap: wrap; gap: 12px; justify-content: center; margin-top: 26px; }
        .billing-btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            padding: 12px 24px;
            border-radius: 14px;
            font-size: 14px;
            font-weight: 700;
            text-decoration: none;
            border: 1px solid transparent;
            transition: transform .16s ease, box-shadow .16s ease, background .16s ease;
        }
        .billing-btn-glow {
            color: #fff;
            background: linear-gradient(135deg, #8b5cf6, #6d28d9);
            box-shadow: 0 14px 34px -14px rgba(139,92,246,.95), inset 0 1px 0 rgba(255,255,255,.18);
        }
        .billing-btn-glow:hover { transform: translateY(-2px); box-shadow: 0 18px 40px -14px rgba(139,92,246,1); }
        .billing-btn-ghost {
            color: rgba(236,232,248,.88);
            background: rgba(255,255,255,.04);
            border-color: rgba(255,255,255,.12);
        }
        .billing-btn-ghost:hover { background: rgba(255,255,255,.08); transform: translateY(-2px); }

        .billing-section-head {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 14px;
            flex-wrap: wrap;
            margin: 6px 2px 0;
        }
        .billing-section-head h2 {
            margin: 0;
            font-size: 20px;
            font-weight: 800;
            letter-spacing: -.4px;
            color: #fff;
        }
        .billing-section-head h2::before {
            content: "";
            display: inline-block;
            width: 4px; height: 17px;
            margin-right: 10px;
            border-radius: 4px;
            vertical-align: -2px;
            background: linear-gradient(180deg, #c4b5fd, #7c3aed);
            box-shadow: 0 0 12px rgba(168,85,247,.8);
        }
        .billing-section-head p { margin: 0; font-size: 13px; color: var(--text-dim); }

        .plans-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(296px, 1fr));
            gap: 20px;
        }
        .plan-card {
            position: relative;
            border-radius: 22px;
            overflow: hidden;
            min-height: 340px;
            display: flex;
            background-size: cover;
            background-position: center;
            background-repeat: no-repeat;
            background-image:
                radial-gradient(420px 220px at 80% 0%, rgba(139,92,246,.35), transparent 70%),
                linear-gradient(135deg, #1b1530, #0b0a13);
            border: 1px solid rgba(255,255,255,.08);
            box-shadow: 0 24px 60px -34px rgba(0,0,0,.95);
            transition: transform .2s ease, box-shadow .2s ease, border-color .2s ease;
        }
        .plan-card::after {
            content: "";
            position: absolute;
            inset: 0;
            border-radius: inherit;
            pointer-events: none;
            box-shadow: inset 0 1px 0 rgba(255,255,255,.10), inset 0 0 0 1px rgba(168,85,247,0);
            transition: box-shadow .2s ease;
        }
        .plan-card:hover {
            transform: translateY(-5px);
            border-color: rgba(168,85,247,.45);
            box-shadow: 0 32px 70px -32px rgba(124,58,237,.75);
        }
        .plan-card:hover::after { box-shadow: inset 0 1px 0 rgba(255,255,255,.16), inset 0 0 0 1px rgba(168,85,247,.35); }
        .plan-card-overlay {
            position: relative;
            z-index: 1;
            flex: 1;
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            gap: 9px;
            padding: 24px 22px;
            background: linear-gradient(180deg, rgba(8,8,15,.08) 0%, rgba(8,8,15,.62) 50%, rgba(6,6,12,.95) 100%);
        }
        .plan-card-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            position: absolute;
            top: 18px;
            left: 20px;
            right: 20px;
        }
        .plan-card-tag {
            font-size: 10.5px;
            font-weight: 700;
            letter-spacing: .10em;
            text-transform: uppercase;
            color: rgba(255,255,255,.9);
            padding: 4px 9px;
            border-radius: 999px;
            background: rgba(12,10,20,.55);
            border: 1px solid rgba(255,255,255,.16);
            backdrop-filter: blur(8px);
        }
        .plan-card-price {
            font-size: 30px;
            font-weight: 800;
            letter-spacing: -1px;
            line-height: 1.05;
            color: #fff;
            text-shadow: 0 2px 18px rgba(0,0,0,.6);
        }
        .plan-card-price .grad {
            background: linear-gradient(100deg, #fff 0%, #d8c9ff 40%, #a855f7 100%);
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
        }
        .plan-card-period { font-size: 13px; font-weight: 500; color: rgba(255,255,255,.66); margin-left: 6px; -webkit-text-fill-color: rgba(255,255,255,.66); }
        .plan-card-name {
            font-size: 20px;
            font-weight: 700;
            letter-spacing: -.3px;
            color: #fff;
            margin: 0;
            text-shadow: 0 2px 14px rgba(0,0,0,.6);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .plan-card-specs {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin: 0;
        }
        .plan-card-specs span {
            font-size: 11.5px;
            font-weight: 600;
            color: rgba(255,255,255,.86);
            padding: 4px 9px;
            border-radius: 999px;
            background: rgba(255,255,255,.08);
            border: 1px solid rgba(255,255,255,.10);
            text-shadow: 0 1px 6px rgba(0,0,0,.4);
        }
        .plan-card-features {
            margin: 0;
            font-size: 12.5px;
            line-height: 1.55;
            color: rgba(255,255,255,.70);
            white-space: pre-line;
        }
        .plan-card-buy {
            margin-top: 8px;
            width: 100%;
            padding: 11px 18px;
            border: 0;
            border-radius: 13px;
            cursor: pointer;
            font-size: 13.5px;
            font-weight: 700;
            font-family: inherit;
            color: #fff;
            background: linear-gradient(135deg, #8b5cf6, #6d28d9);
            box-shadow: 0 14px 34px -18px rgba(139,92,246,.95), inset 0 1px 0 rgba(255,255,255,.2);
            transition: transform .16s ease, box-shadow .16s ease, filter .16s ease;
        }
        .plan-card-buy:hover { transform: translateY(-1px); filter: brightness(1.08); box-shadow: 0 18px 40px -16px rgba(139,92,246,1); }

        .billing-panel {
            border-radius: 22px;
            padding: 22px 22px 8px;
            background: linear-gradient(180deg, rgba(255,255,255,.035), rgba(255,255,255,.012));
            border: 1px solid rgba(255,255,255,.08);
            box-shadow: 0 26px 64px -46px rgba(0,0,0,.9);
        }
        .billing-panel-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            flex-wrap: wrap;
            margin-bottom: 6px;
        }
        .billing-panel-head h2 { margin: 0; font-size: 16px; font-weight: 750; color: #fff; letter-spacing: -.2px; }
        .billing-panel-head p { margin: 3px 0 0; font-size: 12.5px; color: var(--text-dim); }
        .billing-panel .table { margin: 0; }
        .billing-panel .table th {
            font-size: 11px;
            letter-spacing: .08em;
            text-transform: uppercase;
            color: rgba(200,192,220,.55);
            border-bottom: 1px solid rgba(255,255,255,.07);
        }
        .billing-panel .table td { border-bottom: 1px solid rgba(255,255,255,.05); }
        .billing-panel .table tbody tr:hover td { background: rgba(168,85,247,.06); }
        .billing-empty {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 6px;
            padding: 34px 18px;
            text-align: center;
            border-radius: 18px;
            border: 1px dashed rgba(168,85,247,.28);
            background: rgba(168,85,247,.05);
            color: var(--text-dim);
            font-size: 13px;
        }
        .billing-cta-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 18px;
        }
        .billing-note {
            position: relative;
            overflow: hidden;
            border-radius: 20px;
            padding: 22px;
            background: linear-gradient(160deg, rgba(139,92,246,.16), rgba(20,16,32,.75));
            border: 1px solid rgba(168,85,247,.25);
            box-shadow: inset 0 1px 0 rgba(255,255,255,.06);
        }
        .billing-note h3 { margin: 0 0 8px; font-size: 15px; font-weight: 750; color: #fff; letter-spacing: -.2px; }
        .billing-note p { margin: 0; font-size: 13px; line-height: 1.65; color: rgba(226,220,245,.72); white-space: pre-line; }
        .billing-note .billing-btn { margin-top: 14px; }
        @media (max-width: 640px) {
            .billing-hero { padding: 34px 18px 30px; border-radius: 20px; }
            .billing-hero h1 { letter-spacing: -1px; }
            .billing-panel { padding: 18px 14px 6px; }
            .plans-grid { grid-template-columns: 1fr; }
            .billing-chips { grid-template-columns: repeat(2, minmax(0, 1fr)); max-width: 320px; }
        }
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
            <div class="billing-wrap">
                <section class="billing-hero">
                    <span class="billing-brand">
                        <img src="/logo" alt="" onerror="this.style.display='none'">
                        {{ settings.site_name if settings else 'Vizion Panel' }}
                    </span>
                    <div><span class="billing-pill"><span class="dot"></span>Billing is open</span></div>
                    <h1>Pick a plan. <em>Deploy in seconds.</em></h1>
                    <p class="lead">Every plan is provisioned on powerful hardware with full root access. Choose what fits, create the order, and confirm the payment with our team on Discord.</p>
                    <div class="billing-chips">
                        <div class="billing-chip"><strong>{{ plans|length }}</strong>plan{{ '' if plans|length == 1 else 's' }} available</div>
                        <div class="billing-chip"><strong>{{ currency }}</strong>pricing</div>
                        <div class="billing-chip"><strong>Instant</strong>setup</div>
                        <div class="billing-chip"><strong>Root</strong>full access</div>
                    </div>
                    <div class="billing-cta-row">
                        <a class="billing-btn billing-btn-glow" href="#plans">Browse plans</a>
                        {% if discord_link %}<a class="billing-btn billing-btn-ghost" href="{{ discord_link }}" target="_blank" rel="noopener">Talk on Discord</a>{% endif %}
                    </div>
                </section>

                {% if not enabled %}
                <div class="billing-panel" style="padding: 22px;">
                    <div class="billing-empty">Billing is currently disabled by the administrator.</div>
                </div>
                {% else %}
                <div class="billing-section-head" id="plans">
                    <div>
                        <h2>Available plans</h2>
                        <p style="margin-top:5px;">Choose a plan — your order is created instantly and paid on Discord.</p>
                    </div>
                    <p>{{ orders|length }} order{{ '' if orders|length == 1 else 's' }} on your account</p>
                </div>
                <div class="plans-grid">
                    {% for plan in plans %}
                    <div class="plan-card" style="{% if plan['bg_image'] %}background-image: url('{{ plan['bg_image'] }}');{% endif %}">
                        <div class="plan-card-overlay">
                            <div class="plan-card-top">
                                <span class="plan-card-tag">VPS plan</span>
                            </div>
                            <div class="plan-card-price"><span class="grad">{{ currency }} {{ '%.2f'|format(plan['price'] or 0) }}</span><span class="plan-card-period">/ {{ plan['duration_days'] }} days</span></div>
                            <h3 class="plan-card-name">{{ plan['name'] }}</h3>
                            <p class="plan-card-specs">
                                <span>{{ plan['cpu'] }} vCPU</span>
                                <span>{{ plan['ram'] }} MiB RAM</span>
                                <span>{{ plan['disk'] }} GiB SSD</span>
                                <span>{{ plan['vps_limit'] }} VPS</span>
                            </p>
                            {% if plan['features'] %}<p class="plan-card-features">{{ plan['features'] }}</p>{% endif %}
                            <form method="post" action="{{ url_for('billing_buy', plan_id=plan['id']) }}">
                                <button class="plan-card-buy" type="submit">Buy this plan</button>
                            </form>
                        </div>
                    </div>
                    {% else %}
                    <div class="billing-empty" style="grid-column: 1 / -1;">No plans are available right now. Check back soon.</div>
                    {% endfor %}
                </div>

                <div class="billing-panel">
                    <div class="billing-panel-head">
                        <div>
                            <h2>My orders</h2>
                            <p>New orders wait for confirmation on Discord.</p>
                        </div>
                        {% if discord_link %}<a class="billing-btn billing-btn-ghost" style="padding:9px 16px;font-size:12.5px;" href="{{ discord_link }}" target="_blank" rel="noopener">Open Discord</a>{% endif %}
                    </div>
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
                        <tr><td colspan="6" class="text-dim">No orders yet — your first plan is one click away.</td></tr>
                        {% endfor %}
                        </tbody>
                    </table>
                </div>

                {% if note or discord_link %}
                <div class="billing-cta-grid">
                    {% if note %}
                    <div class="billing-note">
                        <h3>How payment works</h3>
                        <p>{{ note }}</p>
                    </div>
                    {% endif %}
                    {% if discord_link %}
                    <div class="billing-note">
                        <h3>Questions or payment</h3>
                        <p>Join our Discord and open a ticket with your order id. Our team confirms the payment and your VPS is delivered right after.</p>
                        <a class="billing-btn billing-btn-glow" href="{{ discord_link }}" target="_blank" rel="noopener">Open our Discord</a>
                    </div>
                    {% endif %}
                </div>
                {% endif %}
                {% endif %}
            </div>
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
        .form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; align-items: start; }
        .form-grid label {
            display: flex; flex-direction: column; gap: 6px;
            font-size: 12.5px; font-weight: 600; color: var(--text-dim);
        }
        .form-grid label:has(textarea), .form-grid .full { grid-column: 1 / -1; }
        .form-grid input, .form-grid select, .form-grid textarea {
            width: 100%;
            box-sizing: border-box;
            margin-top: 0;
            padding: 10px 12px;
            font-family: inherit;
            font-size: 13px;
            font-weight: 500;
            color: var(--text);
            background: var(--bg-input);
            border: 1px solid var(--border);
            border-radius: 10px;
            outline: none;
            transition: border-color .15s ease, box-shadow .15s ease;
        }
        .form-grid input::placeholder, .form-grid textarea::placeholder { color: var(--text-dim); font-weight: 400; }
        .form-grid input:focus, .form-grid select:focus, .form-grid textarea:focus {
            border-color: var(--accent);
            box-shadow: 0 0 0 3px var(--accent-glow);
        }
        .form-grid select { appearance: none; -webkit-appearance: none; padding-right: 32px; cursor: pointer;
            background-image: linear-gradient(45deg, transparent 50%, var(--text-dim) 50%), linear-gradient(135deg, var(--text-dim) 50%, transparent 50%);
            background-position: calc(100% - 17px) 50%, calc(100% - 12px) 50%;
            background-size: 5px 5px, 5px 5px;
            background-repeat: no-repeat;
        }
        .form-grid textarea { min-height: 92px; resize: vertical; line-height: 1.5; }
        .form-grid .editor-hint { font-size: 11.5px; font-weight: 400; color: var(--text-dim); }
        .form-grid button[type="submit"] { margin-top: 2px; }
        @media (max-width: 720px) { .form-grid { grid-template-columns: 1fr; } }
        .plan-preview-wrap { position: sticky; top: 16px; }
        .preview-label { font-size: 11px; text-transform: uppercase; letter-spacing: .12em; color: var(--text-dim); margin: 0 0 8px; }
        .plan-card {
            position: relative; border-radius: 22px; overflow: hidden; min-height: 320px;
            background-size: cover; background-position: center; background-repeat: no-repeat;
            background-image:
                radial-gradient(420px 220px at 80% 0%, rgba(139,92,246,.35), transparent 70%),
                linear-gradient(135deg, #1b1530, #0b0a13);
            border: 1px solid rgba(255,255,255,.08);
            box-shadow: 0 24px 60px -34px rgba(0,0,0,.95);
            transition: transform .2s ease, box-shadow .2s ease, border-color .2s ease;
        }
        .plan-card:hover { transform: translateY(-4px); border-color: rgba(168,85,247,.45); box-shadow: 0 32px 70px -32px rgba(124,58,237,.75); }
        .plan-card-overlay {
            position: absolute; inset: 0; padding: 22px 20px;
            background: linear-gradient(180deg, rgba(8,8,15,.08) 0%, rgba(8,8,15,.62) 50%, rgba(6,6,12,.95) 100%);
            display: flex; flex-direction: column; justify-content: flex-end; gap: 9px;
        }
        .plan-card-tag {
            position: absolute; top: 16px; left: 18px;
            font-size: 10.5px; font-weight: 700; letter-spacing: .10em; text-transform: uppercase;
            color: rgba(255,255,255,.9); padding: 4px 9px; border-radius: 999px;
            background: rgba(12,10,20,.55); border: 1px solid rgba(255,255,255,.16);
        }
        .plan-card-price { font-size: 28px; font-weight: 800; letter-spacing: -1px; line-height: 1.05; color: #fff; }
        .plan-card-price .grad {
            background: linear-gradient(100deg, #fff 0%, #d8c9ff 40%, #a855f7 100%);
            -webkit-background-clip: text; background-clip: text; color: transparent;
        }
        .plan-card-period { font-size: 13px; font-weight: 500; color: rgba(255,255,255,.66); margin-left: 6px; -webkit-text-fill-color: rgba(255,255,255,.66); }
        .plan-card-name { font-size: 20px; font-weight: 700; letter-spacing: -.3px; color: #fff; margin: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .plan-card-specs { display: flex; flex-wrap: wrap; gap: 6px; margin: 0; }
        .plan-card-specs span {
            font-size: 11.5px; font-weight: 600; color: rgba(255,255,255,.86);
            padding: 4px 9px; border-radius: 999px;
            background: rgba(255,255,255,.08); border: 1px solid rgba(255,255,255,.10);
        }
        .plan-card-features { margin: 0; font-size: 12.5px; line-height: 1.55; color: rgba(255,255,255,.70); white-space: pre-line; }
        .editor-hint { font-size: 12px; color: var(--text-dim); margin: 10px 0 0; line-height: 1.5; }
        #planForm .btn, #planEditor .btn { border-radius: 11px; }
        #planForm textarea { min-height: 92px; }
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
                                <span class="plan-card-tag">VPS plan</span>
                                <div class="plan-card-price"><span class="grad" id="pv-price">{{ currency }} 0.00</span><span class="plan-card-period">/ <span id="pv-days">30</span> days</span></div>
                                <h3 class="plan-card-name" id="pv-name">Plan name</h3>
                                <p class="plan-card-specs">
                                    <span id="pv-cpu">1 vCPU</span>
                                    <span id="pv-ram">1024 MiB RAM</span>
                                    <span id="pv-disk">20 GiB SSD</span>
                                    <span id="pv-vps">1 VPS</span>
                                </p>
                                <p class="plan-card-features" id="pv-features"></p>
                                <button class="plan-card-buy" type="button" disabled style="width:100%;padding:11px 18px;border:0;border-radius:13px;color:#fff;font-weight:700;font-size:13.5px;background:linear-gradient(135deg,#8b5cf6,#6d28d9);opacity:.9;">Buy this plan</button>
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
                        document.getElementById('pv-cpu').textContent = cpu + ' vCPU';
                        document.getElementById('pv-ram').textContent = ram + ' MiB RAM';
                        document.getElementById('pv-disk').textContent = disk + ' GiB SSD';
                        document.getElementById('pv-vps').textContent = vps + ' VPS';
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
