# Admin panel handlers for managing users, adding balances, banning, and checking stats.
"""
handlers/admin.py — Admin panel.

Изменения:
  - Убраны все ссылки на trial/pending (пробного периода нет).
  - active count использует CREDIT_LIMIT_RUB вместо trial_ok.
  - Убраны команды /pending и кнопка 📬 Заявки.
"""
import asyncio
import logging
import random
import string
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
    waiting_login = State()
    waiting_password = State()
    waiting_server_host = State()

class AddInboundStates(StatesGroup):
    waiting_panel_selection = State()
    waiting_json = State()

logger = logging.getLogger(__name__)


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


# ── /admin ─────────────────────────────────────────────────────────────────────

@router.message(Command("admin"))
# Command /admin handler: shows the administrator dashboard.
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    users  = await get_all_users()
    active = sum(
        1 for u in users
        if u.get("vless_uuid") and u.get("balance", 0) > CREDIT_LIMIT_RUB
    )
    await message.answer(
        f"👑 <b>Панель администратора</b>\n\n"
        f"👥 Всего: {len(users)} | Активных: {active}\n"
        f"💳 Кредитный лимит: −{abs(CREDIT_LIMIT_RUB):.0f} ₽\n\n"
        f"/addpromo КОД РУБ USES\n"
        f"/reply ID текст\n"
        f"/broadcast текст\n"
        f"/setbalance ID СУММА\n"
        f"/ban ID | /unban ID\n"
        f"/billing — принудительное списание\n"
        f"/debugtraffic — диагностика billing\n"
        f"/userinfo ID — полная информация о пользователе",
        parse_mode="HTML",
        reply_markup=kb_admin(),
    )


# ── Billing и диагностика ──────────────────────────────────────────────────────

@router.message(Command("billing"))
async def cmd_billing(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    await message.answer("⏳ Запускаю billing tick...")
    await billing_tick(bot)
    await message.answer("✅ Готово. Проверь логи.")


@router.message(Command("debugtraffic"))
async def cmd_debugtraffic(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("⏳ Запрашиваю трафик из 3X-UI...")

    traffic = await XUI.get_traffic()
    users   = await get_all_users()

    if traffic is None:
        await message.answer("❌ Не удалось получить данные из 3X-UI")
        return

    lines = [f"🔍 <b>Диагностика трафика</b>\n"]
    lines.append(f"<b>В 3X-UI ({len(traffic)} клиентов):</b>")
    for email, bytes_ in list(traffic.items())[:20]:
        lines.append(f"  • {email}: {fmt_bytes(bytes_)}")

    lines.append(f"\n<b>В БД ({len(users)} пользователей):</b>")
    for u in users[:20]:
        if u.get("vless_uuid"):
            expected = f"user_{u['tg_id']}"
            found    = "✅" if expected in traffic else "❌"
            lines.append(
                f"  {found} {expected} | "
                f"last={fmt_bytes(u.get('last_traffic_bytes') or 0)} | "
                f"bal={u.get('balance', 0):.1f}₽"
            )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("userinfo"))
async def cmd_userinfo(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Формат: /userinfo TELEGRAM_ID")
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer("ID должен быть числом")
        return

    u = await get_user(uid)
    if not u:
        await message.answer("Пользователь не найден")
        return

    email   = f"user_{uid}"
    traffic = await XUI.get_client_traffic(email)

    configs  = u.get("configs_all", "") or u.get("vless_link", "")
    cfg_list = [l for l in configs.split("\n") if l.strip()]

    active = "✅" if u.get("balance", 0) > CREDIT_LIMIT_RUB else "❌"
    lines  = [
        f"👤 <b>Пользователь {uid}</b>",
        f"Username: @{u.get('username') or '—'}",
        f"Имя: {u.get('full_name') or '—'}",
        f"Статус: {active}",
        f"Баланс: <b>{u.get('balance', 0):.2f} ₽</b>",
        f"UUID: <code>{u.get('vless_uuid') or '—'}</code>",
        f"Sub URL: <code>{u.get('sub_url') or '—'}</code>",
        f"Бан: {'🚫 Да' if u.get('is_banned') else '✅ Нет'}",
        f"Трафик (БД): {fmt_bytes(u.get('total_traffic_bytes') or 0)}",
    ]
    if traffic:
        lines.append(
            f"Трафик (панель): "
            f"⬆️{fmt_bytes(traffic['up'])} ⬇️{fmt_bytes(traffic['down'])}"
        )
    if cfg_list:
        lines.append(f"\n<b>Конфиги ({len(cfg_list)}):</b>")
        for lnk in cfg_list:
            lines.append(f"<code>{lnk}</code>")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ── Управление пользователями ──────────────────────────────────────────────────

@router.message(Command("setbalance"))
# Command /setbalance handler: allows admin to modify user balances.
async def cmd_setbalance(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Формат: /setbalance ID СУММА")
        return
    try:
        uid = int(parts[1])
        amt = float(parts[2])
    except ValueError:
        await message.answer("ID и СУММА должны быть числами")
        return
    await add_balance(uid, amt)
    await add_transaction(uid, amt, "admin", f"Администратор: {amt:+.2f} ₽")
    if amt > 0:
        await update_user(uid, notified_low_balance=0)
    await message.answer(f"✅ Баланс {uid} изменён на {amt:+.2f} ₽")
    asyncio.create_task(billing_tick(bot))


@router.message(Command("ban"))
async def cmd_ban(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Формат: /ban TELEGRAM_ID")
        return
    uid = int(parts[1])
    u   = await get_user(uid)
    if not u:
        await message.answer("Пользователь не найден")
        return
    await update_user(uid, is_banned=1, xui_enabled=0)
    if u.get("vless_uuid"):
        await XUI.toggle_client(
            f"user_{uid}", u["vless_uuid"],
            enable=False, sub_id=u.get("sub_id", ""),
        )
    await message.answer(f"🚫 Пользователь {uid} заблокирован.")


@router.message(Command("unban"))
async def cmd_unban(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Формат: /unban TELEGRAM_ID")
        return
    uid = int(parts[1])
    await update_user(uid, is_banned=0, xui_enabled=1)
    u = await get_user(uid)
    if u and u.get("vless_uuid"):
        await XUI.toggle_client(
            f"user_{uid}", u["vless_uuid"],
            enable=True, sub_id=u.get("sub_id", ""),
        )
    await message.answer(f"✅ Пользователь {uid} разблокирован.")


@router.message(Command("reply"))
async def cmd_reply(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /reply TELEGRAM_ID текст")
        return
    try:
        await bot.send_message(
            int(parts[1]),
            f"📩 <b>Ответ поддержки:</b>\n\n{parts[2]}",
            parse_mode="HTML",
        )
        await message.answer("✅ Отправлено.")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    text = message.text.replace("/broadcast", "").strip()
    if not text:
        await message.answer("Формат: /broadcast текст")
        return
    users = await get_all_users()
    sent  = 0
    for u in users:
        try:
            await bot.send_message(u["tg_id"], f"📢 {text}")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await message.answer(f"✅ Отправлено {sent} пользователям")


# ── Промокоды ──────────────────────────────────────────────────────────────────

@router.message(Command("addpromo"))
async def cmd_addpromo(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 4:
        await message.answer(
            "Формат: /addpromo КОД РУБЛЕЙ ИСПОЛЬЗОВАНИЙ\n_ = авто-код"
        )
        return
    code = (
        parts[1].upper()
        if parts[1] != "_"
        else "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    )
    try:
        bonus = float(parts[2])
        uses  = int(parts[3])
    except ValueError:
        await message.answer("РУБЛЕЙ и ИСПОЛЬЗОВАНИЙ должны быть числами")
        return
    await create_promo(code, bonus, uses, message.from_user.id)
    await message.answer(
        f"✅ Промокод создан!\n"
        f"Код: <code>{code}</code>\n"
        f"Бонус: <b>{bonus:.0f} ₽</b> | Использований: <b>{uses}</b>",
        parse_mode="HTML",
    )


# ── Inline-кнопки панели ───────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_users")
async def adm_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    users = await get_all_users()
    lines = [f"👥 <b>Пользователи ({len(users)}):</b>\n"]
    for u in users[:40]:
        bal    = u.get("balance", 0)
        status = "✅" if bal > CREDIT_LIMIT_RUB else "❌"
        tb     = fmt_bytes(u.get("total_traffic_bytes") or 0)
        name   = (u.get("full_name") or u.get("username") or str(u["tg_id"]))[:18]
        ref    = "👥" if u.get("referred_by") else ""
        banned = "🚫" if u.get("is_banned") else ""
        lines.append(
            f"{status} <code>{u['tg_id']}</code> {name} {ref}{banned}\n"
            f"   {bal:.1f}₽ | {tb}"
        )

    text = "\n".join(lines)
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data="adm_back")
    markup = b.as_markup()

    if len(text) <= 4000:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        parts = []
        chunk: list[str] = [lines[0]]
        for line in lines[1:]:
            if sum(len(l) + 1 for l in chunk) + len(line) > 3800:
                parts.append("\n".join(chunk))
                chunk = []
            chunk.append(line)
        if chunk:
            parts.append("\n".join(chunk))
        await callback.message.edit_text(parts[0], parse_mode="HTML")
        for part in parts[1:]:
            await callback.message.answer(part, parse_mode="HTML")
        await callback.message.answer("—", reply_markup=markup)

    await callback.answer()


@router.callback_query(F.data == "adm_revenue")
async def adm_revenue(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    s      = await get_revenue_stats()
    fmt    = lambda v: f"{v:.2f} ₽" if v else "0.00 ₽"
    profit = (s.get("month") or 0) - SERVER_COST_RUB
    b      = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data="adm_back")
    await callback.message.edit_text(
        f"📊 <b>Финансы</b>\n\n"
        f"Сегодня: <b>{fmt(s.get('today'))}</b>\n"
        f"7 дней:  <b>{fmt(s.get('week'))}</b>\n"
        f"Месяц:   <b>{fmt(s.get('month'))}</b>\n"
        f"Всего:   <b>{fmt(s.get('total'))}</b>\n\n"
        f"Расходы: {SERVER_COST_RUB:.0f} ₽/мес\n"
        f"Прибыль: <b>{profit:+.2f} ₽</b>",
        parse_mode="HTML",
        reply_markup=b.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm_promos")
async def adm_promos(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    promos = await get_all_promos()
    b      = InlineKeyboardBuilder()
    b.button(text="➕ /addpromo КОД РУБ USES", callback_data="adm_new_promo")
    b.button(text="◀️ Назад",                  callback_data="adm_back")
    b.adjust(1)
    if not promos:
        await callback.message.edit_text("🎁 Промокодов нет.", reply_markup=b.as_markup())
        await callback.answer()
        return
    lines = ["🎁 <b>Промокоды:</b>\n"]
    for p in promos:
        icon = "✅" if p["used_count"] < p["max_uses"] else "❌"
        lines.append(
            f"{icon} <code>{p['code']}</code> — "
            f"{p['bonus_rub']:.0f} ₽ | {p['used_count']}/{p['max_uses']}"
        )
    await callback.message.edit_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=b.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data == "adm_new_promo")
async def adm_new_promo(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data="adm_promos")
    await callback.message.edit_text(
        "➕ <b>Создать промокод:</b>\n\n"
        "<code>/addpromo КОД РУБЛЕЙ USES</code>\n"
        "<code>/addpromo _ 100 5</code> — авто-код",
        parse_mode="HTML",
        reply_markup=b.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm_back")
async def adm_back(callback: CallbackQuery):
    await callback.message.edit_text(
        "👑 <b>Панель администратора</b>",
        parse_mode="HTML",
        reply_markup=kb_admin(),
    )
    await callback.answer()


# ── Быстрые действия из уведомления о новом пользователе ─────────────────────
# Кнопки: 📋 Профиль | 💳 +50₽ | 🚫 Бан
# Нажатие обновляет карточку деталей прямо в чате администратора.

@router.callback_query(F.data.startswith("adm_userinfo_"))
async def cb_adm_userinfo(callback: CallbackQuery):
    """Показывает полный профиль пользователя прямо в уведомлении."""
    if not is_admin(callback.from_user.id):
        return
    uid = int(callback.data.split("_")[-1])
    u   = await get_user(uid)

    if not u:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    bal     = u.get("balance", 0)
    active  = "✅ Активен" if bal > CREDIT_LIMIT_RUB else "❌ Отключён"
    tb      = fmt_bytes(u.get("total_traffic_bytes") or 0)
    sub_url = u.get("sub_url", "—")

    # Актуальный трафик из панели
    traffic = await XUI.get_client_traffic(f"user_{uid}")
    traffic_line = (
        f"⬆️ {fmt_bytes(traffic['up'])} ⬇️ {fmt_bytes(traffic['down'])}"
        if traffic else f"📦 {tb} (из БД)"
    )

    text = (
        f"👤 <b>Профиль</b> · "
        f"<a href='tg://user?id={uid}'>{u.get('full_name') or uid}</a>\n\n"
        f"🆔 <code>{uid}</code> · @{u.get('username') or '—'}\n"
        f"Статус: {active}\n"
        f"💰 Баланс: <b>{bal:.2f} ₽</b>\n"
        f"Трафик: {traffic_line}\n"
        f"🔗 <code>{sub_url}</code>"
    )

    # Кнопки остаются — плюс кнопка «назад» к исходному виду
    b = InlineKeyboardBuilder()
    b.button(text="💳 +50 ₽",  callback_data=f"adm_gift_{uid}")
    b.button(text="🚫 Бан",     callback_data=f"adm_ban_{uid}")
    b.button(text="◀️ Назад",   callback_data=f"adm_card_{uid}")
    b.adjust(2, 1)

    await callback.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=b.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("adm_gift_"))
# Grants a 50 RUB gift to the user directly from the new user notification.
async def cb_adm_gift(callback: CallbackQuery, bot: Bot):
    """Зачисляет 50₽ пользователю и обновляет карточку."""
    if not is_admin(callback.from_user.id):
        return
    uid  = int(callback.data.split("_")[-1])
    u    = await get_user(uid)

    if not u:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    gift = abs(CREDIT_LIMIT_RUB)
    await add_balance(uid, gift)
    await add_transaction(uid, gift, "admin_gift",
                          f"Подарок от администратора {callback.from_user.id}")
    await update_user(uid, notified_low_balance=0)

    # Уведомляем пользователя
    try:
        await bot.send_message(
            uid,
            f"🎁 Администратор начислил тебе <b>{gift:.0f} ₽</b>!",
            parse_mode="HTML",
        )
    except Exception:
        pass

    # Перезапускаем billing чтобы сразу включить VPN если был отключён
    asyncio.create_task(billing_tick(bot))

    fresh_bal = (await get_user(uid) or {}).get("balance", gift)
    await callback.answer(f"✅ Начислено {gift:.0f} ₽", show_alert=False)

    # Обновляем карточку — меняем кнопку на подтверждение
    b = InlineKeyboardBuilder()
    b.button(text=f"✅ Выдано {gift:.0f} ₽ · баланс {fresh_bal:.2f} ₽",
             callback_data=f"adm_card_{uid}")
    b.button(text="🚫 Бан", callback_data=f"adm_ban_{uid}")
    b.button(text="📋 Профиль", callback_data=f"adm_userinfo_{uid}")
    b.adjust(1, 2)

    try:
        await callback.message.edit_reply_markup(reply_markup=b.as_markup())
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm_ban_"))
async def cb_adm_ban(callback: CallbackQuery):
    """Банит пользователя и обновляет карточку."""
    if not is_admin(callback.from_user.id):
        return
    uid = int(callback.data.split("_")[-1])
    u   = await get_user(uid)

    if not u:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    if u.get("is_banned"):
        await callback.answer("Уже заблокирован", show_alert=False)
        return

    await update_user(uid, is_banned=1, xui_enabled=0)
    if u.get("vless_uuid"):
        await XUI.toggle_client(
            f"user_{uid}", u["vless_uuid"],
            enable=False, sub_id=u.get("sub_id", ""),
        )
    logger.info(f"Админ {callback.from_user.id} заблокировал пользователя {uid}")

    await callback.answer("🚫 Забанен", show_alert=False)

    # Обновляем кнопки — убираем Бан, добавляем Разбан
    b = InlineKeyboardBuilder()
    b.button(text="📋 Профиль",   callback_data=f"adm_userinfo_{uid}")
    b.button(text="💳 +50 ₽",    callback_data=f"adm_gift_{uid}")
    b.button(text="✅ Разбанить", callback_data=f"adm_unban_{uid}")
    b.adjust(2, 1)

    try:
        await callback.message.edit_reply_markup(reply_markup=b.as_markup())
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm_unban_"))
async def cb_adm_unban(callback: CallbackQuery):
    """Разбанивает пользователя прямо из карточки."""
    if not is_admin(callback.from_user.id):
        return
    uid = int(callback.data.split("_")[-1])

    await update_user(uid, is_banned=0, xui_enabled=1)
    u = await get_user(uid)
    if u and u.get("vless_uuid"):
        await XUI.toggle_client(
            f"user_{uid}", u["vless_uuid"],
            enable=True, sub_id=u.get("sub_id", ""),
        )
    logger.info(f"Админ {callback.from_user.id} разблокировал пользователя {uid}")
    await callback.answer("✅ Разблокирован", show_alert=False)

    # Возвращаем кнопку Бан
    b = InlineKeyboardBuilder()
    b.button(text="📋 Профиль", callback_data=f"adm_userinfo_{uid}")
    b.button(text="💳 +50 ₽",  callback_data=f"adm_gift_{uid}")
    b.button(text="🚫 Бан",     callback_data=f"adm_ban_{uid}")
    b.adjust(3)

    try:
        await callback.message.edit_reply_markup(reply_markup=b.as_markup())
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm_card_"))
async def cb_adm_card(callback: CallbackQuery):
    """Возвращает карточку к исходному виду (после Профиля)."""
    if not is_admin(callback.from_user.id):
        return
    uid = int(callback.data.split("_")[-1])
    u   = await get_user(uid)

    if not u:
        await callback.answer()
        return

    bal          = u.get("balance", 0)
    active       = "✅" if bal > CREDIT_LIMIT_RUB else "❌"
    username_str = f"@{u['username']}" if u.get("username") else "нет username"

    text = (
        f"🆕 <b>Новый пользователь</b>\n\n"
        f"👤 <a href='tg://user?id={uid}'>{u.get('full_name') or uid}</a>\n"
        f"🆔 <code>{uid}</code> · {username_str}\n"
        f"Статус: {active} · {bal:.2f} ₽"
    )

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=kb_new_user(uid),
    )
    await callback.answer()


# ── Добавление серверов и конфигов ─────────────────────────────────────────────────────────────

@router.message(Command("addserver"))
async def cmd_addserver(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.set_state(AddServerStates.waiting_name)
    await message.answer("Введи понятное название сервера (например, Server 1 (Finland)):")

@router.message(AddServerStates.waiting_name)
async def process_server_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AddServerStates.waiting_host)
    await message.answer("Введи IP или домен для API (например, 127.0.0.1 или panel.domain.com):")

@router.message(AddServerStates.waiting_host)
async def process_server_host_api(message: Message, state: FSMContext):
    await state.update_data(host=message.text)
    await state.set_state(AddServerStates.waiting_port)
    await message.answer("Введи порт API 3X-UI (например, 29870):")

@router.message(AddServerStates.waiting_port)
async def process_server_port(message: Message, state: FSMContext):
    try:
        port = int(message.text)
        await state.update_data(port=port)
        await state.set_state(AddServerStates.waiting_path)
        await message.answer("Введи секретный путь панели (например, /secretpath):")
    except ValueError:
        await message.answer("Порт должен быть числом. Попробуй еще раз:")

@router.message(AddServerStates.waiting_path)
async def process_server_path(message: Message, state: FSMContext):
    path = message.text
    if not path.startswith('/'): path = '/' + path
    await state.update_data(path=path)
    await state.set_state(AddServerStates.waiting_login)
    await message.answer("Введи логин администратора:")

@router.message(AddServerStates.waiting_login)
async def process_server_login(message: Message, state: FSMContext):
    await state.update_data(login=message.text)
    await state.set_state(AddServerStates.waiting_password)
    await message.answer("Введи пароль администратора:")

@router.message(AddServerStates.waiting_password)
async def process_server_password(message: Message, state: FSMContext):
    await state.update_data(password=message.text)
    await state.set_state(AddServerStates.waiting_server_host)
    await message.answer("Введи публичный IP сервера (для генерации ссылок, например, 138.124.110.49):")

@router.message(AddServerStates.waiting_server_host)
async def process_server_server_host(message: Message, state: FSMContext):
    await state.update_data(server_host=message.text)
    data = await state.get_data()

    # Check connection
    import aiohttp
    import json

    panel_test = {
        "host": data['host'], "port": data['port'], "path": data['path'],
        "login": data['login'], "password": data['password'], "name": data['name']
    }

    await message.answer("⏳ Проверяю подключение...")
    async with XUI._new_session() as sess:
        ok = await XUI._login(sess, panel_test)
        if ok:
            panel_id = await add_panel(data['name'], data['host'], data['port'], data['path'], data['login'], data['password'], data['server_host'])
            await message.answer(f"✅ Сервер успешно добавлен! ID в БД: {panel_id}\n\nТеперь можно добавлять конфиги с помощью /addinbound.")


        else:
            await message.answer("❌ Не удалось подключиться к панели с указанными данными. Сервер не добавлен.")

    await state.clear()


@router.message(Command("addinbound"))
async def cmd_addinbound(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    panels = await get_all_panels()
    if not panels:
        await message.answer("Нет добавленных серверов. Сначала добавь сервер через /addserver.")
        return

    b = InlineKeyboardBuilder()
    for p in panels:
        b.button(text=f"{p['name']} ({p['host']})", callback_data=f"selpanel_{p['id']}")
    b.adjust(1)

    await state.set_state(AddInboundStates.waiting_panel_selection)
    await message.answer("Выбери сервер, к которому хочешь добавить inbound-конфиг:", reply_markup=b.as_markup())

@router.callback_query(AddInboundStates.waiting_panel_selection, F.data.startswith("selpanel_"))
async def process_panel_selection(callback: CallbackQuery, state: FSMContext):
    panel_id = int(callback.data.split("_")[1])
    await state.update_data(panel_id=panel_id)
    await state.set_state(AddInboundStates.waiting_json)
    await callback.message.edit_text("Отлично! Теперь отправь мне сырой JSON инбаунда из 3X-UI панели (включая id, port, protocol, settings, streamSettings).")
    await callback.answer()

@router.message(AddInboundStates.waiting_json)
async def process_inbound_json(message: Message, state: FSMContext):
    try:
        data = message.text
        import json
        inbound = json.loads(data)

        iid = inbound.get("id")
        port = inbound.get("port")
        protocol = inbound.get("protocol")
        stream = inbound.get("streamSettings", {})
        network = stream.get("network", "tcp")
        security = stream.get("security", "none")

        cfg = {
            "label": inbound.get("remark", f"Inbound {iid}"),
            "protocol": protocol,
            "port": port,
            "network": network,
            "security": security
        }

        if security == "reality":
            reality = stream.get("realitySettings", {})
            settings = reality.get("settings", {})
            cfg["public_key"] = settings.get("publicKey", "")
            cfg["fingerprint"] = settings.get("fingerprint", "chrome")
            cfg["sni"] = reality.get("serverNames", [""])[0] if reality.get("serverNames") else ""
            cfg["short_id"] = reality.get("shortIds", [""])[0] if reality.get("shortIds") else ""

            # extract flow from first client if exists
            clients = inbound.get("settings", {}).get("clients", [])
            if clients and clients[0].get("flow"):
                cfg["flow"] = clients[0].get("flow")
            else:
                cfg["flow"] = ""

        elif security == "tls":
            tls = stream.get("tlsSettings", {})
            cfg["sni"] = tls.get("serverName", "")

        if network == "xhttp":
            xhttp = stream.get("xhttpSettings", {})
            cfg["path"] = xhttp.get("path", "/")
            cfg["xhttp_mode"] = xhttp.get("mode", "auto")
        elif network == "grpc":
            grpc = stream.get("grpcSettings", {})
            cfg["grpc_service"] = grpc.get("serviceName", "grpc")
        elif network == "ws":
            ws = stream.get("wsSettings", {})
            cfg["path"] = ws.get("path", "/")
            cfg["ws_host"] = ws.get("headers", {}).get("Host", "")

        elif protocol == "ss":
            ss = inbound.get("settings", {})
            cfg["method"] = ss.get("method", "chacha20-poly1305")
            cfg["password"] = ss.get("password", "")


        # Save to DB
        s_data = await state.get_data()
        from database import get_panel, update_panel_inbounds
        panel = await get_panel(s_data['panel_id'])

        if not panel:
            await message.answer("Ошибка: Сервер не найден в БД.")
            await state.clear()
            return

        inbounds = panel['inbounds']
        inbounds[iid] = cfg

        inbound_ids = panel['inbound_ids']
        if iid not in inbound_ids and protocol == "vless":
            inbound_ids.append(iid)

        billing_inbound_ids = panel['billing_inbound_ids']
        if iid not in billing_inbound_ids:
            billing_inbound_ids.append(iid)

        await update_panel_inbounds(panel['id'], inbound_ids, billing_inbound_ids, inbounds)

        await message.answer(f"✅ Инбаунд {iid} успешно добавлен и распарсен:\n<pre>{json.dumps(cfg, indent=2)}</pre>", parse_mode="HTML")
        await state.clear()

    except json.JSONDecodeError:
        await message.answer("❌ Это не похоже на валидный JSON. Проверь скобки и запятые.")
    except Exception as e:
        await message.answer(f"❌ Произошла ошибка при разборе конфига: {e}")
