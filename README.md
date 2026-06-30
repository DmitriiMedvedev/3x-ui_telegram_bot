# Dobrinya VPN Bot v14

## Что изменилось по сравнению с v13

### 🔧 Критические исправления совместимости с 3X-UI 2.x

#### 1. Retry-логика для `addClient` (исправляет баг 3X-UI 2.6.2)
В 3X-UI 2.6.2 endpoint `/panel/api/inbounds/addClient` периодически возвращает
пустой ответ без тела. Бот теперь делает до **3 попыток** с задержками 0.5 / 1.5 / 3.0 с.

```
# xui.py
_ADD_RETRIES = 3
_RETRY_DELAYS = (0.5, 1.5, 3.0)
```

#### 2. Новый endpoint для трафика клиента
Вместо парсинга всего `/panel/api/inbounds/list` используется:
```
GET /panel/api/inbounds/getClientTraffics/{email}
```
Это быстрее, меньше нагрузка на панель, возвращает up/down отдельно.

#### 3. `subId` в payload клиента
При создании клиента `subId` теперь включается в объект клиента:
```python
client = {
    "id":    client_uuid,
    "email": email,
    "subId": sub_id,    # ← НОВОЕ: панель ведёт свою native subscription
    ...
}
```
Это значит, что нативная subscription 3X-UI (порт 2096) тоже работает.

#### 4. Многопротокольная поддержка
Клиент добавляется во все inbound'ы из `INBOUND_IDS` одновременно.
Все ссылки (VLESS Reality + XHTTP + gRPC + SS) сохраняются в `configs_all`.

#### 5. Правильный формат subscription
`sub_server.py` теперь отдаёт `base64(link1\nlink2\n...)` — стандартный формат,
который понимают v2rayNG, Hiddify, NekoBox, Streisand и другие клиенты.

---

## Быстрый старт

### 1. Добавление серверов и конфигов (через Telegram-бота)
Вместо редактирования `config.py` администратор добавляет новые сервера и конфиги (инбаунды) непосредственно через Telegram-бота, используя команды `/addserver` и `/addinbound`.

Заполни свои реальные значения:

```python
BOT_TOKEN   = "токен от @BotFather"
ADMIN_IDS   = [твой_telegram_id]

XUI_PORT    = 2053              # порт панели
XUI_PATH    = "/твой_секретный_путь"
XUI_LOGIN   = "логин"
XUI_PASSWORD = "пароль"

# ID inbound'ов — проверь в 3X-UI → Inbounds
INBOUND_IDS = [1, 2, 4]        # XHTTP, Reality, gRPC

# Заполни параметры каждого inbound
INBOUND_CONFIGS = {
    1: { "label": "XHTTP", "port": 443, ... },
    2: { "label": "Reality", "port": 8443, "public_key": "...", ... },
    ...
}
```

**Как найти ID inbound'а:** открой 3X-UI → список inbounds → первая колонка.

**Как найти public_key для Reality:** 3X-UI → inbound → настройки → Reality → Public Key.

### 2. Установка на сервере

```bash
cd /root
git clone ... dobrinya_bot   # или скопируй файлы

cd dobrinya_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Проверка подключения к 3X-UI
python3 -c "import asyncio; import xui; print(asyncio.run(xui.check_connection()))"
```

### 3. Systemd сервисы

```bash
# Бот
cp dobrinya-bot.service /etc/systemd/system/
systemctl enable dobrinya-bot
systemctl start dobrinya-bot

# Sub-сервер
cp dobrinya-sub.service /etc/systemd/system/
systemctl enable dobrinya-sub
systemctl start dobrinya-sub

# Логи
journalctl -u dobrinya-bot -f
```

### 4. Миграция с v13

БД совместима. При первом запуске v14 автоматически добавится колонка `configs_all`.
Существующие пользователи сохранят свои `vless_link` (старая Reality ссылка).
Новые пользователи получат полную подписку со всеми протоколами.

---

## Структура файлов

```
dobrinya_bot/
├── config.py           ← РЕДАКТИРОВАТЬ
├── bot.py              ← точка входа
├── database.py         ← SQLite
├── xui.py              ← 3X-UI API wrapper
├── billing.py          ← биллинг по трафику
├── keyboards.py        ← клавиатуры
├── sub_server.py       ← subscription HTTP сервер (port 8080)
├── handlers/
│   ├── user.py         ← пользовательские команды
│   ├── admin.py        ← /admin панель
│   └── trial.py        ← пробный период
├── dobrinya-bot.service
├── dobrinya-sub.service
└── requirements.txt
```

---

## Настройка inbound'ов в config.py

### VLESS Reality

```python
2: {
    "label":      "Reality",
    "protocol":   "vless",
    "host":       SERVER_HOST,
    "port":       8443,
    "network":    "tcp",
    "security":   "reality",
    "public_key": "ПУБЛИЧНЫЙ_КЛЮЧ_ИЗ_ПАНЕЛИ",
    "short_id":   "SHORT_ID_ИЗ_ПАНЕЛИ",
    "sni":        "www.nvidia.com",
    "flow":       "xtls-rprx-vision",
},
```

### VLESS XHTTP (443)

```python
1: {
    "label":    "XHTTP",
    "protocol": "vless",
    "host":     SERVER_HOST,
    "port":     443,
    "network":  "xhttp",
    "security": "tls",
    "sni":      "домен_для_TLS",
    "flow":     "",
},
```

### VLESS gRPC

```python
4: {
    "label":        "gRPC",
    "protocol":     "vless",
    "host":         SERVER_HOST,
    "port":         9443,
    "network":      "grpc",
    "security":     "tls",
    "sni":          "домен_для_TLS",
    "grpc_service": "grpc",   # имя service из настроек inbound
    "flow":         "",
},
```

### Shadowsocks (общий inbound, не добавляем клиентов)

```python
3: {
    "label":    "Shadowsocks",
    "protocol": "ss",
    "host":     SERVER_HOST,
    "port":     8388,
    "method":   "chacha20-poly1305",
    "password": "ПАРОЛЬ_ИЗ_ПАНЕЛИ",
},
```

> ⚠️ SS inbound с `chacha20-poly1305` — **одиночный пользователь**.
> Не включай его в `INBOUND_IDS`. Ссылка добавляется автоматически через `make_ss_link()`.

---

## Команды администратора

| Команда                    | Описание                              |
|----------------------------|---------------------------------------|
| `/admin`                   | Панель со статистикой                 |
| `/pending`                 | Заявки на пробный период              |
| `/setbalance ID СУММА`     | Изменить баланс пользователя          |
| `/ban ID` / `/unban ID`    | Бан / разбан                          |
| `/reply ID текст`          | Ответить пользователю                 |
| `/broadcast текст`         | Рассылка всем                         |
| `/addpromo КОД РУБ USES`   | Создать промокод (`_` = авто-код)     |
| `/billing`                 | Принудительный billing tick           |
| `/debugtraffic`            | Диагностика трафика                   |
| `/userinfo ID`             | Полная инфо + конфиги пользователя    |

---

## Диагностика

### 3X-UI недоступен при старте
```
⚠️ 3X-UI: недоступен — проверь порт/путь
```
Проверь: `XUI_PORT`, `XUI_PATH`, `XUI_LOGIN`, `XUI_PASSWORD` в `config.py`.

### addClient возвращает пустой ответ
Это **известный баг** 3X-UI 2.6.2. Бот автоматически делает до 3 попыток.
Если все 3 провалились — смотри логи: `journalctl -u dobrinya-bot -f`.

### Трафик не считается
Запусти `/debugtraffic` в боте. Проверь, что `BILLING_INBOUND_IDS` содержит
правильные ID inbound'ов, и что email пользователей (`user_{tg_id}`) есть в панели.
