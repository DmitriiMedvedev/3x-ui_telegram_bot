# Database module using aiosqlite for managing users, balances, and transactions.
"""
database.py — Dobrinya VPN Bot v15.0 (Absolute Schema Stability).
"""
import aiosqlite
import logging
import json
import uuid
import copy
from config import DB_PATH


logger  = logging.getLogger(__name__)
_panels_cache = None

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Базовая структура
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY, username TEXT DEFAULT '', full_name TEXT DEFAULT '', balance REAL DEFAULT 0.0,
                vless_uuid TEXT DEFAULT '', sub_id TEXT DEFAULT '', configs_all TEXT DEFAULT '', sub_url TEXT DEFAULT '',
                xui_enabled INTEGER DEFAULT 1, notified_low_balance INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0,
                referred_by INTEGER DEFAULT NULL, last_traffic_bytes INTEGER DEFAULT 0, total_traffic_bytes INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS panels (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, host TEXT NOT NULL, port INTEGER NOT NULL,
                path TEXT NOT NULL, login TEXT NOT NULL, password TEXT NOT NULL, api_token TEXT DEFAULT '',
                server_host TEXT NOT NULL, inbound_ids TEXT DEFAULT '[]', billing_inbound_ids TEXT DEFAULT '[]',
                inbounds TEXT DEFAULT '{}'
            )
        """)

        # Принудительные миграции (проверка каждой колонки)
        migrations = {
            "users": [
                ("vless_uuid", "TEXT DEFAULT ''"), ("sub_id", "TEXT DEFAULT ''"), ("configs_all", "TEXT DEFAULT ''"),
                ("sub_url", "TEXT DEFAULT ''"), ("xui_enabled", "INTEGER DEFAULT 1"), ("notified_low_balance", "INTEGER DEFAULT 0"),
                ("is_banned", "INTEGER DEFAULT 0"), ("referred_by", "INTEGER DEFAULT NULL"),
                ("last_traffic_bytes", "INTEGER DEFAULT 0"), ("total_traffic_bytes", "INTEGER DEFAULT 0")
            ],
            "panels": [
                ("api_token", "TEXT DEFAULT ''"), ("inbound_ids", "TEXT DEFAULT '[]'"),
                ("billing_inbound_ids", "TEXT DEFAULT '[]'"), ("inbounds", "TEXT DEFAULT '{}'"),
                ("server_host", "TEXT DEFAULT ''")
            ]
        }
        for table, cols in migrations.items():
            for col, defn in cols:
                try: await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
                except Exception as e: logger.debug(f"Migration column already exists: {e}")

        # Служебные таблицы
        await db.execute("CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER NOT NULL, amount REAL NOT NULL, method TEXT NOT NULL, comment TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')))")
        await db.execute("CREATE TABLE IF NOT EXISTS promo_codes (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, bonus_rub REAL NOT NULL, max_uses INTEGER DEFAULT 1, used_count INTEGER DEFAULT 0, created_by INTEGER NOT NULL, created_at TEXT DEFAULT (datetime('now')))")
        await db.execute("CREATE TABLE IF NOT EXISTS promo_uses (id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER NOT NULL, promo_id INTEGER NOT NULL, used_at TEXT DEFAULT (datetime('now')), UNIQUE(tg_id, promo_id))")
        await db.execute("CREATE TABLE IF NOT EXISTS referral_rewards (id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER NOT NULL, referred_id INTEGER NOT NULL, amount REAL NOT NULL, created_at TEXT DEFAULT (datetime('now')))")
        await db.execute("CREATE TABLE IF NOT EXISTS crypto_invoices (invoice_id INTEGER PRIMARY KEY, tg_id INTEGER NOT NULL, amount_rub REAL NOT NULL, status TEXT DEFAULT 'active', created_at TEXT DEFAULT (datetime('now')))")

        await db.commit()
    logger.info(f"Database Initialized at {DB_PATH}")

def _safe_json_list(data) -> list:
    if isinstance(data, list): return [str(x) for x in data]
    try:
        d = json.loads(data or '[]')
        return [str(x) for x in d] if isinstance(d, list) else []
    except Exception as e:
        logger.warning(f"Exception caught: {e}")
        return []

def _safe_json_dict(data) -> dict:
    if isinstance(data, dict): return {str(k): v for k, v in data.items()}
    try:
        d = json.loads(data or '{}')
        return {str(k): v for k, v in d.items()} if isinstance(d, dict) else {}
    except Exception as e:
        logger.warning(f"Exception caught: {e}")
        return {}

# ── Users ──

async def get_or_create_user(tg_id: int, username: str, full_name: str, referred_by: int | None = None) -> tuple[dict, bool]:
    from config import FREE_BONUS_RUB
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))).fetchone()
        if row:
            u = dict(row)
            if not u.get("vless_uuid") or not u.get("sub_id"):
                u["vless_uuid"], u["sub_id"] = u.get("vless_uuid") or str(uuid.uuid4()), u.get("sub_id") or uuid.uuid4().hex
                await db.execute("UPDATE users SET vless_uuid=?, sub_id=? WHERE tg_id=?", (u["vless_uuid"], u["sub_id"], tg_id))
                await db.commit()
            return u, False
        u_uuid, u_sub = str(uuid.uuid4()), uuid.uuid4().hex
        await db.execute("INSERT INTO users (tg_id,username,full_name,referred_by,vless_uuid,sub_id,balance) VALUES (?,?,?,?,?,?,?)", (tg_id, username, full_name, referred_by, u_uuid, u_sub, FREE_BONUS_RUB))
        await db.commit()
        row = await (await db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))).fetchone()
        return dict(row), True

async def get_user(tg_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))).fetchone()
        if row:
            u = dict(row)
            if not u.get("vless_uuid") or not u.get("sub_id"):
                u["vless_uuid"], u["sub_id"] = u.get("vless_uuid") or str(uuid.uuid4()), u.get("sub_id") or uuid.uuid4().hex
                await db.execute("UPDATE users SET vless_uuid=?, sub_id=? WHERE tg_id=?", (u["vless_uuid"], u["sub_id"], tg_id))
                await db.commit()
            return u
        return None

async def update_user(tg_id: int, **fields):
    if not fields: return
    allowed = {"username", "full_name", "balance", "vless_uuid", "sub_id", "configs_all", "sub_url", "xui_enabled", "notified_low_balance", "is_banned", "last_traffic_bytes", "total_traffic_bytes"}
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if not filtered: return
    sets = ", ".join(f"{k}=?" for k in filtered)
    vals = list(filtered.values()) + [tg_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {sets} WHERE tg_id=?", vals)  # nosec B608
        await db.commit()

async def add_balance(tg_id: int, delta: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (delta, tg_id))
        await db.commit()

async def get_all_users() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM users")).fetchall()
        return [dict(r) for r in rows]

# ── Panels ──

async def get_all_panels() -> list[dict]:
    global _panels_cache
    if _panels_cache is not None:
        return copy.deepcopy(_panels_cache)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM panels")).fetchall()
        res = []
        for r in rows:
            p = dict(r)
            p['inbound_ids'] = _safe_json_list(p.get('inbound_ids'))
            p['billing_inbound_ids'] = _safe_json_list(p.get('billing_inbound_ids'))
            p['inbounds'] = _safe_json_dict(p.get('inbounds'))
            res.append(p)
        _panels_cache = res
        return copy.deepcopy(res)

async def get_panel(panel_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM panels WHERE id = ?", (panel_id,))).fetchone()
        if row:
            p = dict(row)
            p['inbound_ids'] = _safe_json_list(p.get('inbound_ids'))
            p['billing_inbound_ids'] = _safe_json_list(p.get('billing_inbound_ids'))
            p['inbounds'] = _safe_json_dict(p.get('inbounds'))
            return p
        return None

async def delete_panel(panel_id: int):
    global _panels_cache
    _panels_cache = None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM panels WHERE id = ?", (panel_id,))
        await db.commit()

async def add_panel(name, host, port, path, login, password, server_host, api_token="") -> int:
    global _panels_cache
    _panels_cache = None
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO panels (name, host, port, path, login, password, api_token, server_host) VALUES (?,?,?,?,?,?,?,?)", (name, host, port, path, login, password, api_token, server_host))
        await db.commit()
        return cur.lastrowid

async def update_panel_inbounds(panel_id: int, ib_ids, bib_ids, inbounds: dict):
    global _panels_cache
    _panels_cache = None
    async with aiosqlite.connect(DB_PATH) as db:
        clean_inbounds = {str(k): v for k, v in inbounds.items()}
        await db.execute("UPDATE panels SET inbound_ids=?, billing_inbound_ids=?, inbounds=? WHERE id=?", (json.dumps(ib_ids), json.dumps(bib_ids), json.dumps(clean_inbounds), panel_id))
        await db.commit()

# ── Transactions, Promo, Referrals, Crypto ──
async def add_transaction(tg_id, amount, method, comment=""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO transactions (tg_id,amount,method,comment) VALUES (?,?,?,?)", (tg_id, amount, method, comment))
        await db.commit()

async def get_revenue_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT SUM(CASE WHEN date(created_at)=date('now') THEN amount ELSE 0 END) as today, SUM(CASE WHEN date(created_at)>=date('now','-7 days') THEN amount ELSE 0 END) as week, SUM(CASE WHEN date(created_at)>=date('now','start of month') THEN amount ELSE 0 END) as month, SUM(amount) as total FROM transactions WHERE amount>0 AND method NOT IN ('referral','bonus','admin_gift')")).fetchone()
        return dict(row) if row else {}

async def get_promo(code: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM promo_codes WHERE code=?", (code.upper(),))).fetchone()
        return dict(row) if row else None

async def promo_already_used(tg_id, promo_id) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        return await (await db.execute("SELECT 1 FROM promo_uses WHERE tg_id=? AND promo_id=?", (tg_id, promo_id))).fetchone() is not None

async def use_promo(tg_id, promo_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE id=?", (promo_id,))
        await db.execute("INSERT INTO promo_uses (tg_id,promo_id) VALUES (?,?)", (tg_id, promo_id))
        await db.commit()

async def create_promo(code, bonus, uses, created_by):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO promo_codes (code,bonus_rub,max_uses,created_by) VALUES (?,?,?,?)", (code.upper(), bonus, uses, created_by))
        await db.commit()

async def get_all_promos() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM promo_codes ORDER BY created_at DESC")).fetchall()
        return [dict(r) for r in rows]

async def add_referral_reward(referrer_id, referred_id, amount):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO referral_rewards (referrer_id,referred_id,amount) VALUES (?,?,?)", (referrer_id, referred_id, amount))
        await db.commit()

async def get_referral_stats(tg_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        c = await (await db.execute("SELECT COUNT(*) as cnt FROM users WHERE referred_by=?", (tg_id,))).fetchone()
        t = await (await db.execute("SELECT SUM(amount) as total FROM referral_rewards WHERE referrer_id=?", (tg_id,))).fetchone()
        return {"count": c["cnt"] if c else 0, "earned": t["total"] or 0.0}

async def save_crypto_invoice(invoice_id, tg_id, amount_rub):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO crypto_invoices (invoice_id, tg_id, amount_rub) VALUES (?,?,?)", (invoice_id, tg_id, amount_rub))
        await db.commit()

async def get_pending_crypto_invoices() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM crypto_invoices WHERE status='active' ORDER BY created_at ASC")).fetchall()
        return [dict(r) for r in rows]

async def mark_crypto_invoice_paid(invoice_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE crypto_invoices SET status='paid' WHERE invoice_id=?", (invoice_id,))
        await db.commit()

async def expire_old_crypto_invoices():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE crypto_invoices SET status='expired' WHERE status='active' AND created_at < datetime('now', '-2 hours')")
        await db.commit()
