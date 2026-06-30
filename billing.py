# Billing tasks: calculates user traffic, deducts balances, and toggles VPN access.
"""
billing.py — Traffic billing and balance deduction.

Пороги (из config.py):
  WARN_BALANCE_RUB  = -30₽ → предупреждение пользователю
  CREDIT_LIMIT_RUB  = -50₽ → отключение VPN

Флаги в БД:
  notified_low_balance — сбрасывается при каждом пополнении, чтобы
  предупреждение пришло снова при следующем дипе ниже -30₽.
"""
import asyncio
import logging

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import PRICE_PER_GB, CREDIT_LIMIT_RUB, WARN_BALANCE_RUB
from database import get_all_users, update_user, add_balance, get_user
import xui as XUI

logger = logging.getLogger(__name__)

_LIMIT_ABS = abs(CREDIT_LIMIT_RUB)
_WARN_ABS  = abs(WARN_BALANCE_RUB)


def fmt_bytes(b: int) -> str:
    if b < 1_048_576:
        return f"{b / 1024:.1f} КБ"
    if b < 1_073_741_824:
        return f"{b / 1_048_576:.1f} МБ"
    return f"{b / 1_073_741_824:.2f} ГБ"


def _topup_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💳 Пополнить баланс",
                             callback_data="topup_start")
    ]])


async def billing_tick(bot=None):
    """
    1. Получает трафик из 3X-UI по BILLING_INBOUND_IDS.
    2. Считает дельту, списывает баланс.
    3. Собирает три списка:
         to_warn   — баланс пересёк WARN_BALANCE_RUB (-30₽) впервые
         to_notify — баланс пересёк CREDIT_LIMIT_RUB (-50₽), VPN отключается
         to_toggle — нужно изменить enable-статус в панели
    4. bulk_toggle — один логин на весь список.
    5. Рассылает уведомления.
    """
    logger.info("=== Billing tick начат ===")
    traffic = await XUI.get_traffic()
    if traffic is None:
        logger.error("Billing tick: не удалось получить трафик из 3X-UI")
        return
    if not traffic:
        logger.warning("Billing tick: clientStats пустой — проверяем только статусы")

    logger.info(f"Billing tick: {len(traffic)} клиентов в панели")

    users  = await get_all_users()
    billed = 0

    to_toggle: list[tuple[str, str, bool, str]] = []
    to_warn:   list[int] = []   # предупреждение −30₽
    to_notify: list[int] = []   # отключение −50₽

    for u in users:
        uid    = u["tg_id"]
        email  = f"user_{uid}"
        v_uuid = u.get("vless_uuid", "")
        sub_id = u.get("sub_id", "")

        if not v_uuid:
            continue

        # ── Трафик и биллинг ──────────────────────────────────────────────────
        old_bal = u.get("balance", 0)

        if email in traffic:
            curr  = traffic[email]
            last  = u.get("last_traffic_bytes") or 0
            delta = curr if curr < last else curr - last
            new_total = (u.get("total_traffic_bytes") or 0) + delta

            await update_user(uid,
                last_traffic_bytes=curr,
                total_traffic_bytes=new_total,
            )

            if delta >= 524_288:
                cost = round((delta / 1_073_741_824) * PRICE_PER_GB, 4)
                await add_balance(uid, -cost)
                billed += 1
                logger.info(f"{email}: -{fmt_bytes(delta)} | -{cost:.4f} ₽")
        else:
            logger.debug(f"{email}: отсутствует в clientStats")

        # ── Свежие данные после списания ──────────────────────────────────────
        fresh = await get_user(uid)
        if fresh is None:
            logger.warning(f"billing_tick: пользователь {uid} исчез из БД")
            continue

        bal       = fresh["balance"]
        is_banned = bool(fresh.get("is_banned"))
        should_en = bal > CREDIT_LIMIT_RUB and not is_banned
        was_en    = bool(u.get("xui_enabled", 1))

        # ── Предупреждение при −30₽ ───────────────────────────────────────────
        # Условие: баланс только что пересёк WARN_BALANCE_RUB сверху вниз,
        # ещё не отключён (выше CREDIT_LIMIT_RUB), уведомление ещё не слали.
        just_warned = (
            bot
            and not is_banned
            and CREDIT_LIMIT_RUB < bal <= WARN_BALANCE_RUB
            and old_bal > WARN_BALANCE_RUB          # пересёк именно в этом тике
            and not fresh.get("notified_low_balance")
        )
        if just_warned:
            to_warn.append(uid)
            await update_user(uid, notified_low_balance=1)

        # ── Переключение enable только при изменении состояния ────────────────
        if should_en != was_en:
            to_toggle.append((email, v_uuid, should_en, sub_id))
            await update_user(uid, xui_enabled=int(should_en))
            if not should_en and not is_banned and bot:
                to_notify.append(uid)

    # ── Батч-toggle (1 логин на весь список) ─────────────────────────────────
    if to_toggle:
        logger.info(f"Billing: меняем состояние {len(to_toggle)} клиентов")
        await XUI.bulk_toggle(to_toggle)

    # ── Уведомление: баланс −30₽ ─────────────────────────────────────────────
    for uid in to_warn:
        try:
            remaining = abs(WARN_BALANCE_RUB - CREDIT_LIMIT_RUB)  # 20₽ до откл.
            await bot.send_message(
                uid,
                f"⚠️ <b>Баланс достиг −{_WARN_ABS:.0f} ₽</b>\n\n"
                f"До отключения VPN осталось ~<b>{remaining / PRICE_PER_GB:.0f} ГБ</b> "
                f"({remaining:.0f} ₽).\n\n"
                f"Пополни баланс чтобы не потерять доступ.\n"
                f"Тариф: <b>{PRICE_PER_GB} ₽/ГБ</b>",
                parse_mode="HTML",
                reply_markup=_topup_kb(),
            )
        except Exception:
            pass

    # ── Уведомление: отключение −50₽ ─────────────────────────────────────────
    for uid in to_notify:
        try:
            await bot.send_message(
                uid,
                f"❌ <b>VPN отключён — баланс −{_LIMIT_ABS:.0f} ₽</b>\n\n"
                f"Пополни счёт для восстановления доступа.\n"
                f"Тариф: <b>{PRICE_PER_GB} ₽/ГБ</b>",
                parse_mode="HTML",
                reply_markup=_topup_kb(),
            )
        except Exception:
            pass

    logger.info(
        f"=== Billing tick завершён: списано с {billed}, "
        f"предупреждено {len(to_warn)}, "
        f"отключено {len(to_notify)}, "
        f"переключено {len(to_toggle)} ==="
    )


async def billing_loop(bot):
    logger.info("Планировщик трафика запущен (первое списание через 1 час)")
    while True:
        await asyncio.sleep(3600)
        try:
            await billing_tick(bot)
        except Exception as e:
            logger.error(f"billing_loop error: {e}", exc_info=True)
