#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M3 种子：论坛六板 + NPC actors + 全员 masks（马甲），幂等可重跑。

用法（server venv，任意 cwd）：
    server/venv/bin/python seeds/seed_forum.py            # 正式库 server/homestead.db
    server/venv/bin/python seeds/seed_forum.py --db X.db  # 指定库（冒烟用）
幂等方式：
    - boards 按 key、actors 按 name、masks 按 forum_username 一律 INSERT OR IGNORE，
      重复执行零副作用、不改已有行。
设计依据：docs/m3_forum_design.md（一城六板拍板表）+ 开发方案 §1.3。
马甲红线：forum_username 对照关系仅生成层可见，展示层（web）永不渲染。
"""
import argparse
import json
import os
import sqlite3
from pathlib import Path

SEEDS = json.load(open(Path(__file__).parent / "forum_seeds.json", encoding="utf-8"))
SERVER_DIR = Path(__file__).resolve().parent.parent / "server"


def seed(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    n_board = n_actor = n_mask = 0
    try:
        cur = conn.cursor()
        # 1) boards
        for b in SEEDS["boards"]:
            cur.execute(
                "INSERT OR IGNORE INTO boards(key,name,description,access) VALUES(?,?,?,?)",
                (b["key"], b["name"], b["description"], b["access"]),
            )
            n_board += cur.rowcount if cur.rowcount > 0 else 0
        # 2) NPC actors（idempotent by name）
        for npc in SEEDS["npcs"]:
            cur.execute(
                "INSERT OR IGNORE INTO actors(name,type) VALUES(?,'npc')", (npc["name"],)
            )
            n_actor += cur.rowcount if cur.rowcount > 0 else 0
        # 3) masks：五角色/主人/小梅 + NPC（NPC 用本名，不隐藏）
        for real_name, uname in SEEDS["masks"].items():
            row = cur.execute("SELECT id FROM actors WHERE name=?", (real_name,)).fetchone()
            if row is None:
                raise SystemExit(f"[seed_forum] actors 缺 {real_name}，请先跑 seeds/seed_actors.py")
            cur.execute(
                "INSERT OR IGNORE INTO masks(actor_id,forum_username) VALUES(?,?)",
                (row[0], uname),
            )
            n_mask += cur.rowcount if cur.rowcount > 0 else 0
        for npc in SEEDS["npcs"]:
            row = cur.execute("SELECT id FROM actors WHERE name=?", (npc["name"],)).fetchone()
            cur.execute(
                "INSERT OR IGNORE INTO masks(actor_id,forum_username) VALUES(?,?)",
                (row[0], npc["name"]),
            )
            n_mask += cur.rowcount if cur.rowcount > 0 else 0
        conn.commit()
    finally:
        conn.close()
    counts = {
        "boards": sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM boards").fetchone()[0],
        "npc_actors": sqlite3.connect(db_path).execute(
            "SELECT COUNT(*) FROM actors WHERE type='npc'").fetchone()[0],
        "masks": sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM masks").fetchone()[0],
    }
    print(f"[seed_forum] 新增 boards={n_board} npc_actors={n_actor} masks={n_mask}；"
          f"库内合计 {counts}（幂等，重跑零副作用）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(SERVER_DIR / "homestead.db"))
    args = ap.parse_args()
    if not os.path.exists(args.db):
        # 冒烟/自建库场景：先按 schema.sql 建表
        schema = (SERVER_DIR / "schema.sql").read_text(encoding="utf-8")
        conn = sqlite3.connect(args.db)
        conn.executescript(schema)
        conn.commit()
        conn.close()
    seed(args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
