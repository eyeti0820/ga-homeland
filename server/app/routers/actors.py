"""角色账号：五角色 + 主人 + 小梅的档案查询。写接口等 M1 生成链路一起做。"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_db

router = APIRouter(prefix="/api/actors", tags=["actors"])


class Actor(BaseModel):
    id: int
    name: str
    type: str
    persona_ref: Optional[str]
    avatar: Optional[str]


@router.get("", response_model=list[Actor])
def list_actors(conn=Depends(get_db)):
    rows = conn.execute(
        "SELECT id,name,type,persona_ref,avatar FROM actors ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{actor_id}", response_model=Actor)
def get_actor(actor_id: int, conn=Depends(get_db)):
    row = conn.execute(
        "SELECT id,name,type,persona_ref,avatar FROM actors WHERE id=?", (actor_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "actor not found")
    return dict(row)
