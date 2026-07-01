#!/usr/bin/env python3
"""
sub_server.py — Subscription-сервер v15.6 (Full Path Recovery & Diagnostics).
"""
import base64
import sqlite3
import json
import urllib.parse
import os
import uuid
from aiohttp import web

# Авто-определение пути
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "dobrinya.db")

PRICE_PER_GB = 3.0
CREDIT_LIMIT_RUB = -50.0
BOT_NAME = "dobrinyaVPN_bot"

def get_db_conn():
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database file not found at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_user_by_sub(sub_id: str) -> dict | None:
    try:
        with get_db_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE sub_id=?", (sub_id,)).fetchone()
            if row: return dict(row)
            # Если не нашли по sub_id, пробуем найти по tg_id (если sub_id это на самом деле ID)
            if sub_id.isdigit():
                row = conn.execute("SELECT * FROM users WHERE tg_id=?", (int(sub_id),)).fetchone()
                return dict(row) if row else None
            return None
    except Exception as e:
        print(f"User lookup error: {e}")
        return None

def update_user_ids(tg_id, u_uuid, s_id):
    try:
        with get_db_conn() as conn:
            conn.execute("UPDATE users SET vless_uuid=?, sub_id=? WHERE tg_id=?", (u_uuid, s_id, tg_id))
            conn.commit()
    except: pass

def get_active_panels() -> list[dict]:
    try:
        with get_db_conn() as conn:
            rows = conn.execute("SELECT * FROM panels").fetchall()
            res = []
            for r in rows:
                p = dict(r)
                try:
                    p['inbounds'] = json.loads(p.get('inbounds') or '{}')
                    p['inbound_ids'] = json.loads(p.get('inbound_ids') or '[]')
                except:
                    p['inbounds'], p['inbound_ids'] = {}, []
                res.append(p)
            return res
    except Exception as e:
        print(f"Panels lookup error: {e}")
        return []

def make_vless_link(u_uuid, email, panel, cfg, iid):
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

def make_ss_link(email, panel, cfg):
    method, password = cfg.get("method", "chacha20-poly1305"), cfg.get("password", "")
    if not password: return None
    host, port = panel.get("server_host", "127.0.0.1"), cfg.get("port", 8388)
    label = urllib.parse.quote(f"{email}-{panel.get('name')}-SS")
    cred = base64.b64encode(f"{method}:{password}".encode()).decode()
    return f"ss://{cred}@{host}:{port}#{label}"

async def handle_sub(request: web.Request) -> web.Response:
    sub_id = request.match_info.get("sub_id", "")
    user = get_user_by_sub(sub_id)

    if not user:
        return web.Response(text=f"Error: Subscription '{sub_id}' not found in DB ({DB_PATH}).\nTry clicking 'My Account' in bot to register.", status=404)

    bal = user.get("balance") or 0.0
    if bal <= CREDIT_LIMIT_RUB:
        placeholder = "vless://expired@0.0.0.0:0?type=none#ACCOUNT-EXPIRED"
        return web.Response(text=base64.b64encode(placeholder.encode()).decode())

    u_uuid = user.get("vless_uuid")
    s_id   = user.get("sub_id")

    # Auto-repair UUID/SubID if missing
    if not u_uuid or not s_id:
        u_uuid = u_uuid or str(uuid.uuid4())
        s_id   = s_id or uuid.uuid4().hex
        update_user_ids(user['tg_id'], u_uuid, s_id)

    email = f"user_{user['tg_id']}"
    panels = get_active_panels()
    links = []

    for p in panels:
        inbounds = p.get("inbounds") or {}
        for iid, cfg in inbounds.items():
            prot = str(cfg.get("protocol", "")).lower()
            if prot == "vless":
                l = make_vless_link(u_uuid, email, p, cfg, iid)
                if l: links.append(l)
            elif prot in ["ss", "shadowsocks"]:
                l = make_ss_link(email, p, cfg)
                if l: links.append(l)

    if not links and user.get("configs_all"):
        links = [x for x in user["configs_all"].split("\n") if x.strip()]

    if not links:
        diag = f"User: {email}, Panels count: {len(panels)}, UUID: {u_uuid}"
        return web.Response(text=f"Error: No active configurations found for this user.\n\nDiagnostics: {diag}\n\nAction: Please add Inbounds in admin panel and run /sync.", status=404)

    content = base64.b64encode("\n".join(links).encode()).decode()
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Profile-Title": f"Dobrinya VPN | {bal:.1f} RUB",
        "Subscription-Userinfo": f"upload=0; download={user.get('total_traffic_bytes', 0)}; total=0; expire=0",
    }
    return web.Response(text=content, headers=headers)

async def handle_index(request):
    db_exists = os.path.exists(DB_PATH)
    return web.Response(text=f"Subscription Server v15.6 is running.\nDatabase: {DB_PATH}\nStatus: {'✅ Found' if db_exists else '❌ NOT FOUND'}")

app = web.Application()
app.router.add_get("/", handle_index)
app.router.add_get("/sub/{sub_id}", handle_sub)

if __name__ == "__main__":
    print(f"Starting sub_server on port 8080. DB: {DB_PATH}")
    web.run_app(app, host="0.0.0.0", port=8080)
