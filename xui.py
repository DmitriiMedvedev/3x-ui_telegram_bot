# 3X-UI API wrapper for creating clients, retrieving traffic stats, and managing inbounds.
"""
xui.py — Interaction with 3X-UI API v14.1 with multi-panel support.
"""
import json
import asyncio
import uuid
import logging
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple

import aiohttp

from config import SUB_BASE_URL
from database import get_all_panels

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

async def _login(session: aiohttp.ClientSession, panel: Dict) -> bool:
    base_url = f"https://{panel['host']}:{panel['port']}"
    path = panel['path']
    try:
        async with session.post(
            f"{base_url}{path}/login",
            data={"username": panel['login'], "password": panel['password']},
        ) as r:
            if r.status != 200:
                logger.error(f"3X-UI login HTTP {r.status} on {panel['name']}")
                return False
            text = await r.text()
            try:
                res = json.loads(text)
            except json.JSONDecodeError:
                logger.error(f"3X-UI login bad JSON on {panel['name']}: {text[:200]}")
                return False
            ok = res.get("success", False)
            if not ok:
                logger.error(f"3X-UI login failed on {panel['name']}: {res}")
            return ok
    except Exception as e:
        logger.error(f"3X-UI login error on {panel['name']}: {e}")
        return False

class XUIClient:
    """Контекстный менеджер для батч-операций (использует одну сессию на каждую панель)."""
    def __init__(self):
        self.sessions = {}
        self.loggedIn = set()

    async def __aenter__(self) -> "XUIClient":
        panels = await get_all_panels()
        self.panels_list = panels
        for idx, panel in enumerate(panels):
            self.sessions[idx] = _new_session()
            if await _login(self.sessions[idx], panel):
                self.loggedIn.add(idx)
        return self

    async def __aexit__(self, *_):
        for sess in self.sessions.values():
            await sess.close()

    async def post(self, panel_idx: int, endpoint: str, payload: dict) -> dict | None:
        if panel_idx not in self.loggedIn:
            return None
        panel = self.panels_list[panel_idx]
        base_url = f"https://{panel['host']}:{panel['port']}"
        try:
            async with self.sessions[panel_idx].post(
                f"{base_url}{panel['path']}{endpoint}", data=payload
            ) as r:
                text = await r.text()
                if not text.strip():
                    return {"success": True}
                if r.status == 200:
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return None
                return None
        except Exception as e:
            logger.error(f"XUIClient POST {endpoint} on {panel['name']}: {e}")
        return None

async def _post_single(panel: dict, endpoint: str, payload: dict) -> dict | None:
    async with _new_session() as sess:
        if not await _login(sess, panel):
            return None
        base_url = f"https://{panel['host']}:{panel['port']}"
        try:
            async with sess.post(
                f"{base_url}{panel['path']}{endpoint}", data=payload
            ) as r:
                text = await r.text()
                if not text.strip():
                    return {"success": True}
                if r.status == 200:
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return None
                return None
        except Exception as e:
            logger.error(f"xui POST {endpoint} on {panel['name']}: {e}")
            return None

async def _get_single(panel: dict, endpoint: str) -> dict | None:
    async with _new_session() as sess:
        if not await _login(sess, panel):
            return None
        base_url = f"https://{panel['host']}:{panel['port']}"
        try:
            async with sess.get(f"{base_url}{panel['path']}{endpoint}") as r:
                if r.status == 200:
                    text = await r.text()
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return None
                return None
        except Exception as e:
            logger.error(f"xui GET {endpoint} on {panel['name']}: {e}")
            return None


def _build_client_obj(
    client_uuid: str, email: str, sub_id: str, inbound_id: int, panel: dict, exp_ms: int = 0
) -> dict:
    cfg = panel.get("inbounds", {}).get(inbound_id, {})
    return {
        "id":         client_uuid,
        "email":      email,
        "enable":     True,
        "flow":       cfg.get("flow", ""),
        "limitIp":    0,
        "totalGB":    0,
        "alterId":    0,
        "tgId":       "",
        "expiryTime": exp_ms,
        "subId":      sub_id,
    }

async def _add_to_inbound(panel: dict, inbound_id: int, client_obj: dict) -> bool:
    payload = {
        "id":       str(inbound_id),
        "settings": json.dumps({"clients": [client_obj]})
    }
    for attempt in range(_ADD_RETRIES):
        res = await _post_single(panel, "/panel/api/inbounds/addClient", payload)
        if res and res.get("success"):
            logger.info(
                f"addClient inbound={inbound_id} email={client_obj['email']} on {panel['name']} "
                f"✓ (попытка {attempt + 1})"
            )
            return True
        if attempt < _ADD_RETRIES - 1:
            delay = _RETRY_DELAYS[attempt]
            await asyncio.sleep(delay)
    logger.error(
        f"addClient inbound={inbound_id} email={client_obj['email']} on {panel['name']}: "
        f"все {_ADD_RETRIES} попытки неудачны"
    )
    return False

# Adds a client to the 3X-UI panel and generates links.
async def add_client(email: str, expire_days: int = 0) -> dict | None:
    client_uuid = str(uuid.uuid4())
    sub_id      = uuid.uuid4().hex
    exp_ms      = (
        int((datetime.now() + timedelta(days=expire_days)).timestamp() * 1000)
        if expire_days > 0 else 0
    )

    any_success = False

    panels = await get_all_panels()
    for panel in panels:
        results = []
        for iid in panel.get("inbound_ids", []):
            obj = _build_client_obj(client_uuid, email, sub_id, iid, panel, exp_ms)
            ok  = await _add_to_inbound(panel, iid, obj)
            results.append(ok)
        if any(results):
            any_success = True

    if not any_success:
        logger.error(f"add_client {email}: failed on all panels")
        return None

    return {
        "uuid":    client_uuid,
        "sub_id":  sub_id,
        "configs": await make_configs(client_uuid, email),
    }

# Enables or disables a client in the 3X-UI panel.
async def toggle_client(
    email: str,
    client_uuid: str,
    enable: bool,
    sub_id: str = "",
) -> bool:
    ok_count = 0
    panels = await get_all_panels()
    for panel in panels:
        for iid in panel.get("inbound_ids", []):
            cfg = panel.get("inbounds", {}).get(iid, {})
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
            res = await _post_single(panel, f"/panel/api/inbounds/updateClient/{client_uuid}", payload)
            if res and res.get("success"):
                ok_count += 1
    return ok_count > 0

async def bulk_toggle(
    clients: list[tuple[str, str, bool, str]]
) -> int:
    if not clients:
        return 0
    ok_count = 0
    try:
        async with XUIClient() as xui:
            for email, client_uuid, enable, sub_id in clients:
                for idx, panel in enumerate(xui.panels_list):
                    for iid in panel.get("inbound_ids", []):
                        cfg = panel.get("inbounds", {}).get(iid, {})
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
                        res = await xui.post(idx, f"/panel/api/inbounds/updateClient/{client_uuid}", payload)
                        if res and res.get("success"):
                            ok_count += 1
    except RuntimeError as e:
        logger.error(f"bulk_toggle: {e}")
    return ok_count

# Retrieves upload/download statistics for a given client email.
async def get_client_traffic(email: str) -> dict | None:
    total_up = 0
    total_down = 0
    enable = True
    expiry_time = 0
    found = False

    panels = await get_all_panels()
    for panel in panels:
        res = await _get_single(panel, f"/panel/api/inbounds/getClientTraffics/{email}")
        if not (res and res.get("success") and res.get("obj")):
            continue
        obj = res["obj"]
        found = True
        if isinstance(obj, list):
            if not obj:
                continue
            total_up    += sum(o.get("up",   0) for o in obj)
            total_down  += sum(o.get("down", 0) for o in obj)
            enable      = enable and any(o.get("enable", True) for o in obj)
            if obj[0].get("expiryTime", 0) > expiry_time:
                expiry_time = obj[0].get("expiryTime", 0)
        else:
            total_up    += obj.get("up",   0)
            total_down  += obj.get("down", 0)
            enable      = enable and obj.get("enable", True)
            if obj.get("expiryTime", 0) > expiry_time:
                expiry_time = obj.get("expiryTime", 0)

    if not found:
        return None
    return {"up": total_up, "down": total_down, "total": total_up + total_down,
            "enable": enable, "expiryTime": expiry_time}

async def get_traffic() -> dict[str, int] | None:
    result: dict[str, int] = {}
    found_any = False
    panels = await get_all_panels()
    for panel in panels:
        res = await _get_single(panel, "/panel/api/inbounds/list")
        if not res or not res.get("success"):
            logger.error(f"get_traffic failed on {panel['name']}: {res}")
            continue

        found_any = True
        billing_ids = set(panel.get("billing_inbound_ids", []))
        for inbound in (res.get("obj") or []):
            if inbound.get("id") not in billing_ids:
                continue
            for cs in (inbound.get("clientStats") or []):
                email         = cs.get("email", "")
                total         = cs.get("up", 0) + cs.get("down", 0)
                result[email] = result.get(email, 0) + total

    if not found_any:
        return None
    return result

# ── Link generation ────────────────────────────────────────────────────────────

def make_vless_link(client_uuid: str, email: str, panel: dict, inbound_id: int) -> str:
    cfg     = panel.get("inbounds", {}).get(inbound_id, {})
    host    = cfg.get("host", panel.get("server_host", "127.0.0.1"))
    port    = cfg.get("port", 443)
    network = cfg.get("network", "tcp")
    sec     = cfg.get("security", "none")
    label   = urllib.parse.quote(f"{email}-{panel.get('name', 'Server')}-{cfg.get('label', str(inbound_id))}")

    params: dict[str, str] = {"type": network, "security": sec}

    if sec == "reality":
        params["pbk"] = cfg.get("public_key", "")
        params["fp"]  = cfg.get("fingerprint", "chrome")
        params["sni"] = cfg.get("sni", "")
        params["sid"] = cfg.get("short_id", "")
        if cfg.get("flow"):
            params["flow"] = cfg["flow"]

    elif sec == "tls":
        params["sni"] = cfg.get("sni", "")

    if network == "xhttp":
        params["path"] = cfg.get("path", "/")
        params["mode"] = cfg.get("xhttp_mode", "auto")
    elif network == "grpc":
        params["serviceName"] = cfg.get("grpc_service", "grpc")
        params["mode"]        = "gun"
    elif network == "ws":
        params["path"] = cfg.get("path", "/")
        if cfg.get("ws_host"):
            params["host"] = cfg["ws_host"]

    query = "&".join(
        f"{k}={urllib.parse.quote(str(v), safe='')}"
        for k, v in params.items()
        if v
    )
    return f"vless://{client_uuid}@{host}:{port}?{query}#{label}"


def make_ss_link(email: str, panel: dict) -> str:
    import base64
    for iid, cfg in panel.get("inbounds", {}).items():
        if cfg.get("protocol") == "ss":
            method   = cfg.get("method", "chacha20-poly1305")
            password = cfg.get("password", "")
            host     = cfg.get("host", panel.get("server_host", "127.0.0.1"))
            port     = cfg.get("port", 8388)
            if not password:
                return ""
            label = urllib.parse.quote(f"{email}-{panel.get('name', 'Server')}-SS")
            cred  = base64.b64encode(f"{method}:{password}".encode()).decode()
            return f"ss://{cred}@{host}:{port}#{label}"
    return ""

async def make_configs(client_uuid: str, email: str) -> str:
    links: list[str] = []
    panels = await get_all_panels()
    for panel in panels:
        for iid in panel.get("inbound_ids", []):
            cfg = panel.get("inbounds", {}).get(iid, {})
            if cfg.get("protocol") == "vless":
                links.append(make_vless_link(client_uuid, email, panel, iid))
        ss = make_ss_link(email, panel)
        if ss:
            links.append(ss)
    return "\n".join(links)

def make_sub_url(sub_id: str) -> str:
    return f"{SUB_BASE_URL}/{sub_id}"

async def check_connection() -> bool:
    any_success = False
    panels = await get_all_panels()
    for panel in panels:
        async with _new_session() as sess:
            if await _login(sess, panel):
                any_success = True
            else:
                logger.error(f"Failed to connect to panel: {panel['name']}")
    return any_success
