# User handlers for registration, checking stats, top-ups, and getting VPN configs.
"""
handlers/user.py — User handlers.

Изменения:
  - /start: конфиг создаётся сразу для каждого нового пользователя,
    sub_url отображается прямо в приветственном сообщении.
  - Убран cb_free_bonus: его роль играет кредитный лимит −50₽.
  - Убран trial_ok: статус определяется только через CREDIT_LIMIT_RUB.
  - Все сообщения упоминают лимит −50₽ (≈16 ГБ бесплатно).
  - successful_payment: конфиг всегда создаётся на /start, здесь только
    зачисление баланса и повторный показ sub_url.
"""
import asyncio
import logging
from datetime import datetime
from math import ceil

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, LabeledPrice, PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    ADMIN_IDS, ADMIN_USERNAME, BOT_USERNAME,
    PRICE_PER_GB, CREDIT_LIMIT_RUB,
    STARS_RUB_NET, REFERRAL_PERCENT,
)
from database import (
    get_or_create_user, get_user, update_user, add_balance,
    add_transaction, get_promo, promo_already_used, use_promo,
    add_referral_reward, get_referral_stats, save_crypto_invoice,
)
import cryptobot as CryptoBot
import xui as XUI
from keyboards import kb_main, kb_back, kb_topup_amount, kb_topup_method, kb_new_user
from billing import billing_tick, fmt_bytes

router = Router()
logger = logging.getLogger(__name__)

_CREDIT_ABS = abs(CREDIT_LIMIT_RUB)
_CREDIT_GB  = _CREDIT_ABS / PRICE_PER_GB


class UserStates(StatesGroup):
    waiting_promo = State()


# ── Хелпер: сохранение нового конфига ─────────────────────────────────────────

async def _save_client(uid: int, result: dict, sub_type: str) -> str:
    """Сохраняет данные нового клиента из XUI.add_client() в БД."""
    sub_url = XUI.make_sub_url(result["sub_id"])
    vless   = result["configs"].split("\n")[0] if result["configs"] else ""
    await update_user(uid,
        vless_uuid  = result["uuid"],
        sub_id      = result["sub_id"],
        vless_link  = vless,
        configs_all = result["configs"],
        sub_url     = sub_url,
        sub_type    = sub_type,
        xui_enabled = 1,
    )
    return sub_url


# ── Хелпер: уведомление админов о новом пользователе ─────────────────────────

async def _notify_admins_new_user(
    bot: Bot,
    message: Message,
    referred_by: int | None,
) -> None:
    """
    Отправляет каждому администратору:
    1. Forward сообщения /start — Telegram рендерит его как кликабельный
       «контакт»: тап на имя отправителя открывает профиль пользователя.
    2. Карточку с деталями + кнопки быстрых действий (Профиль / +50₽ / Бан).

    Если forward заблокирован настройками приватности пользователя,
    inline-ссылка tg://user?id=… в карточке деталей всё равно работает.
    """
    u   = message.from_user
    uid = u.id

    # Источник регистрации
    if referred_by:
        source = f"👥 Реферал · от <code>{referred_by}</code>"
    else:
        source = "🌱 Органический"

    # Username (может отсутствовать)
    username_line = f"@{u.username}" if u.username else "нет username"

    details = (
        f"🆕 <b>Новый пользователь</b>\n\n"
        f"👤 <a href='tg://user?id={uid}'>{u.full_name}</a>\n"
        f"🆔 <code>{uid}</code> · {username_line}\n"
        f"🌐 Язык: {u.language_code or '—'}\n"
        f"📥 {source}\n"
        f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )

    for admin_id in ADMIN_IDS:
        # Шаг 1 — forward (опционально: может упасть из-за настроек приватности)
        try:
            await bot.forward_message(
                chat_id=admin_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
        except Exception as e:
            logger.warning(
                f"Forward /start для admin {admin_id} не удался "
                f"(вероятно, приватность пользователя): {e}"
            )

        # Шаг 2 — детали + кнопки (основное уведомление, должно дойти всегда)
        try:
            await bot.send_message(
                admin_id,
                details,
                parse_mode="HTML",
                reply_markup=kb_new_user(uid),
            )
            logger.info(
                f"Уведомление о новом юзере {uid} отправлено админу {admin_id}"
            )
        except Exception as e:
            logger.error(
                f"Не удалось отправить карточку нового юзера {uid} "
                f"админу {admin_id}: {e}"
            )


# ── /start ─────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
# Command /start handler: registers user and automatically provisions a VPN config.
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    uid = message.from_user.id

    # Парсим реферальный код
    referred_by = None
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            rid = int(args[1][4:])
            if rid != uid:
                referred_by = rid
        except ValueError:
            pass

    user, is_new = await get_or_create_user(
        uid,
        message.from_user.username  or "",
        message.from_user.full_name or "",
        referred_by=referred_by,
    )

    # Реферальное уведомление — только при реальной регистрации
    if is_new and referred_by:
        try:
            await bot.send_message(
                referred_by,
                f"👥 По твоей ссылке зарегистрировался новый пользователь!\n"
                f"Ты получишь <b>{int(REFERRAL_PERCENT * 100)}%</b> "
                f"с его пополнений.",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # ── Создаём конфиг сразу для каждого нового пользователя ─────────────────
    sub_url = user.get("sub_url", "")
    if not user.get("vless_uuid"):
        result = await XUI.add_client(f"user_{uid}", expire_days=0)
        if result:
            sub_url = await _save_client(uid, result, "auto")
            logger.info(f"cmd_start: конфиг создан для нового пользователя {uid}")
        else:
            logger.error(f"cmd_start: не удалось создать конфиг для {uid}")

    # ── Уведомление администратора о новом пользователе ──────────────────────
    if is_new:
        await _notify_admins_new_user(bot, message, referred_by)

    # ── Приветственное сообщение ──────────────────────────────────────────────
    bal = user["balance"]
    sub_block = (
        f"\n🔗 <b>Твоя подписка:</b>\n<code>{sub_url}</code>\n\n"
        f"📱 v2rayTUN / Hiddify / v2rayN → + → Добавить подписку\n"
        if sub_url else
        "\n⏳ Конфиг создаётся, попробуй через несколько секунд.\n"
    )
    await message.answer(
        f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
        f"🛡 <b>Dobrinya VPN</b> — 🇫🇮 Финляндия\n"
        f"Тариф: <b>{PRICE_PER_GB} ₽/ГБ</b> · "
        f"Кредит: <b>~{_CREDIT_GB:.0f} ГБ</b> бесплатно\n\n"
        f"💰 Баланс: <b>{bal:.2f} ₽</b>"
        + sub_block +
        f"⚠️ VPN отключается при балансе <b>−{_CREDIT_ABS:.0f} ₽</b>",
        parse_mode="HTML",
        reply_markup=kb_main(),
    )


@router.callback_query(F.data == "back_main")
async def cb_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user = await get_user(callback.from_user.id)
    bal  = (user["balance"] if user else 0.0)
    await callback.message.edit_text(
        f"💰 Баланс: <b>{bal:.2f} ₽</b>\n\nВыбери действие:",
        parse_mode="HTML",
        reply_markup=kb_main(),
    )
    await callback.answer()


# ── Аккаунт ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "my_account")
async def cb_my_account(callback: CallbackQuery):
    await _show_account(callback.from_user.id, callback.message, edit=True)
    await callback.answer()


@router.message(Command("my"))
async def cmd_my(message: Message):
    await _show_account(message.from_user.id, message, edit=False)


async def _show_account(uid: int, message: Message, edit: bool):
    user = await get_user(uid)
    fn   = message.edit_text if edit else message.answer

    if user is None:
        await fn("Нажми /start для регистрации.", reply_markup=kb_back())
        return

    bal   = user["balance"]
    total = user.get("total_traffic_bytes") or 0

    # Статус на основе кредитного лимита
    if bal > CREDIT_LIMIT_RUB:
        if bal >= 0:
            status = f"✅ Активен (~{bal / PRICE_PER_GB:.1f} ГБ)"
        else:
            # В кредите: показываем сколько осталось до отключения
            till_off = bal - CREDIT_LIMIT_RUB
            status   = (
                f"✅ Активен · кредит {bal:.2f} ₽ "
                f"(ещё ~{till_off / PRICE_PER_GB:.1f} ГБ до откл.)"
            )
    else:
        status = f"❌ Отключён (баланс ≤ −{_CREDIT_ABS:.0f} ₽)"

    sub_url = user.get("sub_url", "")

    b = InlineKeyboardBuilder()
    b.button(text="📋 Все конфиги",      callback_data="show_configs")
    b.button(text="💳 Пополнить баланс", callback_data="topup_start")
    b.button(text="◀️ В меню",           callback_data="back_main")
    b.adjust(1)

    await fn(
        f"👤 <b>Мой аккаунт</b>\n\n"
        f"Статус: {status}\n"
        f"💰 Баланс: <b>{bal:.2f} ₽</b>\n"
        f"📦 Использовано: <b>{fmt_bytes(total)}</b>\n\n"
        f"🔗 <b>Subscription URL:</b>\n<code>{sub_url}</code>\n\n"
        f"📱 v2rayTUN / Hiddify / v2rayN → + → Добавить подписку\n\n"
        f"⚠️ Отключение при балансе <b>−{_CREDIT_ABS:.0f} ₽</b>",
        parse_mode="HTML",
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data == "show_configs")
async def cb_show_configs(callback: CallbackQuery):
    uid  = callback.from_user.id
    user = await get_user(uid)
    if not user or not user.get("vless_uuid"):
        await callback.answer("Конфига нет.", show_alert=True)
        return

    configs = user.get("configs_all") or user.get("vless_link", "")
    if not configs:
        await callback.answer("Конфиг не найден.", show_alert=True)
        return

    links = [l.strip() for l in configs.split("\n") if l.strip()]
    lines = ["📋 <b>Конфиги для ручной настройки:</b>\n"]
    for link in links:
        if "reality" in link or ":14539" in link:
            icon = "⚡ Reality-TCP"
        elif "xhttp" in link or ":56224" in link:
            icon = "🌐 XHTTP-Reality"
        elif "grpc" in link:
            icon = "📡 gRPC"
        elif link.startswith("ss://"):
            icon = "🔒 Shadowsocks"
        else:
            icon = "🔗 VPN"
        lines.append(f"<b>{icon}:</b>\n<code>{link}</code>\n")

    await callback.message.edit_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=kb_back()
    )
    await callback.answer()


# ── Статистика трафика ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "traffic_stats")
async def cb_traffic_stats(callback: CallbackQuery):
    uid  = callback.from_user.id
    user = await get_user(uid)
    if not user or not user.get("vless_uuid"):
        await callback.answer("Конфига нет.", show_alert=True)
        return

    traffic = await XUI.get_client_traffic(f"user_{uid}")
    if traffic:
        up, down, total_live = traffic["up"], traffic["down"], traffic["total"]
    else:
        total_live = user.get("total_traffic_bytes") or 0
        up, down   = 0, total_live

    bal      = user["balance"]
    till_off = bal - CREDIT_LIMIT_RUB   # сколько ₽ осталось до отключения

    await callback.message.edit_text(
        f"📊 <b>Статистика трафика</b>\n\n"
        f"⬆️ Загружено:  <b>{fmt_bytes(up)}</b>\n"
        f"⬇️ Скачано:    <b>{fmt_bytes(down)}</b>\n"
        f"📦 Итого:      <b>{fmt_bytes(total_live)}</b>\n\n"
        f"💰 Баланс: <b>{bal:.2f} ₽</b>\n"
        f"Тариф: <b>{PRICE_PER_GB} ₽/ГБ</b>\n"
        f"До отключения: <b>{till_off:.2f} ₽</b> "
        f"(~{till_off / PRICE_PER_GB:.1f} ГБ)\n\n"
        f"⚠️ Отключение при <b>−{_CREDIT_ABS:.0f} ₽</b>",
        parse_mode="HTML",
        reply_markup=kb_back(),
    )
    await callback.answer()


# ── Пополнение баланса ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "topup_start")
async def cb_topup_start(callback: CallbackQuery):
    await callback.message.edit_text(
        f"💳 <b>Пополнение баланса</b>\n\n"
        f"Тариф: <b>{PRICE_PER_GB} ₽/ГБ</b>\n"
        f"Кредит: ~<b>{_CREDIT_GB:.0f} ГБ</b> бесплатно (лимит −{_CREDIT_ABS:.0f} ₽)\n\n"
        f"Выбери сумму:",
        parse_mode="HTML",
        reply_markup=kb_topup_amount(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("topup_amount_"))
async def cb_topup_amount(callback: CallbackQuery):
    rub = int(callback.data.split("_")[-1])
    await callback.message.edit_text(
        f"💳 <b>Пополнение: {rub} ₽</b> (~{rub / PRICE_PER_GB:.0f} ГБ)\n\n"
        f"Способ оплаты:",
        parse_mode="HTML",
        reply_markup=kb_topup_method(rub),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay_stars_"))
async def cb_pay_stars(callback: CallbackQuery, bot: Bot):
    rub          = int(callback.data.split("_")[-1])
    stars_needed = ceil(rub / STARS_RUB_NET)
    await bot.send_invoice(
        callback.from_user.id,
        title="Пополнение баланса Dobrinya VPN",
        description=f"{rub} ₽ (~{rub / PRICE_PER_GB:.0f} ГБ трафика)",
        payload=f"topup_{rub}",
        currency="XTR",
        prices=[LabeledPrice(label="Stars", amount=stars_needed)],
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay_crypto_"))
# Creates a CryptoBot invoice and sends the payment link to the user.
async def cb_pay_crypto(callback: CallbackQuery):
    """Создаёт счёт CryptoBot и отправляет пользователю ссылку для оплаты."""
    rub = int(callback.data.split("_")[-1])
    uid = callback.from_user.id

    await callback.message.edit_text(
        "₿ <b>Создаю счёт...</b>", parse_mode="HTML"
    )

    payload = f"topup_{uid}_{rub}"
    invoice = await CryptoBot.create_invoice(rub, payload)

    if not invoice:
        await callback.message.edit_text(
            "❌ <b>Не удалось создать счёт CryptoBot.</b>\n\n"
            "Попробуй позже или выбери другой способ оплаты.",
            parse_mode="HTML",
            reply_markup=kb_topup_method(rub),
        )
        await callback.answer()
        return

    await save_crypto_invoice(invoice["invoice_id"], uid, float(rub))

    b = InlineKeyboardBuilder()
    b.button(text=f"💸 Оплатить {rub} ₽", url=invoice["pay_url"])
    b.button(text="◀️ Назад", callback_data="topup_start")
    b.adjust(1)

    await callback.message.edit_text(
        f"₿ <b>Оплата через CryptoBot</b>\n\n"
        f"Сумма: <b>{rub} ₽</b> (~{rub / PRICE_PER_GB:.0f} ГБ)\n\n"
        f"Нажми кнопку ниже — откроется @CryptoBot с уже выставленным счётом.\n"
        f"Можно оплатить в USDT, TON, BTC, ETH и других криптовалютах.\n\n"
        f"⏳ Баланс пополнится автоматически в течение 30 секунд после оплаты.\n"
        f"🕐 Счёт действует <b>1 час</b>.",
        parse_mode="HTML",
        reply_markup=b.as_markup(),
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@router.message(F.successful_payment)
# Handles successful payment via Telegram Stars.
async def successful_payment(message: Message, bot: Bot):
    uid = message.from_user.id
    rub = int(message.successful_payment.invoice_payload.split("_")[1])

    await add_balance(uid, rub)
    await add_transaction(uid, rub, "stars", f"Telegram Stars {rub} ₽")
    # Сбрасываем флаг предупреждения — при следующем дипе ниже −30₽
    # пользователь получит уведомление снова
    await update_user(uid, notified_low_balance=0)

    # Реферальное вознаграждение
    user = await get_user(uid)
    if user and (referrer_id := user.get("referred_by")):
        reward = round(rub * REFERRAL_PERCENT, 2)
        await add_balance(referrer_id, reward)
        await add_transaction(referrer_id, reward, "referral",
                              f"Реферал {uid}: {reward:.2f} ₽")
        await add_referral_reward(referrer_id, uid, reward)
        try:
            await bot.send_message(
                referrer_id,
                f"🎉 Реферальный бонус <b>+{reward:.2f} ₽</b>!",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Конфиг создаётся при /start. Если по какой-то причине его нет — создаём.
    sub_url = user.get("sub_url", "") if user else ""
    if user and not user.get("vless_uuid"):
        result = await XUI.add_client(f"user_{uid}", expire_days=0)
        if result:
            sub_url = await _save_client(uid, result, "auto")

    fresh_bal = (await get_user(uid) or {}).get("balance", rub)
    await message.answer(
        f"✅ <b>Баланс пополнен на {rub} ₽</b> "
        f"(~{rub / PRICE_PER_GB:.0f} ГБ)\n\n"
        f"💰 Текущий баланс: <b>{fresh_bal:.2f} ₽</b>\n\n"
        + (f"🔗 <b>Подписка:</b>\n<code>{sub_url}</code>" if sub_url else ""),
        parse_mode="HTML",
        reply_markup=kb_main(),
    )

    asyncio.create_task(billing_tick(bot))


# ── Промокод ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "promo_start")
async def cb_promo_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_promo)
    await callback.message.edit_text(
        "🎁 <b>Промокод</b>\n\nВведи промокод:",
        parse_mode="HTML",
        reply_markup=kb_back(),
    )
    await callback.answer()


@router.message(UserStates.waiting_promo)
async def handle_promo(message: Message, state: FSMContext):
    await state.clear()
    uid   = message.from_user.id
    code  = message.text.strip().upper()
    promo = await get_promo(code)

    if not promo:
        await message.answer("❌ Промокод не найден.", reply_markup=kb_back())
        return
    if promo["used_count"] >= promo["max_uses"]:
        await message.answer("❌ Промокод уже исчерпан.", reply_markup=kb_back())
        return
    if await promo_already_used(uid, promo["id"]):
        await message.answer("❌ Ты уже использовал этот промокод.",
                             reply_markup=kb_back())
        return

    bonus = promo["bonus_rub"]
    await add_balance(uid, bonus)
    await add_transaction(uid, bonus, "promo", f"Промокод {code}")
    await use_promo(uid, promo["id"])
    # Сбрасываем флаг предупреждения
    await update_user(uid, notified_low_balance=0)
    await message.answer(
        f"✅ Промокод <b>{code}</b> применён!\n"
        f"Начислено: <b>+{bonus:.0f} ₽</b> "
        f"(~{bonus / PRICE_PER_GB:.0f} ГБ)",
        parse_mode="HTML",
        reply_markup=kb_back(),
    )


# ── Рефералы ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "referral_info")
async def cb_referral_info(callback: CallbackQuery):
    uid   = callback.from_user.id
    stats = await get_referral_stats(uid)
    link  = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    await callback.message.edit_text(
        f"👥 <b>Реферальная программа</b>\n\n"
        f"Ты получаешь <b>{int(REFERRAL_PERCENT * 100)}%</b> "
        f"с каждого пополнения реферала.\n\n"
        f"📊 Рефералов: <b>{stats['count']}</b>\n"
        f"💰 Заработано: <b>{stats['earned']:.2f} ₽</b>\n\n"
        f"🔗 Твоя реферальная ссылка:\n<code>{link}</code>",
        parse_mode="HTML",
        reply_markup=kb_back(),
    )
    await callback.answer()


# ── Поддержка и О сервисе ──────────────────────────────────────────────────────

@router.callback_query(F.data == "support")
async def cb_support(callback: CallbackQuery):
    b = InlineKeyboardBuilder()
    b.button(text=f"💬 @{ADMIN_USERNAME}", url=f"https://t.me/{ADMIN_USERNAME}")
    b.button(text="◀️ В меню", callback_data="back_main")
    b.adjust(1)
    await callback.message.edit_text(
        "🆘 <b>Поддержка</b>\n\nПиши в личку:",
        parse_mode="HTML",
        reply_markup=b.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "about")
async def cb_about(callback: CallbackQuery):
    from config import PANELS
    protocols_set = set()
    for panel in PANELS:
        for cfg in panel.get("inbounds", {}).values():
            if cfg.get("protocol") == "vless":
                protocols_set.add(cfg.get("label", "VLESS"))
    protocols = ", ".join(protocols_set)
    await callback.message.edit_text(
        f"❓ <b>О сервисе</b>\n\n"
        f"🛡 <b>Dobrinya VPN</b>\n"
        f"Протоколы: {protocols or 'VLESS'}\n"
        f"Сервер: 🇫🇮 Финляндия\n\n"
        f"💰 Тариф: <b>{PRICE_PER_GB} ₽/ГБ</b>\n"
        f"Списание раз в час по факту использования.\n"
        f"Кредит: <b>~{_CREDIT_GB:.0f} ГБ</b> бесплатно для новых.\n"
        f"Отключение при балансе <b>−{_CREDIT_ABS:.0f} ₽</b>.\n\n"
        f"📱 Совместимые клиенты:\n"
        f"v2rayTUN, Hiddify, NekoBox, v2rayN, Streisand",
        parse_mode="HTML",
        reply_markup=kb_back(),
    )
    await callback.answer()
