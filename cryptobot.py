# CryptoBot integration for processing crypto payments via Crypto Pay API.
"""
cryptobot.py — Crypto Pay API integration.

Токен получить: @CryptoBot → /apps → Create App
Документация:   https://help.crypt.bot/crypto-pay-api

Поддерживаемые активы (принимает пользователь): USDT, TON, BTC, ETH и др.
Цена в рублях: currency_type="fiat", fiat="RUB" — CryptoBot сам
конвертирует в крипту по текущему курсу.
"""

import logging
import aiohttp
from config import CRYPTOBOT_TOKEN, CRYPTOBOT_TESTNET

logger = logging.getLogger(__name__)
_BASE = (
    "https://testnet-pay.crypt.bot/api"
    if CRYPTOBOT_TESTNET
    else "https://pay.crypt.bot/api"
)
_HEADERS = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}


async def _get(method: str, params: dict | None = None) -> dict | None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{_BASE}/{method}", headers=_HEADERS, params=params) as r:
                text = await r.text()
                if r.status != 200:
                    logger.error(
                        f"CryptoBot GET {method} HTTP {r.status}: {text[:200]}"
                    )
                    return None
                import json

                data = json.loads(text)
                if not data.get("ok"):
                    logger.error(f"CryptoBot GET {method} not ok: {data}")
                    return None
                return data.get("result")
    except Exception as e:
        logger.error(f"CryptoBot GET {method}: {e}")
    return None


async def _post(method: str, payload: dict) -> dict | None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{_BASE}/{method}", headers=_HEADERS, json=payload) as r:
                text = await r.text()
                if r.status != 200:
                    logger.error(
                        f"CryptoBot POST {method} HTTP {r.status}: {text[:200]}"
                    )
                    return None
                import json

                data = json.loads(text)
                if not data.get("ok"):
                    logger.error(f"CryptoBot POST {method} not ok: {data}")
                    return None
                return data.get("result")
    except Exception as e:
        logger.error(f"CryptoBot POST {method}: {e}")
    return None


async def create_invoice(amount_rub: float, payload: str) -> dict | None:
    """
    Создаёт счёт на оплату в рублях.
    Возвращает {"invoice_id": int, "pay_url": str} или None при ошибке.

    Пользователь платит криптовалютой, CryptoBot конвертирует по курсу.
    """
    result = await _post(
        "createInvoice",
        {
            "currency_type": "fiat",
            "fiat": "RUB",
            "amount": f"{amount_rub:.2f}",
            "description": f"Dobrinya VPN · {amount_rub:.0f} ₽",
            "payload": payload,
            "paid_btn_name": "callback",
            "paid_btn_url": "https://t.me/dobrinyaVPN_bot",
            "expires_in": 3600,  # счёт действует 1 час
        },
    )
    if not result:
        return None
    return {
        "invoice_id": result["invoice_id"],
        "pay_url": result.get("bot_invoice_url") or result.get("pay_url", ""),
    }


async def get_paid_invoices(invoice_ids: list[int]) -> list[dict]:
    """
    Возвращает только оплаченные счета из переданного списка ID.
    Максимум 100 ID за запрос.
    """
    if not invoice_ids:
        return []
    result = await _get(
        "getInvoices",
        {
            "invoice_ids": ",".join(str(i) for i in invoice_ids[:100]),
            "status": "paid",
        },
    )
    if result and isinstance(result.get("items"), list):
        return result["items"]
    return []


async def check_connection() -> bool:
    """Проверяет доступность CryptoBot API и корректность токена."""
    result = await _get("getMe")
    if result:
        logger.info(f"CryptoBot: подключён как '{result.get('name', '?')}'")
        return True
    return False
