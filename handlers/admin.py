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
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_IDS, PRICE_PER_GB, SERVER_COST_RUB, CREDIT_LIMIT_RUB
from database import (
    get_all_users, get_user, update_user, add_balance,
    add_transaction, get_revenue_stats, create_promo, get_all_promos,
)
import xui as XUI
from billing import billing_tick, fmt_bytes
from keyboards import kb_admin, kb_back, kb_new_user, kb_new_user

router = Router()
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
