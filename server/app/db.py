"""SQLite 连接与建库。stdlib sqlite3，不引 ORM（见 task_plan 决策表）。"""
import sqlite3
from pathlib import Path

from . import config


def connect(db_path=None) -> sqlite3.Connection:
    """打开一个连接：Row 工厂 + 外键 + WAL。调用方负责 close（FastAPI 场景经 deps.get_db）。"""
    conn = sqlite3.connect(str(db_path or config.DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path=None) -> None:
    """按 schema.sql 幂等建库（全部 IF NOT EXISTS，可重复执行）。"""
    schema = Path(config.SCHEMA_PATH).read_text(encoding="utf-8")
    conn = connect(db_path)
    try:
        conn.executescript(schema)
        conn.commit()
    finally:
        conn.close()


def count_tables(db_path=None) -> int:
    conn = connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM sqlite_master"
            " WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
    finally:
        conn.close()
