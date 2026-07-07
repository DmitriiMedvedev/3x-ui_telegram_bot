"""keyboards.py — Bot keyboards."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import PRICE_PER_GB


def kb_main() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="👤 Мой аккаунт", callback_data="my_account")
    b.button(text="📊 Статистика", callback_data="traffic_stats")
    b.button(text="💳 Пополнить баланс", callback_data="topup_start")
    b.button(text="🎁 Промокод", callback_data="promo_start")
    b.button(text="👥 Рефералы", callback_data="referral_info")
    b.button(text="🆘 Поддержка", callback_data="support")
    b.button(text="❓ О сервисе", callback_data="about")
    b.adjust(2, 2, 2, 1)
    return b.as_markup()


def kb_back() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="◀️ В меню", callback_data="back_main")
    return b.as_markup()


def kb_topup_amount() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for rub in [30, 50, 100, 200, 500]:
        gb = rub / PRICE_PER_GB
        b.button(text=f"{rub} ₽ (~{gb:.0f} ГБ)", callback_data=f"topup_amount_{rub}")
    b.button(text="◀️ Назад", callback_data="back_main")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()


def kb_topup_method(rub: int) -> InlineKeyboardMarkup:
    from math import ceil
    from config import STARS_RUB_NET, CRYPTOBOT_TOKEN

    stars_needed = ceil(rub / STARS_RUB_NET)
    b = InlineKeyboardBuilder()
    b.button(
        text=f"⭐ Telegram Stars ({stars_needed} Stars)",
        callback_data=f"pay_stars_{rub}",
    )
    if CRYPTOBOT_TOKEN:
        b.button(
            text="₿ CryptoBot (крипта)",
            callback_data=f"pay_crypto_{rub}",
        )
    b.button(text="◀️ Назад", callback_data="topup_start")
    b.adjust(1)
    return b.as_markup()


def kb_admin() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="👥 Пользователи", callback_data="adm_users")
    b.button(text="📊 Прибыль", callback_data="adm_revenue")
    b.button(text="🎁 Промокоды", callback_data="adm_promos")
    b.button(text="🖥 Серверы 3X-UI", callback_data="adm_panels")
    b.adjust(2)
    return b.as_markup()


def kb_new_user(uid: int) -> InlineKeyboardMarkup:
    """Кнопки быстрых действий в уведомлении о новом пользователе."""
    b = InlineKeyboardBuilder()
    b.button(text="📋 Профиль", callback_data=f"adm_userinfo_{uid}")
    b.button(text="💳 +50 ₽", callback_data=f"adm_gift_{uid}")
    b.button(text="🚫 Бан", callback_data=f"adm_ban_{uid}")
    b.adjust(3)
    return b.as_markup()
