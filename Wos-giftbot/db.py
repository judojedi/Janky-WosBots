"""SQLite storage: registered players and redemption history."""
import sqlite3
import time

import config


def _connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    conn = _connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            discord_id INTEGER PRIMARY KEY,
            fid TEXT NOT NULL,
            kid TEXT DEFAULT '',
            registered_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS redemptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fid TEXT NOT NULL,
            code TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT DEFAULT '',
            redeemed_at INTEGER NOT NULL,
            UNIQUE(fid, code)
        );
        CREATE TABLE IF NOT EXISTS seen_codes (
            code TEXT PRIMARY KEY,
            seen_at INTEGER NOT NULL
        );
        """
    )
    conn.commit()
    # migrate older DBs that lack the kid column
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "kid" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN kid TEXT DEFAULT ''")
        conn.commit()
    conn.close()


def register(discord_id: int, fid: str, kid: str = ""):
    conn = _connect()
    conn.execute(
        "INSERT OR REPLACE INTO users (discord_id, fid, kid, registered_at) VALUES (?, ?, ?, ?)",
        (discord_id, fid, kid, int(time.time())),
    )
    conn.commit()
    conn.close()


def unregister(discord_id: int) -> bool:
    conn = _connect()
    cur = conn.execute("DELETE FROM users WHERE discord_id = ?", (discord_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_fid(discord_id: int):
    conn = _connect()
    row = conn.execute("SELECT fid FROM users WHERE discord_id = ?", (discord_id,)).fetchone()
    conn.close()
    return row["fid"] if row else None


def get_user(discord_id: int):
    conn = _connect()
    row = conn.execute(
        "SELECT fid, kid FROM users WHERE discord_id = ?", (discord_id,)
    ).fetchone()
    conn.close()
    return (row["fid"], row["kid"]) if row else (None, None)


def all_users():
    conn = _connect()
    rows = conn.execute("SELECT discord_id, fid, kid FROM users").fetchall()
    conn.close()
    return [(r["discord_id"], r["fid"], r["kid"] or "") for r in rows]


def user_count() -> int:
    conn = _connect()
    row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
    conn.close()
    return row["c"]


def mark_seen(code: str) -> bool:
    """Returns True if this code is new (not seen before)."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO seen_codes (code, seen_at) VALUES (?, ?)",
            (code, int(time.time())),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def record_redemption(fid: str, code: str, status: str, detail: str = ""):
    conn = _connect()
    conn.execute(
        """INSERT OR REPLACE INTO redemptions (fid, code, status, detail, redeemed_at)
           VALUES (?, ?, ?, ?, ?)""",
        (fid, code, status, detail, int(time.time())),
    )
    conn.commit()
    conn.close()


def code_stats(code: str):
    conn = _connect()
    rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM redemptions WHERE code = ? GROUP BY status",
        (code,),
    ).fetchall()
    conn.close()
    return {r["status"]: r["c"] for r in rows}
