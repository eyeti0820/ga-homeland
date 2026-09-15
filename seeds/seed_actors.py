#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M1 种子：写入 7 个 actors（五角色 + 主人小墨 + 小梅），幂等可重跑。

用法（server venv，任意 cwd）：
    server/venv/bin/python seeds/seed_actors.py            # 正式库 server/homestead.db
    server/venv/bin/python seeds/seed_actors.py --db X.db  # 指定库（冒烟用）
幂等方式：按 name INSERT OR IGNORE，重复执行零副作用、不改已有行。
"""
import argparse
import sqlite3
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER_DIR))

from app import db as dbm          # noqa: E402
from app import config             # noqa: E402

# persona_ref 相对 GA 根（GA_ROOT/assets/personas/<char>.md），生成层据此加载人设
ACTORS = [
    # (name,          type,        persona_ref)
    ("夏以昼", "character", "assets/personas/xiazhou.md"),
    ("沈星回", "character", "assets/personas/shenxinghui.md"),
    ("秦彻",   "character", "assets/personas/qinche.md"),
    ("黎深",   "character", "assets/personas/lishen.md"),
    ("祁煜",   "character", "assets/personas/qiyu.md"),
    ("小墨",   "master",    None),
    ("小梅",   "mephisto",  None),
]


def seed(db_path=None) -> int:
    path = Path(db_path) if db_path else config.DB_PATH
    dbm.init_db(path)  # 幂等建表（冒烟场景库文件可能还不存在）
    conn = sqlite3.connect(str(path))
    try:
        before = conn.execute("SELECT COUNT(*) FROM actors").fetchone()[0]
        for name, type_, persona_ref in ACTORS:
            conn.execute(
                "INSERT OR IGNORE INTO actors(name, type, persona_ref) VALUES (?,?,?)",
                (name, type_, persona_ref),
            )
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM actors").fetchone()[0]
        rows = conn.execute("SELECT id,name,type FROM actors ORDER BY id").fetchall()
        return before, after, rows
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="覆盖目标库路径（默认 server/homestead.db 或 $HOMESTEAD_DB）")
    args = ap.parse_args()
    before, after, rows = seed(args.db)
    print(f"[seed_actors] before={before} after={after}")
    for rid, name, type_ in rows:
        print(f"  #{rid:<2} {name}（{type_}）")
    assert after >= len(ACTORS), f"期望至少 {len(ACTORS)} 个 actor，实际 {after}"
    print("[seed_actors] OK（幂等，重复执行安全）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
