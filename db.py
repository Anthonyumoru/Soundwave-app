import sqlite3
import os
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_FILE  = os.path.join(DATA_DIR, "soundwave.db")


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    UNIQUE NOT NULL COLLATE NOCASE,
                email         TEXT    UNIQUE NOT NULL COLLATE NOCASE,
                password_hash TEXT    NOT NULL,
                avatar        TEXT    DEFAULT NULL,
                bio           TEXT    DEFAULT '',
                joined_date   TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_follows (
                follower_id INTEGER NOT NULL,
                followed_id INTEGER NOT NULL,
                PRIMARY KEY (follower_id, followed_id)
            );
            CREATE TABLE IF NOT EXISTS activities (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                username    TEXT    NOT NULL,
                action_type TEXT    NOT NULL,
                payload     TEXT    NOT NULL DEFAULT '{}',
                created_at  TEXT    NOT NULL
            );
        """)


# ── Users ─────────────────────────────────────────────────────────────────────

def create_user(username, email, password):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO users (username, email, password_hash, joined_date) VALUES (?,?,?,?)",
            (username, email, generate_password_hash(password),
             datetime.utcnow().strftime("%Y-%m-%d")),
        )


def get_user_by_username(username):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)
        ).fetchone()


def get_user_by_email(email):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE email=? COLLATE NOCASE", (email,)
        ).fetchone()


def get_user_by_id(user_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def update_avatar(user_id, avatar_filename):
    with get_db() as conn:
        conn.execute("UPDATE users SET avatar=? WHERE id=?", (avatar_filename, user_id))


def update_bio(user_id, bio):
    with get_db() as conn:
        conn.execute("UPDATE users SET bio=? WHERE id=?", (bio, user_id))


def check_password(user, password):
    return check_password_hash(user["password_hash"], password)


def get_all_users():
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM users ORDER BY joined_date DESC"
        ).fetchall()


# ── Follows ───────────────────────────────────────────────────────────────────

def follow_user(follower_id, followed_id):
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO user_follows VALUES (?,?)", (follower_id, followed_id)
            )
        except Exception:
            pass


def unfollow_user(follower_id, followed_id):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM user_follows WHERE follower_id=? AND followed_id=?",
            (follower_id, followed_id),
        )


def is_following(follower_id, followed_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT 1 FROM user_follows WHERE follower_id=? AND followed_id=?",
            (follower_id, followed_id),
        ).fetchone() is not None


def get_followers_count(user_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM user_follows WHERE followed_id=?", (user_id,)
        ).fetchone()[0]


def get_following_count(user_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM user_follows WHERE follower_id=?", (user_id,)
        ).fetchone()[0]


# ── Activities ────────────────────────────────────────────────────────────────

import json as _json

def record_activity(user_id, username, action_type, payload=None):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO activities (user_id, username, action_type, payload, created_at) VALUES (?,?,?,?,?)",
            (user_id, username, action_type,
             _json.dumps(payload or {}),
             datetime.utcnow().strftime("%Y-%m-%d %H:%M")),
        )


def get_user_activities(user_id, limit=25):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM activities WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = _json.loads(d["payload"])
        except Exception:
            d["payload"] = {}
        result.append(d)
    return result


def get_feed_activities(user_ids, limit=40):
    """Get recent activities from a list of user IDs (for home feed)."""
    if not user_ids:
        return []
    placeholders = ",".join("?" * len(user_ids))
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM activities WHERE user_id IN ({placeholders}) ORDER BY id DESC LIMIT ?",
            list(user_ids) + [limit],
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = _json.loads(d["payload"])
        except Exception:
            d["payload"] = {}
        result.append(d)
    return result
