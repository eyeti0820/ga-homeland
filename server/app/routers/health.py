"""探活：确认服务活着 + 库建好了几张表。"""
from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health(request: Request):
    from .. import db as dbm

    n_tables = dbm.count_tables(request.app.state.db_path)
    return {"ok": True, "db_tables": n_tables}
