# Database module using aiosqlite for managing users, balances, and transactions.
"""
database.py — Dobrinya VPN Bot v14.1.

Исправления:
  - [КРИТИЧЕСКИЙ] Добавлена колонка had_trial (используется в trial.py)
  - Добавлена колонка xui_enabled для отслеживания состояния в панели
    (billing_tick пропускает toggle если состояние не изменилось)
  - get_or_create_user возвращает (user_dict, is_new: bool)
    → cmd_start отправляет реферальное уведомление только при реальной регистрации
  - parse_dt переименован из _parse_dt (убираем ложный «приватный» префикс,
    функция используется в 4 модулях)
"""
import aiosqlite
import logging
import json
from datetime import datetime

DB_PATH = "dobrinya.db"
logger  = logging.getLogger(__name__)


def parse_dt(s: str | None) -> datetime | None:
    """Парсит ISO-строку в datetime. Возвращает None при ошибке."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        logger.warning(f"Не удалось распарсить дату: {s!r}")
        return None


    # Initialize SQLite database and create tables if they do not exist.
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id               INTEGER PRIMARY KEY,
                username            TEXT    DEFAULT '',
                full_name           TEXT    DEFAULT '',
                balance             REAL    DEFAULT 0.0,
                had_free_bonus      INTEGER DEFAULT 0,
                had_trial           INTEGER DEFAULT 0,
                vless_uuid          TEXT    DEFAULT '',
                sub_id              TEXT    DEFAULT '',
                vless_link          TEXT    DEFAULT '',
                configs_all         TEXT    DEFAULT '',
                sub_url             TEXT    DEFAULT '',
                expire_at           TEXT,
                sub_type            TEXT    DEFAULT '',
                xui_enabled         INTEGER DEFAULT 1,
                notified_3d         INTEGER DEFAULT 0,
                notified_1d         INTEGER DEFAULT 0,
                notified_expired    INTEGER DEFAULT 0,
                notified_low_balance INTEGER DEFAULT 0,
                is_banned           INTEGER DEFAULT 0,
                referred_by         INTEGER DEFAULT NULL,
                last_traffic_bytes  INTEGER DEFAULT 0,
                total_traffic_bytes INTEGER DEFAULT 0,
                created_at          TEXT    DEFAULT (datetime('now'))
            )
        """)

        # Миграции для существующих БД (порядок не важен, дубли игнорируются)
        _migrations = [
            ("had_free_bonus",      "INTEGER DEFAULT 0"),
            ("had_trial",           "INTEGER DEFAULT 0"),   # ИСПРАВЛЕНИЕ
            ("sub_id",              "TEXT DEFAULT ''"),
            ("sub_url",             "TEXT DEFAULT ''"),
            ("configs_all",         "TEXT DEFAULT ''"),
            ("xui_enabled",         "INTEGER DEFAULT 1"),   # НОВОЕ
            ("referred_by",         "INTEGER DEFAULT NULL"),
            ("last_traffic_bytes",  "INTEGER DEFAULT 0"),
            ("total_traffic_bytes", "INTEGER DEFAULT 0"),
            ("is_banned",           "INTEGER DEFAULT 0"),
            ("notified_3d",           "INTEGER DEFAULT 0"),
            ("notified_1d",           "INTEGER DEFAULT 0"),
            ("notified_expired",      "INTEGER DEFAULT 0"),
            ("notified_low_balance",  "INTEGER DEFAULT 0"),
        ]
        for col, defn in _migrations:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {defn}")
                logger.info(f"Миграция: добавлена колонка users.{col}")
            except Exception:
                pass  # колонка уже существует

        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id      INTEGER NOT NULL,
                amount     REAL    NOT NULL,
                method     TEXT    NOT NULL,
                comment    TEXT    DEFAULT '',
                created_at TEXT    DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                code       TEXT    UNIQUE NOT NULL,
                bonus_rub  REAL    NOT NULL,
                max_uses   INTEGER DEFAULT 1,
                used_count INTEGER DEFAULT 0,
                created_by INTEGER NOT NULL,
                created_at TEXT    DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promo_uses (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id    INTEGER NOT NULL,
                promo_id INTEGER NOT NULL,
                used_at  TEXT    DEFAULT (datetime('now')),
                UNIQUE(tg_id, promo_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referral_rewards (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referred_id INTEGER NOT NULL,
                amount      REAL    NOT NULL,
                created_at  TEXT    DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_trials (
                rid        TEXT    PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                username   TEXT    DEFAULT '',
                source     TEXT    DEFAULT '',
                purpose    TEXT    DEFAULT '',
                created_at TEXT    DEFAULT (datetime('now'))
            )
        """)
        # ── Счета CryptoBot ────────────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS crypto_invoices (
                invoice_id  INTEGER PRIMARY KEY,
                tg_id       INTEGER NOT NULL,
                amount_rub  REAL    NOT NULL,
                status      TEXT    DEFAULT 'active',
                created_at  TEXT    DEFAULT (datetime('now'))
            )
        """)

        # Миграции для панелей
        try:
            await db.execute("ALTER TABLE panels ADD COLUMN api_token TEXT DEFAULT ''")
            logger.info("Миграция: добавлена колонка panels.api_token")
        except Exception:
            pass

        await db.execute('''
            CREATE TABLE IF NOT EXISTS panels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                path TEXT NOT NULL,
                login TEXT NOT NULL,
                password TEXT NOT NULL,
                api_token TEXT DEFAULT '',
                server_host TEXT NOT NULL,
                inbound_ids TEXT DEFAULT '[]',
                billing_inbound_ids TEXT DEFAULT '[]',
                inbounds TEXT DEFAULT '{}'
            )
        ''')

        await db.commit()
    logger.info("БД инициализирована (v14.1)")


# ── Users ──────────────────────────────────────────────────────────────────────

async def get_or_create_user(
    tg_id: int,
    username: str,
    full_name: str,
    referred_by: int | None = None,
) -> tuple[dict, bool]:
    """
    Возвращает (user_dict, is_new).
    is_new=True только при первой регистрации — используется для реферальных
    уведомлений чтобы не слать их при каждом /start.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)) as cur:
            row = await cur.fetchone()
        if row is not None:
            return dict(row), False
        await db.execute(
            "INSERT INTO users (tg_id,username,full_name,referred_by) "
            "VALUES (?,?,?,?)",
            (tg_id, username, full_name, referred_by),
        )
        await db.commit()
        async with db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)) as cur:
            row = await cur.fetchone()
        return dict(row), True


    # Retrieve user by Telegram ID.
async def get_user(tg_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None


async def update_user(tg_id: int, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [tg_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {sets} WHERE tg_id=?", vals)
        await db.commit()


async def add_balance(tg_id: int, delta: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance=balance+? WHERE tg_id=?", (delta, tg_id)
        )
        await db.commit()


async def get_all_users() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users ORDER BY created_at DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── Pending Trials ─────────────────────────────────────────────────────────────

async def save_pending_trial(
    rid: str, user_id: int, username: str, source: str, purpose: str
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO pending_trials "
            "(rid,user_id,username,source,purpose) VALUES (?,?,?,?,?)",
            (rid, user_id, username, source, purpose),
        )
        await db.commit()


async def delete_pending_trial(rid: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pending_trials WHERE rid=?", (rid,))
        await db.commit()


async def load_all_pending_trials() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM pending_trials ORDER BY created_at ASC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── Referrals ──────────────────────────────────────────────────────────────────

async def add_referral_reward(referrer_id: int, referred_id: int, amount: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO referral_rewards (referrer_id,referred_id,amount) "
            "VALUES (?,?,?)",
            (referrer_id, referred_id, amount),
        )
        await db.commit()


async def get_referral_stats(tg_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE referred_by=?", (tg_id,)
        ) as cur:
            row   = await cur.fetchone()
            count = row["cnt"] if row else 0
        async with db.execute(
            "SELECT SUM(amount) as total FROM referral_rewards WHERE referrer_id=?",
            (tg_id,),
        ) as cur:
            row   = await cur.fetchone()
            total = row["total"] or 0.0
    return {"count": count, "earned": total}


async def get_referrals_list(tg_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT tg_id,username,full_name,created_at FROM users WHERE referred_by=?",
            (tg_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── Transactions ───────────────────────────────────────────────────────────────

async def add_transaction(
    tg_id: int, amount: float, method: str, comment: str = ""
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO transactions (tg_id,amount,method,comment) VALUES (?,?,?,?)",
            (tg_id, amount, method, comment),
        )
        await db.commit()


async def get_revenue_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
              SUM(CASE WHEN date(created_at)=date('now')
                       THEN amount ELSE 0 END)                    AS today,
              SUM(CASE WHEN date(created_at)>=date('now','-7 days')
                       THEN amount ELSE 0 END)                    AS week,
              SUM(CASE WHEN date(created_at)>=date('now','start of month')
                       THEN amount ELSE 0 END)                    AS month,
              SUM(amount)                                         AS total
            FROM transactions
            WHERE amount>0 AND method NOT IN ('referral','bonus')
        """) as cur:
            row = await cur.fetchone()
        return dict(row) if row else {}


# ── Promo Codes ────────────────────────────────────────────────────────────────

async def get_promo(code: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM promo_codes WHERE code=?", (code.upper(),)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None


async def promo_already_used(tg_id: int, promo_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM promo_uses WHERE tg_id=? AND promo_id=?",
            (tg_id, promo_id),
        ) as cur:
            return await cur.fetchone() is not None


async def use_promo(tg_id: int, promo_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE promo_codes SET used_count=used_count+1 WHERE id=?", (promo_id,)
        )
        await db.execute(
            "INSERT INTO promo_uses (tg_id,promo_id) VALUES (?,?)", (tg_id, promo_id)
        )
        await db.commit()


async def create_promo(code: str, bonus_rub: float, max_uses: int, created_by: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO promo_codes (code,bonus_rub,max_uses,created_by) "
            "VALUES (?,?,?,?)",
            (code.upper(), bonus_rub, max_uses, created_by),
        )
        await db.commit()


async def get_all_promos() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM promo_codes ORDER BY created_at DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── CryptoBot invoices ─────────────────────────────────────────────────────────

async def save_crypto_invoice(invoice_id: int, tg_id: int, amount_rub: float):
    """Сохраняет новый счёт CryptoBot со статусом 'active'."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO crypto_invoices "
            "(invoice_id, tg_id, amount_rub) VALUES (?,?,?)",
            (invoice_id, tg_id, amount_rub),
        )
        await db.commit()


async def get_pending_crypto_invoices() -> list[dict]:
    """Возвращает все неоплаченные счета."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM crypto_invoices WHERE status='active' "
            "ORDER BY created_at ASC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def mark_crypto_invoice_paid(invoice_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE crypto_invoices SET status='paid' WHERE invoice_id=?",
            (invoice_id,),
        )
        await db.commit()


async def expire_old_crypto_invoices():
    """Помечает счета старше 2 часов как 'expired' (автоочистка)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE crypto_invoices SET status='expired' "
            "WHERE status='active' AND created_at < datetime('now', '-2 hours')",
        )
        await db.commit()


def _safe_inbounds(data_str: str) -> dict:
    try:
        data = json.loads(data_str)
        if not isinstance(data, dict): return {}
        return {int(k): v for k, v in data.items() if str(k).isdigit()}
    except Exception:
        return {}

async def get_all_panels() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM panels") as cursor:
            rows = await cursor.fetchall()
            panels = []
            for r in rows:
                p = dict(r)
                p['inbound_ids'] = json.loads(p.get('inbound_ids', '[]'))
                p['billing_inbound_ids'] = json.loads(p.get('billing_inbound_ids', '[]'))
                p['inbounds'] = _safe_inbounds(p.get('inbounds', '{}'))
                panels.append(p)
            return panels

async def add_panel(name: str, host: str, port: int, path: str, login: str, password: str, server_host: str, api_token: str = "") -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO panels (name, host, port, path, login, password, api_token, server_host) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, host, port, path, login, password, api_token, server_host)
        )
        await db.commit()
        return cursor.lastrowid

async def update_panel_inbounds(panel_id: int, inbound_ids: list[int], billing_inbound_ids: list[int], inbounds: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        # Убеждаемся что ключи — строки для JSON, а не объекты None
        clean_inbounds = {str(k): v for k, v in inbounds.items() if k is not None}
        await db.execute(
            "UPDATE panels SET inbound_ids = ?, billing_inbound_ids = ?, inbounds = ? WHERE id = ?",
            (json.dumps(inbound_ids), json.dumps(billing_inbound_ids), json.dumps(clean_inbounds), panel_id)
        )
        await db.commit()

async def get_panel(panel_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM panels WHERE id = ?", (panel_id,)) as cursor:
            r = await cursor.fetchone()
            if r:
                p = dict(r)
                p['inbound_ids'] = json.loads(p.get('inbound_ids', '[]'))
                p['billing_inbound_ids'] = json.loads(p.get('billing_inbound_ids', '[]'))
                p['inbounds'] = _safe_inbounds(p.get('inbounds', '{}'))
                return p
            return None
