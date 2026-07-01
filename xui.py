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
from typing import Dict

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
        connector=aiohttp.TCPConnector(ssl=False, use_dns_cache=False),
        cookie_jar=aiohttp.CookieJar(unsafe=True),
        timeout=aiohttp.ClientTimeout(total=20),
    )

async def _login(session: aiohttp.ClientSession, panel: Dict) -> bool:
    host = panel.get('host', "").strip()
    if not host: return False
    base_url = f"https://{host}:{panel['port']}"
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
            if panel.get("api_token"):
                self.loggedIn.add(idx)
            elif await _login(self.sessions[idx], panel):
                self.loggedIn.add(idx)
        return self

    async def __aexit__(self, *_):
        for sess in self.sessions.values():
            await sess.close()

    async def post(self, panel_idx: int, endpoint: str, payload: dict) -> dict | None:
        if panel_idx not in self.loggedIn:
            return None
        panel = self.panels_list[panel_idx]
        host = panel.get('host', "").strip()
        if not host: return None
        base_url = f"https://{host}:{panel['port']}"
        headers = {}
        if panel.get("api_token"):
            headers["Authorization"] = f"Bearer {panel['api_token']}"
        try:
            async with self.sessions[panel_idx].post(
                f"{base_url}{panel['path']}{endpoint}", data=payload, headers=headers
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
        headers = {}
        if panel.get("api_token"):
            headers["Authorization"] = f"Bearer {panel['api_token']}"
        else:
            if not await _login(sess, panel):
                return None

        host = panel.get('host', "").strip()
        if not host: return None
        base_url = f"https://{host}:{panel['port']}"
        try:
            async with sess.post(
                f"{base_url}{panel['path']}{endpoint}", data=payload, headers=headers
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
        headers = {}
        if panel.get("api_token"):
            headers["Authorization"] = f"Bearer {panel['api_token']}"
        else:
            if not await _login(sess, panel):
                return None

        host = panel.get('host', "").strip()
        if not host: return None
        base_url = f"https://{host}:{panel['port']}"
        try:
            async with sess.get(f"{base_url}{panel['path']}{endpoint}", headers=headers) as r:
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
    cfg = panel.get("inbounds", {}).get(str(inbound_id)) or panel.get("inbounds", {}).get(int(inbound_id)) or {}
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
        if res:
            if res.get("success") or "already exists" in str(res.get("msg", "")).lower():
                logger.info(f"addClient inbound={inbound_id} email={client_obj['email']} on {panel['name']} ✓")
                return True
        if attempt < _ADD_RETRIES - 1:
            await asyncio.sleep(_RETRY_DELAYS[attempt])
    return False

# Adds a client to all panels in the background.
async def add_client_background(email: str, client_uuid: str, sub_id: str, expire_days: int = 0):
    exp_ms = int((datetime.now() + timedelta(days=expire_days)).timestamp() * 1000) if expire_days > 0 else 0
    panels = await get_all_panels()
    for panel in panels:
        for iid in panel.get("inbound_ids", []):
            obj = _build_client_obj(client_uuid, email, sub_id, iid, panel, exp_ms)
            await _add_to_inbound(panel, iid, obj)

# Legacy helper (still used in some places)
async def add_client(email: str, expire_days: int = 0) -> dict | None:
    client_uuid = str(uuid.uuid4())
    sub_id      = uuid.uuid4().hex
    await add_client_background(email, client_uuid, sub_id, expire_days)
    return {
        "uuid":    client_uuid,
        "sub_id":  sub_id,
        "configs": await make_configs(client_uuid, email),
    }

# Enables or disables a client in the 3X-UI panel.
async def toggle_client(email: str, client_uuid: str, enable: bool, sub_id: str = "") -> bool:
    ok_count = 0
    panels = await get_all_panels()
    for panel in panels:
        for iid in panel.get("inbound_ids", []):
            cfg = panel.get("inbounds", {}).get(str(iid)) or panel.get("inbounds", {}).get(int(iid)) or {}
            client = {
                "id": client_uuid, "email": email, "enable": enable, "flow": cfg.get("flow", ""),
                "limitIp": 0, "totalGB": 0, "alterId": 0, "tgId": "", "expiryTime": 0, "subId": sub_id,
            }
            payload = {"id": str(iid), "settings": json.dumps({"clients": [client]})}
            res = await _post_single(panel, f"/panel/api/inbounds/updateClient/{client_uuid}", payload)
            if res and res.get("success"): ok_count += 1
    return ok_count > 0

async def bulk_toggle(clients: list[tuple[str, str, bool, str]]) -> int:
    if not clients: return 0
    ok_count = 0
    try:
        async with XUIClient() as xui:
            for email, client_uuid, enable, sub_id in clients:
                for idx, panel in enumerate(xui.panels_list):
                    for iid in panel.get("inbound_ids", []):
                        cfg = panel.get("inbounds", {}).get(str(iid)) or panel.get("inbounds", {}).get(int(iid)) or {}
                        client = {
                            "id": client_uuid, "email": email, "enable": enable, "flow": cfg.get("flow", ""),
                            "limitIp": 0, "totalGB": 0, "alterId": 0, "tgId": "", "expiryTime": 0, "subId": sub_id,
                        }
                        payload = {"id": str(iid), "settings": json.dumps({"clients": [client]})}
                        res = await xui.post(idx, f"/panel/api/inbounds/updateClient/{client_uuid}", payload)
                        if res and res.get("success"): ok_count += 1
    except Exception as e: logger.error(f"bulk_toggle: {e}")
    return ok_count

async def get_client_traffic(email: str) -> dict | None:
    total_up, total_down, found = 0, 0, False
    panels = await get_all_panels()
    for panel in panels:
        res = await _get_single(panel, f"/panel/api/inbounds/getClientTraffics/{email}")
        if not (res and res.get("success") and res.get("obj")): continue
        obj = res["obj"]
        found = True
        if isinstance(obj, list):
            total_up += sum(o.get("up", 0) for o in obj)
            total_down += sum(o.get("down", 0) for o in obj)
        else:
            total_up += obj.get("up", 0)
            total_down += obj.get("down", 0)
    return {"up": total_up, "down": total_down, "total": total_up + total_down} if found else None

async def get_traffic() -> dict[str, int] | None:
    result, found_any = {}, False
    panels = await get_all_panels()
    for panel in panels:
        host = panel.get('host', "").strip()
        if not host: continue
        res = await _get_single(panel, "/panel/api/inbounds/list")
        if not res or not res.get("success"): continue
        found_any = True
        billing_ids = set(map(str, panel.get("billing_inbound_ids", [])))
        for inbound in (res.get("obj") or []):
            if str(inbound.get("id")) not in billing_ids: continue
            for cs in (inbound.get("clientStats") or []):
                email = cs.get("email", "")
                result[email] = result.get(email, 0) + cs.get("up", 0) + cs.get("down", 0)
    return result if found_any else None

def make_vless_link(client_uuid: str, email: str, panel: dict, inbound_id: int) -> str:
    inbounds = panel.get("inbounds", {})
    cfg = inbounds.get(str(inbound_id)) or inbounds.get(int(inbound_id)) if str(inbound_id).isdigit() else inbounds.get(str(inbound_id))
    if not cfg: return ""
    host, port, network, sec = cfg.get("host") or panel.get("server_host", "127.0.0.1"), cfg.get("port", 443), cfg.get("network", "tcp"), cfg.get("security", "none")
    label = urllib.parse.quote(f"{email}-{panel.get('name', 'Server')}-{cfg.get('label', str(inbound_id))}")
    params = {"type": network, "security": sec}
    if sec == "reality":
        params.update({"pbk": cfg.get("public_key", ""), "fp": cfg.get("fingerprint", "chrome"), "sni": cfg.get("sni", ""), "sid": cfg.get("short_id", "")})
        if cfg.get("flow"): params["flow"] = cfg["flow"]
    elif sec == "tls": params["sni"] = cfg.get("sni", "")
    if network == "xhttp": params.update({"path": cfg.get("path", "/"), "mode": cfg.get("xhttp_mode", "auto")})
    elif network == "grpc": params.update({"serviceName": cfg.get("grpc_service", "grpc"), "mode": "gun"})
    elif network == "ws":
        params["path"] = cfg.get("path", "/")
        if cfg.get("ws_host"): params["host"] = cfg["ws_host"]
    query = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items() if v)
    return f"vless://{client_uuid}@{host}:{port}?{query}#{label}"

def make_ss_link(email: str, panel: dict) -> str:
    import base64
    for iid, cfg in panel.get("inbounds", {}).items():
        if str(cfg.get("protocol", "")).lower() in ["ss", "shadowsocks"]:
            method, password = cfg.get("method", "chacha20-poly1305"), cfg.get("password", "")
            host, port = panel.get("server_host", "127.0.0.1"), cfg.get("port", 8388)
            if not password: continue
            label = urllib.parse.quote(f"{email}-{panel.get('name', 'Server')}-SS")
            cred = base64.b64encode(f"{method}:{password}".encode()).decode()
            return f"ss://{cred}@{host}:{port}#{label}"
    return ""

async def make_configs(client_uuid: str, email: str) -> str:
    links, panels = [], await get_all_panels()
    for panel in panels:
        for iid in panel.get("inbound_ids", []):
            link = make_vless_link(client_uuid, email, panel, iid)
            if link: links.append(link)
        ss = make_ss_link(email, panel)
        if ss: links.append(ss)
    return "\n".join(links)

def make_sub_url(sub_id: str) -> str:
    return f"{SUB_BASE_URL}/{sub_id}"

async def check_connection() -> bool:
    any_success, panels = False, await get_all_panels()
    for panel in panels:
        async with _new_session() as sess:
            if panel.get("api_token"):
                res = await _get_single(panel, "/panel/api/inbounds/list")
                if res and res.get("success"): any_success = True
            elif await _login(sess, panel): any_success = True
    return any_success
