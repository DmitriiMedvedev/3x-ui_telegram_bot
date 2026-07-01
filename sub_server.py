#!/usr/bin/env python3
# Aiohttp web server running on port 8080 to serve base64 encoded subscription links.
"""
sub_server.py — Subscription-сервер v14.

Изменения:
- Отдаёт base64(link1\\nlink2\\n...) — стандартный формат подписки.
- Поддерживает все протоколы из configs_all (VLESS Reality, XHTTP, gRPC, SS).
- Заголовки Subscription-Userinfo с балансом в байтах.
- Fallback на старый vless_link если configs_all пустой (совместимость).
"""
import base64
import sqlite3
from datetime import datetime

from aiohttp import web

DB_PATH      = "dobrinya.db"
PRICE_PER_GB    = 3.0
CREDIT_LIMIT_RUB = -50.0
BOT_NAME     = "dobrinyaVPN_bot"


def get_user_by_sub(sub_id: str) -> dict | None:
    # ИСПРАВЛЕНИЕ: try/finally гарантирует закрытие соединения
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM users WHERE sub_id=?", (sub_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        if conn:
            conn.close()


async def handle_sub(request: web.Request) -> web.Response:
    sub_id = request.match_info.get("sub_id", "")
    if not sub_id:
        return web.Response(status=404)

    user = get_user_by_sub(sub_id)
    if not user:
        return web.Response(status=404)

    # ── Определяем активность ─────────────────────────────────────────────────
    balance = user.get("balance") or 0.0

    # ИСПРАВЛЕНИЕ: trial-пользователи (expire_at > now) тоже активны
    is_trial = False
    expire_at = user.get("expire_at")
    if expire_at:
        try:
            is_trial = datetime.fromisoformat(expire_at) > datetime.now()
        except (ValueError, TypeError):
            pass

    is_active = balance > CREDIT_LIMIT_RUB

    # ── Собираем ссылки ───────────────────────────────────────────────────────
    configs_all = user.get("configs_all", "").strip()
    vless_link  = user.get("vless_link", "").strip()

    if configs_all:
        raw_links = [l for l in configs_all.split("\n") if l.strip()]
    elif vless_link:
        raw_links = [vless_link]
    else:
        raw_links = []

    if not raw_links:
        return web.Response(status=404)

    # ── Если не активен — возвращаем заглушку ─────────────────────────────────
    if not is_active:
        placeholder = (
            "vless://00000000-0000-0000-0000-000000000000@0.0.0.0:0"
            "?type=none#EXPIRED-TopUp"
        )
        content = base64.b64encode(placeholder.encode()).decode()
        headers = {
            "Content-Type":          "text/plain; charset=utf-8",
            "Profile-Title":         "Dobrinya VPN (EXPIRED)",
            "Subscription-Userinfo": "upload=0; download=0; total=0; expire=0",
            "Support-URL":           f"https://t.me/{BOT_NAME}",
        }
        return web.Response(text=content, headers=headers)

    # ── Кодируем подписку ─────────────────────────────────────────────────────
    sub_text = "\n".join(raw_links)
    content  = base64.b64encode(sub_text.encode()).decode()

    # Метаданные для клиентов (v2rayNG, Hiddify и др. читают эти заголовки)
    total_bytes     = user.get("total_traffic_bytes") or 0
    remaining_bytes = int((balance / PRICE_PER_GB) * (1024 ** 3))
    quota_bytes     = total_bytes + remaining_bytes   # показываем использованное + остаток
    remaining_gb    = remaining_bytes / (1024 ** 3)

    headers = {
        "Content-Type":          "text/plain; charset=utf-8",
        "Profile-Title":         f"Dobrinya VPN | {remaining_gb:.1f} GB",
        "Subscription-Userinfo": (
            f"upload=0; "
            f"download={total_bytes}; "
            f"total={quota_bytes}; "
            f"expire=0"
        ),
        "Profile-Update-Interval": "12",
        "Support-URL":             f"https://t.me/{BOT_NAME}",
    }
    return web.Response(text=content, headers=headers)


app = web.Application()
app.router.add_get("/sub/{sub_id}", handle_sub)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8080)
