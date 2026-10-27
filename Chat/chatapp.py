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
import asyncio, json, os, queue as Q, re, shutil, sqlite3, subprocess, sys, threading, time, uuid
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


# ---- M3媒体常量(切片3 2026-10-27): 识图/发图/语音条 ----
VOICE_DIR = os.path.join(MEDIA_DIR, "voice")   # opus→m4a语音条(前端audio直放, iOS通)
OUT_DIR = os.path.join(MEDIA_DIR, "out")       # 发图从char_media_out搬运入库(/media静态挂载)
CM_OUT_DIR = os.path.join(GA_ROOT, "temp", "char_media_out")   # char_media产物绝对路径(bash工具cwd=GA_ROOT/temp)
GEN_LOG = os.path.join(CM_OUT_DIR, "gen_log.jsonl")            # char_media逐条追加{ts,char,path,desc}
VISION_CFG = (os.environ.get("CHAT_VISION_CFG") or "native_oai_config10").strip()  # fsapp同款GLM-4V识图
_CLONE_CHARS = ("xiazhou", "shenxinghui", "qinche")   # 有云端克隆音色; 黎深/祁煜tts会报错(正常)
MEDIA_HINTS = {c: (
    f"[媒体工具修正·{CHAR_NAMES[c]}专属] 本聊天服务五个角色同住一个进程, 你调用 char_media.py 的"
    f" tts/img 命令时, 必须在命令末尾追加 --char={c} "
    f"(即 ... frontends/char_media.py tts \"要说的话\" --char={c}), "
    f"否则音色和生成日志会错记到夏以昼名下。不要用 wximg/wxvoice(那是微信角色专用命令)。"
    + ("" if c in _CLONE_CHARS else
       f" 另外你({CHAR_NAMES[c]})没有克隆音色档案, tts 会报错, 请直接用文字表达。"))
    for c in CHARS}


def init_db():
    os.makedirs(MEDIA_DIR, exist_ok=True)
    os.makedirs(os.path.join(MEDIA_DIR, "in"), exist_ok=True)
    os.makedirs(os.path.join(MEDIA_DIR, "bg"), exist_ok=True)
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


# ---------------- M3媒体: 识图/发图/语音条 (2026-10-27, 链路照fsapp成熟方案但不import) ----------------
_FILE_TAG_RE = re.compile(r"\[FILE:[^\]]+\]")   # 仅文本清洗用; 提取用_extract_files
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
    final = build_done_text(_LLM_RUNNING_RE.sub("", _FILE_TAG_RE.sub("", raw))) if raw.strip() else ""
    final = (final or "").strip()
    await asyncio.to_thread(_insert_msg, char, "assistant", "text", final or "……")
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
    image_seqs = [int(s) for s in image_seqs if str(s).strip().isdigit()][:3]
    if not text and not image_seqs:
        return _err(400, "text不能为空")
    if len(text) > 4000:
        return _err(400, "text过长(>4000)")
    if image_seqs:   # M3: 校验图片归属(本角色/用户/image_in行), 防跨角色串图
        with db() as conn:
            q = ",".join("?" * len(image_seqs))
            rows = conn.execute(
                "SELECT seq FROM messages WHERE seq IN (%s) AND char_id=? "
                "AND role='user' AND kind='image_in'" % q,
                image_seqs + [char]).fetchall()
        if len(rows) != len(set(image_seqs)):
            return _err(400, "image_seqs含无效或非本角色的图片")
    def _store():   # B2: 消息+job同事务; B9: 服务端权威ts; M3: img_seqs逗号串挂job
        ts = time.time()
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO messages(char_id, role, kind, content, ts) "
                "VALUES (?, 'user', 'text', ?, ?)", (char, text or "[图片]", ts))
            seq = cur.lastrowid
            cur = conn.execute(
                "INSERT INTO jobs(char_id, status, user_seq, img_seqs, created) "
                "VALUES (?, 'pending', ?, ?, ?)",
                (char, seq, ",".join(map(str, image_seqs)), ts))
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
