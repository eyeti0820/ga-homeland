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
import asyncio, datetime, json, os, queue as Q, re, shutil, sqlite3, subprocess, sys, threading, time, uuid
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

# --- 主动消息(1027改道, 原fsapp链路): env开关, 双3h静默门, 8-23点 ---
PROACTIVE = os.environ.get("GA_PROACTIVE", "0").strip() == "1"
PROACTIVE_CHARS = [s for s in os.environ.get("GA_PROACTIVE_CHARS", "xiazhou").replace(" ", "").split(",") if s]
PROACTIVE_IDLE_SEC = int(os.environ.get("GA_PROACTIVE_IDLE_SEC") or "10800")
PROACTIVE_HOUR_START = int(os.environ.get("GA_PROACTIVE_HOUR_START") or "8")
PROACTIVE_HOUR_END = int(os.environ.get("GA_PROACTIVE_HOUR_END") or "23")   # [8, 23)
_PROACTIVE_KEY = "proactive"   # meta表键: {char: last_proactive_ts}

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
from concurrent.futures import ThreadPoolExecutor

app = FastAPI(title="GA Chat", version="0.1")

from starlette.middleware.base import BaseHTTPMiddleware
# 1029字体案2: 字体静态资源长缓存 —— StaticFiles默认无Cache-Control, iOS对无策略字体不落缓存
# (/tmp/chatapp.log实锤: 手机每次会话全量重下同样23个子集, 全200几乎无304); iOS PWA挂起还会
# 杀掉在途字体请求且恢复不重试 → 见面屏苹方/文楷永久混合. 字体文件按内容生成后不改动, 可immutable.
# ⚠若将来重生成字体: 注入处加 ?v=N 破缓存, 或提示用户清PWA网站数据.
class _FontCacheHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        p = request.url.path
        if p.startswith("/chat/"): p = p[len("/chat"):]
        if (p == "/media/font" or p.startswith("/media/font/")) and resp.status_code == 200:
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp
app.add_middleware(_FontCacheHeaders)   # 先注册=内层(Strip剥前缀后此层见净路径, 兼容双写法)

# tailscale serve /chat 前缀剥离: /chat/api/* -> /api/*, /chat/ -> /
from starlette.middleware.base import BaseHTTPMiddleware
class _StripChatPrefix(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        p = request.url.path
        if p == "/chat" or p.startswith("/chat/"):
            request.scope["path"] = p[len("/chat"):] or "/"
        return await call_next(request)
app.add_middleware(_StripChatPrefix)   # 后注册=最外层


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


# ---- M3媒体常量(切片3 2026-10-27): 识图/发图/语音条 ----
VOICE_DIR = os.path.join(MEDIA_DIR, "voice")   # opus→m4a语音条(前端audio直放, iOS通)
OUT_DIR = os.path.join(MEDIA_DIR, "out")       # 发图从char_media_out搬运入库(/media静态挂载)
CM_OUT_DIR = os.path.join(GA_ROOT, "temp", "char_media_out")   # char_media产物绝对路径(bash工具cwd=GA_ROOT/temp)
GEN_LOG = os.path.join(CM_OUT_DIR, "gen_log.jsonl")            # char_media逐条追加{ts,char,path,desc}
VISION_CFG = (os.environ.get("CHAT_VISION_CFG") or "native_oai_config10").strip()  # fsapp同款GLM-4V识图
_CLONE_CHARS = ("xiazhou", "shenxinghui", "qinche", "lishen")   # 有云端克隆音色; 1028黎深档案上线; 祁煜tts会报错(正常)
MEDIA_HINTS = {c: (
    f"[媒体工具修正·{CHAR_NAMES[c]}专属] 本聊天服务五个角色同住一个进程, 你调用 char_media.py 的"
    f" tts/img 命令时, 必须在命令末尾追加 --char={c} "
    f"(即 ... frontends/char_media.py tts \"要说的话\" --char={c}), "
    f"否则音色和生成日志会错记到夏以昼名下。不要用 wximg/wxvoice(那是微信角色专用命令)。"
    + ("" if c in _CLONE_CHARS else
       f" 另外你({CHAR_NAMES[c]})没有克隆音色档案, tts 会报错, 请直接用文字表达。"))
    for c in CHARS}


# ---------------- 切片12: 五角色作息表(读同目录schedule.py; 失败降级为无作息, 不影响服务) ----------------
def _load_schedule():
    import importlib.util as ilu
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schedule.py")
    spec = ilu.spec_from_file_location("chat_schedule", p)
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

try:
    SCHEDULE = _load_schedule()
    print(f"[chatapp] schedule loaded: {len(SCHEDULE.SCHEDULES)}角色作息", flush=True)
except Exception as _e:
    SCHEDULE = None
    print(f"[chatapp] schedule加载失败(降级为无作息): {_e}", flush=True)


def init_db():
    os.makedirs(MEDIA_DIR, exist_ok=True)
    os.makedirs(os.path.join(MEDIA_DIR, "in"), exist_ok=True)
    os.makedirs(os.path.join(MEDIA_DIR, "bg"), exist_ok=True)
    os.makedirs(os.path.join(MEDIA_DIR, "avatars"), exist_ok=True)   # 切片5: 角色头像
    os.makedirs(OUT_DIR, exist_ok=True)     # M3: 发图入库
    os.makedirs(VOICE_DIR, exist_ok=True)   # M3: 语音条m4a
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
        cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        if "img_seqs" not in cols:          # M3迁移(必须在建表后): job挂image_in行seq(逗号分隔, 无图空串)
            conn.execute("ALTER TABLE jobs ADD COLUMN img_seqs TEXT")


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
                    if MEDIA_HINTS.get(self.char):   # M3: per-char媒体命令修正(五角色共进程, 音色防串)
                        agent.extra_sys_prompts.append(MEDIA_HINTS[self.char])
                    if SCHEDULE:   # 切片12: 作息overlay(人设尾部, Chat通道专属)
                        _ov = SCHEDULE.overlay_text(self.char)
                        if _ov:
                            agent.extra_sys_prompts.append(_ov)
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
_RETIRED = []   # 审计P1-2: B10退役的旧CharService留档(agent.run线程无停止口, 计数防泄漏失追踪)


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


def _mode_of_user_row(seq):
    """审计P1-2: assistant/system行mode锚定user发送行(发送时语义), 完成时切模式不串流; 无/坏meta返None"""
    if not seq:
        return None
    with db() as conn:
        m = conn.execute("SELECT meta FROM messages WHERE seq=?", (seq,)).fetchone()
    if not m or not (m["meta"] or "").strip():
        return None
    try:
        v = json.loads(m["meta"]).get("mode")
        return v if v in ("chat", "meet") else None
    except Exception:
        return None


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


# ---------------- M3媒体: 识图/发图/语音条 (2026-10-27, 链路照fsapp成熟方案但不import) ----------------
_FILE_TAG_RE = re.compile(r"\[FILE:[^\]]+\]")   # 仅文本清洗用; 提取用_extract_files

# ---- S6(1027): 思维链/工具轨迹泄漏清扫 ------------------------------------
# 实弹(seq46): 正文后拖 /Applications/...char_media.py tts "..." --char=xiazhou 命令行
# + 英文元叙述(Wait, I need to execute the tool... / Reasoning: 1. Current phase... / Let's run the tool.)
# 既有清洗只剥<think>等标签对, 无标签叙述直接漏出 → 行级清扫, 中文正文零误伤
_CMDLINE_RE = re.compile(r"^(?:/[\w./\- ]*?)?(?:python3?|osascript|node)\b")
_COT_WORDS_RE = re.compile(
    r"tool|TTS|persona|Reasoning:|Strategy:|Current phase|\bGoal:|system allows|\bexecute\b", re.I)

def _is_en_line(t):
    """英文行判定: 非空白字符中ASCII字母占比>70% (中文正文必不过门)"""
    s = "".join(t.split())
    if not s:
        return False
    n = sum(c.isascii() and c.isalpha() for c in s)
    return n / len(s) > 0.7

def _scrub_cot(text, quiet=False):
    """行级清扫: ①绝对路径工具命令行 ②英文元叙述行。```围栏内/[FILE:]行不动。"""
    if not text:
        return text
    kept, hits, fence = [], 0, False
    for ln in text.split("\n"):
        t = ln.strip()
        if t.startswith("```"):
            fence = not fence
            kept.append(ln)
            continue
        if fence or "[FILE:/" in t:
            kept.append(ln)
            continue
        if _CMDLINE_RE.match(t) and (".py" in t or "python" in t.lower() or "--char=" in t or "osascript" in t):
            hits += 1
            continue
        if _is_en_line(t) and _COT_WORDS_RE.search(t):
            hits += 1
            continue
        kept.append(ln)
    if not hits:
        return text
    if not quiet:
        print(f"[chatapp] CoT清扫: 剔除{hits}行泄漏叙述", flush=True)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()

def _scrub_agent_history(char, svc):
    """洗agent会话史内已入史的泄漏叙述(backend.history=下轮prompt来源), 防二次进prompt"""
    try:
        hist = svc.agent.llmclient.backend.history
    except Exception:
        return
    if not isinstance(hist, list):
        return
    n = 0
    for msg in hist:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        c = msg.get("content")
        if isinstance(c, str):
            c2 = _scrub_cot(c, quiet=True)
            if c2 != c:
                msg["content"] = c2
                n += 1
        elif isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict) and blk.get("type") == "text" and isinstance(blk.get("text"), str):
                    t2 = _scrub_cot(blk["text"], quiet=True)
                    if t2 != blk["text"]:
                        blk["text"] = t2
                        n += 1
    if n:
        print(f"[chatapp] {char}: CoT洗史{n}块", flush=True)


def _reset_agent_memory(char):
    """切片7: /new 指令重置角色内存会话史"""
    svc = SERVICES.get(char)
    if not svc or not svc.agent:
        return False
    try:
        hist = svc.agent.llmclient.backend.history
        if isinstance(hist, list):
            hist.clear()
            print(f"[chatapp] {char}: agent memory cleared via /new", flush=True)
            return True
    except Exception as e:
        print(f"[chatapp] {char}: reset agent memory error: {e}", flush=True)
    return False


def _remove_agent_history_msg(char, role, content):
    """切片7: 删除单条消息时同步抹除agent会话内存中的对应条目"""
    if not content or not isinstance(content, str):
        return 0
    svc = SERVICES.get(char)
    if not svc or not svc.agent:
        return 0
    try:
        hist = svc.agent.llmclient.backend.history
        if not isinstance(hist, list):
            return 0
        removed = 0
        target = content.strip()
        i = len(hist) - 1
        while i >= 0:
            m = hist[i]
            if isinstance(m, dict) and m.get("role") == role:
                mc = m.get("content")
                matched = False
                if isinstance(mc, str) and (mc.strip() == target or target in mc):
                    matched = True
                elif isinstance(mc, list):
                    for b in mc:
                        if isinstance(b, dict) and b.get("type") == "text" and target in str(b.get("text") or ""):
                            matched = True
                            break
                if matched:
                    hist.pop(i)
                    removed += 1
            i -= 1
        if removed:
            print(f"[chatapp] {char}: 同步从agent内存清除{removed}条消息 (role={role})", flush=True)
        return removed
    except Exception as e:
        print(f"[chatapp] {char}: remove agent msg error: {e}", flush=True)
        return 0
_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")


def _extract_files(text):
    return re.findall(r"\[FILE:([^\]]+)\]", text or "")


def _media_fp(url_path):
    """/media/in/x.jpg → 磁盘绝对路径; 越权路径(../穿越)返回''(P1-7)"""
    p = str(url_path or "")
    if not p.startswith("/media/"):
        return p
    base = os.path.realpath(MEDIA_DIR)
    fp = os.path.realpath(os.path.join(MEDIA_DIR, p[len("/media/"):]))
    return fp if (fp == base or fp.startswith(base + os.sep)) else ""


def _vision_one(img_path):
    """识图(fsapp _vision_describe同款): VISION_CFG=GLM-4V图转文字; 单图失败返回''(fail-open)"""
    try:
        import base64 as _b64
        from llmcore import resolve_client
        with open(img_path, "rb") as f:
            b64 = _b64.b64encode(f.read()).decode()
        mime = "image/png" if str(img_path).lower().endswith(".png") else "image/jpeg"
        c = resolve_client(VISION_CFG)
        gen = c.chat([{"role": "user", "content": [
            {"type": "text", "text": "用中文客观描述这张图片的关键内容(场景/人物/物体/画面文字), 200字内。"},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}])
        chunks, resp = [], None
        try:
            while True:
                ch = next(gen)
                if ch:
                    chunks.append(str(ch))
        except StopIteration as e:
            resp = e.value
        d = str(getattr(resp, "content", "") or "".join(chunks)).strip()
        return re.sub(r"<think>.*?</think>", "", d, flags=re.S).strip()
    except Exception as e:
        print(f"[chatapp] vision failed {os.path.basename(str(img_path))}: {e}", flush=True)
        return ""


def _describe_job_images(char, job):
    """job.img_seqs→识图文本列表; 并把描述回写image_in行meta.vision(前端可复核)"""
    seqs = [int(x) for x in str(job["img_seqs"] or "").split(",") if x.strip().isdigit()]
    out = []
    if not seqs:
        return out
    with db() as conn:
        q = ",".join("?" * len(seqs))
        rows = conn.execute(
            "SELECT * FROM messages WHERE seq IN (%s) AND char_id=? AND role='user' "
            "AND kind='image_in'" % q, seqs + [char]).fetchall()
    for r in rows:
        d = _vision_one(_media_fp(r["media_path"])) or "(识图失败)"
        out.append(f"[用户发图·自动识图]\n{d}")
        try:   # P2-7: meta为JSON对象, 只更新vision键不整串覆盖
            with db() as conn:
                old = conn.execute("SELECT meta FROM messages WHERE seq=?",
                                   (r["seq"],)).fetchone()
                try:
                    meta = json.loads(old["meta"]) if old and old["meta"] else {}
                except Exception:
                    meta = {}
                if not isinstance(meta, dict):
                    meta = {}
                meta["vision"] = d[:500]
                conn.execute("UPDATE messages SET meta=? WHERE seq=?",
                             (json.dumps(meta, ensure_ascii=False), r["seq"]))
        except Exception:
            pass
    return out


def _opus_to_m4a(opus_path):
    """opus→m4a(aac64k, afconvert实测rc0)存VOICE_DIR; afinfo读时长; 失败(None, None)"""
    try:
        os.makedirs(VOICE_DIR, exist_ok=True)
        name = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}_{os.path.basename(str(opus_path))}.m4a"
        out = os.path.join(VOICE_DIR, name)
        r = subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "64000",
                            str(opus_path), out], capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or not os.path.exists(out):
            print(f"[chatapp] afconvert rc={r.returncode}: {r.stderr.strip()[-120:]}", flush=True)
            try:   # P2-5: 清掉半截产物
                os.unlink(out)
            except Exception:
                pass
            return None, None
        dur = 0.0
        r2 = subprocess.run(["afinfo", out], capture_output=True, text=True, timeout=30)
        m = re.search(r"estimated duration: *([\d.]+)", r2.stdout)
        if m:
            dur = round(float(m.group(1)), 1)
        return f"/media/voice/{name}", dur
    except Exception as e:
        print(f"[chatapp] opus转m4a失败: {e}", flush=True)
        return None, None


def _emit_media_row(char, src, desc=""):
    """单个媒体产物→messages行; 返回宫殿注记(无效产物返回'')"""
    p, low = str(src), str(src).lower()
    if low.endswith(".opus"):
        url, dur = _opus_to_m4a(p)
        if url:
            _insert_msg(char, "assistant", "voice", "(语音)", media_path=url,
                        meta={"dur": dur, "src": os.path.basename(p)})
            return "[发过语音]"
        _insert_msg(char, "system", "system", "(一条语音转码失败, 内容丢失)", None, {"mode": _mode_of(char)})   # P1-6; 审计P2-6: system行随屏
        return ""
    if low.endswith(_IMG_EXT):
        try:
            os.makedirs(OUT_DIR, exist_ok=True)
            name = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}{os.path.splitext(p)[1].lower()}"
            shutil.copy2(p, os.path.join(OUT_DIR, name))
            _insert_msg(char, "assistant", "image_out", "[图片]",
                        media_path=f"/media/out/{name}",
                        meta={"desc": desc[:80]} if desc else None)
            return f"[发过图:{desc[:60]}]" if desc else "[发过图]"
        except Exception as e:
            print(f"[chatapp] 发图搬运失败: {e}", flush=True)
    return ""


_GEN_CURSOR = {}   # P1-4: char → 已处理到的gen_log ts, 跨job去重(防时间窗重叠重复搬运)


def _scan_gen_log(char, t0, got):
    """兜底(照fsapp _scan_media): agent没把[FILE:]写进回复时, 扫gen_log尾120行补漏;
    P2-3: 窗口40→120行/按ts升序/逐行容错ts脏值; P1-4: 游标=max(t0,上次处理点)"""
    out = []
    try:
        if not os.path.exists(GEN_LOG):
            return out
        t_start = max(float(t0), _GEN_CURSOR.get(char, float(t0)))
        with open(GEN_LOG, encoding="utf-8") as f:
            lines = f.readlines()[-120:]
        hit = []
        for ln in lines:
            try:
                r = json.loads(ln.strip())
            except Exception:
                continue
            if r.get("char") != char:
                continue
            try:
                ts = float(r.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if ts < t_start:
                continue
            p = r.get("path") or ""
            if p and os.path.isabs(p) and os.path.exists(p) and p not in got:
                got.add(p)
                hit.append((ts, p, str(r.get("desc") or "")))
        for ts, p, desc in sorted(hit, key=lambda x: x[0]):
            _GEN_CURSOR[char] = max(_GEN_CURSOR.get(char, 0.0), ts)
            out.append((p, desc))
        _GEN_CURSOR[char] = max(_GEN_CURSOR.get(char, 0.0), time.time())
    except Exception as e:
        print(f"[chatapp] gen_log扫描失败: {e}", flush=True)
    return out


def _emit_reply_media(char, raw, t0):
    """回复中[FILE:绝对路径]提取 + gen_log兜底 → 媒体行(硬上限4条, P1-3); 返回去重宫殿注记串"""
    got = set()
    for f in _extract_files(raw):
        f = f.strip()
        if f:
            got.add(f)
    notes = []
    for f in sorted(got)[:4]:
        if os.path.isabs(f) and os.path.exists(f):
            n = _emit_media_row(char, f)
            if n:
                notes.append(n)
    for p, desc in _scan_gen_log(char, t0, got):
        if len(notes) >= 4:
            print(f"[chatapp] 媒体行超4条截断 char={char}", flush=True)
            break
        n = _emit_media_row(char, p, desc)
        if n:
            notes.append(n)
    return " ".join(dict.fromkeys(notes))


async def _process_job(job):
    char, user_seq = job["char_id"], job["user_seq"]
    m = await asyncio.to_thread(_fetch_user_msg, user_seq)
    user_text = (m if m is not None else "").strip()
    if not user_text:
        raise RuntimeError("user消息不存在")

    svc = _svc(char)
    agent = await asyncio.to_thread(svc.get_agent)   # S3: 懒构建挪线程池, 防构建耗时冻结事件循环
    _refresh_schedule_overlay(agent, char)   # 切片12.1: overlay按当天动态换版(周末表不进prompt)
    try:
        mem = await asyncio.to_thread(svc.ctx.recall, user_text)
    except Exception as e:
        print(f"[chatapp] {char}: recall failed: {e}", flush=True)
        mem = ""
    task_text = (f"{mem}\n\n---\n[以上为记忆回响, 以下是用户本轮消息]\n{user_text}" if mem
                 else user_text)

    # M3识图: job挂的image_in行→GLM-4V转述注入本轮prompt(失败fail-open)
    img_parts = await asyncio.to_thread(_describe_job_images, char, job)
    if img_parts:
        task_text += "\n\n" + "\n\n".join(img_parts)

    # 切片8§3.3: 任务头每轮注入mode状态行(meet/chat两态现读); history旧状态行随trim自然淘汰
    dq = agent.put_task(f"{_state_line(char)}\n{FILE_HINT}\n\n{task_text}", source=SERVICE_SOURCE)
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
    try:   # S6: 洗掉已入史的泄漏叙述(backend.history即下轮prompt来源, 含本轮)
        _scrub_agent_history(char, svc)
    except Exception as e:
        print(f"[chatapp] {char}: CoT洗史失败: {e}", flush=True)
    m = _ERR_FENCE_RE.search(raw)
    if m:   # B4: agent内部错误文本(尾随format_error围栏)不入messages/不入宫殿
        raise RuntimeError(f"agent内部错误: {m.group(1)[:120]}")

    # 1027fix(主人报): 上游LLM/工具错误原文("!!!Error: HTTP 400 oMLX prefill...")被模型整段复读
    # 或工具轨迹("🛠️ Tool:"...)喷出 → 错误不入库(转system提示), 轨迹只保留其前真实文本;
    # 报错前先尽力发媒体(工具可能已成功产出图/语音, 与文本失败解耦)
    e_i = raw.find("!!!Error:")
    t_i = raw.find("🛠️ Tool:")
    if e_i >= 0 or t_i >= 0:
        try:
            await asyncio.to_thread(_emit_reply_media, char, raw, start - 2)
        except Exception as e:
            print(f"[chatapp] {char}: 错误前媒体抢救失败: {e}", flush=True)
        if e_i >= 0:
            detail = " ".join(raw[e_i + 9:e_i + 309].split())[:200]
            raise RuntimeError(f"上游模型错误: {detail}")
        raw = raw[:t_i]

    # M3: 文本行先入(去掉[FILE:]占位, 媒体单独成行); 再发媒体行; 宫殿注记只随ingest
    _s6 = _scrub_cot(_LLM_RUNNING_RE.sub("", _FILE_TAG_RE.sub("", raw)))   # S6: 无标签英文叙述/命令行清扫
    final = build_done_text(_s6) if _s6.strip() else ""
    final = (final or "").strip()
    await asyncio.to_thread(_insert_msg, char, "assistant", "text", final or "……",
                            meta={"mode": _mode_of_user_row(user_seq) or _mode_of(char)})   # 切片9: 随行打mode, 前端按屏过滤; 审计P1-2: 锚定user发送行
    mnote = ""
    try:
        mnote = await asyncio.to_thread(_emit_reply_media, char, raw, start - 2)
    except Exception as e:
        print(f"[chatapp] {char}: 媒体行产出失败: {e}", flush=True)
    if final or mnote:
        try:
            await asyncio.to_thread(
                svc.ctx.ingest, SERVICE_SOURCE, user_text,
                (final + (f"\n{mnote}" if mnote else "")) or mnote)
        except Exception as e:
            print(f"[chatapp] {char}: ingest failed: {e}", flush=True)


# --- 主动消息改道(1027): fsapp._proactive_thread同款逻辑搬入Chat, 复用_process_job生成→清洗→入库→ingest管线 ---
_PROACTIVE_TRIGGER = ("[系统内部触发·主动消息] 用户已超过3小时未发消息。请以你的身份结合此前聊天上下文，"
                      "主动给用户发一条自然、简短(100字内)的消息：延续之前话题、关心近况或轻松日常皆可。"
                      "直接输出消息文本本身，不要解释、不要提及系统或本指令、不要调用工具。")
_PROACTIVE_RECALL_Q = "延续之前话题 关心近况 日常闲聊"
# 切片8(§3.4): meet态换稿——现场第一人称, 禁"发消息"口吻; chat态维持现词(回归红线)
_PROACTIVE_TRIGGER_MEET = ("[系统内部触发·主动消息] 你们正处于意识同步(见面)状态，场景：{scene}，"
                           "用户已超过3小时没有回应(可能睡着了或离开了)。以现场第一人称推进："
                           "轻声唤她/替她掖被/安静守着/留一张字条皆可，括号内写动作与场景，100字内，"
                           "不要'发消息'口吻，不要提及系统。")


def _pstate():
    try:
        return json.loads(_meta_get(_PROACTIVE_KEY) or "{}")
    except Exception:
        return {}


# ---------------- 切片8: 见面模式L2状态机(M1, 开发方案_见面模式.md §3) ----------------
# mode不建新表不动消息流: 存meta KV(meet_state:{char}), 用时现读(重启零成本自愈);
# 每轮任务头注入运行时状态行(§3.3), 主动消息按mode分支(§3.4)。先例: _PROACTIVE_KEY。
_MEET_KEY_PREFIX = "meet_state:"   # meet_state:{char} -> {"mode":"meet","scene":..,"since":..,"note":..}
_MODE_RE = re.compile(r"^(?:chat|meet)$")        # B4式单测锁定: mode仅两值
_SCENE_MAX = 12                                   # 场景名≤12字(§3.2)
_SCENE_BAD_RE = re.compile(r"[\x00-\x1f\x7f]")   # 场景名禁控制符/换行


def _meet_key(char):
    return _MEET_KEY_PREFIX + char


def _get_meet_state(char):
    """读mode态; 无记录/坏值自愈为chat缺省(§3.1)。返回 {"mode":"chat"} 或meet记录本体。"""
    try:
        st = json.loads(_meta_get(_meet_key(char)) or "")
        if isinstance(st, dict) and st.get("mode") == "meet":
            return st
    except Exception:
        pass
    return {"mode": "chat"}


def _all_scenes():
    """审计P2-6: digest带scene, 前端openMeet恢复最近场景; 仅meet态有值"""
    out = {}
    with db() as conn:
        for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE ?",
                              (_MEET_KEY_PREFIX + "%",)):
            c = r["key"][len(_MEET_KEY_PREFIX):]
            if c in CHARS:
                try:
                    out[c] = str(json.loads(r["value"]).get("scene") or "")
                except Exception:
                    out[c] = ""
    return out


def _mode_of(char):
    return _get_meet_state(char).get("mode") or "chat"


def _all_modes():
    """一次读全部角色mode(digest单查用); 无记录/坏值=chat"""
    out = {c: "chat" for c in CHARS}
    with db() as conn:
        for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE ?",
                              (_MEET_KEY_PREFIX + "%",)):
            c = r["key"][len(_MEET_KEY_PREFIX):]
            if c in out:
                try:
                    st = json.loads(r["value"] or "")
                    if isinstance(st, dict) and st.get("mode") == "meet":
                        out[c] = "meet"
                except Exception:
                    pass
    return out


def _fmt_since(ts, now=None):
    """since时间戳→'始于昨晚23:05·已持续3小时'式自然语(§3.3状态行用)"""
    now = now or time.time()
    lt, ln = time.localtime(ts), time.localtime(now)
    hhmm = time.strftime("%H:%M", lt)
    d0 = datetime.date(ln.tm_year, ln.tm_mon, ln.tm_mday)
    d1 = datetime.date(lt.tm_year, lt.tm_mon, lt.tm_mday)
    n = (d0 - d1).days
    if n <= 0:
        when = f"今天{hhmm}"
    elif n == 1:
        when = f"{'昨晚' if lt.tm_hour >= 18 or lt.tm_hour < 6 else '昨天'}{hhmm}"
    elif n == 2:
        when = f"前天{hhmm}"
    else:
        when = time.strftime("%m-%d %H:%M", lt)
    dur = max(0.0, now - ts)
    durs = (f"已持续{int(dur // 3600)}小时" if dur >= 3600
            else (f"已持续{int(dur // 60)}分钟" if dur >= 60 else "刚开始"))
    return f"始于{when}·{durs}"


def _refresh_schedule_overlay(agent, char):
    """切片12.1: 作息overlay按当天动态换版(工作日=时刻表/周末·节假日=自由安排一行, 表不进prompt);
    agent常驻跨天也不stale; 找不到旧overlay就补注(兼容构建期未注的老agent); fail-open"""
    if not SCHEDULE:
        return
    try:
        ov = SCHEDULE.overlay_text(char)
        if not ov:
            return
        ps = agent.extra_sys_prompts
        for i, p in enumerate(ps):
            if isinstance(p, str) and p.startswith("## 家园Chat·IM场景补充"):
                if ps[i] != ov:
                    ps[i] = ov
                return
        ps.append(ov)
    except Exception as e:
        print(f"[chatapp] {char}: refresh schedule overlay failed(fail-open): {e}", flush=True)


def _state_line(char, now=None):
    """每轮现读meta拼状态行(§3.3): trim裁不掉当前态(每轮重注), history旧行自然淘汰"""
    st = _get_meet_state(char)
    if st.get("mode") == "meet":
        parts = ["意识同步中(你们在同一空间, 面对面)"]
        scene = str(st.get("scene") or "").strip()
        if scene:
            parts.append(f"场景:{scene}")
        try:
            since = float(st.get("since") or 0)
        except (TypeError, ValueError):
            since = 0.0
        if since > 0:
            parts.append(_fmt_since(since, now))
        note = str(st.get("note") or "").strip()
        if note:
            parts.append(f"备注:{note}")
        parts.append("历史旧状态行一律作废,以本行为准")   # 审计P2-9: trim淘汰旧态行后兜底
        return "[状态: " + " · ".join(parts) + "]"
    base = "[状态: 隔屏聊天 · 你们不在同一空间, 通过手机联系"
    if SCHEDULE:   # 切片12: 拼当前时刻+时段活动(每轮现拼, trim裁不掉; 睡着时提示被吵醒按人设反应)
        _extra = SCHEDULE.state_extra(char, now=now)
        if _extra:
            base += " · " + _extra
    return base + "]"


def _proactive_gates(char, now):
    """门检: 8-23点 + 有真实用户史 + 双3h静默门(用户/上次主动取较新) + 不忙; 返回(ok, 原因)"""
    lt = time.localtime(now)
    h = lt.tm_hour
    if not (PROACTIVE_HOUR_START <= h < PROACTIVE_HOUR_END):
        return False, f"hour={h}非活跃时段"
    if SCHEDULE:   # 切片12: per-char作息窗(沈星回11:00后/秦彻17:10后; 仅缩窗不开窗, 上限仍卡全局23点)
        w = SCHEDULE.proactive_window(char)
        if w and not (w[0] <= lt.tm_hour * 60 + lt.tm_min < w[1]):
            return False, f"hour={h}非{char}作息活跃段"
    with db() as conn:
        r = conn.execute("SELECT MAX(ts) AS mx, COUNT(*) AS n FROM messages "
                         "WHERE char_id=? AND role='user'", (char,)).fetchone()
    if not r or not r["n"]:
        return False, "无用户史"
    last_user = float(r["mx"] or 0)
    last_pro = float(_pstate().get(char, 0) or 0)
    idle_from = max(last_user, last_pro)
    if now - idle_from < PROACTIVE_IDLE_SEC:
        return False, f"静默不足(idle={int(now - idle_from)}s)"
    with db() as conn:
        busy = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE char_id=? AND "
                            "status IN ('pending','running')", (char,)).fetchone()["n"]
    if busy:
        return False, f"jobs忙({busy})"
    svc = SERVICES.get(char)
    if svc and svc.agent is not None and getattr(svc.agent, "is_running", False):
        return False, "agent忙"
    return True, f"idle={int((now - idle_from) / 60)}min"


async def _proactive_once(char):
    """生成并发送一条主动消息(文本only, 清洗/围栏/ingest与_process_job完全同款)"""
    svc = _svc(char)
    agent = await asyncio.to_thread(svc.get_agent)
    _refresh_schedule_overlay(agent, char)   # 切片12.1: 主动消息也走当天版overlay
    # 切片8§3.4: 按mode分支trigger/recall; gates不动(双3h+时段照旧)
    st = _get_meet_state(char)
    meet = st.get("mode") == "meet"
    scene = str(st.get("scene") or "").strip()
    trigger = (_PROACTIVE_TRIGGER_MEET.format(scene=scene or "未定") if meet
               else _PROACTIVE_TRIGGER)
    recall_q = (f"意识同步 {scene} 最近相处 见面" if meet else _PROACTIVE_RECALL_Q)
    try:
        mem = await asyncio.to_thread(svc.ctx.recall, recall_q)
    except Exception as e:
        print(f"[chatapp] {char}: proactive recall failed: {e}", flush=True)
        mem = ""
    task_text = (f"{mem}\n\n---\n[以上为记忆回响, 以下是本轮任务]\n{trigger}" if mem
                 else trigger)
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
            raise TimeoutError(f"proactive agent超时({AGENT_TIMEOUT_SEC}s)已中止")
    raw = raw or ""
    try:
        _scrub_agent_history(char, svc)
    except Exception as e:
        print(f"[chatapp] {char}: proactive CoT洗史失败: {e}", flush=True)
    if _ERR_FENCE_RE.search(raw):
        raise RuntimeError("proactive agent内部错误围栏")
    e_i = raw.find("!!!Error:")
    if e_i >= 0:
        detail = " ".join(raw[e_i + 9:e_i + 189].split())[:120]
        raise RuntimeError(f"proactive 上游错误: {detail}")
    t_i = raw.find("🛠️ Tool:")
    if t_i >= 0:
        raw = raw[:t_i]
    _s6 = _scrub_cot(_LLM_RUNNING_RE.sub("", _FILE_TAG_RE.sub("", raw)))
    final = build_done_text(_s6).strip() if _s6.strip() else ""
    if not final:
        raise RuntimeError("proactive 产出为空")
    await asyncio.to_thread(_insert_msg, char, "assistant", "text", final, None,
                            {"proactive": True, "mode": _mode_of(char)})   # 切片8: mode随行(切片9横幅用); 审计P1-2: 完成时现读, 弃生成前旧值
    try:   # 对齐fsapp B1: 主动消息同样入宫殿账本, 触发侧记为系统触发
        await asyncio.to_thread(svc.ctx.ingest, SERVICE_SOURCE,
                                "[主动消息·系统触发]", final)
    except Exception as e:
        print(f"[chatapp] {char}: proactive ingest failed: {e}", flush=True)
    return final


async def _proactive_loop():
    if not PROACTIVE or not PROACTIVE_CHARS:
        print(f"[chatapp] proactive off (GA_PROACTIVE={'1' if PROACTIVE else '0'}, "
              f"chars={PROACTIVE_CHARS})", flush=True)
        return
    print(f"[chatapp] proactive on: chars={PROACTIVE_CHARS} "
          f"idle={PROACTIVE_IDLE_SEC}s hours={PROACTIVE_HOUR_START}-{PROACTIVE_HOUR_END}",
          flush=True)
    while True:
        try:
            now = time.time()
            for char in PROACTIVE_CHARS:
                if char not in CHARS:
                    continue
                ok, why = await asyncio.to_thread(_proactive_gates, char, now)
                if not ok:
                    continue
                print(f"[chatapp] proactive trigger {char} ({why})", flush=True)
                try:
                    text = await _proactive_once(char)
                    print(f"[chatapp] proactive sent {char}: {text[:60]}", flush=True)
                except Exception as e:
                    print(f"[chatapp] proactive {char} failed: {e}", flush=True)
                st = _pstate()
                st[char] = time.time()   # 成败都记账, 防失败风暴(3h内不再试)
                await asyncio.to_thread(_meta_set, _PROACTIVE_KEY,
                                        json.dumps(st, ensure_ascii=False))
        except Exception as e:
            print(f"[chatapp] proactive loop error: {e}", flush=True)
        await asyncio.sleep(60.0)


async def _char_worker(char):
    while True:
        try:
            job = await asyncio.to_thread(_take_job, char)
            if job is None:
                try:   # B6: 等新job事件5s, 免每秒空轮询
                    await asyncio.wait_for(_wake(char).wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                _wake(char).clear()
                continue
            t0 = time.time()
            attempt = 0
            while True:   # M4: 可重试错误自动重试(5s/15s), 共3次尝试
                try:
                    await _process_job(job)
                    await asyncio.to_thread(_finish_job, job["id"], "done")
                    await asyncio.to_thread(_clear_error_hint, job["user_seq"])
                    print(f"[chatapp] {char}: job#{job['id']} done in {time.time()-t0:.1f}s",
                          flush=True)
                    break
                except Exception as e:
                    attempt += 1
                    if attempt <= 2 and _retryable(e):
                        print(f"[chatapp] {char}: job#{job['id']} 第{attempt}次失败"
                              f"({str(e)[:80]}), 自动重试", flush=True)
                        await asyncio.sleep(5.0 if attempt == 1 else 15.0)
                        continue
                    try:   # 审计P2-2: 终败落库自身异常不能逃逸(否则被外层兜底吃掉FAIL日志)
                        await asyncio.to_thread(_finish_job, job["id"], "error", str(e)[:500])
                        await asyncio.to_thread(_mark_user_error, char, job["user_seq"], e)
                    except Exception as e2:
                        print(f"[chatapp] {char}: job#{job['id']} 终败落库异常 {e2}", flush=True)
                    print(f"[chatapp] {char}: job#{job['id']} FAIL {e}", flush=True)
                    break
            svc = SERVICES.get(char)
            if svc and svc.agent and _agent_ctx_chars(svc) > 150000:   # B10: 滚动重建
                print(f"[chatapp] {char}: 上下文超150k字符, 换新CharService懒重建(记忆在宫殿)",
                      flush=True)
                SERVICES[char] = CharService(char)
                _RETIRED.append(svc)
                print(f"[chatapp] {char}: B10累计退役{len(_RETIRED)}个agent, "
                      f"活线程{threading.active_count()}", flush=True)
        except Exception as e:   # worker外层异常兜底, 不让循环死掉
            print(f"[chatapp] {char}: worker loop error: {e}", flush=True)
            await asyncio.sleep(3.0)


@app.on_event("startup")
async def _startup():
    init_db()        # B7: uvicorn直启(chatapp:app)也能初始化+恢复(幂等)
    _bg_migrate_legacy()   # 切片5: 旧全局背景迁移至各角色桶
    recover_jobs()
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=40))       # B8: 默认线程池40, 防并发饿死
    for c in CHARS:
        asyncio.create_task(_char_worker(c))
    asyncio.create_task(_bg_gc_loop())
    asyncio.create_task(_proactive_loop())   # 1027: 主动消息改道Chat


# ---------------- API ----------------

def _err(status, message):
    return JSONResponse({"error": message}, status_code=status)


def _digest():
    """五角色摘要: 最后一条消息预览+时间+typing"""
    out = []
    with db() as conn:
        for c in CHARS:
            r = conn.execute(   # 切片9: 聊天列表digest只看chat态行(meet行/mode_evt行/历史同步行全滤)
                "SELECT * FROM messages WHERE char_id=? AND "
                "CASE WHEN meta IS NULL OR json_valid(meta) THEN COALESCE(json_extract(meta,'$.mode'),'chat') ELSE 'chat' END='chat' AND "
                "CASE WHEN meta IS NULL OR json_valid(meta) THEN json_extract(meta,'$.mode_evt') END IS NULL AND "
                "content NOT LIKE '—— 意识同步%' "
                "ORDER BY seq DESC LIMIT 1", (c,)).fetchone()   # 审计P2-3: json_valid护栏(坏meta不500); 删不可达条件'同步结束%' 
            if r:
                preview = (r["content"] or "").strip()
                if r["kind"] == "image_in" or r["kind"] == "image_out":
                    preview = "[图片]"
                elif r["kind"] == "voice":
                    preview = "[语音]"
                preview = preview.replace("\n", " ")[:40]
                out.append({"char_id": c, "name": CHAR_NAMES[c],
                            "avatar": _avatar_path(c), "last_seq": r["seq"],
                            "last_kind": r["kind"], "preview": preview, "last_ts": r["ts"],
                            "typing": False})
            else:
                out.append({"char_id": c, "name": CHAR_NAMES[c],
                            "avatar": _avatar_path(c), "last_seq": 0,
                            "last_kind": None, "preview": "", "last_ts": None,
                            "typing": False})
    with db() as conn:
        pend = {r["char_id"]: r["n"] for r in conn.execute(
            "SELECT char_id, COUNT(*) AS n FROM jobs WHERE status IN ('pending','running') "
            "GROUP BY char_id").fetchall()}
    modes = _all_modes()   # 切片8§3.2: 列表屏带mode(前端见面入口/横幅用)
    scenes = _all_scenes()   # 审计P2-6: openMeet恢复最近场景
    for o in out:
        o["typing"] = pend.get(o["char_id"], 0) > 0
        o["mode"] = modes.get(o["char_id"], "chat")
        o["scene"] = scenes.get(o["char_id"], "")
    return out


@app.get("/api/chars")
def chars_ep():
    return {"chars": _digest()}


@app.get("/api/mode")
def mode_get(char_id: str = ""):
    """切片8§3.2: mode态查询; ?char_id=单角色, 缺省=五角色全量"""
    char_id = (char_id or "").strip()
    if char_id and char_id not in CHARS:
        return _err(400, f"char_id须为: {','.join(CHARS)}")
    if char_id:
        st = _get_meet_state(char_id)
        return {"char_id": char_id, "mode": st.get("mode") or "chat",
                "scene": st.get("scene") or "", "since": st.get("since") or 0}
    return {"chars": _all_modes()}


@app.post("/api/mode")
async def mode_post(request: Request):
    """切片8§3.2: chat<->meet切换+插system分隔行; 不调LLM零成本; /new不动mode(§3.5)"""
    try:
        body = await request.json()
    except Exception:
        return _err(400, "请求体不是合法JSON")
    if not isinstance(body, dict):
        return _err(400, "请求体须为JSON对象")
    char = str(body.get("char_id") or body.get("char") or "").strip()
    if char not in CHARS:
        return _err(400, f"char_id须为: {','.join(CHARS)}")
    mode = str(body.get("mode") or "").strip()
    if not _MODE_RE.match(mode):
        return _err(400, "mode须为chat或meet")
    scene = str(body.get("scene") or "").strip()
    note = str(body.get("note") or "").strip()
    if scene and (len(scene) > _SCENE_MAX or _SCENE_BAD_RE.search(scene)):
        return _err(400, f"scene须为{_SCENE_MAX}字内、无换行的场景名")
    if len(note) > 40 or (note and _SCENE_BAD_RE.search(note)):
        return _err(400, "note须为40字内、无换行/控制符")
    quiet = bool(body.get("quiet"))   # 1028-LB: quiet=只切态不落横幅(纯浏览进出不刷屏), 横幅改由/api/msg首条消息惰性补
    prev = _get_meet_state(char)
    ts = time.time()
    if mode == "meet":
        # 再入meet(如只换场景)保留首入since(§3.1), 初入以当下计
        since = prev.get("since") if prev.get("mode") == "meet" else ts
        st = {"mode": "meet", "scene": scene, "since": since or ts, "note": note}
        await asyncio.to_thread(_meta_set, _meet_key(char), json.dumps(st, ensure_ascii=False))   # 审计P2-2: 阻塞写挪出事件循环
        line = f"—— 意识同步开始 · 场景:{scene or '未定'} ——"
    else:
        def _del_meet():   # 审计P2-2: 阻塞写挪出事件循环
            with db() as conn:   # 退出即删记录: 无记录=chat缺省(§3.1), 不留脏KV
                conn.execute("DELETE FROM meta WHERE key=?", (_meet_key(char),))
        await asyncio.to_thread(_del_meet)
        st = {"mode": "chat", "scene": "", "since": 0.0, "note": ""}
        line = "—— 意识同步结束，回到隔屏聊天 ——"
    seq = 0
    if not quiet:   # 1028-LB: quiet模式横幅延后到首条消息时补(见post_msg惰性横幅)
        seq = await asyncio.to_thread(_insert_msg, char, "system", "system", line,
                                      meta={"mode_evt": 1, "mode": st["mode"]})   # 切片9: 随行打标, 聊天屏滤/meet屏横幅
    return {"ok": True, "char_id": char, "seq": seq, "mode": st["mode"], "scene": st["scene"]}


@app.get("/api/health")
def health():
    with db() as conn:
        n = conn.execute("SELECT status, COUNT(*) AS c FROM jobs GROUP BY status").fetchall()
    return {"status": "ok", "port": CHAT_PORT, "chars": CHARS,
            "jobs": {r["status"]: r["c"] for r in n}}


@app.post("/api/upload")
async def upload_image(request: Request):
    """M3识图上传: multipart(file+char_id)→media/in/→image_in行; 返回seq供/api/msg挂job"""
    try:
        cl = int(request.headers.get("content-length") or 0)   # P1-5: 解析前预检
        if cl > 16 * 1024 * 1024:
            return _err(413, "图片过大(>15MB)")
        form = await request.form()
        f = form.get("file")
        if f is None or isinstance(f, str):
            return _err(400, "缺少file字段")
        raw = await f.read()
    except Exception:
        return _err(400, "请求体须为multipart/form-data")
    char = str(form.get("char_id") or "").strip()
    if char not in CHARS:
        return _err(400, f"char_id须为: {','.join(CHARS)}")
    if len(raw) > 15 * 1024 * 1024:
        return _err(400, "图片过大(>15MB)")
    if not raw:   # P1-1: 空文件/魔数校验
        return _err(400, "空文件")
    if not (raw.startswith((b"\xff\xd8\xff", b"\x89PNG", b"RIFF", b"GIF8"))
            or raw[4:8] == b"ftyp"):
        return _err(400, "不是有效的图片文件")
    ext = os.path.splitext(str(getattr(f, "filename", "") or ""))[1].lower().lstrip(".")
    if ext not in ("jpg", "jpeg", "png", "webp", "gif", "heic"):
        ext = "jpg"
    def _store_img():
        nonlocal ext, raw
        os.makedirs(os.path.join(MEDIA_DIR, "in"), exist_ok=True)
        if ext == "heic":   # P2-9: GLM-4V不认heic, sips转jpg
            tmp = os.path.join(MEDIA_DIR, "in", f".tmp_{uuid.uuid4().hex[:8]}.heic")
            jpg = tmp + ".jpg"
            try:
                with open(tmp, "wb") as w:
                    w.write(raw)
                rr = subprocess.run(["sips", "-s", "format", "jpeg", tmp, "--out", jpg],
                                    capture_output=True, text=True, timeout=30)
                if rr.returncode == 0 and os.path.exists(jpg):
                    with open(jpg, "rb") as fr:
                        raw = fr.read()
                    ext = "jpg"
            finally:
                for x in (tmp, jpg):
                    try:
                        os.unlink(x)
                    except Exception:
                        pass
        name = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}.{ext}"
        with open(os.path.join(MEDIA_DIR, "in", name), "wb") as w:
            w.write(raw)
        url = f"/media/in/{name}"
        return _insert_msg(char, "user", "image_in", "[图片]", media_path=url), url
    seq, url = await asyncio.to_thread(_store_img)
    return {"seq": seq, "media_path": url}


@app.post("/api/att_del")
async def att_del(request: Request):
    """P0-1: 撤回未发送的上传图(前端取消附件/关闭会话时调); 仅删本角色user image_in行+文件"""
    try:
        body = await request.json()
    except Exception:
        return _err(400, "请求体不是合法JSON")
    body = body if isinstance(body, dict) else {}
    char = str(body.get("char_id") or "").strip()
    seq = body.get("seq")
    if char not in CHARS or not str(seq).strip().isdigit():
        return _err(400, "参数不合法")
    def _del():
        with db() as conn:
            r = conn.execute(
                "SELECT media_path FROM messages WHERE seq=? AND char_id=? "
                "AND role='user' AND kind='image_in'", (int(seq), char)).fetchone()
            if not r:
                return False
            conn.execute("DELETE FROM messages WHERE seq=?", (int(seq),))
            fp = _media_fp(r["media_path"])
            if fp and os.path.isfile(fp):
                try:
                    os.unlink(fp)
                except Exception:
                    pass
            return True
    ok = await asyncio.to_thread(_del)
    return {"deleted": ok}


@app.post("/api/msg_del")
async def msg_del(request: Request):
    """切片7: 聊天长按删除单条记录(同时抹除DB与agent内存历史)"""
    try:
        body = await request.json()
    except Exception:
        return _err(400, "请求体不是合法JSON")
    if not isinstance(body, dict):
        return _err(400, "请求体须为JSON对象")
    char = str(body.get("char_id") or "").strip()
    if char not in CHARS:
        return _err(400, f"char_id须为: {','.join(CHARS)}")
    try:
        seq = int(body.get("seq"))
    except Exception:
        return _err(400, "seq必须为整数")

    def _del_msg():
        with db() as conn:
            row = conn.execute(
                "SELECT seq, role, kind, content, media_path FROM messages "
                "WHERE seq=? AND char_id=?", (seq, char)).fetchone()
            if not row:
                return False, None
            conn.execute("DELETE FROM messages WHERE seq=? AND char_id=?", (seq, char))
            # 若有关联job还在排队/运行，也做清理
            conn.execute("DELETE FROM jobs WHERE user_seq=? AND char_id=?", (seq, char))
            return True, dict(row)

    ok, deleted_row = await asyncio.to_thread(_del_msg)
    if not ok:
        return _err(404, "消息不存在或已删除")

    # 联动清理本地媒体文件
    if deleted_row and deleted_row.get("media_path"):
        fp = _media_fp(deleted_row["media_path"])
        if fp and os.path.isfile(fp):
            try:
                os.unlink(fp)
            except Exception:
                pass

    # 联动清除 agent 内存中对应的对话
    if deleted_row and deleted_row.get("content"):
        await asyncio.to_thread(
            _remove_agent_history_msg, char, deleted_row.get("role"), deleted_row.get("content")
        )

    return {"ok": True, "seq": seq}


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
    image_seqs = body.get("image_seqs")
    if image_seqs is None:
        image_seqs = []
    if not isinstance(image_seqs, list):
        return _err(400, "image_seqs须为数组")
    image_seqs = list(dict.fromkeys(   # P2-4: 去重保序
        int(s) for s in image_seqs if str(s).strip().isdigit()))[:3]
    if not text and not image_seqs:
        return _err(400, "text不能为空")
    if len(text) > 4000:
        return _err(400, "text过长(>4000)")

    # 切片7: 拦截 /new 指令重置当前角色的短期会话记忆
    if text.lower() == "/new" and not image_seqs:
        ts = time.time()
        # 清除进程内 agent 记忆
        await asyncio.to_thread(_reset_agent_memory, char)
        # 写入一条灰色居中系统提示行（kind='system'）
        def _store_sys():
            with db() as conn:
                cur = conn.execute(
                    "INSERT INTO messages(char_id, role, kind, content, ts) "
                    "VALUES (?, 'system', 'system', ?, ?)",
                    (char, "—— 上下文已重置，开启新会话 ——"
                     + ("(仍在意识同步中)" if _mode_of(char) == "meet" else ""), ts))
                return cur.lastrowid
        seq = await asyncio.to_thread(_store_sys)
        return JSONResponse({"ok": True, "is_new": True, "seq": seq, "ts": ts}, status_code=200)

    if image_seqs:   # M3: 校验图片归属(本角色/用户/image_in行), 防跨角色串图
        with db() as conn:
            q = ",".join("?" * len(image_seqs))
            rows = conn.execute(
                "SELECT seq FROM messages WHERE seq IN (%s) AND char_id=? "
                "AND role='user' AND kind='image_in'" % q,
                image_seqs + [char]).fetchall()
        if len(rows) != len(set(image_seqs)):
            return _err(400, "image_seqs含无效或非本角色的图片")
    # 1028-LB: 惰性横幅——meet态首条消息补"开始", meet段后的首条chat消息补"结束"; 纯浏览进出零落库
    cur_mode = _mode_of(char)
    def _lazy_banner():
        with db() as conn:
            r = conn.execute(
                "SELECT meta FROM messages WHERE char_id=? AND "
                "CASE WHEN meta IS NULL OR json_valid(meta) THEN json_extract(meta,'$.mode_evt') END IS NOT NULL "
                "ORDER BY seq DESC LIMIT 1", (char,)).fetchone()
            last_evt = None
            if r and r["meta"]:
                try:
                    last_evt = (json.loads(r["meta"]) or {}).get("mode")
                except Exception:
                    last_evt = None
            if cur_mode == "meet" and last_evt != "meet":
                scene = ((_get_meet_state(char) or {}).get("scene") or "").strip()
                conn.execute(
                    "INSERT INTO messages(char_id, role, kind, content, meta, ts) "
                    "VALUES (?, 'system', 'system', ?, ?, ?)",
                    (char, f"—— 意识同步开始 · 场景:{scene or '未定'} ——",
                     json.dumps({"mode_evt": 1, "mode": "meet"}, ensure_ascii=False), time.time()))
            elif cur_mode == "chat" and last_evt == "meet":
                conn.execute(
                    "INSERT INTO messages(char_id, role, kind, content, meta, ts) "
                    "VALUES (?, 'system', 'system', ?, ?, ?)",
                    (char, "—— 意识同步结束，回到隔屏聊天 ——",
                     json.dumps({"mode_evt": 1, "mode": "chat"}, ensure_ascii=False), time.time()))
    await asyncio.to_thread(_lazy_banner)
    def _store():   # B2: 消息+job同事务; B9: 服务端权威ts; M3: img_seqs逗号串挂job
        ts = time.time()
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO messages(char_id, role, kind, content, meta, ts) "
                "VALUES (?, 'user', 'text', ?, ?, ?)", (char, text or "[图片]",
                json.dumps({"mode": _mode_of(char)}, ensure_ascii=False), ts))   # 切片9: meta.mode随行
            seq = cur.lastrowid
            cur = conn.execute(
                "INSERT INTO jobs(char_id, status, user_seq, img_seqs, created) "
                "VALUES (?, 'pending', ?, ?, ?)",
                (char, seq, ",".join(map(str, image_seqs)), ts))
            return seq, cur.lastrowid, ts
    seq, job_id, ts = await asyncio.to_thread(_store)
    _wake(char).set()   # B6: 立即唤醒该角色worker
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
        errs = []
        for r in conn.execute(
                "SELECT seq, meta FROM messages WHERE char_id=? AND role='user' "
                "AND meta LIKE '%\"error\"%' ORDER BY seq ASC LIMIT 50",
                (char_id,)):
            try:
                m = json.loads(r["meta"]) if r["meta"] else {}
            except Exception:
                m = {}
            if isinstance(m, dict) and m.get("error"):
                errs.append({"seq": r["seq"],
                             "error": m["error"] if isinstance(m["error"], str) else ""})
    return {"msgs": [_msg_dict(r) for r in rows], "typing": _is_typing(char_id),
            "err_seqs": errs}   # 审计P0-1: 实时会话失败红!pill同步源


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


# ---------------- M4: 唤醒/背景配置/GC/重试 ----------------

WAKE_EVENTS = {}   # B6: char → asyncio.Event, post_msg后唤醒worker


def _wake(char):
    ev = WAKE_EVENTS.get(char)
    if ev is None:
        ev = WAKE_EVENTS[char] = asyncio.Event()
    return ev


def _meta_get(key, default=None):
    with db() as conn:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def _meta_set(key, value):
    with db() as conn:
        conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


_BG_LEGACY_KEY = "bg_config"   # 切片5前全局背景; 启动时迁移至各角色桶后删除


def _bg_key(char):
    return f"bg:{char}"


def _bg_current(char):
    try:
        v = json.loads(_meta_get(_bg_key(char)) or "{}")
        if isinstance(v, dict) and v.get("mode") in ("none", "color", "image"):
            if v["mode"] == "color":   # 切片5R: 纯色退役, 遗留桶降级为none
                return {"mode": "none", "value": ""}
            return {"mode": v["mode"], "value": str(v.get("value") or "")}
    except Exception:
        pass
    return {"mode": "none", "value": ""}


def _bg_all():
    """切片5: 五角色背景 {char_id: {mode, value}}"""
    return {c: _bg_current(c) for c in CHARS}


def _bg_referenced():
    """切片5: 当前被任一角色引用的背景图绝对路径集合(存图清理/GC共用)"""
    keep = set()
    for bg in _bg_all().values():
        if bg["mode"] == "image" and bg["value"]:
            fp = _media_fp(bg["value"])
            if fp:
                keep.add(os.path.abspath(fp))
    return keep


def _bg_migrate_legacy():
    """切片5一次性: 旧全局bg_config → 复制到五角色桶, 防升级丢背景"""
    raw = _meta_get(_BG_LEGACY_KEY)
    if not raw:
        return
    try:
        v = json.loads(raw)
        assert isinstance(v, dict) and v["mode"] in ("none", "color", "image")
    except Exception:
        v = {"mode": "none", "value": ""}
    for c in CHARS:
        m = "none" if v["mode"] == "color" else v["mode"]   # 切片5R: 纯色退役
        _meta_set(_bg_key(c), json.dumps(
            {"mode": m, "value": str(v.get("value") or "")},
            ensure_ascii=False))
    with db() as conn:
        conn.execute("DELETE FROM meta WHERE key=?", (_BG_LEGACY_KEY,))
    print(f"[chatapp] bg迁移: legacy->{len(CHARS)}桶 mode={v['mode']}", flush=True)


def _avatar_path(cid):
    """切片5: 头像存在才返回/media路径, 前端无图时回退色块"""
    fp = os.path.join(MEDIA_DIR, "avatars", f"{cid}.jpg")
    return f"/media/avatars/{cid}.jpg" if os.path.isfile(fp) else ""


def _media_gc():
    """P2-8: 删除无引用媒体(in/out/voice/bg, age>1h); 各角色当前背景图豁免; 返回删除数"""
    keep = set()
    with db() as conn:
        for r in conn.execute(
                "SELECT media_path FROM messages WHERE media_path IS NOT NULL").fetchall():
            fp = _media_fp(r["media_path"])
            if fp:
                keep.add(os.path.abspath(fp))
    keep |= _bg_referenced()   # 切片5: 所有角色当前背景图均豁免
    n, now = 0, time.time()
    for sub in ("in", "out", "voice", "bg"):
        d = os.path.join(MEDIA_DIR, sub)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            fp = os.path.join(d, f)
            try:
                if os.path.abspath(fp) in keep:
                    continue
                if now - os.path.getmtime(fp) > 3600:
                    os.unlink(fp)
                    n += 1
            except OSError:
                continue
    return n


def _retryable(e):
    """M4: 瞬时/上游错误自动重试判定(429/上游模型错误/超时等)"""
    s = str(e)
    return ("上游模型错误" in s or "agent内部错误" in s or "429" in s
            or "timeout" in s.lower() or "timed out" in s.lower()
            or isinstance(e, TimeoutError))


def _friendly_err(e):
    s = str(e)
    if isinstance(e, TimeoutError) or "超时" in s:
        return "网络超时了"
    if "429" in s:
        return "对方占线中"
    return "信号不好"


def _update_msg_meta(seq, fn):
    try:
        with db() as conn:
            old = conn.execute("SELECT meta FROM messages WHERE seq=?",
                               (seq,)).fetchone()
            if not old:
                return
            try:
                meta = json.loads(old["meta"]) if old["meta"] else {}
            except Exception:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            ret = fn(meta)
            conn.execute("UPDATE messages SET meta=? WHERE seq=?",
                         (json.dumps(meta, ensure_ascii=False), seq))
            return ret   # 审计P2-3: 返回fn结果供_clear_error_hint拿err_sys_seq
    except Exception:
        return None


def _mark_user_error(char, user_seq, e):
    """M4: 终败→user行meta.error打标(前端红!角标+可重试)
    审计P2-3: 记err_sys_seq, 成功后由_clear_error_hint撤掉配套system提示行"""
    if not user_seq:
        return
    sys_seq = _insert_msg(char, "system", "system", f"{_friendly_err(e)}, 点红色感叹号可重试", None, {"mode": _mode_of_user_row(user_seq) or _mode_of(char)})   # 审计P2-6: 与user行同屏
    _update_msg_meta(user_seq, lambda m: m.update(error=_friendly_err(e), err_sys_seq=sys_seq))


def _clear_error_hint(user_seq):
    """成功后清error标; 审计P2-3: 连带撤掉'点红!重试'system提示行"""
    if not user_seq:
        return

    def _fn(m):
        ss = m.pop("err_sys_seq", None)
        m.pop("error", None)
        return ss
    sys_seq = _update_msg_meta(user_seq, _fn)
    if isinstance(sys_seq, int):
        try:
            with db() as conn:
                conn.execute("DELETE FROM messages WHERE seq=? AND role='system'",
                             (sys_seq,))
        except Exception:
            pass


def _agent_ctx_chars(svc):
    """B10: 估算agent进程内上下文字符量(取不到返回0)"""
    try:
        hist = list(svc.agent.llmclient.backend.history) if svc.agent else []   # 快照: 防遍历中agent线程append
        return sum(len(str(getattr(m, "content", "") or "")) for m in hist)
    except Exception:
        return 0


async def _bg_gc_loop():
    """P2-8: 启动先跑一轮, 之后每24h一轮"""
    while True:
        try:
            n = await asyncio.to_thread(_media_gc)
            if n:
                print(f"[chatapp] 媒体GC回收{n}个文件", flush=True)
        except Exception as e:
            print(f"[chatapp] 媒体GC失败: {e}", flush=True)
        await asyncio.sleep(24 * 3600)


@app.get("/api/config")
def config_get():
    """切片5: 前端启动拉取各角色背景+用户头像"""
    return {"bg_all": _bg_all(), "avatar_user": _avatar_path("user")}


@app.post("/api/config/bg")
async def bg_set(request: Request):
    """M4: 会话背景 none|image; image=multipart(file), 存media/bg/只留最新一张"""
    try:
        cl = int(request.headers.get("content-length") or 0)
        if cl > 8 * 1024 * 1024:
            return _err(413, "背景图过大(>8MB)")
        form = await request.form()
    except Exception:
        return _err(400, "请求体须为multipart/form-data")
    char = str(form.get("char_id") or "").strip()
    if char not in CHARS:   # 切片5: 背景按角色分桶
        return _err(400, "char_id须为: " + "/".join(CHARS))
    mode = str(form.get("mode") or "").strip()
    if mode not in ("none", "image"):   # 切片5R: 纯色已退役
        return _err(400, "mode须为 none/image")
    value = ""
    if mode == "image":
        f = form.get("file")
        if f is None or isinstance(f, str):
            return _err(400, "image模式须携带file字段")
        raw = await f.read()
        is_jpg = raw.startswith(b"\xff\xd8\xff")
        is_png = raw.startswith(b"\x89PNG")
        is_webp = raw.startswith(b"RIFF") and len(raw) >= 12 and raw[8:12] == b"WEBP"
        if not raw or not (is_jpg or is_png or is_webp):
            return _err(400, "背景图仅支持jpg/png/webp")
        if len(raw) > 8 * 1024 * 1024:   # 审计P1-1: 读后限长, 恶意块头谎报length时兜底
            return _err(413, "背景图过大(>8MB)")

        def _save_bg():
            d = os.path.join(MEDIA_DIR, "bg")
            os.makedirs(d, exist_ok=True)
            ext = "webp" if is_webp else ("png" if is_png else "jpg")
            name = f"bg_{int(time.time() * 1000)}.{ext}"
            with open(os.path.join(d, name), "wb") as w:
                w.write(raw)
            return name
        name = await asyncio.to_thread(_save_bg)
        value = f"/media/bg/{name}"
    await asyncio.to_thread(_meta_set, _bg_key(char),
                            json.dumps({"mode": mode, "value": value},
                                       ensure_ascii=False))
    keep = _bg_referenced()   # 切片5R: 落库后算引用, 无人引用的旧图即回收
    def _gc_bg():
        d = os.path.join(MEDIA_DIR, "bg")
        if not os.path.isdir(d):
            return
        for f2 in os.listdir(d):
            fp = os.path.abspath(os.path.join(d, f2))
            if fp not in keep:
                try:
                    os.unlink(fp)
                except OSError:
                    pass
    await asyncio.to_thread(_gc_bg)
    return {"ok": True, "bg": {"mode": mode, "value": value}}


@app.post("/api/retry")
async def retry_msg(request: Request):
    """M4: 重试失败消息: 复用原user行+原job的img_seqs建新job; 成功后worker清error标"""
    try:
        body = await request.json()
    except Exception:
        return _err(400, "请求体不是合法JSON")
    if not isinstance(body, dict):
        return _err(400, "请求体须为JSON对象")
    char = str(body.get("char_id") or "").strip()
    seq = body.get("seq")
    if char not in CHARS or not str(seq).strip().isdigit():
        return _err(400, "参数不合法")

    def _store():
        with db() as conn:
            r = conn.execute(
                "SELECT seq FROM messages WHERE seq=? AND char_id=? AND role='user'",
                (int(seq), char)).fetchone()
            if not r:
                return None
            # 审计P0-2: 在途守卫——同user_seq已有pending/running job直接拒(防重复消费)
            j_now = conn.execute(
                "SELECT id FROM jobs WHERE char_id=? AND user_seq=? "
                "AND status IN ('pending','running') LIMIT 1",
                (char, int(seq))).fetchone()
            if j_now:
                return -1
            # 审计P0-2: 只允许重试终败消息(meta.error存在), 防隐性重发
            mrow = conn.execute("SELECT meta FROM messages WHERE seq=?",
                                (int(seq),)).fetchone()
            try:
                mmeta = json.loads(mrow["meta"]) if mrow and mrow["meta"] else {}
            except Exception:
                mmeta = {}
            if not (isinstance(mmeta, dict) and mmeta.get("error")):
                return -2
            j = conn.execute(
                "SELECT img_seqs FROM jobs WHERE char_id=? AND user_seq=? "
                "ORDER BY id DESC LIMIT 1", (char, int(seq))).fetchone()
            img_seqs = (j["img_seqs"] if j and j["img_seqs"] else "") or ""
            keep = [s for s in img_seqs.split(",") if s.strip().isdigit()]
            if keep:
                q = ",".join("?" * len(keep))
                rows = conn.execute(
                    "SELECT seq FROM messages WHERE seq IN (%s) AND char_id=? "
                    "AND role='user' AND kind='image_in'" % q,
                    [int(s) for s in keep] + [char]).fetchall()
                keep = [str(x["seq"]) for x in rows]
            cur = conn.execute(
                "INSERT INTO jobs(char_id, status, user_seq, img_seqs, created) "
                "VALUES (?, 'pending', ?, ?, ?)",
                (char, int(seq), ",".join(keep), time.time()))
            return cur.lastrowid
    job_id = await asyncio.to_thread(_store)
    if job_id is None:
        return _err(404, "消息不存在或非用户消息")
    if job_id == -1:   # P0-2: 在途守卫
        return _err(409, "该消息已有任务在途, 请稍候")
    if job_id == -2:   # P0-2: 非终败消息不可重试
        return _err(400, "该消息无失败标记, 无需重试")
    _wake(char).set()
    return JSONResponse({"ok": True, "job_id": job_id}, status_code=202)


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
    # no-cache: 允许缓存但强制revalidate, 防iOS PWA启发式缓存吃旧CSS(PWA灰缝1027案)
    return FileResponse(os.path.join(WEB_DIR, "index.html"),
                        headers={"Cache-Control": "no-cache"})


@app.get("/manifest.json")   # M4 PWA
def manifest_ep():
    return FileResponse(os.path.join(WEB_DIR, "manifest.json"),
                        media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})


@app.get("/apple-touch-icon.png")
def apple_icon_ep():
    return FileResponse(os.path.join(WEB_DIR, "icons", "192.png"),
                        media_type="image/png")


@app.get("/icons/{name}")           # M4 PWA图标
def icons_ep(name: str):
    fp = os.path.join(WEB_DIR, "icons", os.path.basename(name))
    if not os.path.isfile(fp):
        return _err(404, "图标不存在")
    return FileResponse(fp, media_type="image/png")


os.makedirs(MEDIA_DIR, exist_ok=True)   # B3: 无条件挂载, 非main入口跑(如uvicorn直启)不丢媒体路由
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


def main():
    lock = ensure_single_instance(LOCK_PORT, "chatapp")   # 单例锁(独立GA_LOCK_PORT, 勿用878x)
    init_db()
    recover_jobs()
    _meets = [c for c, m in _all_modes().items() if m == "meet"]
    if _meets:   # 切片8: mode态用时现读零恢复成本, 此行=重启自愈开机自报告
        print(f"[chatapp] meet_state 恢复: {_meets}", flush=True)
    print(f"[chatapp] serving {CHAT_HOST}:{CHAT_PORT} (db={DB_PATH})", flush=True)
    uvicorn.run(app, host=CHAT_HOST, port=CHAT_PORT, log_level="info")


if __name__ == "__main__":
    main()
