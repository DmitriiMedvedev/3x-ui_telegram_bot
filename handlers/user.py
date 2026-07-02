# User handlers for registration, checking stats, top-ups, and getting VPN configs.
"""
handlers/user.py — User handlers.
"""
import asyncio
import logging
from datetime import datetime
from math import ceil

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery
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
    waiting_topup_amount = State()

async def _ensure_user_on_panels(u: dict):
    """Фоновое добавление пользователя на все панели 3X-UI."""
    try:
        await XUI.add_client_background(
            email=f"user_{u['tg_id']}",
            client_uuid=u['vless_uuid'],
            sub_id=u['sub_id']
        )
    except Exception as e:
        logger.error(f"Background sync error for {u['tg_id']}: {e}")

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    uid = message.from_user.id
    referred_by = None
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            rid = int(args[1][4:])
            if rid != uid: referred_by = rid
        except: pass

    user, is_new = await get_or_create_user(
        uid, message.from_user.username or "", message.from_user.full_name or "", referred_by=referred_by
    )

    if is_new and referred_by:
        try:
            await bot.send_message(referred_by, f"👥 По твоей ссылке зарегистрировался новый пользователь!", parse_mode="HTML")
        except: pass

    if is_new:
        # Уведомление админам
        for admin_id in ADMIN_IDS:
            try: await bot.send_message(admin_id, f"🆕 Новый юзер: {uid}", reply_markup=kb_new_user(uid))
            except: pass

    # Запускаем синхронизацию с панелями в фоне, не заставляя юзера ждать
    asyncio.create_task(_ensure_user_on_panels(user))

    sub_url = XUI.make_sub_url(user['sub_id'])
    bal = user["balance"]

    from html import escape
    first_name = escape(message.from_user.first_name)
    await message.answer(
        f"👋 Привет, <b>{first_name}</b>!\n\n"
        f"🛡 <b>Dobrinya VPN</b>\n"
        f"Тариф: <b>{PRICE_PER_GB} ₽/ГБ</b>\n"
        f"Кредит: <b>~{_CREDIT_GB:.0f} ГБ</b> бесплатно\n\n"
        f"💰 Баланс: <b>{bal:.2f} ₽</b>\n"
        f"🔗 <b>Твоя подписка:</b>\n<code>{sub_url}</code>\n\n"
        f"⚠️ VPN отключается при балансе <b>−{_CREDIT_ABS:.0f} ₽</b>",
        parse_mode="HTML", reply_markup=kb_main(),
    )

@router.callback_query(F.data == "back_main")
async def cb_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user = await get_user(callback.from_user.id)
    bal  = (user["balance"] if user else 0.0)
    await callback.message.edit_text(f"💰 Баланс: <b>{bal:.2f} ₽</b>\n\nВыбери действие:", parse_mode="HTML", reply_markup=kb_main())
    await callback.answer()

@router.callback_query(F.data == "my_account")
async def cb_my_account(callback: CallbackQuery):
    await _show_account(callback.from_user.id, callback.message, edit=True)
    await callback.answer()

@router.message(Command("my"))
async def cmd_my(message: Message):
    await _show_account(message.from_user.id, message, edit=False)

async def _show_account(uid: int, message: Message, edit: bool):
    user = await get_user(uid)
    if not user: return

    # Синхронизация в фоне
    asyncio.create_task(_ensure_user_on_panels(user))

    bal, total = user["balance"], user.get("total_traffic_bytes") or 0
    status = f"✅ Активен" if bal > CREDIT_LIMIT_RUB else f"❌ Отключён"
    sub_url = XUI.make_sub_url(user['sub_id'])

    b = InlineKeyboardBuilder()
    b.button(text="💳 Пополнить баланс", callback_data="topup_start")
    b.button(text="◀️ В меню", callback_data="back_main")
    b.adjust(1)

    from html import escape
    full_name = escape(user.get('full_name') or '—')
    fn = message.edit_text if edit else message.answer
    await fn(
        f"👤 <b>Аккаунт: {full_name}</b>\n\n"
        f"Статус: {status}\n"
        f"💰 Баланс: <b>{bal:.2f} ₽</b>\n"
        f"📦 Использовано: <b>{fmt_bytes(total)}</b>\n\n"
        f"🔗 <b>Subscription URL:</b>\n<code>{sub_url}</code>\n\n"
        f"⚠️ Отключение при балансе <b>−{_CREDIT_ABS:.0f} ₽</b>",
        parse_mode="HTML", reply_markup=b.as_markup(),
    )

@router.callback_query(F.data == "traffic_stats")
async def cb_traffic_stats(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    traffic = await XUI.get_client_traffic(f"user_{user['tg_id']}")
    total = traffic["total"] if traffic else (user.get("total_traffic_bytes") or 0)

    await callback.message.edit_text(
        f"📊 <b>Статистика трафика</b>\n\n"
        f"📦 Итого: <b>{fmt_bytes(total)}</b>\n"
        f"💰 Баланс: <b>{user['balance']:.2f} ₽</b>\n"
        f"Тариф: <b>{PRICE_PER_GB} ₽/ГБ</b>\n",
        parse_mode="HTML", reply_markup=kb_back(),
    )
    await callback.answer()

# ── Пополнение (STARS / CRYPTO) ──
@router.callback_query(F.data == "topup_start")
async def cb_topup_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_topup_amount)
    await callback.message.edit_text(
        f"💳 <b>Пополнение баланса</b>\n\n"
        f"Введи сумму пополнения в рублях (от 30 до 10000):",
        parse_mode="HTML", reply_markup=kb_back()
    )
    await callback.answer()

@router.message(UserStates.waiting_topup_amount)
async def handle_topup_amount(message: Message, state: FSMContext):
    try:
        rub = int(message.text.strip())
        if not (30 <= rub <= 10000):
            return await message.answer("❌ Сумма должна быть от 30 до 10000 ₽. Попробуй ещё раз:")

        await state.clear()
        await message.answer(
            f"💳 <b>Пополнение: {rub} ₽</b>\n\nСпособ оплаты:",
            parse_mode="HTML", reply_markup=kb_topup_method(rub)
        )
    except ValueError:
        await message.answer("❌ Введи сумму числом (например, 100):")

@router.callback_query(F.data.startswith("pay_stars_"))
async def cb_pay_stars(callback: CallbackQuery, bot: Bot):
    rub = int(callback.data.split("_")[-1])
    stars = ceil(rub / STARS_RUB_NET)
    await bot.send_invoice(callback.from_user.id, title="Пополнение VPN", description=f"{rub} ₽", payload=f"topup_{rub}", currency="XTR", prices=[LabeledPrice(label="Stars", amount=stars)])
    await callback.answer()

@router.callback_query(F.data.startswith("pay_crypto_"))
async def cb_pay_crypto(callback: CallbackQuery):
    rub = int(callback.data.split("_")[-1])
    uid = callback.from_user.id
    await callback.message.edit_text("₿ <b>Создаю счёт...</b>", parse_mode="HTML")
    invoice = await CryptoBot.create_invoice(rub, f"topup_{uid}_{rub}")
    if not invoice:
        await callback.message.edit_text("❌ Ошибка CryptoBot.", reply_markup=kb_topup_method(rub))
        return
    await save_crypto_invoice(invoice["invoice_id"], uid, float(rub))
    b = InlineKeyboardBuilder()
    b.button(text=f"💸 Оплатить {rub} ₽", url=invoice["pay_url"])
    b.button(text="◀️ Назад", callback_data="topup_start")
    await callback.message.edit_text(f"₿ <b>Оплата через CryptoBot</b>\n\nСумма: <b>{rub} ₽</b>", parse_mode="HTML", reply_markup=b.as_markup())
    await callback.answer()

@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

@router.message(F.successful_payment)
async def successful_payment(message: Message, bot: Bot):
    uid = message.from_user.id
    rub = int(message.successful_payment.invoice_payload.split("_")[1])
    await add_balance(uid, rub)
    await add_transaction(uid, rub, "stars", f"Stars {rub} ₽")
    await update_user(uid, notified_low_balance=0)
    await message.answer(f"✅ Баланс пополнен на {rub} ₽!", reply_markup=kb_main())
    asyncio.create_task(billing_tick(bot))

@router.callback_query(F.data == "promo_start")
async def cb_promo_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_promo)
    await callback.message.edit_text("🎁 Введи промокод:", reply_markup=kb_back())
    await callback.answer()

@router.message(UserStates.waiting_promo)
async def handle_promo(message: Message, state: FSMContext):
    await state.clear()
    uid, code = message.from_user.id, message.text.strip().upper()
    promo = await get_promo(code)
    if not promo or promo["used_count"] >= promo["max_uses"] or await promo_already_used(uid, promo["id"]):
        await message.answer("❌ Промокод недействителен.", reply_markup=kb_back())
        return
    await add_balance(uid, promo["bonus_rub"])
    await use_promo(uid, promo["id"])
    await message.answer(f"✅ Начислено {promo['bonus_rub']} ₽!", reply_markup=kb_back())

@router.callback_query(F.data == "referral_info")
async def cb_referral_info(callback: CallbackQuery):
    uid = callback.from_user.id
    stats = await get_referral_stats(uid)
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    await callback.message.edit_text(f"👥 <b>Рефералы</b>\n\n📊 Приглашено: <b>{stats['count']}</b>\n💰 Заработано: <b>{stats['earned']:.2f} ₽</b>\n\n🔗 Ссылка:\n<code>{link}</code>", parse_mode="HTML", reply_markup=kb_back())
    await callback.answer()

@router.callback_query(F.data == "support")
async def cb_support(callback: CallbackQuery):
    b = InlineKeyboardBuilder()
    b.button(text=f"💬 @{ADMIN_USERNAME}", url=f"https://t.me/{ADMIN_USERNAME}")
    b.button(text="◀️ В меню", callback_data="back_main")
    await callback.message.edit_text("🆘 Напиши нам:", reply_markup=b.as_markup())
    await callback.answer()

@router.callback_query(F.data == "about")
async def cb_about(callback: CallbackQuery):
    await callback.message.edit_text(f"❓ <b>О сервисе</b>\n\n🛡 <b>Dobrinya VPN</b>\n💰 Тариф: <b>{PRICE_PER_GB} ₽/ГБ</b>\n📱 Поддерживает: VLESS, Shadowsocks", parse_mode="HTML", reply_markup=kb_back())
    await callback.answer()
