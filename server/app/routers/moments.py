# -*- coding: utf-8 -*-
"""M2 朋友圈 API：发圈 / 楼中楼评论 / 点赞。

- 薄 CRUD，与 notes.py / diary.py 同构；业务节奏（谁何时发圈/互评）在 ops 生成层（gen_moment）。
- 语义（开发方案 §1.3）：主人评论角色朋友圈 → 顶层评论写 unread(kind='master_comment')、
  楼中楼回复写 unread(kind='master_reply')，供 gen_moment 下一窗口"看到"并回评。
- 角色互评/角色点赞不写 unread（节奏由 ops 层控制，避免互相打扰刷屏）。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..deps import get_db

router = APIRouter(prefix="/api", tags=["moments"])


class PostIn(BaseModel):
    actor_id: int
    content: str = Field(min_length=1, max_length=1000)
    gen_task_id: Optional[str] = None


class CommentIn(BaseModel):
    actor_id: int
    content: str = Field(min_length=1, max_length=500)
    parent_id: Optional[int] = None   # 楼中楼：指向被回复的评论


class LikeIn(BaseModel):
    actor_id: int


def _actor(conn, actor_id: int):
    row = conn.execute("SELECT id,name,type FROM actors WHERE id=?", (actor_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"actor {actor_id} not found")
    return row


def _post(conn, post_id: int):
    row = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"post {post_id} not found")
    return row


def _comments_of(conn, post_id: int):
    return [
        dict(x) for x in conn.execute(
            """SELECT c.*, a.name AS author_name, a.type AS author_type
               FROM comments c JOIN actors a ON a.id = c.actor_id
               WHERE c.post_id=? ORDER BY c.id""", (post_id,)
        ).fetchall()
    ]


def _likes_of(conn, post_id: int):
    return [
        {"actor_id": r["actor_id"], "name": r["name"], "created_at": r["created_at"]}
        for r in conn.execute(
            """SELECT l.actor_id, l.created_at, a.name
               FROM likes l JOIN actors a ON a.id = l.actor_id
               WHERE l.post_id=? ORDER BY l.id""", (post_id,)
        ).fetchall()
    ]


def _post_dict(conn, row) -> dict:
    d = dict(row)
    d["comments"] = _comments_of(conn, d["id"])
    d["likes"] = _likes_of(conn, d["id"])
    return d


# ---------------- 发圈 ----------------

@router.post("/posts", status_code=201)
def create_post(body: PostIn, conn=Depends(get_db)):
    _actor(conn, body.actor_id)
    cur = conn.execute(
        "INSERT INTO posts(actor_id, content, gen_task_id) VALUES (?,?,?)",
        (body.actor_id, body.content.strip(), body.gen_task_id),
    )
    conn.commit()
    return get_post(cur.lastrowid, conn)


@router.get("/posts")
def list_posts(limit: int = 20, before_id: Optional[int] = None,
               after_id: Optional[int] = None, actor_id: Optional[int] = None,
               conn=Depends(get_db)):
    """游标分页，语义与 /api/notes 一致：默认最新一页（id DESC）；
    before_id → 更旧一页；after_id → 更新一页（id ASC，供增量轮询）。"""
    lim = min(limit, 100)
    where, order, params = "", "DESC", []
    conds = []
    if before_id is not None:
        conds.append("p.id < ?"); params.append(before_id)
    elif after_id is not None:
        conds.append("p.id > ?"); params.append(after_id); order = "ASC"
    if actor_id is not None:
        conds.append("p.actor_id = ?"); params.append(actor_id)
    if conds:
        where = "WHERE " + " AND ".join(conds)
    rows = conn.execute(
        f"""SELECT p.*, a.name AS author_name, a.type AS author_type
            FROM posts p JOIN actors a ON a.id = p.actor_id {where}
            ORDER BY p.id {order} LIMIT ?""",
        (*params, lim),
    ).fetchall()
    posts = [_post_dict(conn, r) for r in rows]
    return {"count": len(posts), "has_more": len(rows) == lim, "posts": posts}


@router.get("/posts/{post_id}")
def get_post(post_id: int, conn=Depends(get_db)):
    row = conn.execute(
        """SELECT p.*, a.name AS author_name, a.type AS author_type
           FROM posts p JOIN actors a ON a.id = p.actor_id WHERE p.id=?""",
        (post_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"post {post_id} not found")
    return _post_dict(conn, row)


# ---------------- 评论（含楼中楼） ----------------

@router.post("/posts/{post_id}/comments", status_code=201)
def create_comment(post_id: int, body: CommentIn, conn=Depends(get_db)):
    post = _post(conn, post_id)                 # 404 if missing
    actor = _actor(conn, body.actor_id)
    parent = None
    if body.parent_id is not None:
        parent = conn.execute(
            "SELECT * FROM comments WHERE id=?", (body.parent_id,)
        ).fetchone()
        if parent is None or parent["post_id"] != post_id:
            raise HTTPException(400, f"parent comment {body.parent_id} 不属于 post {post_id}")

    cur = conn.execute(
        "INSERT INTO comments(post_id, actor_id, content, parent_id) VALUES (?,?,?,?)",
        (post_id, body.actor_id, body.content.strip(), body.parent_id),
    )
    comment_id = cur.lastrowid

    # 主人的评论 → 生成层视角的"未读"（§7.2）
    if actor["type"] == "master":
        if parent is not None:
            # 楼中楼：通知被回复的角色（主人自己/其他主人除外）
            tgt = conn.execute("SELECT type FROM actors WHERE id=?", (parent["actor_id"],)).fetchone()
            if tgt and tgt["type"] != "master":
                conn.execute(
                    "INSERT INTO unread_interactions(actor_id, kind, ref_id) VALUES (?,?,?)",
                    (parent["actor_id"], "master_reply", comment_id),
                )
        else:
            # 顶层评论：通知 post 作者 + 既有评论者中的非主人角色（语义同 notes.py）
            participants = {post["actor_id"]} | {
                r["actor_id"] for r in conn.execute(
                    "SELECT DISTINCT actor_id FROM comments WHERE post_id=? AND actor_id!=?",
                    (post_id, body.actor_id),
                ).fetchall()
            }
            for target in participants - {body.actor_id}:
                t = conn.execute("SELECT type FROM actors WHERE id=?", (target,)).fetchone()
                if t and t["type"] != "master":
                    conn.execute(
                        "INSERT INTO unread_interactions(actor_id, kind, ref_id) VALUES (?,?,?)",
                        (target, "master_comment", comment_id),
                    )
    conn.commit()

    row = conn.execute(
        """SELECT c.*, a.name AS author_name, a.type AS author_type
           FROM comments c JOIN actors a ON a.id = c.actor_id WHERE c.id=?""",
        (comment_id,),
    ).fetchone()
    return dict(row)


# ---------------- 点赞 ----------------

@router.post("/posts/{post_id}/likes", status_code=201)
def like_post(post_id: int, body: LikeIn, conn=Depends(get_db)):
    _post(conn, post_id)                        # 404 if missing
    _actor(conn, body.actor_id)
    try:
        conn.execute(
            "INSERT INTO likes(post_id, actor_id) VALUES (?,?)",
            (post_id, body.actor_id),
        )
        conn.commit()
    except Exception:
        raise HTTPException(409, f"actor {body.actor_id} 已赞过 post {post_id}")
    return {"post_id": post_id, "actor_id": body.actor_id, "liked": True,
            "like_count": conn.execute(
                "SELECT COUNT(*) AS n FROM likes WHERE post_id=?", (post_id,)
            ).fetchone()["n"]}


@router.delete("/posts/{post_id}/likes")
def unlike_post(post_id: int, actor_id: int, conn=Depends(get_db)):
    _post(conn, post_id)
    _actor(conn, actor_id)
    conn.execute("DELETE FROM likes WHERE post_id=? AND actor_id=?", (post_id, actor_id))
    conn.commit()
    return {"post_id": post_id, "actor_id": actor_id, "liked": False,
            "like_count": conn.execute(
                "SELECT COUNT(*) AS n FROM likes WHERE post_id=?", (post_id,)
            ).fetchone()["n"]}

# ---------------- 主人删除（M4.5: 只删主人自己发的内容）----------------
def _master_guard(conn, actor_id: int):
    row = conn.execute("SELECT type FROM actors WHERE id=?", (actor_id,)).fetchone()
    if row is None or row["type"] != "master":
        raise HTTPException(403, "仅主人可删除自己发的内容")


@router.delete("/posts/{post_id}")
def delete_post(post_id: int, conn=Depends(get_db)):
    post = _post(conn, post_id)
    _master_guard(conn, post["actor_id"])
    for r in conn.execute("SELECT id FROM comments WHERE post_id=?", (post_id,)).fetchall():
        conn.execute("DELETE FROM unread_interactions WHERE kind='master_comment' AND ref_id=?", (r["id"],))
    conn.execute("DELETE FROM comments WHERE post_id=?", (post_id,))
    conn.execute("DELETE FROM likes WHERE post_id=?", (post_id,))
    conn.execute("DELETE FROM posts WHERE id=?", (post_id,))
    conn.commit()
    return {"deleted": "post", "id": post_id}


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, conn=Depends(get_db)):
    row = conn.execute("SELECT actor_id FROM comments WHERE id=?", (comment_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"comment {comment_id} not found")
    _master_guard(conn, row["actor_id"])
    subtree, frontier = [comment_id], [comment_id]
    while frontier:
        qs = ",".join("?" * len(frontier))
        frontier = [r["id"] for r in conn.execute(
            "SELECT id FROM comments WHERE parent_id IN (" + qs + ")", frontier).fetchall()]
        subtree += frontier
    for cid in subtree:
        conn.execute("DELETE FROM unread_interactions WHERE kind='master_comment' AND ref_id=?", (cid,))
    qs = ",".join("?" * len(subtree))
    conn.execute("DELETE FROM comments WHERE id IN (" + qs + ")", subtree)
    conn.commit()
    return {"deleted": "comment", "ids": subtree}
