#!/usr/bin/env python3
# Main entry point for the Telegram bot. Initializes DB, connects to 3X-UI, and starts polling.
"""
bot.py — Entry point for Dobrinya VPN Bot.

Background tasks:
  billing_loop      — Deducts traffic cost every hour
  cryptobot_poller  — Checks paid CryptoBot invoices every 30 seconds
                      (runs only if CRYPTOBOT_TOKEN is set in .env)
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, CRYPTOBOT_TOKEN, REFERRAL_PERCENT, PRICE_PER_GB
from database import (
    init_db, get_user, add_balance, add_transaction, update_user,
    add_referral_reward, get_pending_crypto_invoices,
    mark_crypto_invoice_paid, expire_old_crypto_invoices,
)
import xui as XUI
import cryptobot as CryptoBot
from billing import billing_loop, billing_tick
from handlers import user, admin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _process_crypto_payment(bot: Bot, invoice_id: int,
                                  tg_id: int, amount_rub: float):
    """
    Processes a single paid CryptoBot transaction:
    credits balance, referral rewards, notifies the user.
    """
    await mark_crypto_invoice_paid(invoice_id)
    await add_balance(tg_id, amount_rub)
    await add_transaction(tg_id, amount_rub, "cryptobot",
                          f"CryptoBot {amount_rub:.0f} ₽")
    await update_user(tg_id, notified_low_balance=0)

    # Реферальное вознаграждение
    u = await get_user(tg_id)
    if u and (referrer_id := u.get("referred_by")):
        reward = round(amount_rub * REFERRAL_PERCENT, 2)
        await add_balance(referrer_id, reward)
        await add_transaction(referrer_id, reward, "referral",
                              f"Реферал {tg_id}: {reward:.2f} ₽")
        await add_referral_reward(referrer_id, tg_id, reward)
        try:
            await bot.send_message(
                referrer_id,
                f"🎉 Реферальный бонус <b>+{reward:.2f} ₽</b>!",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Уведомление пользователю
    fresh_bal = (await get_user(tg_id) or {}).get("balance", amount_rub)
    try:
        await bot.send_message(
            tg_id,
            f"✅ <b>Оплата через CryptoBot получена!</b>\n\n"
            f"Зачислено: <b>{amount_rub:.0f} ₽</b> "
            f"(~{amount_rub / PRICE_PER_GB:.0f} ГБ)\n"
            f"💰 Баланс: <b>{fresh_bal:.2f} ₽</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    # Включаем VPN если был отключён из-за баланса
    asyncio.create_task(billing_tick(bot))
    logger.info(f"CryptoBot: invoice {invoice_id} для {tg_id} +{amount_rub} ₽ ✅")


async def cryptobot_poller(bot: Bot):
    """
    Checks paid CryptoBot invoices every 30 seconds.
    Also clears expired invoices (older than 2 hours) once per cycle.
    """
    logger.info("CryptoBot poller запущен")
    cleanup_counter = 0

    while True:
        await asyncio.sleep(30)
        try:
            pending = await get_pending_crypto_invoices()
            if not pending:
                continue

            invoice_ids = [inv["invoice_id"] for inv in pending]
            paid        = await CryptoBot.get_paid_invoices(invoice_ids)

            # Создаём индекс для быстрого поиска
            pending_by_id = {inv["invoice_id"]: inv for inv in pending}

            for paid_inv in paid:
                iid   = paid_inv["invoice_id"]
                local = pending_by_id.get(iid)
                if not local:
                    continue
                await _process_crypto_payment(
                    bot, iid, local["tg_id"], local["amount_rub"]
                )

            # Очистка просроченных счетов раз в 10 циклов (~5 минут)
            cleanup_counter += 1
            if cleanup_counter >= 10:
                await expire_old_crypto_invoices()
                cleanup_counter = 0

        except Exception as e:
            logger.error(f"cryptobot_poller: {e}", exc_info=True)


# Main execution function: starts the bot, connects to db and services.
async def main():
    await init_db()
    logger.info("Dobrinya VPN Bot запускается...")

    ok = await XUI.check_connection()
    logger.info(f"3X-UI: {'✅ доступен' if ok else '⚠️ недоступен'}")

    if CRYPTOBOT_TOKEN:
        ok_cb = await CryptoBot.check_connection()
        logger.info(f"CryptoBot: {'✅ доступен' if ok_cb else '⚠️ недоступен — проверь токен'}")
    else:
        logger.info("CryptoBot: ⏭ токен не задан, пропускаем")

    bot = Bot(token=BOT_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())

    dp.include_router(admin.router)
    dp.include_router(user.router)

    asyncio.create_task(billing_loop(bot))

    if CRYPTOBOT_TOKEN:
        asyncio.create_task(cryptobot_poller(bot))

    logger.info("Polling started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
