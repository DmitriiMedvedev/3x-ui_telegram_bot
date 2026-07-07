# Admin panel handlers for managing users, adding balances, banning, and checking stats.
"""
handlers/admin.py — Admin panel v17.2 (Robust Inbound Sync + Full UI).
"""
import asyncio
import logging
import secrets
import string
import json

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_IDS, SERVER_COST_RUB, CREDIT_LIMIT_RUB
from database import (
    get_all_users, get_user, update_user, add_balance,
    add_transaction, get_revenue_stats, create_promo, get_all_promos, add_panel, get_all_panels, update_panel_inbounds, get_panel,
)
import xui as XUI
from billing import billing_tick, fmt_bytes
from keyboards import kb_admin, kb_back, kb_new_user

router = Router()

class AddServerStates(StatesGroup):
    waiting_name = State()
    waiting_host = State()
    waiting_port = State()
    waiting_path = State()
    waiting_auth_method = State()
    waiting_login = State()
    waiting_password = State()
    waiting_api_token = State()
    waiting_server_host = State()

class AddInboundStates(StatesGroup):
    waiting_panel_selection = State()
    waiting_json = State()

logger = logging.getLogger(__name__)

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id): return
    users  = await get_all_users()
    active = sum(1 for u in users if u.get("vless_uuid") and u.get("balance", 0) > CREDIT_LIMIT_RUB)
    await message.answer(
        f"👑 <b>Панель администратора</b>\n\n"
        f"👥 Всего: {len(users)} | Активных: {active}\n"
        f"💳 Кредитный лимит: −{abs(CREDIT_LIMIT_RUB):.0f} ₽\n\n"
        f"/addpromo КОД РУБ USES\n"
        f"/reply ID текст\n"
        f"/broadcast текст\n"
        f"/addserver — подключить 3X-UI\n"
        f"/addinbound — добавить конфиг\n"
        f"/setbalance ID СУММА\n"
        f"/ban ID | /unban ID\n"
        f"/billing — принудительное списание\n"
        f"/debugtraffic — диагностика\n"
        f"/userinfo ID — инфо о юзере\n"
        f"/sync — синхронизировать всех юзеров\n"
        f"/status — статус серверов",
        parse_mode="HTML", reply_markup=kb_admin(),
    )

@router.message(Command("sync"))
async def cmd_sync(message: Message):
    if not is_admin(message.from_user.id): return
    msg = await message.answer("🔄 Синхронизация запущена...")
    users = await get_all_users()
    panels = await get_all_panels()

    clients_to_add = []
    for u in users:
        if u.get("vless_uuid") and u.get("sub_id"):
            clients_to_add.append({
                "email": f"user_{u['tg_id']}",
                "client_uuid": u['vless_uuid'],
                "sub_id": u['sub_id']
            })

    if clients_to_add:
        await XUI.add_clients_background(clients_to_add)

    await msg.edit_text(f"✅ Готово! Синхронизировано пользователей: {len(clients_to_add)} на {len(panels)} панелях.")

@router.message(Command("status"))
async def cmd_status(message: Message):
    if not is_admin(message.from_user.id): return
    panels = await get_all_panels()
    if not panels: return await message.answer("Нет серверов.")

    text = "🖥 <b>Статус серверов:</b>\n\n"
    for p in panels:
        async with XUI._new_session() as sess:
            ok = False
            if p.get("api_token"):
                res = await XUI._get_single(p, "/panel/api/inbounds/list")
                if res and res.get("success"): ok = True
            else:
                ok = await XUI._login(sess, p)

            icon = "✅" if ok else "❌"
            ibs = p.get("inbounds") or {}
            text += f"{icon} <b>{escape(p['name'])}</b> (<code>{p['host']}</code>)\n   Inbounds: {len(ibs)}\n   API: {'Token' if p.get('api_token') else 'Login'}\n\n"

    await message.answer(text, parse_mode="HTML")

# ── Управление серверами ──

@router.callback_query(F.data == "adm_panels")
async def adm_panels(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    panels = await get_all_panels()
    if not panels:
        await callback.message.edit_text("🖥 Серверов пока нет.", reply_markup=kb_back())
        return
    b = InlineKeyboardBuilder()
    for p in panels:
        ibs = p.get('inbounds', {})
        count = len(ibs)
        b.button(text=f"{p['name']} ({count} inbounds)", callback_data=f"pcfg_{p['id']}")
    b.button(text="➕ Добавить сервер", callback_data="adm_add_srv_info")
    b.button(text="◀️ Назад", callback_data="adm_back")
    b.adjust(1)
    await callback.message.edit_text("🖥 <b>Список серверов:</b>", parse_mode="HTML", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("pcfg_"))
async def panel_cfg(callback: CallbackQuery):
    pid = int(callback.data.split("_")[-1])
    p = await get_panel(pid)
    if not p: return
    text = f"⚙️ <b>Сервер: {escape(p['name'])}</b>\nIP: <code>{p['host']}</code>\n\n<b>Конфиги в базе:</b>\n"
    b = InlineKeyboardBuilder()
    ibs = p.get('inbounds', {})
    if not ibs: text += "— нет данных —"
    for iid, cfg in ibs.items():
        text += f"• [{iid}] {cfg.get('protocol')} - {cfg.get('label')}\n"
        b.button(text=f"🗑 {iid}", callback_data=f"pdel_ib_{pid}_{iid}")
    b.button(text="🌐 Обновить из 3X-UI", callback_data=f"psync_ibs_{pid}")
    b.button(text="🗑 Удалить сервер", callback_data=f"pdel_srv_{pid}")
    b.button(text="◀️ Назад", callback_data="adm_panels")
    b.adjust(2, 1)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("pdel_srv_"))
async def pdel_srv(callback: CallbackQuery):
    from database import delete_panel
    await delete_panel(int(callback.data.split("_")[-1]))
    await callback.answer("Удалено")
    await adm_panels(callback)

@router.callback_query(F.data.startswith("pdel_ib_"))
async def pdel_ib(callback: CallbackQuery):
    parts = callback.data.split("_")
    pid, iid = int(parts[2]), str(parts[3])
    p = await get_panel(pid)
    if not p: return
    ibs = p.get('inbounds', {})
    if iid in ibs: del ibs[iid]
    ib_ids = [str(x) for x in p.get('inbound_ids', []) if str(x) != iid]
    bib_ids = [str(x) for x in p.get('billing_inbound_ids', []) if str(x) != iid]
    await update_panel_inbounds(pid, ib_ids, bib_ids, ibs)
    await callback.answer("Удален")
    await panel_cfg(callback)

@router.callback_query(F.data.startswith("psync_ibs_"))
async def psync_ibs(callback: CallbackQuery):
    pid = int(callback.data.split("_")[-1])
    p = await get_panel(pid)
    if not p: return await callback.answer("❌ Сервер не найден")

    await callback.answer("⏳ Синхронизация запущена...")

    try:
        res = await XUI._get_single(p, "/panel/api/inbounds/list")
        if not (res and res.get("success") and res.get("obj")):
            msg = res.get("msg") if res else "Connection Error"
            return await callback.message.answer(f"❌ Ошибка 3X-UI на сервере {p['name']}: {msg}")

        ibs, ib_ids, bib_ids = {}, [], []
        for ib in res["obj"]:
            iid, prot = str(ib["id"]), str(ib["protocol"]).lower()
            if prot == "shadowsocks": prot = "ss"

            stream = ib.get("streamSettings") or {}
            cfg = {
                "label": ib.get("remark", f"Inbound {iid}"),
                "protocol": prot, "port": ib.get("port"),
                "network": stream.get("network", "tcp"), "security": stream.get("security", "none")
            }

            # Парсинг транспорта
            net = cfg["network"]
            if net == "xhttp":
                xs = stream.get("xhttpSettings") or {}
                cfg.update({"path": xs.get("path", "/"), "xhttp_mode": xs.get("mode", "auto"), "ws_host": xs.get("host", "")})
            elif net == "ws":
                ws = stream.get("wsSettings") or {}
                cfg.update({"path": ws.get("path", "/"), "ws_host": (ws.get("headers") or {}).get("Host", "")})
            elif net == "grpc":
                gs = stream.get("grpcSettings") or {}
                cfg.update({"grpc_service": gs.get("serviceName", "")})
            elif net == "tcp":
                ts = stream.get("tcpSettings") or {}
                hdr = ts.get("header") or {}
                if hdr.get("type") == "http":
                    cfg.update({"type": "http"})
                    req = (hdr.get("request") or {})
                    paths = req.get("path", ["/"])
                    cfg.update({"path": paths[0] if paths else "/"})
                    hosts = req.get("headers", {}).get("Host", [""])
                    if hosts: cfg.update({"ws_host": hosts[0]})

            # Парсинг безопасности
            if cfg["security"] == "reality":
                rs = stream.get("realitySettings") or {}
                r_set = rs.get("settings") or {}
                cfg.update({
                    "public_key": r_set.get("publicKey") or "",
                    "fingerprint": r_set.get("fingerprint") or "chrome",
                    "sni": rs.get("serverNames", [""])[0] if rs.get("serverNames") else "",
                    "short_id": rs.get("shortIds", [""])[0] if rs.get("shortIds") else "",
                    "spiderX": r_set.get("spiderX") or "/"
                })
                cls = (ib.get("settings") or {}).get("clients", [])
                cfg["flow"] = cls[0].get("flow", "") if cls else ""
            elif cfg["security"] == "tls":
                ts = stream.get("tlsSettings") or {}
                cfg.update({"sni": ts.get("serverName", "")})
                cls = (ib.get("settings") or {}).get("clients", [])
                cfg["flow"] = cls[0].get("flow", "") if cls else ""

            if prot == "ss":
                s_set = ib.get("settings") or {}
                cfg.update({"method": s_set.get("method"), "password": s_set.get("password")})

            ibs[iid] = cfg
            if prot == "vless": ib_ids.append(iid)
            bib_ids.append(iid)

        await update_panel_inbounds(pid, ib_ids, bib_ids, ibs)
        await callback.message.answer(f"✅ Успешно! Синхронизировано {len(ibs)} конфигов на сервере {p['name']}")
        await panel_cfg(callback)
    except Exception as e:
        logger.exception(f"Sync error for {p['name']}")
        await callback.message.answer(f"❌ Фатальная ошибка при синхронизации {p['name']}: {e}")

# ── Добавление серверов ──

@router.message(Command("addserver"))
async def cmd_addserver(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.set_state(AddServerStates.waiting_name)
    await message.answer("Название сервера (например, Германия):")

@router.message(AddServerStates.waiting_name)
async def process_server_name(message: Message, state: FSMContext):
    if message.text.startswith('/'):
        return await message.answer("Название не может начинаться с /. Введи название сервера:")
    await state.update_data(name=message.text)
    await state.set_state(AddServerStates.waiting_host)
    await message.answer("IP или домен для API:")

@router.message(AddServerStates.waiting_host)
async def process_server_host(message: Message, state: FSMContext):
    import re
    raw = message.text.strip().lower()
    host = re.sub(r'^https?://', '', raw).split('/')[0].split(':')[0].strip('.')
    if not host:
        return await message.answer("❌ Неверный хост. Введи IP или домен:")
    await state.update_data(host=host)
    await state.set_state(AddServerStates.waiting_port)
    await message.answer("Порт панели:")

@router.message(AddServerStates.waiting_port)
async def process_server_port(message: Message, state: FSMContext):
    try:
        await state.update_data(port=int(message.text))
        await state.set_state(AddServerStates.waiting_path)
        await message.answer("Путь панели (например, /xui):")
    except Exception as e:
        logger.warning(f"Exception caught: {e}")
        await message.answer("Числом:")

@router.message(AddServerStates.waiting_path)
async def process_server_path(message: Message, state: FSMContext):
    path = message.text if message.text.startswith('/') else '/' + message.text
    await state.update_data(path=path)
    b = InlineKeyboardBuilder()
    b.button(text="Login/Password", callback_data="auth_login")
    b.button(text="API Token", callback_data="auth_token")
    await state.set_state(AddServerStates.waiting_auth_method)
    await message.answer("Метод авторизации:", reply_markup=b.as_markup())

@router.callback_query(AddServerStates.waiting_auth_method, F.data == "auth_login")
async def auth_method_login(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddServerStates.waiting_login)
    await callback.message.edit_text("Логин:")

@router.callback_query(AddServerStates.waiting_auth_method, F.data == "auth_token")
async def auth_method_token(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddServerStates.waiting_api_token)
    await callback.message.edit_text("API Token:")

@router.message(AddServerStates.waiting_login)
async def process_server_login(message: Message, state: FSMContext):
    await state.update_data(login=message.text)
    await state.set_state(AddServerStates.waiting_password)
    await message.answer("Пароль:")

@router.message(AddServerStates.waiting_password)
async def process_server_password(message: Message, state: FSMContext):
    await state.update_data(password=message.text, api_token="")  # nosec B106
    await state.set_state(AddServerStates.waiting_server_host)
    await message.answer("Публичный IP сервера (для ссылок):")

@router.message(AddServerStates.waiting_api_token)
async def process_server_api_token(message: Message, state: FSMContext):
    await state.update_data(api_token=message.text, login="", password="")  # nosec B106
    await state.set_state(AddServerStates.waiting_server_host)
    await message.answer("Публичный IP сервера (для ссылок):")

@router.message(AddServerStates.waiting_server_host)
async def process_server_finish(message: Message, state: FSMContext):
    await state.update_data(server_host=message.text)
    data = await state.get_data()
    panel_test = {
        "host": data['host'], "port": data['port'], "path": data['path'],
        "login": data.get('login', ""), "password": data.get('password', ""),
        "api_token": data.get('api_token', ""), "name": data['name']
    }
    await message.answer("⏳ Проверка...")
    ok = False
    if panel_test.get("api_token"):
        res = await XUI._get_single(panel_test, "/panel/api/inbounds/list")
        if res and res.get("success"): ok = True
    else:
        async with XUI._new_session() as sess:
            ok = await XUI._login(sess, panel_test)
    if ok:
        pid = await add_panel(data['name'], data['host'], data['port'], data['path'], data.get('login', ""), data.get('password', ""), data['server_host'], data.get('api_token', ""))
        await message.answer(f"✅ Сервер добавлен! ID: {pid}. Теперь обнови конфиги кнопкой в меню серверов.")
    else: await message.answer("❌ Ошибка подключения.")
    await state.clear()

@router.message(Command("addinbound"))
async def cmd_addinbound(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    panels = await get_all_panels()
    if not panels:
        await message.answer("Сначала добавь сервер")
        return
    b = InlineKeyboardBuilder()
    for p in panels: b.button(text=p['name'], callback_data=f"selpanel_{p['id']}")
    await state.set_state(AddInboundStates.waiting_panel_selection)
    await message.answer("Выбери сервер:", reply_markup=b.as_markup())

@router.callback_query(AddInboundStates.waiting_panel_selection, F.data.startswith("selpanel_"))
async def process_panel_selection(callback: CallbackQuery, state: FSMContext):
    await state.update_data(panel_id=int(callback.data.split("_")[1]))
    await state.set_state(AddInboundStates.waiting_json)
    await callback.message.edit_text("Отправь JSON инбаунда (Export из 3X-UI):")

@router.message(AddInboundStates.waiting_json)
async def process_inbound_json(message: Message, state: FSMContext):
    try:
        raw = json.loads(message.text)
        if isinstance(raw, list) and len(raw) > 0: inbound = raw[0]
        elif isinstance(raw, dict) and isinstance(raw.get("obj"), dict): inbound = raw["obj"]
        else: inbound = raw

        raw_iid = inbound.get("id") or inbound.get("tag")
        prot = str(inbound.get("protocol", "")).lower()
        if prot == "shadowsocks": prot = "ss"

        if raw_iid is None or not prot:
            await message.answer("❌ Ошибка: В JSON не найден ID/Tag или протокол.")
            return

        data = await state.get_data()
        panel = await get_panel(data['panel_id'])

        real_id = await XUI.get_real_inbound_id(panel, raw_iid)
        iid = str(real_id) if real_id else str(raw_iid)

        stream = inbound.get("streamSettings") or {}
        cfg = {
            "label": inbound.get("remark", f"Inbound {iid}"),
            "protocol": prot, "port": inbound.get("port"),
            "network": stream.get("network", "tcp"), "security": stream.get("security", "none")
        }
        net = cfg["network"]
        if net == "xhttp":
            xs = stream.get("xhttpSettings") or {}
            cfg.update({"path": xs.get("path", "/"), "xhttp_mode": xs.get("mode", "auto"), "ws_host": xs.get("host", "")})
        elif net == "ws":
            ws = stream.get("wsSettings") or {}
            cfg.update({"path": ws.get("path", "/"), "ws_host": (ws.get("headers") or {}).get("Host", "")})
        elif net == "grpc":
            gs = stream.get("grpcSettings") or {}
            cfg.update({"grpc_service": gs.get("serviceName", "")})
        elif net == "tcp":
            ts = stream.get("tcpSettings") or {}
            hdr = ts.get("header") or {}
            if hdr.get("type") == "http":
                cfg.update({"type": "http"})
                req = (hdr.get("request") or {})
                paths = req.get("path", ["/"])
                cfg.update({"path": paths[0] if paths else "/"})
                hosts = req.get("headers", {}).get("Host", [""])
                if hosts: cfg.update({"ws_host": hosts[0]})

        if cfg["security"] == "reality":
            rs = stream.get("realitySettings") or {}
            r_set = rs.get("settings") or {}
            cfg.update({
                "public_key": r_set.get("publicKey") or "",
                "fingerprint": r_set.get("fingerprint") or "chrome",
                "sni": rs.get("serverNames", [""])[0] if rs.get("serverNames") else "",
                "short_id": rs.get("shortIds", [""])[0] if rs.get("shortIds") else "",
                "spiderX": r_set.get("spiderX") or "/"
            })
            cls = (inbound.get("settings") or {}).get("clients", [])
            cfg["flow"] = cls[0].get("flow", "") if cls else ""
        elif cfg["security"] == "tls":
            ts = stream.get("tlsSettings") or {}
            cfg.update({"sni": ts.get("serverName", "")})
            cls = (inbound.get("settings") or {}).get("clients", [])
            cfg["flow"] = cls[0].get("flow", "") if cls else ""

        if prot == "ss":
            s_set = inbound.get("settings") or {}
            cfg.update({"method": s_set.get("method"), "password": s_set.get("password")})

        inbounds, ib_ids, bib_ids = panel.get('inbounds', {}), panel.get('inbound_ids', []), panel.get('billing_inbound_ids', [])

        inbounds[iid] = cfg
        if iid not in ib_ids and prot == "vless": ib_ids.append(iid)
        if iid not in bib_ids: bib_ids.append(iid)

        await update_panel_inbounds(panel['id'], ib_ids, bib_ids, inbounds)
        await message.answer(f"✅ Конфиг {iid} ({prot}) успешно добавлен на {panel['name']}.")
    except Exception as e: await message.answer(f"❌ Ошибка парсинга: {e}")
    await state.clear()

# ── Стандартные команды управления ──

@router.message(Command("billing"))
async def cmd_billing(message: Message, bot: Bot):
    if not is_admin(message.from_user.id): return
    await message.answer("⏳ Billing tick...")
    await billing_tick(bot)
    await message.answer("✅ Готово.")

@router.message(Command("debugtraffic"))
async def cmd_debugtraffic(message: Message):
    if not is_admin(message.from_user.id): return
    status_msg = await message.answer("⏳ Запрашиваю трафик со всех панелей...")
    try:
        traffic = await XUI.get_traffic()
        users = await get_all_users()
        if traffic is None:
            return await status_msg.edit_text("❌ Ошибка связи с 3X-UI (не удалось опросить ни одну панель).")

        lines = ["🔍 <b>Диагностика трафика (Топ 20):</b>\n"]
        count = 0
        for u in users:
            if u.get("vless_uuid"):
                email = f"user_{u['tg_id']}"
                found = "✅" if email in traffic else "❌"
                val = traffic.get(email, 0)
                lines.append(f"{found} <code>{u['tg_id']}</code> | {fmt_bytes(val)} | {u['balance']:.1f}₽")
                count += 1
                if count >= 20: break

        if count == 0:
            lines.append("<i>Пользователей с конфигами пока нет.</i>")

        await status_msg.edit_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.exception("Error in debugtraffic")
        await status_msg.edit_text(f"❌ Фатальная ошибка: {str(e)}")

@router.message(Command("setbalance"))
async def cmd_setbalance(message: Message, bot: Bot):
    if not is_admin(message.from_user.id): return
    parts = message.text.split()
    if len(parts) != 3: return await message.answer("Формат: /setbalance ID СУММА")
    uid, amt = int(parts[1]), float(parts[2])
    await add_balance(uid, amt)
    await add_transaction(uid, amt, "admin", f"Админ: {amt:+.2f} ₽")
    await message.answer(f"✅ Баланс {uid} изменён на {amt:+.2f} ₽")
    asyncio.create_task(billing_tick(bot))

@router.message(Command("userinfo"))
async def cmd_userinfo(message: Message):
    if not is_admin(message.from_user.id): return
    parts = message.text.split()
    if len(parts) != 2: return await message.answer("Формат: /userinfo ID")
    uid = int(parts[1])
    u = await get_user(uid)
    if not u: return await message.answer("Не найден")
    traffic = await XUI.get_client_traffic(f"user_{uid}")
    status = "✅" if u.get("balance", 0) > CREDIT_LIMIT_RUB else "❌"
    sub_url = XUI.make_sub_url(u['sub_id'])
    text = f"👤 {escape(u['full_name'])} (@{escape(u['username'])})\nID: {uid}\nСтатус: {status}\nБаланс: {u['balance']:.2f} ₽\nТрафик (БД): {fmt_bytes(u['total_traffic_bytes'])}"
    if traffic: text += f"\nТрафик (панель): {fmt_bytes(traffic['total'])}"
    text += f"\n🔗 Подписка: <code>{sub_url}</code>"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("ban"))
async def cmd_ban(message: Message):
    if not is_admin(message.from_user.id): return
    uid = int(message.text.split()[1])
    u = await get_user(uid)
    await update_user(uid, is_banned=1, xui_enabled=0)
    if u and u.get("vless_uuid"): await XUI.toggle_client(f"user_{uid}", u["vless_uuid"], False, u.get("sub_id", ""))
    await message.answer("🚫 Забанен")

@router.message(Command("unban"))
async def cmd_unban(message: Message):
    if not is_admin(message.from_user.id): return
    uid = int(message.text.split()[1])
    u = await get_user(uid)
    await update_user(uid, is_banned=0, xui_enabled=1)
    if u and u.get("vless_uuid"): await XUI.toggle_client(f"user_{uid}", u["vless_uuid"], True, u.get("sub_id", ""))
    await message.answer("✅ Разбанен")

@router.message(Command("reply"))
async def cmd_reply(message: Message, bot: Bot):
    if not is_admin(message.from_user.id): return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3: return
    await bot.send_message(int(parts[1]), f"📩 <b>Ответ поддержки:</b>\n\n{parts[2]}", parse_mode="HTML")
    await message.answer("✅")

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, bot: Bot):
    if not is_admin(message.from_user.id): return
    text = message.text.replace("/broadcast", "").strip()
    if not text: return
    users = await get_all_users()
    for u in users:
        try: await bot.send_message(u["tg_id"], f"📢 {text}"); await asyncio.sleep(0.05)
        except Exception as e: logging.getLogger(__name__).warning(f"Failed to send broadcast: {e}")
    await message.answer("✅")

@router.message(Command("addpromo"))
async def cmd_addpromo(message: Message):
    if not is_admin(message.from_user.id): return
    p = message.text.split()
    if len(p) != 4: return await message.answer("Формат: /addpromo КОД РУБ USES")
    code = p[1].upper() if p[1] != "_" else "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    await create_promo(code, float(p[2]), int(p[3]), message.from_user.id)
    await message.answer(f"✅ Промокод {code} на {p[2]}₽ создан.")

# ── Callback Handlers (Keyboard buttons) ──

@router.callback_query(F.data == "adm_users")
async def adm_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    users = await get_all_users()
    lines = [f"👥 <b>Пользователи ({len(users)}):</b>\n"]
    for u in users[:40]:
        status = "✅" if u.get("balance", 0) > CREDIT_LIMIT_RUB else "❌"
        lines.append(f"{status} <code>{u['tg_id']}</code> {escape(u.get('full_name')[:18])}\n   {u['balance']:.1f}₽ | {fmt_bytes(u.get('total_traffic_bytes') or 0)}")
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data="adm_back")
    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=b.as_markup())
    await callback.answer()

@router.callback_query(F.data == "adm_revenue")
async def adm_revenue(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    s = await get_revenue_stats()
    profit = (s.get("month") or 0) - SERVER_COST_RUB
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data="adm_back")
    await callback.message.edit_text(f"📊 <b>Финансы</b>\n\nСегодня: {s.get('today'):.2f}₽\nМесяц: {s.get('month'):.2f}₽\nВсего: {s.get('total'):.2f}₽\nПрибыль: {profit:+.2f}₽", parse_mode="HTML", reply_markup=b.as_markup())
    await callback.answer()

@router.callback_query(F.data == "adm_promos")
async def adm_promos(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    promos = await get_all_promos()
    b = InlineKeyboardBuilder(); b.button(text="◀️ Назад", callback_data="adm_back"); b.adjust(1)
    if not promos: return await callback.message.edit_text("🎁 Промокодов нет.", reply_markup=b.as_markup())
    lines = ["🎁 <b>Промокоды:</b>\n"]
    for p in promos: lines.append(f"<code>{p['code']}</code> — {p['bonus_rub']:.0f}₽ | {p['used_count']}/{p['max_uses']}")
    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=b.as_markup())
    await callback.answer()

@router.callback_query(F.data == "adm_back")
async def adm_back(callback: CallbackQuery):
    await callback.message.edit_text("👑 <b>Панель администратора</b>", parse_mode="HTML", reply_markup=kb_admin())
    await callback.answer()

@router.callback_query(F.data == "adm_add_srv_info")
async def adm_add_srv_info(callback: CallbackQuery):
    await callback.message.answer("Используй команду /addserver.")
    await callback.answer()

# ── Быстрые действия из уведомлений ──

@router.callback_query(F.data.startswith("adm_userinfo_"))
async def cb_adm_userinfo_btn(callback: CallbackQuery):
    uid = int(callback.data.split("_")[-1])
    u = await get_user(uid)
    if not u: return await callback.answer("Не найден")
    status = "✅" if u.get("balance", 0) > CREDIT_LIMIT_RUB else "❌"
    text = f"👤 {escape(u['full_name'])}\nID: {uid}\nСтатус: {status}\nБаланс: {u['balance']:.2f} ₽"
    b = InlineKeyboardBuilder(); b.button(text="💳 +50 ₽", callback_data=f"adm_gift_{uid}"); b.button(text="🚫 Бан", callback_data=f"adm_ban_{uid}"); b.button(text="◀️ Назад", callback_data=f"adm_card_{uid}"); b.adjust(2, 1)
    await callback.message.edit_text(text, reply_markup=b.as_markup()); await callback.answer()

@router.callback_query(F.data.startswith("adm_gift_"))
async def cb_adm_gift_btn(callback: CallbackQuery, bot: Bot):
    uid = int(callback.data.split("_")[-1])
    await add_balance(uid, 50.0); await add_transaction(uid, 50.0, "admin_gift", "Gift")
    try: await bot.send_message(uid, "🎁 Начислено 50 ₽!")
    except Exception as e: logging.getLogger(__name__).warning(f"Failed to send gift notification: {e}")
    asyncio.create_task(billing_tick(bot))
    await callback.answer("✅ Начислено 50 ₽"); await adm_back(callback)

@router.callback_query(F.data.startswith("adm_ban_"))
async def cb_adm_ban_btn(callback: CallbackQuery):
    uid = int(callback.data.split("_")[-1])
    u = await get_user(uid)
    await update_user(uid, is_banned=1, xui_enabled=0)
    if u and u.get("vless_uuid"): await XUI.toggle_client(f"user_{uid}", u["vless_uuid"], False, u.get("sub_id", ""))
    await callback.answer("🚫 Забанен"); await adm_back(callback)

@router.callback_query(F.data.startswith("adm_card_"))
async def cb_adm_card_btn(callback: CallbackQuery):
    uid = int(callback.data.split("_")[-1])
    await callback.message.edit_text(f"🆕 Пользователь {uid}", reply_markup=kb_new_user(uid))
    await callback.answer()
