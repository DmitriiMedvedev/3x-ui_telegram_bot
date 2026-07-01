#!/usr/bin/env python3
"""
sub_server.py — Subscription-сервер v15.1 (Dynamic & Robust).
"""
import base64
import sqlite3
import json
import urllib.parse
import logging
from aiohttp import web

DB_PATH = "/root/dobrinya_bot/3x-ui_telegram_bot/dobrinya.db"
PRICE_PER_GB = 3.0
CREDIT_LIMIT_RUB = -50.0
BOT_NAME = "dobrinyaVPN_bot"

def get_db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_user_by_sub(sub_id: str) -> dict | None:
    try:
        with get_db_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE sub_id=?", (sub_id,)).fetchone()
            return dict(row) if row else None
    except Exception as e:
        print(f"Error user lookup: {e}")
        return None

def get_active_panels() -> list[dict]:
    try:
        with get_db_conn() as conn:
            rows = conn.execute("SELECT * FROM panels").fetchall()
            res = []
            for r in rows:
                p = dict(r)
                try:
                    p['inbound_ids'] = json.loads(p.get('inbound_ids') or '[]')
                    p['inbounds'] = json.loads(p.get('inbounds') or '{}')
                except:
                    p['inbound_ids'], p['inbounds'] = [], {}
                res.append(p)
            return res
    except Exception as e:
        print(f"Error panels lookup: {e}")
        return []

def make_vless_link(u_uuid, email, panel, iid):
    inbounds = panel.get("inbounds") or {}
    cfg = inbounds.get(str(iid)) or inbounds.get(int(iid)) if str(iid).isdigit() else inbounds.get(str(iid))
    if not cfg: return None

    host = cfg.get("host") or panel.get("server_host") or "127.0.0.1"
    port = cfg.get("port", 443)
    net = cfg.get("network", "tcp")
    sec = cfg.get("security", "none")
    label = urllib.parse.quote(f"{email}-{panel.get('name')}-{cfg.get('label', iid)}")

    params = {"type": net, "security": sec}
    if sec == "reality":
        params.update({"pbk": cfg.get("public_key", ""), "fp": cfg.get("fingerprint", "chrome"), "sni": cfg.get("sni", ""), "sid": cfg.get("short_id", "")})
        if cfg.get("flow"): params["flow"] = cfg["flow"]
    elif sec == "tls":
        params["sni"] = cfg.get("sni", "")

    if net == "xhttp": params.update({"path": cfg.get("path", "/"), "mode": cfg.get("xhttp_mode", "auto")})
    elif net == "grpc": params.update({"serviceName": cfg.get("grpc_service", "grpc"), "mode": "gun"})
    elif net == "ws":
        params["path"] = cfg.get("path", "/")
        if cfg.get("ws_host"): params["host"] = cfg["ws_host"]

    query = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items() if v)
    return f"vless://{u_uuid}@{host}:{port}?{query}#{label}"

def make_ss_link(email, panel):
    inbounds = panel.get("inbounds") or {}
    for iid, cfg in inbounds.items():
        if str(cfg.get("protocol", "")).lower() in ["ss", "shadowsocks"]:
            method, password = cfg.get("method", "chacha20-poly1305"), cfg.get("password", "")
            if not password: continue
            host, port = panel.get("server_host", "127.0.0.1"), cfg.get("port", 8388)
            label = urllib.parse.quote(f"{email}-{panel.get('name')}-SS")
            cred = base64.b64encode(f"{method}:{password}".encode()).decode()
            return f"ss://{cred}@{host}:{port}#{label}"
    return None

async def handle_sub(request: web.Request) -> web.Response:
    sub_id = request.match_info.get("sub_id", "")
    user = get_user_by_sub(sub_id)
    if not user: return web.Response(status=404)

    bal = user.get("balance") or 0.0
    if bal <= CREDIT_LIMIT_RUB:
        placeholder = "vless://00000000-0000-0000-0000-000000000000@0.0.0.0:0?type=none#EXPIRED-TopUp"
        return web.Response(text=base64.b64encode(placeholder.encode()).decode(), headers={"Content-Type": "text/plain; charset=utf-8", "Profile-Title": "Dobrinya VPN (EXPIRED)"})

    u_uuid = user.get("vless_uuid")
    if not u_uuid: return web.Response(status=404)

    email = f"user_{user['tg_id']}"
    panels = get_active_panels()
    links = []

    for p in panels:
        for iid in (p.get("inbound_ids") or []):
            l = make_vless_link(u_uuid, email, p, iid)
            if l: links.append(l)
        ss_l = make_ss_link(email, p)
        if ss_l: links.append(ss_l)

    if not links:
        # Fallback to baked links
        old = user.get("configs_all", "").strip()
        if old: links = [x for x in old.split("\n") if x.strip()]

    if not links: return web.Response(status=404)

    content = base64.b64encode("\n".join(links).encode()).decode()
    total_bytes = user.get("total_traffic_bytes") or 0
    rem_bytes = int((max(0, bal) / PRICE_PER_GB) * (1024 ** 3))

    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Profile-Title": f"Dobrinya VPN | {bal:.1f} RUB",
        "Subscription-Userinfo": f"upload=0; download={total_bytes}; total={total_bytes+rem_bytes}; expire=0",
        "Support-URL": f"https://t.me/{BOT_NAME}",
    }
    return web.Response(text=content, headers=headers)

app = web.Application()
app.router.add_get("/sub/{sub_id}", handle_sub)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8080)
