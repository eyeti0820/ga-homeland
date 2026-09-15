"""FastAPI 公共依赖：每请求独立连接，用完即关。"""
from fastapi import Request

from .db import connect


def get_db(request: Request):
    conn = connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()
