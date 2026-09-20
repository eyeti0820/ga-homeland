#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M3 论坛生成器（NPC 起楼 / NPC 续楼 / 角色马甲跨板 / 小梅主编运营）。

用法（GA 系统 python3，非 server venv）：
    python3 ops/gen_forum.py --npc                        # 随机 NPC 起楼 1 帖
    python3 ops/gen_forum.py --npc --count 3              # 多帖（不同 NPC，密度门内）
    python3 ops/gen_forum.py --npc 舰上食堂主理 --board fleet
    python3 ops/gen_forum.py --npc-reply                  # 随机 NPC 续楼
    python3 ops/gen_forum.py --npc-reply --thread 3       # 指定帖续楼
    python3 ops/gen_forum.py --cross xiazhou              # 角色马甲给帖续楼
    python3 ops/gen_forum.py --cross qiyu --new-thread    # 马甲起楼（默认非主场公共板）
    python3 ops/gen_forum.py --editor                     # 小梅主编：城市八卦帖
    python3 ops/gen_forum.py --editor --pin               # 编辑部置顶公告
    公共：--dry-run / --board key / --thread ID / --count N / --base http://127.0.0.1:7842 / --ga-root

链路：forum_seeds.json(NPC 人设/话题池) → GLM 会话 raw_ask → difflib 查重(≥0.7 弃) →
      POST /api/forum/{threads,threads/:id/posts} → POST /api/genlog 审计。
密度门：全站当日 ≥12 帖停；单板当日 ≥3 帖换板。
红线：马甲对照只存在于本层与 /api/forum/masks（生成层专用），展示端永不渲染。
"""
import argparse
import difflib
import hashlib
import json
import random
import sys
import uuid
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))          # ops/ 内互相 import
from gen_note_reply import setup_ga, resolve_char_session, api, CHAR_NAME, DEFAULT_GA_ROOT  # noqa: E402
from gen_moment import parse_json_loose                              # noqa: E402

SEEDS = json.load(open(ROOT / "seeds" / "forum_seeds.json", encoding="utf-8"))
TPL_NPC = (ROOT / "prompts" / "forum_npc.md").read_text(encoding="utf-8")
TPL_MASK = (ROOT / "prompts" / "forum_mask.md").read_text(encoding="utf-8")
BOARD_MAP = {b["key"]: b for b in SEEDS["boards"]}
MASK_MAP = SEEDS["masks"]                                          # 中文名 -> 马甲名

DAILY_SITE_CAP = 12      # 全站日更上限
DAILY_BOARD_CAP = 3      # 单板日更上限
SIM_THRESHOLD = 0.7      # 标题相似度弃帖门槛（findings §8 M2 既定）


# ---------- 模板抽取 ----------
def _section(md: str, heading: str) -> str:
    """取 md 中指定 ## 小节之后的第一个 fenced code block 内容。"""
    i = md.find(heading)
    if i < 0:
        raise KeyError(f"template section not found: {heading}")
    seg = md[i:]
    j = seg.find("```")
    k = seg.find("```", j + 3)
    return seg[j + 3:k].strip()


def fill(tpl: str, **kw) -> str:
    for k, v in kw.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    return tpl


# ---------- API helpers ----------
def boards_index(base):
    """[{key,id,name,...}] + key->id 映射（threads 列表只带 board_id）。"""
    items = api(base, "GET", "/api/forum/boards").get("items", [])
    return items, {b["key"]: b["id"] for b in items}


def recent_threads(base, board_key=None):
    """GET /threads 的 board_key 必填：无参时逐板合并，按 last_activity 倒序。"""
    keys = [board_key] if board_key else list(BOARD_MAP.keys())
    out = []
    for k in keys:
        for t in api(base, "GET", f"/api/forum/threads?board_key={k}&limit=50").get("threads", []):
            t.setdefault("board_key", k)
            out.append(t)
    out.sort(key=lambda x: str(x.get("last_activity", "")), reverse=True)
    return out


def thread_detail(base, tid):
    return api(base, "GET", f"/api/forum/threads/{tid}")


def today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


def board_today_count(items, board_key) -> int:
    t = today_str()
    return sum(1 for x in items if x.get("board_key") == board_key and str(x.get("created_at", "")).startswith(t))


def site_today_count(items) -> int:
    t = today_str()
    return sum(1 for x in items if str(x.get("created_at", "")).startswith(t))


def title_too_similar(title, recents) -> bool:
    return any(difflib.SequenceMatcher(None, title, r).ratio() >= SIM_THRESHOLD
               for r in recents if r)


def masks_of(base):
    return api(base, "GET", "/api/forum/masks").get("items", [])


def mask_id_of(base, forum_username):
    for m in masks_of(base):
        if m.get("forum_username") == forum_username:
            return m["id"], m["actor_id"]
    return None, None


# ---------- LLM ----------
def ask_json(sess, prompt, keys):
    for _ in range(2):
        body = "".join(sess.raw_ask([{"role": "user", "content": prompt}]))
        data = parse_json_loose(body)
        if isinstance(data, dict) and all((data.get(k) or "").strip() for k in keys):
            return {k: data[k].strip() for k in keys}
    return None


# ---------- 写库 + 审计 ----------
def submit(base, kind, actor_id, result_ref, text, dry):
    if dry:
        print(f"    [dry] 本应写 gen_log: action=forum_{kind} ref={result_ref}")
        return
    api(base, "POST", "/api/genlog", {
        "task_id": f"forum_{uuid.uuid4().hex[:8]}",
        "actor_id": actor_id,
        "action": f"forum_{kind}",
        "result_ref": result_ref,
        "content_hash": hashlib.md5(text.encode("utf-8")).hexdigest(),
    })


def post_thread(base, board_key, mask_id, payload, actor_id, pin=False, dry=False):
    if dry:
        print(f"    [dry] 本应 POST /threads board={board_key} mask={mask_id} pin={pin}")
        return None
    body = {"board_key": board_key, "mask_id": mask_id,
            "title": payload["title"], "content": payload["content"]}
    if pin:
        body["is_pinned"] = True
    r = api(base, "POST", "/api/forum/threads", body)
    submit(base, "thread", actor_id, f"threads:{r['id']}", payload["title"] + payload["content"], dry)
    return r


def post_floor(base, tid, mask_id, payload, actor_id, dry=False):
    if dry:
        print(f"    [dry] 本应 POST /threads/{tid}/posts mask={mask_id}")
        return None
    r = api(base, "POST", f"/api/forum/threads/{tid}/posts",
            {"mask_id": mask_id, "content": payload["content"]})
    submit(base, "floor", actor_id, f"threads:{tid}:posts:{r['id']}", payload["content"], dry)
    return r


# ---------- 各模式 ----------
def floors_text(detail, limit=8):
    posts = detail.get("posts", [])[:limit]
    lines = []
    for p in posts:
        au = p.get("author") or p.get("author_username") or "?"
        lines.append(f"{au}：{p.get('content', '')[:80]}")
    return "\n".join(lines) if lines else "（还没有人回帖）"


def run_npc_thread(base, args, sess):
    npcs = SEEDS["npcs"]
    names = [args.npc] if isinstance(args.npc, str) and args.npc else None
    picked = []
    pool = [n for n in npcs if (not names or n["name"] == args.npc)]
    if names and not pool:
        print(f"[skip] NPC 不存在：{args.npc}（可用：{[n['name'] for n in npcs][:6]}...）")
        return 0
    want = args.count or 1
    random.shuffle(pool)
    ok = 0
    for npc in pool:
        if ok >= want:
            break
        all_items = recent_threads(base)
        if site_today_count(all_items) >= DAILY_SITE_CAP:
            print("[gate] 全站今日帖数已达上限，停。")
            break
        board_key = args.board or random.choice(npc["home"])
        if board_today_count(all_items, board_key) >= DAILY_BOARD_CAP:
            alt = [b for b in npc["home"] if board_today_count(all_items, b) < DAILY_BOARD_CAP]
            if not alt:
                print(f"[gate] {npc['name']} 的常驻板今日均满，跳过")
                continue
            board_key = random.choice(alt)
        recents = [t.get("title", "") for t in recent_threads(base, board_key)][:10]
        topic = random.choice(SEEDS["topics"].get(board_key, ["日常"]))
        tone = random.choice(SEEDS["tones"])
        b = BOARD_MAP[board_key]
        tpl = _section(TPL_NPC, "## 一、NPC 起楼模板")
        prompt = fill(tpl, persona_npc=f"{npc['name']}：{npc['persona']}",
                      board_name=b["name"], board_desc=b["description"],
                      recent_titles="\n".join(f"- {r}" for r in recents) or "（暂无）",
                      topic=topic, tone=tone,
                      lore=lore_block(args.lore_file))
        if args.dry_run:
            print(f"\n=== [dry] NPC={npc['name']} board={board_key} topic={topic} tone={tone} ===")
            print(prompt[:1500] + ("\n...<截断>" if len(prompt) > 1500 else ""))
            picked.append(npc["name"]); ok += 1
            continue
        payload = ask_json(sess, prompt, ["title", "content"])
        if payload is None:
            print(f"[fail] {npc['name']} 生成失败（两次无合法 JSON）")
            continue
        if title_too_similar(payload["title"], recents):
            topic2 = random.choice([t for t in SEEDS["topics"].get(board_key, []) if t != topic])
            payload2 = ask_json(sess, fill(tpl, lore=lore_block(args.lore_file), persona_npc=f"{npc['name']}：{npc['persona']}",
                                           board_name=b["name"], board_desc=b["description"],
                                           recent_titles="\n".join(f"- {r}" for r in recents),
                                           topic=topic2, tone=random.choice(SEEDS["tones"])),
                                ["title", "content"])
            if payload2 is None or title_too_similar(payload2["title"], recents):
                print(f"[gate] {npc['name']} 标题查重未过，弃帖")
                continue
            payload = payload2
        mid, aid = mask_id_of(base, npc["name"])
        r = post_thread(base, board_key, mid, payload, aid)
        print(f"[OK] {b['name']} 《{payload['title']}》 by {npc['name']} -> thread {r['id']}")
        ok += 1
    if args.dry_run:
        print(f"\n[dry] 共 {ok} 个 NPC 待发（未调模型未写库）")
    return ok


def run_npc_reply(base, args, sess):
    items = [t for t in recent_threads(base) if t.get("id") != args.thread or not args.thread]
    if args.thread:
        tid = args.thread
    else:
        if not items:
            print("[skip] 全站无帖可回")
            return 0
        tid = random.choice(items)["id"]
    detail = thread_detail(base, tid)
    op_author = detail.get("thread", {}).get("author") or detail.get("author") or "?"
    npc = random.choice(SEEDS["npcs"])
    bkey = detail.get("thread", {}).get("board_key") or detail.get("board_key") or "city"
    b = BOARD_MAP.get(bkey, BOARD_MAP["city"])
    tpl = _section(TPL_NPC, "## 二、NPC 续楼模板")
    prompt = fill(tpl, persona_npc=f"{npc['name']}：{npc['persona']}",
                  thread_title=detail.get("thread", {}).get("title", "?"),
                  board_name=b["name"], thread_author=op_author,
                  thread_op=detail.get("thread", {}).get("content", "")[:600],
                  floors=floors_text(detail),
                  lore=lore_block(args.lore_file))
    if args.dry_run:
        print(f"\n=== [dry] NPC-reply={npc['name']} thread={tid} ===")
        print(prompt[:1500])
        return 1
    payload = ask_json(sess, prompt, ["content"])
    if payload is None:
        print(f"[fail] {npc['name']} 续楼失败")
        return 0
    mid, aid = mask_id_of(base, npc["name"])
    r = post_floor(base, tid, mid, payload, aid, args.dry_run)
    print(f"[OK] thread {tid} +1楼 by {npc['name']}：{payload['content'][:40]}...")
    return 1



def lore_block(path):
    """--lore-file 世界知识注入：文件存在则读入（供模板 {{lore}} 替换，位于 persona 之后）；未传/不存在返回空串，行为与原来完全一致。"""
    if not path:
        return ""
    try:
        t = open(path, encoding="utf-8").read().strip()
    except FileNotFoundError:
        print(f"[warn] --lore-file 不存在，忽略：{path}")
        return ""
    return f"\n【补充设定（世界知识参考）】\n{t}\n" if t else ""

def run_cross(base, args):
    actor = args.cross
    cn = CHAR_NAME.get(actor, actor)
    llmcore, char_config, persona = setup_ga(args.ga_root, actor)
    sess, _ = resolve_char_session(llmcore, args.ga_root, actor)
    mask_name = MASK_MAP.get(cn) or MASK_MAP.get(actor)
    if not mask_name:
        print(f"[fail] 无马甲映射：{actor}({cn})，seeds/masks={list(MASK_MAP.keys())}")
        return 0
    mid, aid = mask_id_of(base, mask_name)
    if mid is None:
        print(f"[fail] DB 无此马甲：{mask_name}")
        return 0
    guard = _section(TPL_MASK, "## 通用马甲保密规则")

    if args.new_thread:
        home = {"xiazhou": ["city"], "shenxinghui": ["city"], "qingche": ["city", "hunters"],
                "lishen": ["city"], "qiyu": ["city", "gallery"]}.get(actor, ["city"])
        bkey = args.board or random.choice([k for k in BOARD_MAP
                                            if k not in home and BOARD_MAP[k]["access"] == "public"])
        b = BOARD_MAP[bkey]
        recents = [t.get("title", "") for t in recent_threads(base, bkey)][:10]
        topic = random.choice(SEEDS["topics"].get(bkey, ["日常"]))
        tpl = _section(TPL_MASK, "## 一、马甲起楼模板")
        prompt = fill(guard, mask_name=mask_name) + "\n\n" + fill(tpl, persona_self=persona[:3000],
                                       lore=lore_block(args.lore_file),
                                       recall_mem="", mask_name=mask_name,
                                       board_name=b["name"], board_desc=b["description"],
                                       recent_titles="\n".join(f"- {r}" for r in recents) or "（暂无）",
                                       topic=topic, tone=random.choice(SEEDS["tones"]))
        if args.dry_run:
            print(f"\n=== [dry] cross-new actor={actor}({cn}) mask={mask_name} board={bkey} ===")
            print(prompt[:1800] + ("...<截断>" if len(prompt) > 1800 else ""))
            return 1
        payload = ask_json(sess, prompt, ["title", "content"])
        if payload is None:
            print(f"[fail] {cn} 马甲起楼失败")
            return 0
        r = post_thread(base, bkey, mid, payload, aid)
        print(f"[OK] {b['name']} 《{payload['title']}》 by {mask_name}(马甲:{cn}) -> thread {r['id']}")
        from homeland_ingest import homeland_ingest
        homeland_ingest(char_config, "forum_thread", "论坛马甲起楼",
                        f"用马甲「{mask_name}」在《{b['name']}》板块发了帖《{payload['title']}》", payload["content"])
        return 1

    items = recent_threads(base)
    if args.thread:
        tid = args.thread
    else:
        if not items:
            print("[skip] 全站无帖")
            return 0
        tid = random.choice(items)["id"]
    detail = thread_detail(base, tid)
    th = detail.get("thread", {})
    bkey = th.get("board_key") or "city"
    b = BOARD_MAP.get(bkey, BOARD_MAP["city"])
    try:
        recall = char_config.recall(th.get("title", "")[:50]) if char_config else ""
    except Exception:
        recall = ""
    tpl = _section(TPL_MASK, "## 二、马甲留言模板")
    prompt = fill(guard, mask_name=mask_name) + "\n\n" + fill(tpl, persona_self=persona[:3000],
                                   lore=lore_block(args.lore_file),
                                   recall_mem=recall or "（无特别相关记忆）",
                                   mask_name=mask_name, thread_title=th.get("title", "?"),
                                   board_name=b["name"],
                                   thread_author=th.get("author") or "?",
                                   thread_op=(th.get("content") or "")[:600],
                                   floors=floors_text(detail))
    if args.dry_run:
        print(f"\n=== [dry] cross-floor actor={actor}({cn}) mask={mask_name} thread={tid} ===")
        print(prompt[:1800] + ("...<截断>" if len(prompt) > 1800 else ""))
        return 1
    payload = ask_json(sess, prompt, ["content"])
    if payload is None:
        print(f"[fail] {cn} 马甲留言失败")
        return 0
    post_floor(base, tid, mid, payload, aid)
    print(f"[OK] thread {tid} +1楼 by {mask_name}(马甲:{cn})：{payload['content'][:40]}...")
    from homeland_ingest import homeland_ingest
    homeland_ingest(char_config, "forum_floor", "论坛马甲回帖",
                    f"用马甲「{mask_name}」在《{th.get('title', '?')}》帖下回了楼", payload["content"])
    return 1


def run_editor(base, args, sess):
    mask_name = MASK_MAP.get("小梅")
    mid, aid = mask_id_of(base, mask_name)
    if mid is None:
        print(f"[fail] 编辑部马甲缺失：{mask_name}")
        return 0
    kind = "notice-公告" if args.pin else "city-八卦"
    bkey = "city"
    recents = [t.get("title", "") for t in recent_threads(base)][:12]
    topic = random.choice(SEEDS["topics"]["city"] + ["编辑部收到的奇怪来稿", "深夜电台点歌箱"])
    tpl = _section(TPL_NPC, "## 三、小梅主编运营模板")
    prompt = fill(tpl, editor_kind=kind,
                  recent_titles="\n".join(f"- {r}" for r in recents) or "（暂无）", topic=topic,
                  lore=lore_block(args.lore_file))
    if args.dry_run:
        print(f"\n=== [dry] editor kind={kind} topic={topic} ===")
        print(prompt[:1500])
        return 1
    payload = ask_json(sess, prompt, ["title", "content"])
    if payload is None:
        print("[fail] 编辑部生成失败")
        return 0
    r = post_thread(base, bkey, mid, payload, aid, pin=args.pin)
    print(f"[OK] 城市杂谈 《{payload['title']}》 by {mask_name} pin={args.pin} -> thread {r['id']}")
    return 1


def main():
    ap = argparse.ArgumentParser(description="M3 论坛生成器")
    ap.add_argument("--npc", nargs="?", const=True, default=None,
                    help="NPC 起楼（可跟 NPC 名，不给则随机）")
    ap.add_argument("--npc-reply", action="store_true", help="NPC 续楼")
    ap.add_argument("--cross", metavar="ACTOR", help="角色马甲跨板（xiazhou/...）")
    ap.add_argument("--editor", action="store_true", help="小梅主编运营帖")
    ap.add_argument("--pin", action="store_true", help="主编公告置顶（仅 --editor）")
    ap.add_argument("--new-thread", action="store_true", help="马甲起楼（仅 --cross）")
    ap.add_argument("--board", help="指定板块 key")
    ap.add_argument("--thread", type=int, help="指定帖 ID")
    ap.add_argument("--count", type=int, help="NPC 起楼数")
    ap.add_argument("--dry-run", action="store_true", help="只打印 prompt，不调模型不写库")
    ap.add_argument("--lore-file", help="世界知识文件路径（注入模板 {{lore}}，位于 persona 之后；不传则置空）")
    ap.add_argument("--base", default="http://127.0.0.1:7842")
    ap.add_argument("--ga-root", default=DEFAULT_GA_ROOT)
    args = ap.parse_args()

    modes = sum(bool(x) for x in (args.npc, args.npc_reply, args.cross, args.editor))
    if modes != 1:
        ap.error("四选一：--npc / --npc-reply / --cross ACTOR / --editor")

    # NPC 与主编走 GLM 会话（不带任何真实角色 persona）
    if args.npc or args.npc_reply or args.editor:
        ga_root = args.ga_root
        sys.path.insert(0, ga_root)
        try:
            import llmcore  # noqa: F401  (GA 系统内)
            sess, _ = resolve_char_session(llmcore, ga_root, "GLM")
        except Exception as e:
            print(f"[fail] GA llmcore 不可用：{e}（--dry-run 可跳过模型）")
            sess = None
        if sess is None and not args.dry_run:
            return 1

    if args.npc:
        sys.exit(0 if run_npc_thread(args.base, args, sess) else 1)
    if args.npc_reply:
        sys.exit(0 if run_npc_reply(args.base, args, sess) else 1)
    if args.cross:
        sys.exit(0 if run_cross(args.base, args) else 1)
    sys.exit(0 if run_editor(args.base, args, sess) else 1)


if __name__ == "__main__":
    main()
