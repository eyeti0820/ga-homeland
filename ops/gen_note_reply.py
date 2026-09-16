#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M1 角色回复生成器（手动触发版；定时接入 M2）。

用法（GA 系统 python，非 server venv）：
    python3 ops/gen_note_reply.py                     # 夏以昼默认，处理待回复
    python3 ops/gen_note_reply.py --actor qiyu        # 换角色（ personas 文件名 ）
    python3 ops/gen_note_reply.py --dry-run           # 只打印任务与 prompt，不调模型不写库

链路：GA llmcore(per-char 模型) × char_config(persona+记忆宫殿) → HTTP API 写回复 → unread 标 done。
"""
import argparse
import json
import os
import re
import sys
import unicodedata

DEFAULT_GA_ROOT = "/Users/potato/Library/Application Support/GenericAgent/runtime/app"
CHAR_NAME = {  # slug -> 中文名（与 seeds/seed_actors.py 一致）
    "xiazhou": "夏以昼", "shenxinghui": "沈星回", "qinche": "秦彻",
    "lishen": "黎深", "qiyu": "祁煜",
}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", str(s)).lower())


# ---------- GA 侧：persona / 记忆 / 模型 ----------
def setup_ga(ga_root, actor):
    os.environ["GA_CHAR"] = actor
    if ga_root not in sys.path:
        sys.path.insert(0, ga_root)
    import llmcore
    from frontends import char_config
    persona = char_config.load_persona()
    if not persona:
        raise SystemExit(f"[gen_note_reply] persona 加载失败：assets/personas/{actor}.md")
    return llmcore, char_config, persona


def _strings(v):
    """任意配置结构 → 字符串token列表（str/list/dict 通吃）。"""
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        out = []
        for vv in v.values():
            out += _strings(vv)
        return out
    if isinstance(v, (list, tuple)):
        out = []
        for vv in v:
            out += _strings(vv)
        return out
    return []


def resolve_char_session(llmcore, ga_root, actor):
    """复用 char_models.json 语义：目标名对 mykey 配置做归一化子串匹配（双向）。"""
    path = os.path.join(ga_root, "ga_config", "char_models.json")
    target = json.load(open(path, encoding="utf-8")).get(actor, "")
    if not target:
        target = "GLM"
    mk = llmcore.reload_mykeys()[0]
    t = norm(target)
    hit = None
    # 只把 dict 型条目当会话候选（fs_/qq_ 等 str/list 是杂项）；
    # 且必须能被 resolve_session 真正解析（native/claude/oai 按键名分流）
    for k, cfg in mk.items():
        if not isinstance(cfg, dict):
            continue
        cands = [k] + _strings(cfg)
        if any(t and (t in norm(c) or norm(c) in t) for c in cands if c):
            try:
                sess = llmcore.resolve_session(k)
            except Exception:
                sess = None
            if sess:
                hit = k
                break
    if not hit:
        # 兜底：任意可解析会话里第一个（保持"缺省=GLM"语义的可用性）
        for k, cfg in mk.items():
            if isinstance(cfg, dict):
                try:
                    sess = llmcore.resolve_session(k)
                except Exception:
                    sess = None
                if sess:
                    hit = k
                    break
    if not hit:
        raise SystemExit(f"[gen_note_reply] char_models 目标 '{target}' 在 mykey 无可用会话（红线：不打印键值明细）")
    sess = llmcore.resolve_session(hit)
    if not sess:
        raise SystemExit(f"[gen_note_reply] 会话 '{hit}' 类型不受支持")
    return sess, hit


# ---------- 服务侧：API ----------
def api(base, method, path, body=None):
    import requests
    r = requests.request(method, base.rstrip("/") + path, json=body, timeout=30)
    r.raise_for_status()
    return r.json() if r.content else {}


def collect_tasks(base, char_id):
    """两类待回复：① pending unread（主人回了角色的话） ② 主人新便签且本角色未回。"""
    tasks, seen = [], set()
    for u in api(base, "GET", f"/api/unread?actor_id={char_id}&status=pending").get("items", []):
        ref = u.get("ref") or {}      # 路由已附带 {note_id, actor_id, content}
        nid = ref.get("note_id")
        if nid and nid not in seen:
            tasks.append({"note_id": nid, "unread_id": u["id"],
                          "master_words": ref.get("content", ""),
                          "via": "unread"})
            seen.add(nid)
    for n in api(base, "GET", f"/api/notes/pending-reply/{char_id}?limit=3").get("notes", []):
        if n["id"] not in seen:
            tasks.append({"note_id": n["id"], "unread_id": None,
                          "master_words": "", "via": "new_note"})
            seen.add(n["id"])
    return tasks


def note_context(base, note_id):
    n = api(base, "GET", f"/api/notes/{note_id}")
    convo = [f"小墨贴的便签：「{n['content']}」"]
    for r in n.get("replies", []):
        who = "小墨" if r["author_type"] == "master" else r["author_name"]
        convo.append(f"{who} 回：「{r['content']}」")
    return n, convo



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

def build_prompt(persona, memory, char_cn, convo, master_words, lore=""):
    mem = (f"\n{memory}\n" if memory else "")
    tail = (f"\n小墨刚刚又补了一句：「{master_words}」，针对这句回应。\n" if master_words else "")
    lore_seg = (f"\n【补充设定（世界知识参考）】\n{lore}\n" if lore else "")
    return (
        f"{persona}{lore_seg}\n"
        "——— 以下是「家园便签墙」场景 ———\n"
        "你和主人（她叫小墨，你的恋人）住在一个数字家园里，家里有一面共享便签墙。"
        "现在轮到你回应墙上的留言。\n"
        f"{mem}\n"
        + "\n".join(convo) + f"{tail}\n\n"
        f"请以{char_cn}本人身份写一张回应便签：\n"
        "- 口吻、称呼、性格、说话习惯严格按你的人设来\n"
        "- 40~90 字，口语化，像随手写在便签上的一两句话\n"
        "- 只输出便签正文本身：不要旁白、不要引号、不要署名、不要 emoji 堆砌\n"
    )


def clean_reply(text):
    t = re.sub(r"\s+", " ", text).strip().strip('「」"“”\'').strip()
    return t[:200]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--actor", default="xiazhou", help="personas 文件名，如 xiazhou/qiyu")
    ap.add_argument("--api", default="http://127.0.0.1:7842")
    ap.add_argument("--ga-root", default=DEFAULT_GA_ROOT)
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--lore-file", help="世界知识文件路径（persona 之后注入；不传则置空）")
    args = ap.parse_args()

    if args.actor not in CHAR_NAME:
        raise SystemExit(f"[gen_note_reply] 未知角色 {args.actor}，可选：{list(CHAR_NAME)}")
    char_cn = CHAR_NAME[args.actor]

    actors = api(args.api, "GET", "/api/actors")
    me = next((a for a in actors if f"personas/{args.actor}.md" in (a.get("persona_ref") or "")), None)
    if not me:
        raise SystemExit(f"[gen_note_reply] 服务端找不到 {char_cn}，先跑 seeds/seed_actors.py")

    tasks = collect_tasks(args.api, me["id"])[: args.limit]
    if not tasks:
        print(f"[gen_note_reply] {char_cn}：墙上没有等着他的话，收工。")
        return 0

    llmcore, char_config, persona = setup_ga(args.ga_root, args.actor)
    sess, sess_name = resolve_char_session(llmcore, args.ga_root, args.actor)
    print(f"[gen_note_reply] {char_cn}(#{me['id']}) 待处理 {len(tasks)} 条，模型≈{sess_name}")

    for t in tasks:
        note, convo = note_context(args.api, t["note_id"])
        memory = char_config.recall(note["content"] + " " + t["master_words"])
        prompt = build_prompt(persona, memory, char_cn, convo, t["master_words"], lore_block(args.lore_file))
        print(f"\n--- 便签 #{t['note_id']}（via {t['via']}） ---")
        if args.dry_run:
            print(prompt[:1200] + "\n…(dry-run 截断)")
            continue
        raw = "".join(sess.raw_ask([{"role": "user", "content": prompt}]))
        reply = clean_reply(raw)
        print(f"生成：{reply}")
        if len(reply) < 8:
            print("过短，跳过写入（防串味）")
            continue
        api(args.api, "POST", f"/api/notes/{t['note_id']}/replies",
            {"actor_id": me["id"], "content": reply})
        from homeland_ingest import homeland_ingest  # 家园产出→记忆宫殿(方案A: 原文直存+场景标签)
        homeland_ingest(char_config, "note", "便签回贴",
                        f"在便签「{note['content'][:40]}」下回贴（主人说：{t['master_words'][:60]}）", reply)
        if t["unread_id"]:
            api(args.api, "POST", f"/api/unread/{t['unread_id']}/done")
        print("已回贴 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
