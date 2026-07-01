#!/usr/bin/env python3
"""
sub_server.py — Multi-Panel Subscription Proxy v17.0.
Fetches native subscriptions from 3X-UI panels and aggregates them.
Ensures 100% compatibility with 3X-UI standards.
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

# Определение пути к БД
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "dobrinya.db")
if not os.path.exists(DB_PATH):
    DB_PATH = "/root/dobrinya_bot/3x-ui_telegram_bot/dobrinya.db"

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
    except: return None
    finally:
        if conn: conn.close()

def get_active_panels() -> list[dict]:
    conn = None
    try:
        conn = get_db_conn()
        rows = conn.execute("SELECT * FROM panels").fetchall()
        return [dict(r) for r in rows]
    except: return []
    finally:
        if conn: conn.close()

async def fetch_panel_sub(session: aiohttp.ClientSession, panel: dict, sub_id: str) -> str:
    """Запрашивает нативную подписку с конкретной панели."""
    host = panel.get('host', "").strip()
    if not host: return ""

    # Пытаемся угадать URL подписки. Обычно это /sub/{sub_id}
    # Но в некоторых версиях может быть /sub/v2/{sub_id}
    url = f"https://{host}:{panel['port']}/sub/{sub_id}"

    try:
        async with session.get(url, timeout=5, ssl=False) as resp:
            if resp.status == 200:
                return await resp.text()
            else:
                logger.warning(f"Panel {panel['name']} returned {resp.status} for {url}")
    except Exception as e:
        logger.error(f"Error fetching from {panel['name']}: {e}")
    return ""

def decode_sub(content: str) -> list[str]:
    """Декодирует base64 подписку в список ссылок."""
    try:
        if not content.strip(): return []
        # Добавляем padding если нужно
        missing_padding = len(content) % 4
        if missing_padding: content += '=' * (4 - missing_padding)

        decoded = base64.b64decode(content).decode('utf-8', errors='ignore')
        return [l.strip() for l in decoded.split('\n') if l.strip()]
    except Exception as e:
        logger.error(f"Decode error: {e}")
        return []

async def handle_sub(request: web.Request) -> web.Response:
    try:
        sub_id = request.match_info.get("sub_id", "")
        user = get_user_by_sub(sub_id)
        if not user: return web.Response(text=f"ERROR: Sub ID '{sub_id}' not found.", status=404)

        panels = get_active_panels()
        all_links = []

        async with aiohttp.ClientSession() as session:
            tasks = [fetch_panel_sub(session, p, user['sub_id']) for p in panels]
            results = await asyncio.gather(*tasks)

            for res in results:
                if res:
                    all_links.extend(decode_sub(res))

        if not all_links:
            # Если не удалось получить с панелей, пробуем сгенерировать сами (резервный вариант)
            return web.Response(text="ERROR: No configurations found on remote panels.", status=404)

        # Удаляем дубликаты
        unique_links = list(dict.fromkeys(all_links))

        content = base64.b64encode("\n".join(unique_links).encode()).decode()
        return web.Response(
            text=content,
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Profile-Title": f"VPN | {user.get('balance',0):.1f} RUB",
                "Subscription-Userinfo": f"upload=0; download=0; total=0; expire=0" # Можно добавить реальные данные если нужно
            }
        )
    except Exception as e:
        logger.exception("Fatal error in sub_server")
        return web.Response(text=f"FATAL: {str(e)}", status=500)

async def handle_index(request):
    db_exists = os.path.exists(DB_PATH)
    return web.Response(text=f"Subscription Server v17.0 (Proxy Mode) is active.\nDB Path: {DB_PATH}\nStatus: {'✅ OK' if db_exists else '❌ NOT FOUND'}")

app = web.Application()
app.router.add_get("/", handle_index)
app.router.add_get("/sub/{sub_id}", handle_sub)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8080)
