#!/usr/bin/env python3
"""
sub_server.py — Subscription-сервер v16.5 (Final Robust Version).
"""
import base64
import sqlite3
import json
import urllib.parse
import os
import uuid
import traceback
from aiohttp import web

# Определение пути к БД
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "dobrinya.db")
if not os.path.exists(DB_PATH):
    DB_PATH = "/root/dobrinya_bot/3x-ui_telegram_bot/dobrinya.db"

PRICE_PER_GB = 3.0
CREDIT_LIMIT_RUB = -50.0
BOT_NAME = "dobrinyaVPN_bot"

def get_db_conn():
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database not found at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_user_by_sub(sub_id: str) -> dict | None:
    conn = None
    try:
        conn = get_db_conn()
        row = conn.execute("SELECT * FROM users WHERE sub_id=?", (sub_id,)).fetchone()
        if row: return dict(row)
        if sub_id.isdigit():
            row = conn.execute("SELECT * FROM users WHERE tg_id=?", (int(sub_id),)).fetchone()
            return dict(row) if row else None
        return None
    except: return None
    finally:
        if conn: conn.close()

def get_active_panels() -> list[dict]:
    conn = None
    try:
        conn = get_db_conn()
        rows = conn.execute("SELECT * FROM panels").fetchall()
        res = []
        for r in rows:
            p = dict(r)
            for k in ['inbounds', 'inbound_ids']:
                val = p.get(k)
                if isinstance(val, str):
                    try: p[k] = json.loads(val or ('{}' if k=='inbounds' else '[]'))
                    except: p[k] = {} if k=='inbounds' else []
                if p[k] is None: p[k] = {} if k=='inbounds' else []
            res.append(p)
        return res
    except: return []
    finally:
        if conn: conn.close()

def make_link(u_uuid, email, panel, cfg, iid):
    try:
        prot = str(cfg.get("protocol", "")).lower()
        if prot == "shadowsocks": prot = "ss"
        host = cfg.get("host") or panel.get("server_host") or "127.0.0.1"
        port = cfg.get("port", 443)
        label = urllib.parse.quote(f"{email}-{panel.get('name')}-{cfg.get('label', iid)}")

        if prot == "vless":
            net, sec = cfg.get("network", "tcp"), cfg.get("security", "none")
            params = {"type": net, "security": sec}
            if sec == "reality":
                params.update({"pbk": cfg.get("public_key", ""), "fp": cfg.get("fingerprint", "chrome"), "sni": cfg.get("sni", ""), "sid": cfg.get("short_id", "")})
                if cfg.get("flow"): params["flow"] = cfg["flow"]
            elif sec == "tls": params["sni"] = cfg.get("sni", "")
            if net == "xhttp":
                params.update({"path": cfg.get("path", "/"), "mode": cfg.get("xhttp_mode", "auto")})
                if cfg.get("ws_host"): params["host"] = cfg["ws_host"]
            elif net == "grpc": params.update({"serviceName": cfg.get("grpc_service", "grpc"), "mode": "gun"})
            elif net == "ws":
                params["path"] = cfg.get("path", "/")
                if cfg.get("ws_host"): params["host"] = cfg["ws_host"]
            query = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items() if v)
            return f"vless://{u_uuid}@{host}:{port}?{query}#{label}"
        elif prot == "ss":
            m, pwd = cfg.get("method"), cfg.get("password")
            if not m or not pwd: return None
            cred = base64.b64encode(f"{m}:{pwd}".encode()).decode()
            return f"ss://{cred}@{host}:{port}#{label}"
        elif prot == "trojan":
            return f"trojan://{u_uuid}@{host}:{port}?security=tls&sni={cfg.get('sni', '')}#{label}"
        return None
    except: return None

async def handle_sub(request: web.Request) -> web.Response:
    try:
        sub_id = request.match_info.get("sub_id", "")
        user = get_user_by_sub(sub_id)
        if not user: return web.Response(text=f"ERROR: Sub ID '{sub_id}' not found.", status=404)

        u_uuid = user.get("vless_uuid")
        email = f"user_{user['tg_id']}"
        panels = get_active_panels()
        links, diag = [], [f"Diagnostic v16.5 for {email}:", f"UUID: {u_uuid}", f"Panels: {len(panels)}"]

        for p in panels:
            ibs = p.get("inbounds") or {}
            p_links = 0
            for iid, cfg in ibs.items():
                l = make_link(u_uuid, email, p, cfg, iid)
                if l:
                    links.append(l)
                    p_links += 1
            diag.append(f" - Server '{p['name']}': {len(ibs)} inbounds, {p_links} links generated.")

        if not links:
            return web.Response(text="ERROR: No active configurations.\n\n" + "\n".join(diag), status=404)

        content = base64.b64encode("\n".join(links).encode()).decode()
        return web.Response(text=content, headers={"Content-Type": "text/plain; charset=utf-8", "Profile-Title": f"VPN | {user.get('balance',0):.1f} RUB"})
    except Exception as e: return web.Response(text=f"FATAL: {str(e)}", status=500)

async def handle_index(request):
    db_exists = os.path.exists(DB_PATH)
    return web.Response(text=f"Subscription Server v16.5 is active.\nDB Path: {DB_PATH}\nStatus: {'✅ OK' if db_exists else '❌ NOT FOUND'}")

app = web.Application()
app.router.add_get("/", handle_index)
app.router.add_get("/sub/{sub_id}", handle_sub)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8080)
