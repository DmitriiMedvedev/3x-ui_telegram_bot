#!/usr/bin/env python3
"""
sub_server.py — Multi-Panel Subscription Proxy & Generator v17.1.
Aggregates native subscriptions from 3X-UI panels.
Fallbacks to manual generation if panels are unreachable.
"""
import base64
import sqlite3
import json
import urllib.parse
import os
import asyncio
import aiohttp
import logging
from aiohttp import web

from config import DB_PATH

# Standard User-Agent to avoid blocks
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    except Exception as e:
        logger.warning(f"Exception caught: {e}")
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
            # Декодируем inbounds из JSON
            try: p['inbounds'] = json.loads(p.get('inbounds') or '{}')
            except Exception as e:
                logger.warning(f"Exception caught: {e}")
                p["inbounds"] = {}
            res.append(p)
        return res
    except Exception as e:
        logger.warning(f"Exception caught: {e}")
        return []
    finally:
        if conn: conn.close()

async def fetch_panel_sub(session: aiohttp.ClientSession, panel: dict, sub_id: str) -> str:
    """Запрашивает нативную подписку с конкретной панели, перебирая варианты URL."""
    # Перебираем как IP/хост API, так и публичный host сервера
    hosts = [panel.get('host', "").strip().strip(".")]
    if panel.get('server_host'):
        hosts.append(panel.get('server_host').strip())

    # Варианты путей
    base_paths = ["", panel.get('path', "")]
    sub_paths = ["/sub", "/sub/v2"]

    # If panel has API Token, use it as fallback auth for sub endpoint if panel restricts it
    headers = dict(session._default_headers) if session._default_headers else {}
    if panel.get("api_token"):
        headers["Authorization"] = f"Bearer {panel['api_token']}"

    for host in hosts:
        if not host: continue
        for proto in ["https", "http"]:
            for bp in base_paths:
                bp = bp.strip("/")
                if bp: bp = "/" + bp
                for sp in sub_paths:
                    url = f"{proto}://{host}:{panel['port']}{bp}{sp}/{sub_id}"
                    try:
                        async with session.get(url, headers=headers, timeout=3.0) as resp:
                            if resp.status == 200:
                                text = await resp.text()
                                if text and len(text) > 10:
                                    logger.info(f"Success fetching sub from {panel['name']} via {url}")
                                    return text
                            else:
                                logger.debug(f"Sub fetch 404/Error from {panel['name']} via {url}: {resp.status}")
                    except Exception as e:
                        logger.debug(f"Sub fetch exception from {panel['name']} via {url}: {e}")
    return ""

def decode_sub(content: str) -> list[str]:
    """Декодирует base64 или plain-text подписку в список ссылок."""
    if not content or not content.strip(): return []
    try:
        # Пробуем декодировать как base64
        data = content.strip()
        missing_padding = len(data) % 4
        if missing_padding: data += '=' * (4 - missing_padding)
        decoded = base64.b64decode(data).decode('utf-8', errors='ignore')
        return [l.strip() for l in decoded.split('\n') if l.strip()]
    except Exception as e:
        logger.warning(f"Exception caught: {e}")
        # Если не base64, возможно это просто список ссылок
        return [l.strip() for l in content.split('\n') if l.strip() and "://" in l]

def make_fallback_link(u_uuid, email, panel, cfg, iid):
    """Генерация ссылки вручную, если проксирование не сработало."""
    try:
        prot = str(cfg.get("protocol", "")).lower()
        if prot == "shadowsocks": prot = "ss"
        host = cfg.get("host") or panel.get("server_host") or panel.get("host") or "127.0.0.1"
        port = cfg.get("port", 443)
        label = urllib.parse.quote(f"{email}-{panel.get('name')}-{cfg.get('label', iid)}")

        if prot == "vless":
            net, sec = cfg.get("network", "tcp"), cfg.get("security", "none")
            params = {"type": net, "security": sec}
            if sec == "reality":
                params.update({
                    "pbk": cfg.get("public_key", ""),
                    "fp": cfg.get("fingerprint", "chrome"),
                    "sni": cfg.get("sni", ""),
                    "sid": cfg.get("short_id", ""),
                    "spx": cfg.get("spiderX", "/")
                })
                if cfg.get("flow"): params["flow"] = cfg["flow"]
            elif sec == "tls":
                params["sni"] = cfg.get("sni", "")
                if cfg.get("flow"): params["flow"] = cfg["flow"]

            if net == "xhttp":
                params.update({"path": cfg.get("path", "/"), "mode": cfg.get("xhttp_mode", "auto")})
                if cfg.get("ws_host"): params["host"] = cfg["ws_host"]
            elif net == "grpc":
                params.update({"serviceName": cfg.get("grpc_service", ""), "mode": "gun"})
            elif net == "ws":
                params["path"] = cfg.get("path", "/")
                if cfg.get("ws_host"): params["host"] = cfg["ws_host"]
            elif net == "tcp":
                if cfg.get("type") == "http":
                    params["type"] = "http"
                    if cfg.get("path"): params["path"] = cfg["path"]
                    if cfg.get("ws_host"): params["host"] = cfg["ws_host"]

            # Remove empty parameters to clean up the URL
            params = {k: v for k, v in params.items() if v}

            query = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items())
            return f"vless://{u_uuid}@{host}:{port}?{query}#{label}"
        elif prot == "ss":
            m, pwd = cfg.get("method"), cfg.get("password")
            if not (m and pwd): return None
            cred = base64.b64encode(f"{m}:{pwd}".encode()).decode()
            return f"ss://{cred}@{host}:{port}#{label}"
        elif prot == "trojan":
            return f"trojan://{u_uuid}@{host}:{port}?security=tls&sni={cfg.get('sni', '')}#{label}"
    except Exception as e: logger.warning(f"Fallback generation error: {e}")
    return None

async def handle_sub(request: web.Request) -> web.Response:
    try:
        sub_id = request.match_info.get("sub_id", "")
        user = get_user_by_sub(sub_id)
        if not user: return web.Response(text=f"ERROR: User with Sub ID '{sub_id}' not found in Database.", status=404)

        u_uuid, email = user.get("vless_uuid"), f"user_{user['tg_id']}"
        panels = get_active_panels()
        all_links = []

        # 1. Пытаемся получить нативные ссылки (Proxy mode)
        diag_log = [f"Sub Request: {sub_id}", f"User: {email}", f"UUID: {u_uuid}", f"Panels count: {len(panels)}"]

        async with aiohttp.ClientSession(headers=HEADERS) as session:
            tasks = [fetch_panel_sub(session, p, user['sub_id']) for p in panels]
            results = await asyncio.gather(*tasks)
            for i, res in enumerate(results):
                p = panels[i]
                if res:
                    links = decode_sub(res)
                    all_links.extend(links)
                    diag_log.append(f" - Panel '{p['name']}': Success, got {len(links)} links.")
                else:
                    diag_log.append(f" - Panel '{p['name']}': Failed (unreachable or 404).")

        # 2. Если проксирование не дало результатов, генерируем сами (Fallback mode)
        if not all_links:
            diag_log.append("Proxy failed for all panels. Trying fallback generation...")
            for p in panels:
                ibs = p.get("inbounds") or {}
                p_links = 0
                for iid, cfg in ibs.items():
                    link = make_fallback_link(u_uuid, email, p, cfg, iid)
                    if link:
                        all_links.append(link)
                        p_links += 1
                diag_log.append(f" - Fallback '{p['name']}': generated {p_links} links.")

        if not all_links:
            msg = "ERROR: No active configurations found.\n\nDIAGNOSTIC LOG:\n" + "\n".join(diag_log)
            logger.error(msg)
            return web.Response(text=msg, status=404)

        unique_links = list(dict.fromkeys(all_links))
        content = base64.b64encode("\n".join(unique_links).encode()).decode()

        return web.Response(
            text=content,
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Profile-Title": f"VPN | {user.get('balance',0):.1f} RUB",
                "Subscription-Userinfo": "upload=0; download=0; total=0; expire=0"
            }
        )
    except Exception as e:
        logger.exception("Fatal error in sub_server")
        return web.Response(text=f"FATAL ERROR: {str(e)}", status=500)

async def handle_index(request):
    return web.Response(text=f"Dobrinya Subscription Server v17.1 is active.\nDB: {'✅' if os.path.exists(DB_PATH) else '❌ NOT FOUND'}")

app = web.Application()
app.router.add_get("/", handle_index)
app.router.add_get("/sub/{sub_id}", handle_sub)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8080)  # nosec B104
