# -*- coding: utf-8 -*-
"""M3 论坛 API：六板 / 帖子流（游标）/ 楼层回复 / 马甲。

- 薄 CRUD，与 moments.py 同构；业务节奏（NPC 起楼/续楼、角色马甲留言、小梅主编运营）在 ops 生成层（gen_forum）。
- 马甲红线（开发方案 §1.3 + m3_forum_design）：mask↔actor 对照仅生成层可见，
  **threads / thread_posts 的展示型 API 永不返回 actor_id / actor_name**，只带 forum_username。
  GET /api/forum/masks 是生成层/主人发言入口专用（本机单用户，可接受）。
- 暗点板 access='secret'：N109 暗号彩蛋在前端实现，API 不设锁（本机玩具，防君子不防小人）。
- 置顶帖：is_pinned=1 仅 master/mephisto 型马甲可设（小梅主编运营动作）；
  列表排序 is_pinned DESC, id DESC，游标 before_id 只作用于普通帖（置顶少，跨页重复由前端 id 缓存去重）。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..deps import get_db

router = APIRouter(prefix="/api/forum", tags=["forum"])


class ThreadIn(BaseModel):
    board_key: str
    mask_id: int
    title: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=2000)
    is_pinned: bool = False   # 运营置顶：仅 master/mephisto 马甲可用


class FloorIn(BaseModel):
    mask_id: int
    content: str = Field(min_length=1, max_length=1000)


def _mask(conn, mask_id: int):
    """校验马甲存在，返回 (id, forum_username, actor_type)。生成层专用入口才允许拿到 actor_type。"""
    row = conn.execute(
        "SELECT m.id, m.forum_username, a.type FROM masks m"
        " JOIN actors a ON a.id = m.actor_id WHERE m.id = ?",
        (mask_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"mask {mask_id} not found")
    return row


@router.get("/boards")
def list_boards(conn=Depends(get_db)):
    rows = conn.execute(
        "SELECT b.id, b.key, b.name, b.description, b.access,"
        "       (SELECT COUNT(*) FROM threads t WHERE t.board_id = b.id) AS thread_count,"
        "       COALESCE((SELECT MAX(p.created_at) FROM thread_posts p"
        "                 JOIN threads t2 ON t2.id = p.thread_id WHERE t2.board_id = b.id),"
        "                (SELECT MAX(t3.created_at) FROM threads t3 WHERE t3.board_id = b.id)"
        "       ) AS last_activity"
        " FROM boards b ORDER BY b.id"
    ).fetchall()
    return {"count": len(rows), "items": [dict(r) for r in rows]}


@router.get("/threads")
def list_threads(board_key: str, before_id: Optional[int] = None, limit: int = 20,
                 conn=Depends(get_db)):
    limit = max(1, min(limit, 50))
    b = conn.execute("SELECT id FROM boards WHERE key=?", (board_key,)).fetchone()
    if b is None:
        raise HTTPException(404, f"board '{board_key}' not found")
    sql = (
        "SELECT t.id, t.board_id, t.title, t.is_pinned, t.views, t.created_at,"
        "       m.forum_username AS author,"
        "       (SELECT COUNT(*) FROM thread_posts p WHERE p.thread_id = t.id) AS reply_count,"
        "       COALESCE((SELECT MAX(p.created_at) FROM thread_posts p WHERE p.thread_id = t.id),"
        "                t.created_at) AS last_activity"
        " FROM threads t JOIN masks m ON m.id = t.author_mask_id"
        " WHERE t.board_id = ?"
    )
    params: list = [b["id"]]
    if before_id is not None:
        sql += " AND t.id < ?"
        params.append(before_id)
    sql += " ORDER BY t.is_pinned DESC, t.id DESC LIMIT ?"
    params.append(limit + 1)
    rows = conn.execute(sql, params).fetchall()
    has_more = len(rows) > limit
    items = [dict(r) for r in rows[:limit]]
    return {"count": len(items), "threads": items, "has_more": has_more,
            "board": {"id": b["id"], "key": board_key}}


@router.post("/threads", status_code=201)
def create_thread(body: ThreadIn, conn=Depends(get_db)):
    b = conn.execute("SELECT id,access FROM boards WHERE key=?", (body.board_key,)).fetchone()
    if b is None:
        raise HTTPException(404, f"board '{body.board_key}' not found")
    m = _mask(conn, body.mask_id)
    if body.is_pinned and m["type"] not in ("master", "mephisto"):
        raise HTTPException(403, "置顶是编辑部的权限，普通马甲不行")
    cur = conn.execute(
        "INSERT INTO threads(board_id, author_mask_id, title, content, is_pinned)"
        " VALUES(?,?,?,?,?)",
        (b["id"], body.mask_id, body.title.strip(), body.content.strip(),
         1 if body.is_pinned else 0),
    )
    conn.commit()
    return {"id": cur.lastrowid, "board_key": body.board_key,
            "author": m["forum_username"], "title": body.title.strip()}


@router.get("/threads/{thread_id}")
def get_thread(thread_id: int, before_id: Optional[int] = None, limit: int = 100,
               conn=Depends(get_db)):
    limit = max(1, min(limit, 200))
    t = conn.execute(
        "SELECT t.id, t.board_id, b.key AS board_key, b.name AS board_name,"
        "       t.title, t.content, t.is_pinned, t.views, t.created_at,"
        "       m.forum_username AS author"
        " FROM threads t JOIN masks m ON m.id = t.author_mask_id"
        " JOIN boards b ON b.id = t.board_id WHERE t.id = ?",
        (thread_id,),
    ).fetchone()
    if t is None:
        raise HTTPException(404, f"thread {thread_id} not found")
    conn.execute("UPDATE threads SET views = views + 1 WHERE id = ?", (thread_id,))
    conn.commit()
    thread = dict(t)
    thread["views"] += 1
    sql = ("SELECT p.id, p.thread_id, p.content, p.created_at, m.forum_username AS author"
           " FROM thread_posts p JOIN masks m ON m.id = p.author_mask_id"
           " WHERE p.thread_id = ?")
    params: list = [thread_id]
    if before_id is not None:
        sql += " AND p.id < ?"
        params.append(before_id)
    sql += " ORDER BY p.id ASC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    posts = [dict(r) for r in rows]
    return {"count": len(posts), "thread": thread, "posts": posts}


@router.post("/threads/{thread_id}/posts", status_code=201)
def create_floor(thread_id: int, body: FloorIn, conn=Depends(get_db)):
    t = conn.execute("SELECT id FROM threads WHERE id=?", (thread_id,)).fetchone()
    if t is None:
        raise HTTPException(404, f"thread {thread_id} not found")
    m = _mask(conn, body.mask_id)
    cur = conn.execute(
        "INSERT INTO thread_posts(thread_id, author_mask_id, content) VALUES(?,?,?)",
        (thread_id, body.mask_id, body.content.strip()),
    )
    conn.commit()
    return {"id": cur.lastrowid, "thread_id": thread_id,
            "author": m["forum_username"], "content": body.content.strip()}


@router.get("/masks")
def list_masks(actor_id: Optional[int] = None, conn=Depends(get_db)):
    """生成层 + 主人发言入口专用（含 actor 对照）。展示型路由永不引用本接口的数据做对照渲染。"""
    sql = ("SELECT m.id, m.actor_id, m.forum_username, a.name AS actor_name, a.type AS actor_type"
           " FROM masks m JOIN actors a ON a.id = m.actor_id")
    params: list = []
    if actor_id is not None:
        sql += " WHERE m.actor_id = ?"
        params.append(actor_id)
    sql += " ORDER BY m.id"
    rows = conn.execute(sql, params).fetchall()
    return {"count": len(rows), "items": [dict(r) for r in rows]}
