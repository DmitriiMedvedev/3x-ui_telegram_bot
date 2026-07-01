# Admin panel handlers for managing users, adding balances, banning, and checking stats.
"""
handlers/admin.py — Admin panel v16.3 (Ultimate Robust Inbound Parsing).
"""
import asyncio
import logging
import random
import string
import json
from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_IDS, PRICE_PER_GB, SERVER_COST_RUB, CREDIT_LIMIT_RUB
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
        f"/sync — синхронизировать всех юзеров",
        parse_mode="HTML", reply_markup=kb_admin(),
    )

@router.message(Command("sync"))
async def cmd_sync(message: Message):
    if not is_admin(message.from_user.id): return
    msg = await message.answer("🔄 Синхронизация запущена...")
    users = await get_all_users()
    panels = await get_all_panels()
    count = 0
    for u in users:
        if u.get("vless_uuid") and u.get("sub_id"):
            # Добавляем на все инбаунды всех панелей
            await XUI.add_client_background(f"user_{u['tg_id']}", u['vless_uuid'], u['sub_id'])
            count += 1
            if count % 10 == 0: await asyncio.sleep(0.2)
    await msg.edit_text(f"✅ Готово! Синхронизировано пользователей: {count} на {len(panels)} панелях.")

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
    text = f"⚙️ <b>Сервер: {p['name']}</b>\nIP: <code>{p['host']}</code>\n\n<b>Конфиги в базе:</b>\n"
    b = InlineKeyboardBuilder()
    ibs = p.get('inbounds', {})
    if not ibs: text += "— нет данных —"
    for iid, cfg in ibs.items():
        text += f"• [{iid}] {cfg.get('protocol')} - {cfg.get('label')}\n"
        b.button(text=f"🗑 Удал. {iid}", callback_data=f"pdel_ib_{pid}_{iid}")
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
    # Обновляем списки
    ib_ids = [str(x) for x in p.get('inbound_ids', []) if str(x) != iid]
    bib_ids = [str(x) for x in p.get('billing_inbound_ids', []) if str(x) != iid]
    await update_panel_inbounds(pid, ib_ids, bib_ids, ibs)
    await callback.answer(f"Конфиг {iid} удален")
    await panel_cfg(callback)

# ── Добавление серверов ──

@router.message(Command("addserver"))
async def cmd_addserver(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.set_state(AddServerStates.waiting_name)
    await message.answer("Название сервера (например, Германия):")

@router.message(AddServerStates.waiting_name)
async def process_server_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AddServerStates.waiting_host)
    await message.answer("IP или домен для API:")

@router.message(AddServerStates.waiting_host)
async def process_server_host(message: Message, state: FSMContext):
    await state.update_data(host=message.text)
    await state.set_state(AddServerStates.waiting_port)
    await message.answer("Порт панели:")

@router.message(AddServerStates.waiting_port)
async def process_server_port(message: Message, state: FSMContext):
    try:
        await state.update_data(port=int(message.text))
        await state.set_state(AddServerStates.waiting_path)
        await message.answer("Путь панели (например, /xui):")
    except: await message.answer("Числом:")

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
    await state.update_data(password=message.text, api_token="")
    await state.set_state(AddServerStates.waiting_server_host)
    await message.answer("Публичный IP сервера (для ссылок):")

@router.message(AddServerStates.waiting_api_token)
async def process_server_api_token(message: Message, state: FSMContext):
    await state.update_data(api_token=message.text, login="", password="")
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
        await message.answer(f"✅ Сервер добавлен! ID: {pid}. Теперь добавь конфиг через /addinbound.")
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
        # Обработка разных форматов 3X-UI (обертка obj или массив)
        if isinstance(raw, list) and len(raw) > 0: inbound = raw[0]
        elif isinstance(raw, dict) and isinstance(raw.get("obj"), dict): inbound = raw["obj"]
        else: inbound = raw

        iid = inbound.get("id")
        prot = str(inbound.get("protocol", "")).lower()
        if prot == "shadowsocks": prot = "ss"

        if not iid or not prot:
            await message.answer("❌ Ошибка: В JSON не найден ID или протокол. Убедись, что это Export из Inbounds.")
            return

        stream = inbound.get("streamSettings", {})
        cfg = {
            "label": inbound.get("remark", f"Inbound {iid}"),
            "protocol": prot, "port": inbound.get("port"),
            "network": stream.get("network", "tcp"), "security": stream.get("security", "none")
        }
        # Раскрываем настройки Reality/TLS
        if cfg["security"] == "reality":
            rs = stream.get("realitySettings", {})
            cfg.update({"public_key": rs.get("settings", {}).get("publicKey", ""), "fingerprint": rs.get("settings", {}).get("fingerprint", "chrome"), "sni": rs.get("serverNames", [""])[0], "short_id": rs.get("shortIds", [""])[0]})
            cls = inbound.get("settings", {}).get("clients", [])
            cfg["flow"] = cls[0].get("flow", "") if cls else ""
        elif prot == "ss":
            s_set = inbound.get("settings", {})
            cfg.update({"method": s_set.get("method"), "password": s_set.get("password")})

        data = await state.get_data()
        panel = await get_panel(data['panel_id'])
        inbounds, ib_ids, bib_ids = panel.get('inbounds', {}), panel.get('inbound_ids', []), panel.get('billing_inbound_ids', [])

        inbounds[str(iid)] = cfg
        if str(iid) not in ib_ids and prot == "vless": ib_ids.append(str(iid))
        if str(iid) not in bib_ids: bib_ids.append(str(iid))

        await update_panel_inbounds(panel['id'], ib_ids, bib_ids, inbounds)
        await message.answer(f"✅ Конфиг {iid} ({prot}) успешно добавлен на {panel['name']}.")
    except Exception as e: await message.answer(f"❌ Ошибка парсинга: {e}")
    await state.clear()

@router.callback_query(F.data == "adm_back")
async def adm_back(callback: CallbackQuery):
    await callback.message.edit_text("👑 <b>Панель администратора</b>", parse_mode="HTML", reply_markup=kb_admin())
    await callback.answer()

@router.callback_query(F.data == "adm_add_srv_info")
async def adm_add_srv_info(callback: CallbackQuery):
    await callback.message.answer("Используй команду /addserver.")
    await callback.answer()
