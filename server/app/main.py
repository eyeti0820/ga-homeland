"""GA家园系统 · 服务入口。路由保持薄，业务逻辑放 ops 层（M1 起引入）。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import config, db as dbm
from .routers import actors, diary, forum, genlog, health, notes, moments

APP_NAME = "GA家园系统"


def create_app(db_path=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        dbm.init_db(app.state.db_path)  # 幂等：IF NOT EXISTS
        yield

    app = FastAPI(title=APP_NAME, version="0.1.0", lifespan=lifespan)
    app.state.db_path = db_path  # None → config.DB_PATH（或环境变量 HOMESTEAD_DB）

    app.include_router(health.router)
    app.include_router(actors.router)
    app.include_router(notes.router)
    app.include_router(diary.router)
    app.include_router(moments.router)
    app.include_router(genlog.router)
    app.include_router(forum.router)

    # 静态便签墙（M1 纯静态；React+Vite 到 M3 再评估）
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles
    web_dir = config.WEB_ROOT
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

        # HTML 不缓存：Safari 曾拿旧 index.html 导致新入口/结构不生效（2026-09-14 便签墙→日记本入口）
        @app.middleware("http")
        async def _html_no_cache(request, call_next):
            resp = await call_next(request)
            if resp.headers.get("content-type", "").startswith("text/html"):
                resp.headers["Cache-Control"] = "no-cache"
            return resp
    return app


app = create_app()  # uvicorn 入口：uvicorn app.main:app
