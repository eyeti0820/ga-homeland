# -*- coding: utf-8 -*-
"""「我们的故事」小说版块 API：故事 CRUD / 开关 / 章节 / 主人批注与插入。

- 与 diary.py 同构的薄 CRUD；续写节奏（谁何时写章）在 ops 生成层（gen_story）。
- 语义（我们的故事 · 定稿方案 2026-09-24）：
  - stories：主人创建的架空 IF 故事（背景/user 身份/cast/文风参数），enabled=今日开关；
  - story_chapters：kind=chapter（角色续写，占 idx）/ master_seg（主人插入正文，占 idx）/
    master_note（导演纸条·吐槽，不占 idx，consumed 标记是否已注入续写 prompt）；
  - 圣经（bible）滚动摘要由生成层每次写完章后 PATCH 回来。
- 滚动窗口上下文（近 2 章全文 + 前章摘要）由生成层组装，服务端只存取。
"""
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..deps import get_db

router = APIRouter(prefix="/api", tags=["stories"])

CHAPTER_MAX = 20000   # 正文章节（含主人插入）
NOTE_MAX = 2000       # 主人纸条/吐槽
TITLE_MAX = 60
IDENTITY_MAX = 4000   # 背景 / user 故事身份
KINDS = ("chapter", "master_note", "master_seg")
SLUGS = ("xiazhou", "shenxinghui", "qinche", "lishen", "qiyu")  # 与 ops 层 CHAR_NAME 一致


# ---------------- 校验工具 ----------------

def _story(conn, sid: int):
    row = conn.execute("SELECT * FROM stories WHERE id=?", (sid,)).fetchone()
    if row is None:
        raise HTTPException(404, f"story {sid} not found")
    return row


def _chapter(conn, sid: int, cid: int):
    row = conn.execute(
        "SELECT * FROM story_chapters WHERE id=? AND story_id=?", (cid, sid)
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"chapter {cid} of story {sid} not found")
    return row


def _cast_list(raw: str) -> List[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        raise HTTPException(422, "cast_json 不是合法 JSON")
    if not isinstance(data, list) or not data or not all(isinstance(x, str) for x in data):
        raise HTTPException(422, "cast_json 须为非空字符串数组")
    bad = [x for x in data if x not in SLUGS]
    if bad:
        raise HTTPException(422, f"未知角色 slug：{bad}，可选 {list(SLUGS)}")
    return data


def _style_dict(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        raise HTTPException(422, "style_json 不是合法 JSON")
    if not isinstance(data, dict):
        raise HTTPException(422, "style_json 须为对象")
    return data


def _next_idx(conn, sid: int) -> int:
    row = conn.execute(
        """SELECT COALESCE(MAX(idx),0) AS m FROM story_chapters
           WHERE story_id=? AND kind IN ('chapter','master_seg')""", (sid,)
    ).fetchone()
    return row["m"] + 1


def _story_out(conn, row) -> dict:
    d = dict(row)
    d["cast"] = json.loads(d.pop("cast_json") or "[]")
    d["style"] = json.loads(d.pop("style_json") or "{}")
    st = conn.execute(
        """SELECT COUNT(*) AS n,
                  SUM(CASE WHEN kind='chapter' THEN 1 ELSE 0 END) AS by_chars,
                  MAX(created_at) AS last_at
           FROM story_chapters WHERE story_id=?""", (row["id"],)
    ).fetchone()
    d["chapter_count"] = st["n"] or 0
    d["chapter_by_chars"] = st["by_chars"] or 0
    d["last_chapter_at"] = st["last_at"]
    return d


# ---------------- 故事 ----------------

class StoryIn(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    background: str = Field(default="", max_length=IDENTITY_MAX)
    user_identity: str = Field(default="", max_length=IDENTITY_MAX)
    cast_json: str = "[]"            # JSON 数组字符串（前端直传 JSON.stringify）
    style_json: str = "{}"
    enabled: int = Field(default=0, ge=0, le=1)


class StoryPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=TITLE_MAX)
    background: Optional[str] = Field(default=None, max_length=IDENTITY_MAX)
    user_identity: Optional[str] = Field(default=None, max_length=IDENTITY_MAX)
    cast_json: Optional[str] = None
    style_json: Optional[str] = None
    enabled: Optional[int] = Field(default=None, ge=0, le=1)
    bible: Optional[str] = Field(default=None, max_length=20000)  # 生成层回写


@router.post("/stories", status_code=201)
def create_story(body: StoryIn, conn=Depends(get_db)):
    cast = _cast_list(body.cast_json)
    style = _style_dict(body.style_json)
    cur = conn.execute(
        """INSERT INTO stories(title, background, user_identity, cast_json, style_json, enabled)
           VALUES (?,?,?,?,?,?)""",
        (body.title.strip(), body.background.strip(), body.user_identity.strip(),
         json.dumps(cast, ensure_ascii=False), json.dumps(style, ensure_ascii=False),
         body.enabled),
    )
    conn.commit()
    return _story_out(conn, conn.execute("SELECT * FROM stories WHERE id=?", (cur.lastrowid,)).fetchone())


@router.get("/stories")
def list_stories(conn=Depends(get_db)):
    rows = conn.execute("SELECT * FROM stories ORDER BY id DESC").fetchall()
    return {"count": len(rows), "stories": [_story_out(conn, r) for r in rows]}


@router.get("/stories/{sid}")
def get_story(sid: int, conn=Depends(get_db)):
    d = _story_out(conn, _story(conn, sid))
    chapters = [
        {"id": c["id"], "idx": c["idx"], "kind": c["kind"], "author_slug": c["author_slug"],
         "title": c["title"], "consumed": c["consumed"], "created_at": c["created_at"],
         "chars": len(c["content"] or "")}
        for c in conn.execute(
            "SELECT * FROM story_chapters WHERE story_id=? ORDER BY idx, id", (sid,)
        ).fetchall()
    ]
    d["chapters"] = chapters
    return d


@router.patch("/stories/{sid}")
def patch_story(sid: int, body: StoryPatch, conn=Depends(get_db)):
    _story(conn, sid)
    sets, vals = [], []
    if body.title is not None:
        sets.append("title=?"); vals.append(body.title.strip())
    if body.background is not None:
        sets.append("background=?"); vals.append(body.background.strip())
    if body.user_identity is not None:
        sets.append("user_identity=?"); vals.append(body.user_identity.strip())
    if body.cast_json is not None:
        cast = _cast_list(body.cast_json)
        sets.append("cast_json=?"); vals.append(json.dumps(cast, ensure_ascii=False))
    if body.style_json is not None:
        style = _style_dict(body.style_json)
        sets.append("style_json=?"); vals.append(json.dumps(style, ensure_ascii=False))
    if body.enabled is not None:
        sets.append("enabled=?"); vals.append(body.enabled)
    if body.bible is not None:
        sets.append("bible=?, bible_updated_at=datetime('now','localtime')")
        vals.append(body.bible)
    if not sets:
        raise HTTPException(422, "无可更新字段")
    conn.execute(f"UPDATE stories SET {', '.join(sets)} WHERE id=?", (*vals, sid))
    conn.commit()
    return _story_out(conn, conn.execute("SELECT * FROM stories WHERE id=?", (sid,)).fetchone())


@router.delete("/stories/{sid}")
def delete_story(sid: int, conn=Depends(get_db)):
    _story(conn, sid)
    conn.execute("DELETE FROM story_chapters WHERE story_id=?", (sid,))
    conn.execute("DELETE FROM stories WHERE id=?", (sid,))
    conn.commit()
    return {"deleted": "story", "id": sid}


# ---------------- 章节 ----------------

class ChapterIn(BaseModel):
    kind: str = Field(pattern="^(master_note|master_seg|chapter)$")
    content: str = Field(min_length=1)
    title: Optional[str] = Field(default=None, max_length=TITLE_MAX)
    author_slug: Optional[str] = None   # kind=chapter 时生成层传
    summary: Optional[str] = Field(default=None, max_length=2000)  # chapter：章摘要（生成层）


class ChapterPatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=TITLE_MAX)
    content: Optional[str] = Field(default=None, min_length=1)
    summary: Optional[str] = Field(default=None, max_length=2000)
    consumed: Optional[int] = Field(default=None, ge=0, le=1)


@router.post("/stories/{sid}/chapters", status_code=201)
def create_chapter(sid: int, body: ChapterIn, conn=Depends(get_db)):
    _story(conn, sid)
    limit = NOTE_MAX if body.kind == "master_note" else CHAPTER_MAX
    if len(body.content) > limit:
        raise HTTPException(422, f"{body.kind} 内容超长（>{limit} 字）")
    if body.kind == "chapter":
        if body.author_slug not in SLUGS:
            raise HTTPException(422, f"chapter 须携带合法 author_slug，可选 {list(SLUGS)}")
        idx = _next_idx(conn, sid)
    elif body.kind == "master_seg":
        idx = _next_idx(conn, sid)
    else:  # master_note 挂在最新一章之后，不占号
        idx = conn.execute(
            """SELECT COALESCE(MAX(idx),0) AS m FROM story_chapters
               WHERE story_id=? AND kind IN ('chapter','master_seg')""", (sid,)
        ).fetchone()["m"]
    cur = conn.execute(
        """INSERT INTO story_chapters(story_id, idx, kind, author_slug, title, content, summary)
           VALUES (?,?,?,?,?,?,?)""",
        (sid, idx, body.kind, body.author_slug if body.kind == "chapter" else None,
         (body.title or "").strip() or None, body.content.strip(), body.summary),
    )
    conn.commit()
    return dict(_chapter(conn, sid, cur.lastrowid))


@router.get("/stories/{sid}/chapters/{cid}")
def get_chapter(sid: int, cid: int, conn=Depends(get_db)):
    return dict(_chapter(conn, sid, cid))


@router.patch("/stories/{sid}/chapters/{cid}")
def patch_chapter(sid: int, cid: int, body: ChapterPatch, conn=Depends(get_db)):
    row = _chapter(conn, sid, cid)
    limit = NOTE_MAX if row["kind"] == "master_note" else CHAPTER_MAX
    if body.content is not None and len(body.content) > limit:
        raise HTTPException(422, f"{row['kind']} 内容超长（>{limit} 字）")
    sets, vals = [], []
    if body.title is not None:
        sets.append("title=?"); vals.append(body.title.strip() or None)
    if body.content is not None:
        sets.append("content=?"); vals.append(body.content.strip())
    if body.summary is not None:
        sets.append("summary=?"); vals.append(body.summary)
    if body.consumed is not None:
        sets.append("consumed=?"); vals.append(body.consumed)
    if not sets:
        raise HTTPException(422, "无可更新字段")
    conn.execute(f"UPDATE story_chapters SET {', '.join(sets)} WHERE id=?", (*vals, cid))
    conn.commit()
    return dict(_chapter(conn, sid, cid))


@router.delete("/stories/{sid}/chapters/{cid}")
def delete_chapter(sid: int, cid: int, conn=Depends(get_db)):
    _chapter(conn, sid, cid)
    conn.execute("DELETE FROM story_chapters WHERE id=?", (cid,))
    conn.commit()
    return {"deleted": "chapter", "id": cid, "story_id": sid}


# ---------------- 生成层辅助（gen_story 输入）----------------

@router.get("/stories/duty/{slug}")
def duty_stories(slug: str, conn=Depends(get_db)):
    """该角色今日需要续写的故事：enabled=1 且 cast 包含 slug。"""
    if slug not in SLUGS:
        raise HTTPException(422, f"未知 slug {slug}，可选 {list(SLUGS)}")
    rows = conn.execute("SELECT * FROM stories WHERE enabled=1 ORDER BY id").fetchall()
    out = [_story_out(conn, r) for r in rows if slug in json.loads(r["cast_json"] or "[]")]
    return {"count": len(out), "stories": out}


@router.get("/stories/{sid}/pending-notes")
def pending_notes(sid: int, limit: int = 10, conn=Depends(get_db)):
    """未注入过续写的主人纸条（生成层取走、写完章后逐条标 consumed）。"""
    _story(conn, sid)
    rows = conn.execute(
        """SELECT * FROM story_chapters
           WHERE story_id=? AND kind='master_note' AND consumed=0 ORDER BY id LIMIT ?""",
        (sid, min(limit, 50)),
    ).fetchall()
    return {"count": len(rows), "notes": [dict(r) for r in rows]}
