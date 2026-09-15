# -*- coding: utf-8 -*-
"""M1.5 交换日记 API：日记页 / 回信 / 未读流转。

- 与 notes.py 同构的薄 CRUD；业务节奏（谁何时写页）在 ops 生成层（gen_diary_page）。
- 语义（开发方案 §1.4）：每角色一本与主人共有的日记本。角色写页（diary_entries），
  主人在页下回信（diary_replies，author=master）→ 写 unread_interactions
  (kind='master_diary_reply', actor_id=日记本主人, ref_id=回信行)，
  供 gen_diary_page 下一窗口"翻到回信"、回应并写新页。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..deps import get_db

router = APIRouter(prefix="/api", tags=["diary"])

ENTRY_MAX = 2000   # 日记成篇，比便签（500）长
REPLY_MAX = 1000   # 主人回信


class EntryIn(BaseModel):
    actor_id: int
    content: str = Field(min_length=1, max_length=ENTRY_MAX)
    mood: Optional[str] = Field(default=None, max_length=12)  # 轻量心情标签，可空


class ReplyIn(BaseModel):
    actor_id: int
    content: str = Field(min_length=1, max_length=REPLY_MAX)


def _actor(conn, actor_id: int):
    row = conn.execute("SELECT id,name,type FROM actors WHERE id=?", (actor_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"actor {actor_id} not found")
    return row


def _entry(conn, entry_id: int):
    row = conn.execute("SELECT * FROM diary_entries WHERE id=?", (entry_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"diary entry {entry_id} not found")
    return row


def _entry_out(conn, entry_id: int) -> dict:
    d = dict(conn.execute(
        """SELECT e.*, a.name AS author_name, a.type AS author_type
           FROM diary_entries e JOIN actors a ON a.id = e.actor_id
           WHERE e.id=?""", (entry_id,)
    ).fetchone())
    d["replies"] = [
        dict(x) for x in conn.execute(
            """SELECT r.*, a.name AS author_name, a.type AS author_type
               FROM diary_replies r JOIN actors a ON a.id = r.author_id
               WHERE r.entry_id=? ORDER BY r.id""", (entry_id,)
        ).fetchall()
    ]
    return d


# ---------------- 日记页 ----------------

@router.post("/diary/entries", status_code=201)
def create_entry(body: EntryIn, conn=Depends(get_db)):
    _actor(conn, body.actor_id)
    cur = conn.execute(
        "INSERT INTO diary_entries(actor_id, content, mood) VALUES (?,?,?)",
        (body.actor_id, body.content.strip(),
         body.mood.strip() if body.mood else None),
    )
    conn.commit()
    return _entry_out(conn, cur.lastrowid)


@router.get("/diary/entries")
def list_entries(limit: int = 10, actor_id: Optional[int] = None,
                 before_id: Optional[int] = None, after_id: Optional[int] = None,
                 conn=Depends(get_db)):
    """游标分页（同 notes）：默认最新一页 DESC；before_id 更旧；after_id 增量 ASC。
    日记按时间排成一条线：新页在上，旧页在下。"""
    lim = min(limit, 100)
    where, order, params = "", "DESC", []
    conds = []
    if actor_id is not None:
        conds.append("e.actor_id=?"); params.append(actor_id)
    if before_id is not None:
        conds.append("e.id<?"); params.append(before_id)
    elif after_id is not None:
        conds.append("e.id>?"); params.append(after_id); order = "ASC"
    if conds:
        where = "WHERE " + " AND ".join(conds)
    rows = conn.execute(
        f"""SELECT e.id FROM diary_entries e {where}
            ORDER BY e.id {order} LIMIT ?""",
        (*params, lim),
    ).fetchall()
    entries = [_entry_out(conn, r["id"]) for r in rows]
    return {"count": len(entries), "has_more": len(rows) == lim, "entries": entries}


@router.get("/diary/entries/{entry_id}")
def get_entry(entry_id: int, conn=Depends(get_db)):
    return _entry_out(conn, entry_id)


# ---------------- 回信 ----------------

@router.post("/diary/entries/{entry_id}/replies", status_code=201)
def create_reply(entry_id: int, body: ReplyIn, conn=Depends(get_db)):
    entry = _entry(conn, entry_id)
    actor = _actor(conn, body.actor_id)
    cur = conn.execute(
        "INSERT INTO diary_replies(entry_id, author_id, content) VALUES (?,?,?)",
        (entry_id, body.actor_id, body.content.strip()),
    )
    reply_id = cur.lastrowid
    # 主人回了角色的日记页 → 日记本主人的"未读"（下一窗口翻到回信）
    unread_created = False
    if actor["type"] == "master" and entry["actor_id"] != body.actor_id:
        t = conn.execute("SELECT type FROM actors WHERE id=?", (entry["actor_id"],)).fetchone()
        if t and t["type"] != "master":
            conn.execute(
                "INSERT INTO unread_interactions(actor_id, kind, ref_id) VALUES (?,?,?)",
                (entry["actor_id"], "master_diary_reply", reply_id),
            )
            unread_created = True
    conn.commit()
    out = dict(conn.execute(
        """SELECT r.*, a.name AS author_name, a.type AS author_type
           FROM diary_replies r JOIN actors a ON a.id = r.author_id WHERE r.id=?""",
        (reply_id,),
    ).fetchone())
    out["unread_created"] = unread_created
    return out


# ---------------- 生成层辅助 ----------------

@router.get("/diary/pending-replies/{actor_id}")
def diary_pending_replies(actor_id: int, limit: int = 5, conn=Depends(get_db)):
    """该角色名下 pending 的主人回信（gen_diary_page 的输入；done 由生成层标记）。"""
    _actor(conn, actor_id)
    rows = conn.execute(
        """SELECT u.id AS unread_id, u.created_at AS unread_at, r.*
           FROM unread_interactions u
           JOIN diary_replies r ON r.id = u.ref_id
           WHERE u.actor_id=? AND u.kind='master_diary_reply' AND u.status='pending'
           ORDER BY u.id LIMIT ?""",
        (actor_id, min(limit, 20)),
    ).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["author_name"] = dict(conn.execute(
            "SELECT name FROM actors WHERE id=?", (d["author_id"],)
        ).fetchone())["name"]
        items.append(d)
    return {"count": len(items), "replies": items}
