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

# ── 3X-UI Panel ───────────────────────────────────────────────────────────────
# Из x-ui.db → settings: webPort=29870, webBasePath=/CmlR1gL2nQBgY1IrEY/
# Логин/пароль — из env или вставь напрямую
XUI_LOGIN    = os.getenv("XUI_LOGIN",    "YOUR_XUI_LOGIN")
XUI_PASSWORD = os.getenv("XUI_PASSWORD", "YOUR_XUI_PASSWORD")
XUI_HOST     = "127.0.0.1"
XUI_PORT     = 29870
XUI_PATH     = os.getenv("XUI_PATH", "/YOUR_SECRET_PATH")   # без слеша на конце
XUI_BASE_URL = f"https://{XUI_HOST}:{XUI_PORT}"

# ── Сервер ─────────────────────────────────────────────────────────────────────
SERVER_HOST = os.getenv("SERVER_HOST", "YOUR_SERVER_IP")

# ── Inbound'ы ─────────────────────────────────────────────────────────────────
# В панели 8 активных inbound'ов. Для бота используем лучшие два:
#   ID=28  — VLESS Reality TCP    (SNI: aws.amazon.com,  fp: firefox)
#   ID=41  — VLESS XHTTP Reality  (SNI: microsoft.com,   fp: chrome)
# Оба — с включённым sniffing, разные транспорты = максимальная совместимость.
#
# ИСКЛЮЧЕНЫ из INBOUND_IDS:
#   ID=35 — дублирует 28 (другой Reality TCP, addClient к двум = двойной счёт трафика)
#   ID=39 — дублирует 41 (тот же ключ Reality + XHTTP)
#   ID=37, 38, 42, 43 — security=none, без шифрования (для внутреннего тестирования)

INBOUND_IDS         = [28, 41]   # в эти inbound'ы добавляем клиентов
BILLING_INBOUND_IDS = [28, 41]   # по этим считаем трафик для списания

# ── Конфигурация inbound'ов (для генерации ссылок) ────────────────────────────
# Данные извлечены напрямую из x-ui.db → inbounds.stream_settings
INBOUND_CONFIGS: dict[int, dict] = {

    # ── ID=28: VLESS Reality TCP  ──────────────────────────────────────────────
    # target: aws.amazon.com:443 | SNI: aws.amazon.com | fp: firefox
    # Private key: REMOVED_FOR_SECURITY
    28: {
        "label":       "Reality-TCP",
        "protocol":    "vless",
        "host":        SERVER_HOST,
        "port":        14539,
        "network":     "tcp",
        "security":    "reality",
        "public_key":  os.getenv("XUI_INBOUND_28_PUBKEY", "YOUR_PUBLIC_KEY"),
        "short_id":    os.getenv("XUI_INBOUND_28_SHORTID", "YOUR_SHORT_ID"),
        "sni":         "aws.amazon.com",
        "fingerprint": "firefox",
        "flow":        "xtls-rprx-vision",
    },

    # ── ID=41: VLESS XHTTP Reality  ───────────────────────────────────────────
    # target: www.microsoft.com:443 | SNI: microsoft.com | fp: chrome
    # Private key: REMOVED_FOR_SECURITY
    # Тот же ключ что и у ID=39 (vlss_xttp_reality_no_snif) — разные SNI
    41: {
        "label":       "XHTTP-Reality",
        "protocol":    "vless",
        "host":        SERVER_HOST,
        "port":        56224,
        "network":     "xhttp",
        "security":    "reality",
        "public_key":  os.getenv("XUI_INBOUND_41_PUBKEY", "YOUR_PUBLIC_KEY"),
        "short_id":    os.getenv("XUI_INBOUND_41_SHORTID", "YOUR_SHORT_ID"),
        "sni":         "microsoft.com",
        "fingerprint": "chrome",
        "flow":        "",        # у XHTTP flow не нужен
        "path":        "/",
        "xhttp_mode":  "auto",     # из x-ui.db: xhttpSettings.mode
    },

    # ── Остальные inbound'ы — справочно, не входят в INBOUND_IDS ──────────────
    # 35: Reality TCP (dl.google.com, fp=chrome)   — дубль 28
    # 39: XHTTP Reality (www.oracle.com, fp=edge)  — дубль 41 (тот же ключ)
    # 37: VLESS plain TCP no-sniff, port=49395     — без шифрования
    # 38: VLESS plain TCP sniff,    port=41037     — без шифрования
    # 42: VLESS XHTTP plain sniff,  port=23780     — без шифрования
    # 43: VLESS XHTTP plain no-sniff, port=43644   — без шифрования
}

# ── Subscription Server ────────────────────────────────────────────────────────
# sub_server.py слушает SUB_PORT и отдаёт base64-подписку пользователям бота.
# Нативная подписка 3X-UI: http://SERVER_HOST:2096/sub/{subId}
SUB_PORT     = 8080
SUB_BASE_URL = f"http://{SERVER_HOST}:{SUB_PORT}/sub"

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
