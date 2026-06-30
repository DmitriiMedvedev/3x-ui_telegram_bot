# Configuration file. Contains Telegram tokens, 3X-UI credentials, and inbound settings.
"""
config.py - Settings for Dobrinya VPN Bot v14.
Заполнено на основе x-ui.db от 11.06.2026.

The only file you need to touch when moving to another server.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ───────────────────────────────────────────────────────────────────
BOT_TOKEN      = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_БОТА")
ADMIN_IDS      = [7148594440]
ADMIN_USERNAME = "dobrinyaVPN"      # без @, для кнопки поддержки
BOT_USERNAME   = "dobrinyaVPN_bot"  # без @, для реферальных ссылок


# ── 3X-UI Panels Configuration ──────────────────────────────────────────────────
# Configure multiple 3X-UI panels here.
PANELS = [
    {
        "name": "Server 1 (Finland)",
        "host": "127.0.0.1",
        "port": 29870,
        "path": os.getenv("XUI_PATH_1", "/YOUR_SECRET_PATH"),
        "login": os.getenv("XUI_LOGIN_1", "YOUR_XUI_LOGIN"),
        "password": os.getenv("XUI_PASSWORD_1", "YOUR_XUI_PASSWORD"),
        "server_host": os.getenv("SERVER_HOST_1", "YOUR_SERVER_IP"),
        "inbound_ids": [28, 41],
        "billing_inbound_ids": [28, 41],
        "inbounds": {
            28: {
                "label":       "Reality-TCP",
                "protocol":    "vless",
                "port":        14539,
                "network":     "tcp",
                "security":    "reality",
                "public_key":  os.getenv("XUI_INBOUND_28_PUBKEY", "YOUR_PUBLIC_KEY"),
                "short_id":    os.getenv("XUI_INBOUND_28_SHORTID", "YOUR_SHORT_ID"),
                "sni":         "aws.amazon.com",
                "fingerprint": "firefox",
                "flow":        "xtls-rprx-vision",
            },
            41: {
                "label":       "XHTTP-Reality",
                "protocol":    "vless",
                "port":        56224,
                "network":     "xhttp",
                "security":    "reality",
                "public_key":  os.getenv("XUI_INBOUND_41_PUBKEY", "YOUR_PUBLIC_KEY"),
                "short_id":    os.getenv("XUI_INBOUND_41_SHORTID", "YOUR_SHORT_ID"),
                "sni":         "microsoft.com",
                "fingerprint": "chrome",
                "flow":        "",
                "path":        "/",
                "xhttp_mode":  "auto",
            }
        }
    }
    # Add more panels here as needed
]

# ── Subscription Server ────────────────────────────────────────────────────────
SUB_PORT     = int(os.getenv('SUB_PORT', 8080))
SUB_BASE_URL = os.getenv('SUB_BASE_URL', f'http://{os.getenv("SERVER_HOST_1", "YOUR_SERVER_IP")}:{SUB_PORT}/sub')


# ── Тарифы и оплата ────────────────────────────────────────────────────────────
PRICE_PER_GB     = 3.0          # рублей за гигабайт
FREE_BONUS_RUB   = 33.0         # стартовый бонус (~11 ГБ)

# Кредитный лимит — заменяет дневной пробный период.
# Подписка создаётся для КАЖДОГО пользователя сразу (см. handlers/user.cmd_start),
# и работает до тех пор, пока баланс не достигнет этого значения.
# При балансе <= CREDIT_LIMIT_RUB VPN отключается.
# -50₽ при тарифе 3₽/ГБ ≈ 16.7 ГБ бесплатного трафика — это и есть «пробный период».
CREDIT_LIMIT_RUB = -50.0

# Порог предупреждения: бот уведомит пользователя что баланс близок к лимиту.
# Должен быть между 0 и CREDIT_LIMIT_RUB.
WARN_BALANCE_RUB  = -30.0

REFERRAL_PERCENT = 0.10         # 10% реферального вознаграждения
STARS_RUB_NET    = 1.31         # рублей за один Telegram Star
SERVER_COST_RUB  = 270.00       # расходы на сервер в месяц (для статистики)

# ── CryptoBot (Crypto Pay API) ─────────────────────────────────────────────────
# Токен от @CryptoBot → /apps → Create App
# Если пустой — кнопка CryptoBot не показывается в меню оплаты.
CRYPTOBOT_TOKEN   = os.getenv("CRYPTOBOT_TOKEN", "")
CRYPTOBOT_TESTNET = os.getenv("CRYPTOBOT_TESTNET", "0") == "1"
