# -*- coding: utf-8 -*-
"""M2 生成审计 API（薄）：gen_log 写入 + 查询，供 ops 层防重复（content_hash 对比）。

- 写入：每次生成成功后记录 task_id/action/result_ref/content_hash；
- 查询：GET /api/genlog?actor_id=&action=&days= 返回近 N 天记录（id DESC），
  生成层取 hash 集合做精确/近似查重。
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import get_db

router = APIRouter(prefix="/api", tags=["genlog"])


class GenLogIn(BaseModel):
    task_id: str
    actor_id: Optional[int] = None
    action: str                      # post|comment|like|note|diary|...
    result_ref: Optional[str] = None  # 表:ID
    content_hash: Optional[str] = None


@router.post("/genlog", status_code=201)
def create_genlog(body: GenLogIn, conn=Depends(get_db)):
    cur = conn.execute(
        "INSERT INTO gen_log(task_id, actor_id, action, result_ref, content_hash) VALUES (?,?,?,?,?)",
        (body.task_id, body.actor_id, body.action, body.result_ref, body.content_hash),
    )
    conn.commit()
    return {"id": cur.lastrowid, "status": "ok"}


@router.get("/genlog")
def list_genlog(actor_id: Optional[int] = None, action: Optional[str] = None,
                days: int = 30, limit: int = 500, conn=Depends(get_db)):
    q = "SELECT * FROM gen_log WHERE created_at >= datetime('now','localtime',?)"
    args: list = [f"-{min(days, 365)} days"]
    if actor_id is not None:
        q += " AND actor_id=?"; args.append(actor_id)
    if action:
        q += " AND action=?"; args.append(action)
    q += " ORDER BY id DESC LIMIT ?"; args.append(min(limit, 1000))
    rows = [dict(x) for x in conn.execute(q, args).fetchall()]
    return {"count": len(rows), "items": rows}
