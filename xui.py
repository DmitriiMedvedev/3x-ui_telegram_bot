# 3X-UI API wrapper for creating clients, retrieving traffic stats, and managing inbounds.
"""
xui.py — Interaction with 3X-UI API v14.1.

Исправления:
  - [КРИТИЧЕСКИЙ] _post: json.loads(text) вместо r.json() после r.text()
    (aiohttp нельзя читать тело дважды — второй вызов вернул бы ошибку)
  - toggle_client теперь принимает sub_id и передаёт его в payload
  - Новый XUIClient — контекстный менеджер с единой сессией для батч-операций
  - bulk_toggle — все toggle в billing_tick через 1 логин вместо N×M
  - XHTTP-ссылка: добавлен параметр path
  - Убраны лишние импорты (base64 не нужен в этом модуле)
"""
import json
import asyncio
import uuid
import logging
import urllib.parse
from datetime import datetime, timedelta

import aiohttp

from config import (
    XUI_BASE_URL, XUI_PATH, XUI_LOGIN, XUI_PASSWORD,
    INBOUND_IDS, BILLING_INBOUND_IDS, INBOUND_CONFIGS,
    SERVER_HOST, SUB_BASE_URL,
)

logger = logging.getLogger(__name__)

_ADD_RETRIES  = 3
_RETRY_DELAYS = (0.5, 1.5, 3.0)


# ── Session helpers ────────────────────────────────────────────────────────────

def _new_session() -> aiohttp.ClientSession:
    """Сессия без SSL-верификации (self-signed на localhost)."""
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=False),
        cookie_jar=aiohttp.CookieJar(unsafe=True),
        timeout=aiohttp.ClientTimeout(total=20),
    )


async def _login(session: aiohttp.ClientSession) -> bool:
    try:
        async with session.post(
            f"{XUI_BASE_URL}{XUI_PATH}/login",
            data={"username": XUI_LOGIN, "password": XUI_PASSWORD},
        ) as r:
            if r.status != 200:
                logger.error(f"3X-UI login HTTP {r.status}")
                return False
            text = await r.text()
            try:
                res = json.loads(text)
            except json.JSONDecodeError:
                logger.error(f"3X-UI login bad JSON: {text[:200]}")
                return False
            ok = res.get("success", False)
            if not ok:
                logger.error(f"3X-UI login failed: {res}")
            return ok
    except Exception as e:
        logger.error(f"3X-UI login error: {e}")
        return False


async def _request_post(session: aiohttp.ClientSession,
                        endpoint: str, payload: dict) -> dict | None:
    """
    POST с уже открытой сессией.
    ИСПРАВЛЕНИЕ: читаем тело один раз через r.text(),
    затем парсим json.loads() — нельзя вызывать r.json() после r.text().
    """
    try:
        async with session.post(
            f"{XUI_BASE_URL}{XUI_PATH}{endpoint}", data=payload
        ) as r:
            text = await r.text()
            if not text.strip():
                # 3X-UI 2.6.x иногда возвращает пустое тело при успехе
                return {"success": True}
            if r.status == 200:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    logger.error(f"POST {endpoint} bad JSON: {text[:200]}")
                    return None
            logger.error(f"POST {endpoint} HTTP {r.status}: {text[:200]}")
    except Exception as e:
        logger.error(f"xui POST {endpoint}: {e}")
    return None


async def _request_get(session: aiohttp.ClientSession,
                       endpoint: str) -> dict | None:
    """GET с уже открытой сессией."""
    try:
        async with session.get(
            f"{XUI_BASE_URL}{XUI_PATH}{endpoint}"
        ) as r:
            if r.status != 200:
                logger.error(f"GET {endpoint} HTTP {r.status}")
                return None
            text = await r.text()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                logger.error(f"GET {endpoint} bad JSON: {text[:100]}")
                return None
    except Exception as e:
        logger.error(f"xui GET {endpoint}: {e}")
    return None


# Однократные вызовы (логин на каждый запрос — для одиночных операций)

async def _post(endpoint: str, payload: dict) -> dict | None:
    async with _new_session() as sess:
        if not await _login(sess):
            return None
        return await _request_post(sess, endpoint, payload)


async def _get(endpoint: str) -> dict | None:
    async with _new_session() as sess:
        if not await _login(sess):
            return None
        return await _request_get(sess, endpoint)


# ── XUIClient — контекстный менеджер для батч-операций ────────────────────────

class XUIClient:
    """
    Использует одну сессию + один логин для серии операций.
    Применяется в billing_tick: вместо N логинов — один.

    async with XUIClient() as client:
        data = await client.get("/panel/api/inbounds/list")
        await client.post("/panel/api/inbounds/addClient", payload)
    """
    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "XUIClient":
        self._session = _new_session()
        if not await _login(self._session):
            await self._session.close()
            raise RuntimeError("3X-UI: не удалось авторизоваться")
        return self

    async def __aexit__(self, *_):
        if self._session:
            await self._session.close()

    async def post(self, endpoint: str, payload: dict) -> dict | None:
        return await _request_post(self._session, endpoint, payload)

    async def get(self, endpoint: str) -> dict | None:
        return await _request_get(self._session, endpoint)


# ── Client helpers ─────────────────────────────────────────────────────────────

def _build_client_obj(
    client_uuid: str,
    email: str,
    sub_id: str,
    inbound_id: int,
    exp_ms: int,
) -> dict:
    cfg = INBOUND_CONFIGS.get(inbound_id, {})
    return {
        "id":         client_uuid,
        "email":      email,
        "enable":     True,
        "expiryTime": exp_ms,
        "flow":       cfg.get("flow", ""),
        "limitIp":    0,
        "totalGB":    0,
        "alterId":    0,
        "tgId":       "",
        "subId":      sub_id,
    }


async def _add_to_inbound(inbound_id: int, client_obj: dict) -> bool:
    """Добавляет клиента в один inbound с retry-логикой."""
    payload = {
        "id":       str(inbound_id),
        "settings": json.dumps({"clients": [client_obj]}),
    }
    email = client_obj.get("email", "?")
    for attempt in range(_ADD_RETRIES):
        res = await _post("/panel/api/inbounds/addClient", payload)
        if res and res.get("success"):
            logger.info(
                f"addClient inbound={inbound_id} email={email} "
                f"✓ (попытка {attempt + 1})"
            )
            return True
        if attempt < _ADD_RETRIES - 1:
            delay = _RETRY_DELAYS[attempt]
            logger.warning(
                f"addClient inbound={inbound_id} попытка {attempt + 1} "
                f"неудача: {res}. Повтор через {delay}с..."
            )
            await asyncio.sleep(delay)
    logger.error(
        f"addClient inbound={inbound_id} email={email}: "
        f"все {_ADD_RETRIES} попытки неудачны"
    )
    return False


# ── Public API ─────────────────────────────────────────────────────────────────

# Adds a client to the 3X-UI panel and generates links.
async def add_client(email: str, expire_days: int = 0) -> dict | None:
    """
    Создаёт клиента во всех INBOUND_IDS с одним UUID и subId.
    Возвращает {"uuid", "sub_id", "configs"} или None.
    """
    client_uuid = str(uuid.uuid4())
    sub_id      = uuid.uuid4().hex
    exp_ms      = (
        int((datetime.now() + timedelta(days=expire_days)).timestamp() * 1000)
        if expire_days > 0 else 0
    )
    results = []
    for iid in INBOUND_IDS:
        obj = _build_client_obj(client_uuid, email, sub_id, iid, exp_ms)
        ok  = await _add_to_inbound(iid, obj)
        results.append(ok)

    if not any(results):
        logger.error(f"add_client {email}: все inbound'ы провалились")
        return None

    logger.info(f"add_client {email}: {sum(results)}/{len(INBOUND_IDS)} inbound'ов ок")
    return {
        "uuid":    client_uuid,
        "sub_id":  sub_id,
        "configs": make_configs(client_uuid, email),
    }


# Enables or disables a client in the 3X-UI panel.
async def toggle_client(
    email: str,
    client_uuid: str,
    enable: bool,
    sub_id: str = "",
) -> bool:
    """
    Включает / отключает клиента во всех INBOUND_IDS.
    sub_id передаётся в payload чтобы не затирать нативную подписку 3X-UI.
    """
    ok_count = 0
    for iid in INBOUND_IDS:
        cfg = INBOUND_CONFIGS.get(iid, {})
        client = {
            "id":         client_uuid,
            "email":      email,
            "enable":     enable,
            "flow":       cfg.get("flow", ""),
            "limitIp":    0,
            "totalGB":    0,
            "alterId":    0,
            "tgId":       "",
            "expiryTime": 0,
            "subId":      sub_id,  # ИСПРАВЛЕНИЕ: передаём sub_id
        }
        payload = {
            "id":       str(iid),
            "settings": json.dumps({"clients": [client]}),
        }
        res = await _post(f"/panel/api/inbounds/updateClient/{client_uuid}", payload)
        if res and res.get("success"):
            ok_count += 1
    ok = ok_count > 0
    logger.info(
        f"toggle_client {email} enable={enable}: "
        f"{ok_count}/{len(INBOUND_IDS)} inbound'ов ок"
    )
    return ok


async def bulk_toggle(
    clients: list[tuple[str, str, bool, str]]
) -> int:
    """
    Батч-переключение клиентов в ОДНОЙ сессии (1 логин на весь список).
    clients: [(email, uuid, enable, sub_id), ...]
    Возвращает количество успешно обработанных inbound-операций.

    Используется billing_tick вместо N×M отдельных toggle_client.
    """
    if not clients:
        return 0

    ok_count = 0
    try:
        async with XUIClient() as xui:
            for email, client_uuid, enable, sub_id in clients:
                for iid in INBOUND_IDS:
                    cfg = INBOUND_CONFIGS.get(iid, {})
                    client = {
                        "id":         client_uuid,
                        "email":      email,
                        "enable":     enable,
                        "flow":       cfg.get("flow", ""),
                        "limitIp":    0,
                        "totalGB":    0,
                        "alterId":    0,
                        "tgId":       "",
                        "expiryTime": 0,
                        "subId":      sub_id,
                    }
                    payload = {
                        "id":       str(iid),
                        "settings": json.dumps({"clients": [client]}),
                    }
                    res = await xui.post(
                        f"/panel/api/inbounds/updateClient/{client_uuid}", payload
                    )
                    if res and res.get("success"):
                        ok_count += 1
    except RuntimeError as e:
        logger.error(f"bulk_toggle: {e}")

    logger.info(
        f"bulk_toggle: {len(clients)} клиентов, "
        f"{ok_count}/{len(clients) * len(INBOUND_IDS)} inbound-операций ок"
    )
    return ok_count


# Retrieves upload/download statistics for a given client email.
async def get_client_traffic(email: str) -> dict | None:
    """
    GET /panel/api/inbounds/getClientTraffics/{email}
    Возвращает {"up", "down", "total", "enable", "expiryTime"} или None.
    """
    res = await _get(f"/panel/api/inbounds/getClientTraffics/{email}")
    if not (res and res.get("success") and res.get("obj")):
        return None
    obj = res["obj"]
    if isinstance(obj, list):
        if not obj:
            return None
        up    = sum(o.get("up",   0) for o in obj)
        down  = sum(o.get("down", 0) for o in obj)
        en    = any(o.get("enable", True) for o in obj)
        exp_t = obj[0].get("expiryTime", 0)
    else:
        up    = obj.get("up",   0)
        down  = obj.get("down", 0)
        en    = obj.get("enable", True)
        exp_t = obj.get("expiryTime", 0)
    return {"up": up, "down": down, "total": up + down,
            "enable": en, "expiryTime": exp_t}


async def get_traffic() -> dict[str, int] | None:
    """
    Возвращает {email: total_bytes} по BILLING_INBOUND_IDS.
    Используется billing_loop.
    """
    res = await _get("/panel/api/inbounds/list")
    if not res or not res.get("success"):
        logger.error(f"get_traffic failed: {res}")
        return None

    billing_ids = set(BILLING_INBOUND_IDS)
    result: dict[str, int] = {}
    for inbound in (res.get("obj") or []):
        if inbound.get("id") not in billing_ids:
            continue
        for cs in (inbound.get("clientStats") or []):
            email         = cs.get("email", "")
            total         = cs.get("up", 0) + cs.get("down", 0)
            result[email] = result.get(email, 0) + total

    logger.info(f"get_traffic: {len(result)} клиентов в billing inbound'ах")
    return result


# ── Link generation ────────────────────────────────────────────────────────────

def make_vless_link(client_uuid: str, email: str, inbound_id: int) -> str:
    """Генерирует vless:// ссылку для конкретного inbound'а."""
    cfg     = INBOUND_CONFIGS.get(inbound_id, {})
    host    = cfg.get("host", SERVER_HOST)
    port    = cfg.get("port", 443)
    network = cfg.get("network", "tcp")
    sec     = cfg.get("security", "none")
    label   = urllib.parse.quote(f"{email}-{cfg.get('label', str(inbound_id))}")

    params: dict[str, str] = {"type": network, "security": sec}

    # ── Параметры специфичные для типа безопасности ───────────────────────────
    if sec == "reality":
        params["pbk"] = cfg.get("public_key", "")
        params["fp"]  = cfg.get("fingerprint", "chrome")  # из конфига, не хардкод
        params["sni"] = cfg.get("sni", "")
        params["sid"] = cfg.get("short_id", "")
        if cfg.get("flow"):
            params["flow"] = cfg["flow"]

    elif sec == "tls":
        params["sni"] = cfg.get("sni", "")

    # ── Параметры специфичные для транспорта (независимо от security) ─────────
    # Разделение security и network — ключевое: XHTTP работает поверх Reality
    # и поверх TLS, поэтому path нужен в обоих случаях.
    if network == "xhttp":
        params["path"] = cfg.get("path", "/")
        # mode: auto / packet-up / stream-up / stream-one (см. xhttpSettings.mode)
        params["mode"] = cfg.get("xhttp_mode", "auto")
    elif network == "grpc":
        params["serviceName"] = cfg.get("grpc_service", "grpc")
        params["mode"]        = "gun"
    elif network == "ws":
        params["path"] = cfg.get("path", "/")
        # ИСПРАВЛЕНИЕ: cfg["host"] — это IP сервера (для адресной части ссылки),
        # а не Host-заголовок транспорта. Используем отдельный ключ ws_host.
        if cfg.get("ws_host"):
            params["host"] = cfg["ws_host"]

    query = "&".join(
        f"{k}={urllib.parse.quote(str(v), safe='')}"
        for k, v in params.items()
        if v  # не добавляем пустые параметры
    )
    return f"vless://{client_uuid}@{host}:{port}?{query}#{label}"


def make_ss_link(email: str) -> str:
    """Генерирует ss:// ссылку для общего Shadowsocks inbound'а."""
    import base64
    for iid, cfg in INBOUND_CONFIGS.items():
        if cfg.get("protocol") == "ss":
            method   = cfg.get("method", "chacha20-poly1305")
            password = cfg.get("password", "")
            host     = cfg.get("host", SERVER_HOST)
            port     = cfg.get("port", 8388)
            if not password:
                logger.warning(f"SS inbound {iid}: password не задан в config.py")
                return ""
            label = urllib.parse.quote(f"{email}-SS")
            cred  = base64.b64encode(f"{method}:{password}".encode()).decode()
            return f"ss://{cred}@{host}:{port}#{label}"
    return ""


def make_configs(client_uuid: str, email: str) -> str:
    """Возвращает newline-разделённый список всех ссылок клиента."""
    links: list[str] = []
    for iid in INBOUND_IDS:
        cfg = INBOUND_CONFIGS.get(iid, {})
        if cfg.get("protocol") == "vless":
            links.append(make_vless_link(client_uuid, email, iid))
    ss = make_ss_link(email)
    if ss:
        links.append(ss)
    return "\n".join(links)


def make_sub_url(sub_id: str) -> str:
    return f"{SUB_BASE_URL}/{sub_id}"


# ── Utility ────────────────────────────────────────────────────────────────────

async def get_inbound(inbound_id: int) -> dict | None:
    res = await _get(f"/panel/api/inbounds/get/{inbound_id}")
    if res and res.get("success"):
        return res.get("obj")
    return None


async def check_connection() -> bool:
    async with _new_session() as sess:
        return await _login(sess)
