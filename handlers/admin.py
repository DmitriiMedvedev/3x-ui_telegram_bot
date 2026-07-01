# Admin panel handlers for managing users, adding balances, banning, and checking stats.
"""
handlers/admin.py — Admin panel.
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
        f"/userinfo ID — инфо о юзере\n"
        f"/sync — добавить всех юзеров на панели",
        parse_mode="HTML", reply_markup=kb_admin(),
    )

@router.message(Command("sync"))
async def cmd_sync(message: Message):
    if not is_admin(message.from_user.id): return
    await message.answer("🔄 Синхронизация запущена. Это может занять время...")
    users = await get_all_users()
    panels = await get_all_panels()

    count = 0
    for u in users:
        uid = u["tg_id"]
        email = f"user_{uid}"
        u_uuid = u.get("vless_uuid")
        sub_id = u.get("sub_id")

        # На каждый инбаунд каждой панели
        for p in panels:
            for iid in p.get("inbound_ids", []):
                obj = XUI._build_client_obj(u_uuid, email, sub_id, iid, p)
                await XUI._add_to_inbound(p, iid, obj)
        count += 1
        if count % 10 == 0:
            await asyncio.sleep(0.5) # Не спамим API

    await message.answer(f"✅ Готово! Синхронизировано пользователей: {count}")

# ── Billing ───────────────────────────────────────────────────────────────────

@router.message(Command("billing"))
async def cmd_billing(message: Message, bot: Bot):
    if not is_admin(message.from_user.id): return
    await message.answer("⏳ Запускаю billing tick...")
    await billing_tick(bot)
    await message.answer("✅ Готово.")

@router.message(Command("debugtraffic"))
async def cmd_debugtraffic(message: Message):
    if not is_admin(message.from_user.id): return
    await message.answer("⏳ Запрашиваю трафик из 3X-UI...")
    traffic = await XUI.get_traffic()
    users   = await get_all_users()
    if traffic is None:
        await message.answer("❌ Не удалось получить данные из 3X-UI")
        return
    lines = [f"🔍 <b>Диагностика трафика</b>\n", f"<b>В 3X-UI ({len(traffic)} клиентов):</b>"]
    for email, bytes_ in list(traffic.items())[:20]:
        lines.append(f"  • {email}: {fmt_bytes(bytes_)}")
    lines.append(f"\n<b>В БД ({len(users)} пользователей):</b>")
    for u in users[:20]:
        if u.get("vless_uuid"):
            expected = f"user_{u['tg_id']}"
            found = "✅" if expected in traffic else "❌"
            lines.append(f"  {found} {expected} | bal={u.get('balance', 0):.1f}₽")
    await message.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("userinfo"))
async def cmd_userinfo(message: Message):
    if not is_admin(message.from_user.id): return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Формат: /userinfo TELEGRAM_ID")
        return
    try: uid = int(parts[1])
    except: return
    u = await get_user(uid)
    if not u:
        await message.answer("Пользователь не найден")
        return
    email = f"user_{uid}"
    traffic = await XUI.get_client_traffic(email)
    active = "✅" if u.get("balance", 0) > CREDIT_LIMIT_RUB else "❌"
    lines = [
        f"👤 <b>Пользователь {uid}</b>",
        f"Статус: {active}",
        f"Баланс: <b>{u.get('balance', 0):.2f} ₽</b>",
        f"UUID: <code>{u.get('vless_uuid') or '—'}</code>",
        f"Sub URL: <code>{u.get('sub_url') or '—'}</code>",
        f"Трафик: {fmt_bytes(u.get('total_traffic_bytes') or 0)}",
    ]
    if traffic:
        lines.append(f"Панель: ⬆️{fmt_bytes(traffic['up'])} ⬇️{fmt_bytes(traffic['down'])}")
    await message.answer("\n".join(lines), parse_mode="HTML")

# ── Управление пользователями ──────────────────────────────────────────────────

@router.message(Command("setbalance"))
async def cmd_setbalance(message: Message, bot: Bot):
    if not is_admin(message.from_user.id): return
    parts = message.text.split()
    if len(parts) != 3: return
    try:
        uid, amt = int(parts[1]), float(parts[2])
    except: return
    await add_balance(uid, amt)
    await add_transaction(uid, amt, "admin", f"Админ: {amt:+.2f} ₽")
    if amt > 0: await update_user(uid, notified_low_balance=0)
    await message.answer(f"✅ Баланс {uid} изменён на {amt:+.2f} ₽")
    asyncio.create_task(billing_tick(bot))

@router.message(Command("ban"))
async def cmd_ban(message: Message):
    if not is_admin(message.from_user.id): return
    try: uid = int(message.text.split()[1])
    except: return
    u = await get_user(uid)
    if not u: return
    await update_user(uid, is_banned=1, xui_enabled=0)
    if u.get("vless_uuid"):
        await XUI.toggle_client(f"user_{uid}", u["vless_uuid"], enable=False, sub_id=u.get("sub_id", ""))
    await message.answer(f"🚫 Пользователь {uid} заблокирован.")

@router.message(Command("unban"))
async def cmd_unban(message: Message):
    if not is_admin(message.from_user.id): return
    try: uid = int(message.text.split()[1])
    except: return
    await update_user(uid, is_banned=0, xui_enabled=1)
    u = await get_user(uid)
    if u and u.get("vless_uuid"):
        await XUI.toggle_client(f"user_{uid}", u["vless_uuid"], enable=True, sub_id=u.get("sub_id", ""))
    await message.answer(f"✅ Пользователь {uid} разблокирован.")

@router.message(Command("reply"))
async def cmd_reply(message: Message, bot: Bot):
    if not is_admin(message.from_user.id): return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3: return
    try:
        await bot.send_message(int(parts[1]), f"📩 <b>Ответ поддержки:</b>\n\n{parts[2]}", parse_mode="HTML")
        await message.answer("✅ Отправлено.")
    except Exception as e: await message.answer(f"❌ Ошибка: {e}")

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, bot: Bot):
    if not is_admin(message.from_user.id): return
    text = message.text.replace("/broadcast", "").strip()
    if not text: return
    users = await get_all_users()
    sent = 0
    for u in users:
        try:
            await bot.send_message(u["tg_id"], f"📢 {text}")
            sent += 1
            await asyncio.sleep(0.05)
        except: pass
    await message.answer(f"✅ Отправлено {sent} пользователям")

# ── Промокоды ──────────────────────────────────────────────────────────────────

@router.message(Command("addpromo"))
async def cmd_addpromo(message: Message):
    if not is_admin(message.from_user.id): return
    parts = message.text.split()
    if len(parts) != 4: return
    code = parts[1].upper() if parts[1] != "_" else "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    try: bonus, uses = float(parts[2]), int(parts[3])
    except: return
    await create_promo(code, bonus, uses, message.from_user.id)
    await message.answer(f"✅ Промокод <code>{code}</code> на {bonus:.0f} ₽ создан.", parse_mode="HTML")

# ── Инлайн-кнопки ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_users")
async def adm_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    users = await get_all_users()
    lines = [f"👥 <b>Пользователи ({len(users)}):</b>\n"]
    for u in users[:40]:
        status = "✅" if u.get("balance", 0) > CREDIT_LIMIT_RUB else "❌"
        lines.append(f"{status} <code>{u['tg_id']}</code> | {u.get('balance', 0):.1f}₽")
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
    await callback.message.edit_text(f"📊 Доход за месяц: {s.get('month', 0):.0f} ₽\nПрибыль: {profit:+.0f} ₽", reply_markup=b.as_markup())
    await callback.answer()

@router.callback_query(F.data == "adm_promos")
async def adm_promos(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    promos = await get_all_promos()
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data="adm_back")
    if not promos: await callback.message.edit_text("🎁 Нет промокодов.", reply_markup=b.as_markup())
    else:
        lines = [f"✅ {p['code']} ({p['bonus_rub']:.0f}₽) {p['used_count']}/{p['max_uses']}" for p in promos]
        await callback.message.edit_text("\n".join(lines), reply_markup=b.as_markup())
    await callback.answer()

@router.callback_query(F.data == "adm_back")
async def adm_back(callback: CallbackQuery):
    await callback.message.edit_text(
        "👑 <b>Панель администратора</b>",
        parse_mode="HTML",
        reply_markup=kb_admin(),
    )
    await callback.answer()

# ── Управление серверами (Панелями) ───────────────────────────────────────────

@router.callback_query(F.data == "adm_panels")
async def adm_panels(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    panels = await get_all_panels()
    if not panels:
        await callback.message.edit_text("🖥 <b>Серверов пока нет.</b>\nДобавь через /addserver", parse_mode="HTML", reply_markup=kb_back())
        return

    lines = ["🖥 <b>Подключенные серверы:</b>\n"]
    b = InlineKeyboardBuilder()
    for p in panels:
        lines.append(f"• <b>{p['name']}</b> ({p['host']})")
        b.button(text=f"⚙️ {p['name']}", callback_data=f"panel_cfg_{p['id']}")

    b.button(text="➕ Добавить сервер", callback_data="adm_add_srv_info")
    b.button(text="◀️ Назад", callback_data="adm_back")
    b.adjust(1)

    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=b.as_markup())
    await callback.answer()

@router.callback_query(F.data == "adm_add_srv_info")
async def adm_add_srv_info(callback: CallbackQuery):
    await callback.message.answer("Для добавления нового сервера используй команду: /addserver")
    await callback.answer()

@router.callback_query(F.data.startswith("panel_cfg_"))
async def panel_cfg(callback: CallbackQuery):
    pid = int(callback.data.split("_")[-1])
    p = await get_panel(pid)
    if not p: return

    text = (
        f"⚙️ <b>Настройка сервера: {p['name']}</b>\n\n"
        f"Host: <code>{p['host']}</code>\n"
        f"Port: {p['port']}\n"
        f"Path: <code>{p['path']}</code>\n"
        f"Public IP: <code>{p['server_host']}</code>\n\n"
        f"<b>Конфиги (Inbounds):</b>\n"
    )

    b = InlineKeyboardBuilder()
    if not p['inbounds']:
        text += "— нет добавленных конфигов —"
    else:
        for iid, cfg in p['inbounds'].items():
            text += f"• [{iid}] {cfg.get('protocol')} - {cfg.get('label')}\n"
            b.button(text=f"🗑 Удал. {iid}", callback_data=f"pdel_ib_{pid}_{iid}")

    b.button(text="➕ Добавить конфиг", callback_data=f"padd_ib_info")
    b.button(text="🗑 Удалить сервер", callback_data=f"pdel_srv_{pid}")
    b.button(text="◀️ К списку", callback_data="adm_panels")
    b.adjust(2, 1, 1)

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    await callback.answer()

@router.callback_query(F.data == "padd_ib_info")
async def padd_ib_info(callback: CallbackQuery):
    await callback.message.answer("Для добавления конфига (Inbound) используй команду: /addinbound")
    await callback.answer()

@router.callback_query(F.data.startswith("pdel_srv_"))
async def pdel_srv(callback: CallbackQuery):
    pid = int(callback.data.split("_")[-1])
    from database import delete_panel
    await delete_panel(pid)
    await callback.answer("✅ Сервер удален", show_alert=True)
    await adm_panels(callback)

@router.callback_query(F.data.startswith("pdel_ib_"))
async def pdel_ib(callback: CallbackQuery):
    # Format: pdel_ib_PID_IID
    parts = callback.data.split("_")
    pid, iid = int(parts[2]), parts[3]

    p = await get_panel(pid)
    if not p: return

    ibs = p['inbounds']
    if iid in ibs: del ibs[iid]

    # Также убираем из списков ID
    ib_ids = [x for x in p['inbound_ids'] if str(x) != str(iid)]
    bib_ids = [x for x in p['billing_inbound_ids'] if str(x) != str(iid)]

    await update_panel_inbounds(pid, ib_ids, bib_ids, ibs)
    await callback.answer(f"✅ Конфиг {iid} удален")
    await panel_cfg(callback)

# ── Добавление серверов ─────────────────────────────────────────────────────────

@router.message(Command("addserver"))
async def cmd_addserver(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.set_state(AddServerStates.waiting_name)
    await message.answer("Название сервера (например, Финляндия):")

@router.message(AddServerStates.waiting_name)
async def process_server_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AddServerStates.waiting_host)
    await message.answer("IP или домен API (например, 1.2.3.4):")

@router.message(AddServerStates.waiting_host)
async def process_server_host(message: Message, state: FSMContext):
    await state.update_data(host=message.text)
    await state.set_state(AddServerStates.waiting_port)
    await message.answer("Порт панели (например, 2053):")

@router.message(AddServerStates.waiting_port)
async def process_server_port(message: Message, state: FSMContext):
    try:
        await state.update_data(port=int(message.text))
        await state.set_state(AddServerStates.waiting_path)
        await message.answer("Путь панели (обычно / или /xui):")
    except: await message.answer("Числом, пожалуйста:")

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
    await callback.answer()

@router.callback_query(AddServerStates.waiting_auth_method, F.data == "auth_token")
async def auth_method_token(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddServerStates.waiting_api_token)
    await callback.message.edit_text("API Token:")
    await callback.answer()

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
    if not panels: return
    b = InlineKeyboardBuilder()
    for p in panels: b.button(text=f"{p['name']}", callback_data=f"selpanel_{p['id']}")
    await state.set_state(AddInboundStates.waiting_panel_selection)
    await message.answer("Выбери сервер:", reply_markup=b.as_markup())

@router.callback_query(AddInboundStates.waiting_panel_selection, F.data.startswith("selpanel_"))
async def process_panel_selection(callback: CallbackQuery, state: FSMContext):
    await state.update_data(panel_id=int(callback.data.split("_")[1]))
    await state.set_state(AddInboundStates.waiting_json)
    await callback.message.edit_text("Отправь JSON инбаунда (из 3X-UI -> Export):")
    await callback.answer()

@router.message(AddInboundStates.waiting_json)
async def process_inbound_json(message: Message, state: FSMContext):
    try:
        inbound = json.loads(message.text)
        iid, prot = inbound.get("id"), str(inbound.get("protocol", "")).lower()
        if prot == "shadowsocks": prot = "ss"
        stream = inbound.get("streamSettings", {})
        cfg = {
            "label": inbound.get("remark", f"Inbound {iid}"),
            "protocol": prot, "port": inbound.get("port"),
            "network": stream.get("network", "tcp"), "security": stream.get("security", "none")
        }
        if cfg["security"] == "reality":
            rs = stream.get("realitySettings", {})
            cfg.update({"public_key": rs.get("settings", {}).get("publicKey", ""), "fingerprint": rs.get("settings", {}).get("fingerprint", "chrome"), "sni": rs.get("serverNames", [""])[0], "short_id": rs.get("shortIds", [""])[0]})
            cls = inbound.get("settings", {}).get("clients", [])
            cfg["flow"] = cls[0].get("flow", "") if cls else ""
        elif prot == "ss":
            cfg.update({"method": inbound.get("settings", {}).get("method"), "password": inbound.get("settings", {}).get("password")})

        data = await state.get_data()
        panel = await get_panel(data['panel_id'])
        inbounds, ib_ids, bib_ids = panel['inbounds'], panel['inbound_ids'], panel['billing_inbound_ids']
        inbounds[iid] = cfg
        if iid not in ib_ids and prot == "vless": ib_ids.append(iid)
        if iid not in bib_ids: bib_ids.append(iid)
        await update_panel_inbounds(panel['id'], ib_ids, bib_ids, inbounds)
        await message.answer(f"✅ Конфиг {iid} ({prot}) добавлен.")
    except Exception as e: await message.answer(f"❌ Ошибка: {e}")
    await state.clear()
