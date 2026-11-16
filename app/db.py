"""SQLite 连接、事务和结构；每个请求独享连接。"""
import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS households (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, owner_id INTEGER
);
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 name TEXT NOT NULL, password TEXT NOT NULL, household_id INTEGER REFERENCES households(id),
 reminder TEXT NOT NULL DEFAULT '21:00', reminder_enabled INTEGER NOT NULL DEFAULT 0,
 categories TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS entries (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 occurred TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('expense','income','transfer','refund')),
 amount INTEGER NOT NULL CHECK(amount>0), category TEXT NOT NULL, merchant TEXT NOT NULL,
 channel TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', shared INTEGER NOT NULL DEFAULT 0,
 fingerprint TEXT NOT NULL, batch_id INTEGER, deleted INTEGER NOT NULL DEFAULT 0,
 UNIQUE(user_id,fingerprint)
);
CREATE INDEX IF NOT EXISTS entries_user_date ON entries(user_id,occurred);
CREATE TABLE IF NOT EXISTS budgets (
 household_id INTEGER NOT NULL REFERENCES households(id), month TEXT NOT NULL,
 category TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>=0),
 PRIMARY KEY(household_id,month,category)
);
CREATE TABLE IF NOT EXISTS invites (
 token TEXT PRIMARY KEY, household_id INTEGER NOT NULL REFERENCES households(id),
 expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS batches (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 filename TEXT NOT NULL, created TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
 undone INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS previews (
 token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 filename TEXT NOT NULL, payload TEXT NOT NULL, expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
 identity TEXT NOT NULL, attempted INTEGER NOT NULL
);
"""


def get_db():
    """返回请求级连接；退出请求时自动关闭，未提交事务自动回滚。"""
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def init_db():
    """仅创建缺失表；不覆盖已有用户数据。"""
    db = get_db()
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()
