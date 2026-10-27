#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chatapp: GA角色仿微信IM后端 (2026-10-27 立项, 切片1=M0骨架+M1文字异步闭环)

设计(照 开发方案_Chat.md §4; 复用 apiapp.py 角色链路模式, 不import apiapp):
- 单服务五角色: 夏以昼/沈星回/黎深/祁煜/秦彻; per-char常驻agent(懒构建, 进程内上下文累积)
- IM异步模型: POST /api/msg 立即202入库(jobs表驱动, 同角色天然串行排队, 不429)
- worker: 每角色一个asyncio任务轮询jobs表; recall→put_task→done→入库assistant→宫殿ingest
- 消息即真相源: chat.db(sqlite WAL); 轮询=WHERE seq>?; 重启恢复=running→error+system提示
- P2宫殿: recall注入(只读) + 完成后ingest(user_id="chatapp"); 与fsapp/apiapp同账本跨进程flock
- 回复清洗: build_done_text(内含repair_folded_tags+tidy_lines, 全局规则8)

部署: launchd com.ga.chatapp -> 127.0.0.1:8789; tailscale serve /chat -> 8789
端口事由: 方案原定8788, 但apiapp单例锁(API_PORT+1=8788)bind-only占用该端口,
  服务改8789, 单例锁独立用GA_LOCK_PORT=19544(避开878x段, 防再撞)。
文件位置事由: GADIR非git仓, 真文件放家园repo Chat/入库; GA运行时经GA_RUNTIME_DIR引用。
"""
import asyncio, json, os, queue as Q, re, sqlite3, sys, threading, time
from contextlib import contextmanager

# ---------------- 自举(照apiapp L20-35; 必须在import agentmain之前) ----------------
GA_ROOT = os.environ.get("GA_RUNTIME_DIR") or os.path.expanduser(
    "~/Library/Application Support/GenericAgent/runtime/app")
CHAT_ROOT = os.path.dirname(os.path.abspath(__file__))   # 家园repo/Chat
sys.path.insert(0, GA_ROOT)
sys.path.insert(0, os.path.join(GA_ROOT, "frontends"))   # chatapp_common平铺import continue_cmd需要
os.chdir(GA_ROOT)

if "--no-user-tools" not in sys.argv:  # 角色实例服务: 禁ask_user+禁写GA长期记忆; 须在import agentmain前注入
    sys.argv.append("--no-user-tools")

from agentmain import GeneraticAgent
from char_config import CharCtx
from chatapp_common import FILE_HINT, build_done_text, ensure_single_instance

CHAT_HOST = (os.environ.get("CHATAPP_HOST") or "127.0.0.1").strip()
CHAT_PORT = int(os.environ.get("CHATAPP_PORT") or "8789")
LOCK_PORT = int(os.environ.get("GA_LOCK_PORT") or "19544")   # 单例锁(独立于服务端口)
AGENT_TIMEOUT_SEC = int(os.environ.get("CHATAPP_TIMEOUT") or "900")
SERVICE_SOURCE = "chatapp"     # put_task source + 宫殿ingest的user_id(转录sessionId=<char>_chatapp)

CHARS = ["xiazhou", "shenxinghui", "lishen", "qiyu", "qinche"]
CHAR_NAMES = {"xiazhou": "夏以昼", "shenxinghui": "沈星回", "lishen": "黎深",
              "qiyu": "祁煜", "qinche": "秦彻"}

DB_PATH = os.path.join(CHAT_ROOT, "chat.db")
WEB_DIR = os.path.join(CHAT_ROOT, "web")
MEDIA_DIR = os.path.join(CHAT_ROOT, "media")

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

app = FastAPI(title="GA Chat", version="0.1")

# tailscale serve /chat 前缀剥离: /chat/api/* -> /api/*, /chat/ -> /
from starlette.middleware.base import BaseHTTPMiddleware
class _StripChatPrefix(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        p = request.url.path
        if p == "/chat" or p.startswith("/chat/"):
            request.scope["path"] = p[len("/chat"):] or "/"
        return await call_next(request)
app.add_middleware(_StripChatPrefix)


# ---------------- 数据库 ----------------

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    os.makedirs(MEDIA_DIR, exist_ok=True)
    os.makedirs(os.path.join(MEDIA_DIR, "in"), exist_ok=True)
    os.makedirs(os.path.join(MEDIA_DIR, "bg"), exist_ok=True)
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages(
          seq INTEGER PRIMARY KEY AUTOINCREMENT,
          char_id TEXT NOT NULL,
          role TEXT NOT NULL,              -- user/assistant/system
          kind TEXT NOT NULL DEFAULT 'text', -- text/image_in/image_out/voice/system
          content TEXT NOT NULL,
          media_path TEXT,
          meta TEXT,
          ts REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_msg_char_seq ON messages(char_id, seq);
        CREATE TABLE IF NOT EXISTS jobs(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          char_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',  -- pending/running/done/error
          user_seq INTEGER, error TEXT, created REAL, finished REAL
        );
        CREATE INDEX IF NOT EXISTS idx_job_char_status ON jobs(char_id, status);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        """)


def recover_jobs():
    """重启恢复: 残留running→error(pending保留由worker继续消化), 并给该角色补一条system提示"""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, char_id FROM jobs WHERE status='running'").fetchall()
        for r in rows:
            conn.execute("UPDATE jobs SET status='error', error='服务重启中断', finished=? "
                         "WHERE id=?", (time.time(), r["id"]))
            conn.execute("INSERT INTO messages(char_id, role, kind, content, ts) "
                         "VALUES (?, 'system', 'system', ?, ?)",
                         (r["char_id"], "服务重启中断了回复, 重发一下即可", time.time()))
    if rows:
        print(f"[chatapp] 重启恢复: {len(rows)}个job标error", flush=True)


def _msg_dict(r):
    meta = None
    try:
        meta = json.loads(r["meta"]) if r["meta"] else None
    except Exception:
        meta = None
    return {"seq": r["seq"], "char_id": r["char_id"], "role": r["role"], "kind": r["kind"],
            "content": r["content"], "media_path": r["media_path"], "meta": meta, "ts": r["ts"]}


# ---------------- per-char agent服务(照apiapp CharService, 不import) ----------------

class CharService:
    """懒构建常驻agent(进程内上下文累积) + 宫殿ctx"""

    def __init__(self, char):
        self.char = char
        self.ctx = CharCtx(char)
        self._build_lock = threading.Lock()
        self.agent = None
        self.build_error = None

    def get_agent(self):
        with self._build_lock:
            if self.agent is None and self.build_error is None:
                try:
                    agent = GeneraticAgent()
                    persona = self.ctx.load_persona()
                    if persona:
                        agent.extra_sys_prompts.append(persona)
                    _apply_worldbook(agent)   # M2: persona之后注入共享世界书(读meta; 无则不注入)
                    self.ctx.apply_model(agent)
                    threading.Thread(target=agent.run, daemon=True).start()
                    self.agent = agent
                    _apply_worldbook(agent)   # 竞态自愈: 构建期间若世界书被热更, 落成时再校一次
                    print(f"[chatapp] {self.char}: agent ready", flush=True)
                except Exception as e:
                    self.build_error = str(e)
                    print(f"[chatapp] {self.char}: agent build failed: {e}", flush=True)
        if self.agent is None:
            raise RuntimeError(f"agent构建失败: {self.build_error}")
        return self.agent


SERVICES = {}   # char -> CharService


def _svc(char):
    svc = SERVICES.get(char)
    if svc is None:
        svc = SERVICES.setdefault(char, CharService(char))
    return svc


# ---------------- M2 共享世界书(meta表持久化 + 已构建agent热更新) ----------------

WORLDBOOK_KEY = "worldbook"
WORLDBOOK_TAG = "[共享世界书]"


def get_worldbook():
    with db() as conn:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (WORLDBOOK_KEY,)).fetchone()
    return r["value"] if r else ""


def set_worldbook(text):
    with db() as conn:
        conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (WORLDBOOK_KEY, text))


def _apply_worldbook(agent):
    """把最新世界书同步进agent.extra_sys_prompts: 替换旧段/新增/清空即删段.
    agentmain.run 每次task重组sys_prompt都重读该列表(2026-10-27核实), 故热更新下一轮即生效."""
    text = get_worldbook().strip()
    prompts = agent.extra_sys_prompts
    out, replaced = [], False
    for p in prompts:
        if isinstance(p, str) and p.startswith(WORLDBOOK_TAG):
            if text and not replaced:
                out.append(WORLDBOOK_TAG + "\n" + text)
                replaced = True
            # 旧[共享世界书]段一律丢弃(空=删除; 多段收敛为一段)
        else:
            out.append(p)
    if text and not replaced:
        out.append(WORLDBOOK_TAG + "\n" + text)
    prompts[:] = out
    return len(prompts)


def _hot_update_worldbook():
    """保存后同步所有已构建agent; 未构建角色在get_agent时注入(含构建期竞态自愈)"""
    return sum(1 for svc in list(SERVICES.values())   # S1: 快照迭代, 防worker并发setdefault致RuntimeError
               if svc.agent is not None and _apply_worldbook(svc.agent))


# ---------------- worker(jobs表驱动, 每角色一个串行worker) ----------------

_LLM_RUNNING_RE = re.compile(r'\**LLM Running \(Turn \d+\)[^\n]*\n?')
# B4: agent内部错误的done尾部格式= ```\nExcType: ... @ file:line, fn -> `line`\n``` (format_error)
_ERR_FENCE_RE = re.compile(r"\n```\n([\w.]+(?:Error|Exception|Interrupt)[^\n]{0,200})\n```\s*$")


def _take_job(char):
    """原子领取: pending→running(同角色最老优先), 返回job row或None"""
    with db() as conn:
        r = conn.execute("SELECT * FROM jobs WHERE char_id=? AND status='pending' "
                         "ORDER BY id LIMIT 1", (char,)).fetchone()
        if not r:
            return None
        cur = conn.execute("UPDATE jobs SET status='running' WHERE id=? AND status='pending'",
                           (r["id"],))
        if cur.rowcount != 1:   # 被并发抢走(理论不会, 稳妥起见)
            return None
        return r


def _finish_job(job_id, status, error=None):
    with db() as conn:
        conn.execute("UPDATE jobs SET status=?, error=?, finished=? WHERE id=?",
                     (status, error, time.time(), job_id))


def _fetch_user_msg(seq):
    with db() as conn:
        m = conn.execute("SELECT content FROM messages WHERE seq=?", (seq,)).fetchone()
    return (m["content"] if m else "").strip()


def _insert_msg(char, role, kind, content, media_path=None, meta=None):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO messages(char_id, role, kind, content, media_path, meta, ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (char, role, kind, content, media_path,
             json.dumps(meta, ensure_ascii=False) if meta else None, time.time()))
        return cur.lastrowid


def _is_typing(char):
    with db() as conn:
        r = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE char_id=? AND "
                         "status IN ('pending','running')", (char,)).fetchone()
        return r["n"] > 0


async def _process_job(job):
    char, user_seq = job["char_id"], job["user_seq"]
    m = await asyncio.to_thread(_fetch_user_msg, user_seq)
    user_text = (m if m is not None else "").strip()
    if not user_text:
        raise RuntimeError("user消息不存在")

    svc = _svc(char)
    agent = await asyncio.to_thread(svc.get_agent)   # S3: 懒构建挪线程池, 防构建耗时冻结事件循环
    try:
        mem = await asyncio.to_thread(svc.ctx.recall, user_text)
    except Exception as e:
        print(f"[chatapp] {char}: recall failed: {e}", flush=True)
        mem = ""
    task_text = (f"{mem}\n\n---\n[以上为记忆回响, 以下是用户本轮消息]\n{user_text}" if mem
                 else user_text)

    dq = agent.put_task(f"{FILE_HINT}\n\n{task_text}", source=SERVICE_SOURCE)
    start, raw = time.time(), None
    while True:
        try:
            item = await asyncio.to_thread(dq.get, True, 1)
        except Q.Empty:
            item = None
        if item and "done" in item:
            raw = item.get("done", "")
            break
        if time.time() - start > AGENT_TIMEOUT_SEC:
            try:
                agent.abort()
            except Exception:
                pass
            raise TimeoutError(f"agent超时({AGENT_TIMEOUT_SEC}s)已中止")

    raw = raw or ""
    m = _ERR_FENCE_RE.search(raw)
    if m:   # B4: agent内部错误文本(尾随format_error围栏)不入messages/不入宫殿
        raise RuntimeError(f"agent内部错误: {m.group(1)[:120]}")
    final = build_done_text(_LLM_RUNNING_RE.sub("", raw)) if raw.strip() else ""
    final = (final or "").strip()
    await asyncio.to_thread(_insert_msg, char, "assistant", "text", final or "……")
    if final:
        try:
            await asyncio.to_thread(svc.ctx.ingest, SERVICE_SOURCE, user_text, final)
        except Exception as e:
            print(f"[chatapp] {char}: ingest failed: {e}", flush=True)


async def _char_worker(char):
    while True:
        try:
            job = await asyncio.to_thread(_take_job, char)
            if job is None:
                await asyncio.sleep(1.0)
                continue
            t0 = time.time()
            try:
                await _process_job(job)
                await asyncio.to_thread(_finish_job, job["id"], "done")
                print(f"[chatapp] {char}: job#{job['id']} done in {time.time()-t0:.1f}s",
                      flush=True)
            except Exception as e:
                await asyncio.to_thread(_finish_job, job["id"], "error", str(e)[:500])
                await asyncio.to_thread(_insert_msg, char, "system", "system", "信号不好, 重发一下?")
                print(f"[chatapp] {char}: job#{job['id']} FAIL {e}", flush=True)
        except Exception as e:   # worker外层异常兜底, 不让循环死掉
            print(f"[chatapp] {char}: worker loop error: {e}", flush=True)
            await asyncio.sleep(3.0)


@app.on_event("startup")
async def _startup():
    for c in CHARS:
        asyncio.create_task(_char_worker(c))


# ---------------- API ----------------

def _err(status, message):
    return JSONResponse({"error": message}, status_code=status)


def _digest():
    """五角色摘要: 最后一条消息预览+时间+typing"""
    out = []
    with db() as conn:
        for c in CHARS:
            r = conn.execute("SELECT * FROM messages WHERE char_id=? ORDER BY seq DESC LIMIT 1",
                             (c,)).fetchone()
            if r:
                preview = (r["content"] or "").strip()
                if r["kind"] == "image_in" or r["kind"] == "image_out":
                    preview = "[图片]"
                elif r["kind"] == "voice":
                    preview = "[语音]"
                preview = preview.replace("\n", " ")[:40]
                out.append({"char_id": c, "name": CHAR_NAMES[c], "last_seq": r["seq"],
                            "last_kind": r["kind"], "preview": preview, "last_ts": r["ts"],
                            "typing": False})
            else:
                out.append({"char_id": c, "name": CHAR_NAMES[c], "last_seq": 0,
                            "last_kind": None, "preview": "", "last_ts": None, "typing": False})
    with db() as conn:
        pend = {r["char_id"]: r["n"] for r in conn.execute(
            "SELECT char_id, COUNT(*) AS n FROM jobs WHERE status IN ('pending','running') "
            "GROUP BY char_id").fetchall()}
    for o in out:
        o["typing"] = pend.get(o["char_id"], 0) > 0
    return out


@app.get("/api/chars")
def chars_ep():
    return {"chars": _digest()}


@app.get("/api/health")
def health():
    with db() as conn:
        n = conn.execute("SELECT status, COUNT(*) AS c FROM jobs GROUP BY status").fetchall()
    return {"status": "ok", "port": CHAT_PORT, "chars": CHARS,
            "jobs": {r["status"]: r["c"] for r in n}}


@app.post("/api/msg")
async def post_msg(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _err(400, "请求体不是合法JSON")
    if not isinstance(body, dict):
        return _err(400, "请求体须为JSON对象")
    char = str(body.get("char_id") or "").strip()
    text = str(body.get("text") or "").strip()
    if char not in CHARS:
        return _err(400, f"char_id须为: {','.join(CHARS)}")
    if not text:
        return _err(400, "text不能为空")
    if len(text) > 4000:
        return _err(400, "text过长(>4000)")
    def _store():   # B2: 消息+job同事务; B9: 服务端权威ts
        ts = time.time()
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO messages(char_id, role, kind, content, ts) "
                "VALUES (?, 'user', 'text', ?, ?)", (char, text, ts))
            seq = cur.lastrowid
            cur = conn.execute("INSERT INTO jobs(char_id, status, user_seq, created) "
                               "VALUES (?, 'pending', ?, ?)", (char, seq, ts))
            return seq, cur.lastrowid, ts
    seq, job_id, ts = await asyncio.to_thread(_store)
    return JSONResponse({"seq": seq, "job_id": job_id, "ts": ts}, status_code=202)


@app.get("/api/poll")
def poll(char_id: str = "", since: int = 0, full: int = 0):
    if char_id not in CHARS:
        return _err(400, f"char_id须为: {','.join(CHARS)}")
    with db() as conn:
        if full:
            rows = conn.execute(
                "SELECT * FROM messages WHERE char_id=? ORDER BY seq DESC LIMIT 200",
                (char_id,)).fetchall()
            rows.reverse()
        else:
            rows = conn.execute(
                "SELECT * FROM messages WHERE char_id=? AND seq>? ORDER BY seq ASC LIMIT 200",
                (char_id, since)).fetchall()
    return {"msgs": [_msg_dict(r) for r in rows], "typing": _is_typing(char_id)}


@app.get("/api/history")
def history(char_id: str = "", before_seq: int = 0, limit: int = 50):
    """游标分页: 取seq<brfore_seq的更早消息, 返回seq升序"""
    if char_id not in CHARS:
        return _err(400, f"char_id须为: {','.join(CHARS)}")
    limit = max(1, min(int(limit), 100))
    with db() as conn:
        if before_seq > 0:
            rows = conn.execute(
                "SELECT * FROM messages WHERE char_id=? AND seq<? ORDER BY seq DESC LIMIT ?",
                (char_id, before_seq, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages WHERE char_id=? ORDER BY seq DESC LIMIT ?",
                (char_id, limit)).fetchall()
    rows.reverse()
    return {"msgs": [_msg_dict(r) for r in rows]}


@app.get("/api/worldbook")
def worldbook_get():
    """M2: 世界书(五角色共享一份)"""
    return {"worldbook": get_worldbook()}


@app.put("/api/worldbook")
async def worldbook_put(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _err(400, "body须为JSON")
    if not isinstance(body, dict) or "worldbook" not in body or not isinstance(body["worldbook"], str):
        return _err(400, "body须为 {\"worldbook\": 文本}(空串=清空,须显式传键)")
    text = body["worldbook"]
    await asyncio.to_thread(set_worldbook, text)
    updated = await asyncio.to_thread(_hot_update_worldbook)
    print(f"[chatapp] worldbook已保存({len(text)}字), 热更新{updated}个agent", flush=True)
    return {"ok": True, "updated_agents": updated}


@app.get("/")
async def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


os.makedirs(MEDIA_DIR, exist_ok=True)   # B3: 无条件挂载, 非main入口跑(如uvicorn直启)不丢媒体路由
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


def main():
    lock = ensure_single_instance(LOCK_PORT, "chatapp")   # 单例锁(独立GA_LOCK_PORT, 勿用878x)
    init_db()
    recover_jobs()
    print(f"[chatapp] serving {CHAT_HOST}:{CHAT_PORT} (db={DB_PATH})", flush=True)
    uvicorn.run(app, host=CHAT_HOST, port=CHAT_PORT, log_level="info")


if __name__ == "__main__":
    main()
