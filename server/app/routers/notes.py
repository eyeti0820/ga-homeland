# -*- coding: utf-8 -*-
"""M1 便签墙 API：便签 / 回复 / 未读互动。

- 便签与回复均为薄 CRUD；业务节奏（谁何时说话）在 ops 生成层。
- 主人（type='master'）对角色便签/回复的回应 → 写 unread_interactions(pending)，
  供 gen_note_reply 下一窗口"看到"并回应（开发方案 §7.2）。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..deps import get_db

router = APIRouter(prefix="/api", tags=["notes"])

# 便签纸色板（奶油莫兰迪冷暖撞色，与 web 端保持一致）
PALETTE = ["butter", "terracotta", "rose", "sage", "mist", "lavender"]


class NoteIn(BaseModel):
    actor_id: int
    content: str = Field(min_length=1, max_length=500)
    color: Optional[str] = None       # 缺省由服务端从色板随机
    pos_x: Optional[float] = None
    pos_y: Optional[float] = None


class ReplyIn(BaseModel):
    actor_id: int
    content: str = Field(min_length=1, max_length=300)


def _actor(conn, actor_id: int):
    row = conn.execute("SELECT id,name,type FROM actors WHERE id=?", (actor_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"actor {actor_id} not found")
    return row


def _note(conn, note_id: int):
    row = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"note {note_id} not found")
    return row


# ---------------- 便签 ----------------

@router.post("/notes", status_code=201)
def create_note(body: NoteIn, conn=Depends(get_db)):
    _actor(conn, body.actor_id)
    import random
    color = body.color if body.color in PALETTE else random.choice(PALETTE)
    cur = conn.execute(
        "INSERT INTO notes(actor_id, content, color, pos_x, pos_y) VALUES (?,?,?,?,?)",
        (body.actor_id, body.content.strip(), color,
         body.pos_x if body.pos_x is not None else random.uniform(0.08, 0.88),
         body.pos_y if body.pos_y is not None else random.uniform(0.08, 0.88)),
    )
    conn.commit()
    return get_note(cur.lastrowid, conn)


@router.get("/notes")
def list_notes(limit: int = 20, before_id: int | None = None,
               after_id: int | None = None, conn=Depends(get_db)):
    """游标分页：默认取最新一页（id DESC）；
    before_id=N → 取比 N 更旧的一页；after_id=N → 取比 N 更新的（id ASC，供增量轮询）。
    注：pinned 置顶与游标分页互斥，M1 无置顶功能，统一纯 id 序。"""
    lim = min(limit, 500)
    where, order, params = "", "DESC", []
    if before_id is not None:
        where, params = "WHERE n.id < ?", [before_id]
    elif after_id is not None:
        where, order, params = "WHERE n.id > ?", "ASC", [after_id]
    rows = conn.execute(
        f"""SELECT n.*, a.name AS author_name, a.type AS author_type
           FROM notes n JOIN actors a ON a.id = n.actor_id {where}
           ORDER BY n.id {order} LIMIT ?""",
        (*params, lim),
    ).fetchall()
    notes = []
    for r in rows:
        d = dict(r)
        d["replies"] = [
            dict(x) for x in conn.execute(
                """SELECT r.*, a.name AS author_name, a.type AS author_type
                   FROM note_replies r JOIN actors a ON a.id = r.actor_id
                   WHERE r.note_id=? ORDER BY r.id""", (d["id"],)
            ).fetchall()
        ]
        notes.append(d)
    return {"count": len(notes), "has_more": len(rows) == lim, "notes": notes}


@router.get("/notes/{note_id}")
def get_note(note_id: int, conn=Depends(get_db)):
    row = conn.execute(
        """SELECT n.*, a.name AS author_name, a.type AS author_type
           FROM notes n JOIN actors a ON a.id = n.actor_id WHERE n.id=?""",
        (note_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"note {note_id} not found")
    d = dict(row)
    d["replies"] = [
        dict(x) for x in conn.execute(
            """SELECT r.*, a.name AS author_name, a.type AS author_type
               FROM note_replies r JOIN actors a ON a.id = r.actor_id
               WHERE r.note_id=? ORDER BY r.id""", (note_id,)
        ).fetchall()
    ]
    return d


# ---------------- 回复 ----------------

@router.post("/notes/{note_id}/replies", status_code=201)
def create_reply(note_id: int, body: ReplyIn, conn=Depends(get_db)):
    note = _note(conn, note_id)          # 404 if missing
    actor = _actor(conn, body.actor_id)
    cur = conn.execute(
        "INSERT INTO note_replies(note_id, actor_id, content) VALUES (?,?,?)",
        (note_id, body.actor_id, body.content.strip()),
    )
    reply_id = cur.lastrowid
    # 主人回应了角色的内容 → 生成层视角的"未读"（§7.2）
    # 通知对象：该便签下所有参与过的非主人角色（便签作者 + 既有回复者），排除主人自己
    unread_created = False
    if actor["type"] == "master":
        participants = {note["actor_id"]} | {
            r["actor_id"] for r in conn.execute(
                "SELECT DISTINCT actor_id FROM note_replies WHERE note_id=? AND actor_id!=?",
                (note_id, body.actor_id),
            ).fetchall()
        }
        for target in participants - {body.actor_id}:
            t = conn.execute("SELECT type FROM actors WHERE id=?", (target,)).fetchone()
            if t and t["type"] != "master":
                conn.execute(
                    "INSERT INTO unread_interactions(actor_id, kind, ref_id) VALUES (?,?,?)",
                    (target, "master_note_reply", reply_id),
                )
                unread_created = True
    conn.commit()
    row = conn.execute(
        """SELECT r.*, a.name AS author_name, a.type AS author_type
           FROM note_replies r JOIN actors a ON a.id = r.actor_id WHERE r.id=?""",
        (reply_id,),
    ).fetchone()
    out = dict(row)
    out["unread_created"] = unread_created
    return out


# ---------------- 生成层辅助 ----------------

@router.get("/notes/pending-reply/{actor_id}")
def notes_pending_reply(actor_id: int, limit: int = 5, conn=Depends(get_db)):
    """主人的、该角色尚未回复过的便签（gen_note_reply 的输入之一）。"""
    _actor(conn, actor_id)
    rows = conn.execute(
        """SELECT n.*, a.name AS author_name,
                  (SELECT COUNT(*) FROM note_replies r
                    WHERE r.note_id = n.id AND r.actor_id = ?) AS my_replies
           FROM notes n JOIN actors a ON a.id = n.actor_id
           WHERE a.type = 'master' AND n.actor_id != ?
           ORDER BY n.id DESC LIMIT ?""",
        (actor_id, actor_id, min(limit, 20)),
    ).fetchall()
    pending = []
    for r in rows:
        d = dict(r)
        if d.pop("my_replies"):
            continue
        d["replies"] = [
            dict(x) for x in conn.execute(
                """SELECT r.*, a.name AS author_name
                   FROM note_replies r JOIN actors a ON a.id = r.actor_id
                   WHERE r.note_id=? ORDER BY r.id""", (d["id"],)
            ).fetchall()
        ]
        pending.append(d)
    return {"count": len(pending), "notes": pending}


@router.get("/unread")
def list_unread(actor_id: Optional[int] = None, status: str = "pending", conn=Depends(get_db)):
    q = "SELECT * FROM unread_interactions WHERE status=?"
    args: list = [status]
    if actor_id is not None:
        q += " AND actor_id=?"
        args.append(actor_id)
    q += " ORDER BY id DESC LIMIT 200"
    rows = [dict(x) for x in conn.execute(q, args).fetchall()]
    # 附带 ref 内容，便于前端/生成层直接展示
    for r in rows:
        # ref 按 kind 分流：便签回复 → note_replies(note_id)；交换日记回信 → diary_replies(entry_id)；
        # 朋友圈评论 → comments(post_id)（M2：master_comment=顶层评论 / master_reply=楼中楼回复）
        if r["kind"] == "master_diary_reply":
            ref = conn.execute(
                "SELECT entry_id, author_id, content FROM diary_replies WHERE id=?", (r["ref_id"],)
            ).fetchone()
        elif r["kind"] in ("master_comment", "master_reply"):
            ref = conn.execute(
                "SELECT post_id, actor_id, parent_id, content FROM comments WHERE id=?",
                (r["ref_id"],),
            ).fetchone()
        else:
            ref = conn.execute(
                "SELECT note_id, actor_id, content FROM note_replies WHERE id=?", (r["ref_id"],)
            ).fetchone()
        r["ref"] = dict(ref) if ref else None
    return {"count": len(rows), "items": rows}


@router.post("/unread/{unread_id}/done")
def unread_done(unread_id: int, conn=Depends(get_db)):
    cur = conn.execute(
        "UPDATE unread_interactions SET status='done' WHERE id=? AND status='pending'",
        (unread_id,),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, f"unread {unread_id} 不存在或已非 pending")
    return {"id": unread_id, "status": "done"}
