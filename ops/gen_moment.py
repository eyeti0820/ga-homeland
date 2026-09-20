#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M2 朋友圈生成器（发圈 + 回评主人 + 角色互评）。

用法（GA 系统 python3，非 server venv）：
    python3 ops/gen_moment.py --actor xiazhou                 # 发圈（默认）
    python3 ops/gen_moment.py --actor qiyu --dry-run          # 只打印 prompt，不调模型不写库
    python3 ops/gen_moment.py --actor xiazhou --force         # 当日已发也强制再发
    python3 ops/gen_moment.py --actor lishen --interact       # 处理待回评（master_comment/master_reply）
    python3 ops/gen_moment.py --actor qiyu --cross            # 看别人的圈，决定是否互评

防重复三件套：
    1) 每日门：当日该角色已有 post → 跳过（--force 覆盖）
    2) 种子随机：seeds/moments_seeds.json 随机 event/mood/scene
    3) 相似度门：生成结果与本角色近期 post 相似度 > 0.7 → 重试一次，仍超 → 放弃
"""
import argparse
import difflib
import json
import os
import random
import re
import sys
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
sys.path.insert(0, BASE)
from gen_note_reply import setup_ga, resolve_char_session, api, CHAR_NAME  # noqa: E402

DEFAULT_GA_ROOT = "/Users/potato/Library/Application Support/GenericAgent/runtime/app"
SEEDS = json.load(open(os.path.join(ROOT, "seeds", "moments_seeds.json"), encoding="utf-8"))

# 角色关系提示（互评用，简化版；后续可挪进 seeds）
RELATION = {
    ("xiazhou", "shenxinghui"): "多年搭档，默契但话少，互相信任",
    ("shenxinghui", "xiazhou"): "多年搭档，默契但话少，互相信任",
    ("xiazhou", "qiyu"): "熟人，互相看不顺眼又总一起吃饭",
    ("qiyu", "xiazhou"): "熟人，互相看不顺眼又总一起吃饭",
    ("lishen", "xiazhou"): "旧识，彼此尊重，说话直接",
    ("xiazhou", "lishen"): "旧识，彼此尊重，说话直接",
    ("qinche", "xiazhou"): "道上打过照面，互相警惕又有点欣赏",
    ("xiazhou", "qinche"): "道上打过照面，互相警惕又有点欣赏",
    ("lishen", "shenxinghui"): "点头之交，客气但有距离",
    ("shenxinghui", "lishen"): "点头之交，客气但有距离",
    ("qiyu", "lishen"): "认识，一个感性一个理性，偶尔互相调侃",
    ("lishen", "qiyu"): "认识，一个感性一个理性，偶尔互相调侃",
    ("qiyu", "shenxinghui"): "不熟，只在共同朋友场合见过",
    ("shenxinghui", "qiyu"): "不熟，只在共同朋友场合见过",
    ("qinche", "lishen"): "地下诊所打过交道，互欠过人情",
    ("lishen", "qinche"): "地下诊所打过交道，互欠过人情",
    ("qiyu", "qinche"): "委托关系认识，互相觉得对方有意思",
    ("qinche", "qiyu"): "委托关系认识，互相觉得对方有意思",
    ("shenxinghui", "qinche"): "几乎不认识，只在情报里见过名字",
    ("qinche", "shenxinghui"): "几乎不认识，只在情报里见过名字",
}


def load_tpl(name):
    """从 prompts/*.md 抽第一个 ``` 代码块作为模板。"""
    txt = open(os.path.join(ROOT, "prompts", name), encoding="utf-8").read()
    m = re.search(r"```\n(.*?)\n```", txt, re.S)
    if not m:
        raise SystemExit(f"[gen_moment] 模板缺失：prompts/{name}")
    return m.group(1)


def parse_json_loose(raw):
    """宽松解析模型输出：先 json.loads，再正则兜底，最后整行当 content。"""
    t = raw.strip()
    t = re.sub(r"^```(json)?|```$", "", t, flags=re.M).strip()
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r'\{[^{}]*"content"[^{}]*\}', t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                c = re.search(r'"content"\s*:\s*"([^"]+)"', m.group(0))
                if c:
                    return {"content": c.group(1)}
        return {"content": t.splitlines()[0] if t.strip() else ""}


def today_str():
    import datetime
    return datetime.date.today().isoformat()


def actor_id_of(base, actor):
    r = api(base, "GET", "/api/actors")
    actors = r["items"] if isinstance(r, dict) and "items" in r else r
    for a in actors:
        if a["name"] == CHAR_NAME[actor]:
            return a["id"]
    raise SystemExit(f"[gen_moment] 找不到角色 {actor}")


def my_recent_posts(base, aid, limit=8):
    r = api(base, "GET", f"/api/posts?actor_id={aid}&limit={limit}")
    for k in ("posts", "items"):
        if isinstance(r, dict) and k in r:
            return r[k]
    return r


def similar(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def home_context(base):
    """最近 5 条动态摘要（喂 prompt 的家园上下文）。"""
    r = api(base, "GET", "/api/posts?limit=5")
    posts = r.get("posts") if isinstance(r, dict) else r
    lines = [f"- {p.get('author_name', '?')}：{p['content'][:40]}" for p in posts]
    return "\n".join(lines) if lines else "（家园最近很安静）"



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

def build_post_prompt(persona, recall_mem, few_shot, seed, ctx, lore=""):
    tpl = load_tpl("moment_post.md")
    return (tpl.replace("{{persona_self}}", persona[:3000])
            .replace("{{lore}}", lore)
            .replace("{{recall_mem}}", recall_mem or "（无相关记忆）")
            .replace("{{few_shot_posts}}", few_shot or "你还没发过圈，这是第一条")
            .replace("{{seed_event}}", seed["event"])
            .replace("{{seed_mood}}", seed["mood"])
            .replace("{{seed_scene}}", seed["scene"])
            .replace("{{home_context}}", ctx))


def run_post(args, llmcore, char_config, persona):
    base, aid = args.api, args.actor_id
    recent = my_recent_posts(base, aid)
    today = today_str()
    if not args.force and any(str(p.get("created_at", "")).startswith(today) for p in recent):
        print(f"[skip] {CHAR_NAME[args.actor]} 今天已发过圈（--force 可覆盖）")
        return 0
    seed = {k: random.choice(SEEDS[args.actor][k]) for k in ("events", "moods", "scenes")}
    seed = {"event": seed["events"], "mood": seed["moods"], "scene": seed["scenes"]}
    few_shot = "\n".join(f"- {p['content'][:50]}" for p in recent[:2]) or None
    mem = char_config.recall(seed["event"])
    prompt = build_post_prompt(persona, mem, few_shot, seed, home_context(base), lore_block(args.lore_file))

    print(f"== 发圈任务：{CHAR_NAME[args.actor]} | 种子: {seed['event']} / {seed['mood']} / {seed['scene']}")
    if args.dry_run:
        print(prompt)
        print("== dry-run 结束，未调模型未写库")
        return 0

    sess, _ = resolve_char_session(llmcore, args.ga_root, args.actor)
    body, ok = "", False
    for attempt in (1, 2):
        body = "".join(sess.raw_ask([{"role": "user", "content": prompt}]))
        content = (parse_json_loose(body).get("content") or "").strip()
        if not content:
            print(f"[retry{attempt}] 输出解析为空，原始：{body[:120]}")
            continue
        worst = max((similar(content, p["content"]) for p in recent), default=0.0)
        if worst > 0.7:
            print(f"[retry{attempt}] 相似度 {worst:.2f} > 0.70，重试")
            continue
        r = api(base, "POST", "/api/posts", {
            "actor_id": aid, "content": content, "gen_task_id": f"gen_moment:{today}:{args.actor}"})
        print(f"[ok] 已发圈 post_id={r['id']}：{content}")
        from homeland_ingest import homeland_ingest
        homeland_ingest(char_config, "moment", "朋友圈", "发了新朋友圈动态", content)
        ok = True
        break
    if not ok:
        print("[giveup] 两次尝试未过相似度/解析门，放弃本次（不写库）")
    return 0


def run_interact(args, llmcore, char_config, persona):
    """处理 kind=master_comment / master_reply 的 pending unread。"""
    base, aid = args.api, args.actor_id
    unread = api(base, "GET", f"/api/unread?actor_id={aid}&status=pending")["items"]
    tasks = [u for u in unread if u["kind"] in ("master_comment", "master_reply")]
    if not tasks:
        print(f"[skip] {CHAR_NAME[args.actor]} 无待回评")
        return 0
    tpl = load_tpl("moment_interact.md")
    sess = None
    for t in tasks:
        # unread 条目自带 ref：{post_id, actor_id, parent_id, content}（服务端已拼好）
        ref = t.get("ref") or {}
        posts = api(base, "GET", "/api/posts?limit=50")
        posts = posts.get("posts") if isinstance(posts, dict) else posts
        target = next((p for p in posts if p["id"] == ref.get("post_id")), None)
        if not target:
            print(f"[warn] unread {t['id']} 找不到对应帖子，标 done 跳过")
            api(base, "POST", f"/api/unread/{t['id']}/done")
            continue
        master_c = {"id": t["ref_id"], "content": ref.get("content", ""),
                    "author_name": "主人", "parent_id": ref.get("parent_id")}
        thread = "\n".join(
            f"{'  ' if c.get('parent_id') else ''}{c.get('author_name', '?')}：{c['content']}"
            for c in target["comments"])
        _on = target.get("author_name") or "对方"
        own_post = target.get("author_name") == CHAR_NAME.get(args.actor)
        if own_post:
            own_label = "【你发的朋友圈】"
        else:
            own_label = f"【{_on} 发的圈】（注意：这是{_on}的圈，不是你的；主人这条评论默认是对{_on}说的）"
        if sess is None:
            sess, _ = resolve_char_session(llmcore, args.ga_root, args.actor)
        mem = char_config.recall(master_c["content"])
        prompt = (tpl.replace("{{persona_self}}", persona[:3000])
                  .replace("{{lore}}", lore_block(args.lore_file))
                  .replace("{{recall_mem}}", mem or "（无相关记忆）")
                  .replace("{{post_ownership}}", own_label)
                  .replace("{{post_content}}", target["content"])
                  .replace("{{comments_thread}}", thread)
                  .replace("{{master_comment}}", f"{master_c.get('author_name', '主人')}：{master_c['content']}")
                  .replace("{{home_context}}", home_context(base)))
        print(f"== 回评任务：{CHAR_NAME[args.actor]} ← {master_c['content'][:30]}")
        if args.dry_run:
            print(prompt); continue
        body = "".join(sess.raw_ask([{"role": "user", "content": prompt}]))
        content = (parse_json_loose(body).get("content") or "").strip()
        if not content:
            print(f"[fail] 解析为空：{body[:120]}"); continue
        api(base, "POST", f"/api/posts/{target['id']}/comments", {
            "actor_id": aid, "content": content,
            "parent_id": master_c["id"], "gen_task_id": f"reply:{t['id']}"})
        api(base, "POST", f"/api/unread/{t['id']}/done")
        print(f"[ok] 已回评：{content}")
        from homeland_ingest import homeland_ingest
        homeland_ingest(char_config, "moment_reply", "朋友圈回评",
                        f"主人评论{'我' if own_post else _on}的动态「{target['content'][:40]}」说：{master_c['content'][:60]}，我回复", content)
    return 0


def run_cross(args, llmcore, char_config, persona):
    """看别人的最新圈，决定是否互评。"""
    base, aid = args.api, args.actor_id
    posts = api(base, "GET", "/api/posts?limit=10")
    posts = posts.get("posts") if isinstance(posts, dict) else posts
    cands = [p for p in posts if p["actor_id"] != aid and p["actor_id"] != 6]
    if not cands:
        print("[skip] 没有别人的圈可看"); return 0
    target = random.choice(cands[:5])
    other = next((k for k, v in CHAR_NAME.items() if v == target.get("author_name")), None)
    if other is None:
        print("[skip] 对方不在五角色名单"); return 0
    already = any(c.get("actor_id") == aid for c in target.get("comments", []))
    if already:
        print(f"[skip] 已评过 {target['id']}"); return 0
    tpl = load_tpl("moment_interact.md")
    cross = tpl[tpl.find("## 角色互评"):] if "## 角色互评" in tpl else tpl
    m = re.search(r"```\n(.*?)\n```", cross, re.S)
    cross_tpl = m.group(1) if m else tpl
    sess = None
    mem = char_config.recall(target["content"][:60])
    thread = "\n".join(f"{'  ' if c.get('parent_id') else ''}{c.get('author_name','?')}：{c['content']}"
                       for c in target.get("comments", []))
    prompt = (cross_tpl.replace("{{persona_self}}", persona[:3000])
              .replace("{{lore}}", lore_block(args.lore_file))
              .replace("{{recall_mem}}", mem or "（无相关记忆）")
              .replace("{{other_name}}", CHAR_NAME[other])
              .replace("{{post_content}}", target["content"])
              .replace("{{comments_thread}}", thread or "（还没有人评论）")
              .replace("{{relation_hint}}", RELATION.get((args.actor, other), "认识，但不算熟"))
              .replace("{{home_context}}", home_context(base)))
    print(f"== 互评任务：{CHAR_NAME[args.actor]} → {CHAR_NAME[other]} 的圈：{target['content'][:30]}")
    if args.dry_run:
        print(prompt); return 0
    sess, _ = resolve_char_session(llmcore, args.ga_root, args.actor)
    body = "".join(sess.raw_ask([{"role": "user", "content": prompt}]))
    out = parse_json_loose(body)
    action, content = (out.get("action") or "comment"), (out.get("content") or "").strip()
    if action == "skip" or not content:
        print(f"[skip] 模型选择不评论（{body[:80]}）"); return 0
    api(base, "POST", f"/api/posts/{target['id']}/comments", {
        "actor_id": aid, "content": content, "parent_id": None,
        "gen_task_id": f"cross:{target['id']}"})
    print(f"[ok] 已互评：{content}")
    from homeland_ingest import homeland_ingest
    homeland_ingest(char_config, "moment_cross", "朋友圈互评",
                    f"给{CHAR_NAME[other]}的动态「{target['content'][:40]}」留言", content)
    return 0


def run_npc_comment(args, llmcore, char_config):
    """剧情NPC（seeds/story_npcs.json）评论关联角色的朋友圈。"""
    base, aid = args.api, args.actor_id
    story = json.load(open(os.path.join(ROOT, "seeds", "story_npcs.json"), encoding="utf-8"))
    pool = [n for n in story["npcs"] if n.get("tie") == args.actor]
    if not pool:
        print(f"[skip] 没有关联 {CHAR_NAME[args.actor]} 的剧情NPC"); return 0
    posts = api(base, "GET", "/api/posts?limit=15")
    posts = posts.get("posts") if isinstance(posts, dict) else posts
    mine = [p for p in posts if p["actor_id"] == aid]
    if not mine:
        print(f"[skip] {CHAR_NAME[args.actor]} 还没有朋友圈"); return 0
    target = random.choice(mine[:3])
    cands = [n for n in pool if not any(
        c.get("actor_id") == n["actor_id"] for c in target.get("comments", []))]
    if not cands:
        print(f"[skip] {CHAR_NAME[args.actor]} 的圈 {target['id']} 已被全部关联NPC评过"); return 0
    npc = random.choice(cands)
    if not args.force and random.random() > 0.75:
        print(f"[skip] {npc['name']} 本次掷骰未出勤（--force 可强制）"); return 0
    tpl = load_tpl("moment_npc_comment.md")
    thread = "\n".join(f"{'  ' if c.get('parent_id') else ''}{c.get('author_name','?')}：{c['content']}"
                       for c in target.get("comments", []))
    display = npc.get("display_name") or npc["name"]
    prompt = (tpl.replace("{{npc_name}}", display)
              .replace("{{npc_persona}}", npc["persona"])
              .replace("{{npc_style}}", npc.get("style", ""))
              .replace("{{host_name}}", CHAR_NAME[args.actor])
              .replace("{{post_content}}", target["content"])
              .replace("{{comments_thread}}", thread or "（还没有人评论）")
              .replace("{{home_context}}", home_context(base)))
    print(f"== NPC评论任务：{display} → {CHAR_NAME[args.actor]} 的圈：{target['content'][:30]}")
    if args.dry_run:
        print(prompt); return 0
    sess, _ = resolve_char_session(llmcore, args.ga_root, "GLM")
    body = "".join(sess.raw_ask([{"role": "user", "content": prompt}]))
    content = (parse_json_loose(body).get("content") or "").strip()
    if not content:
        print(f"[fail] 解析为空：{body[:120]}"); return 0
    api(base, "POST", f"/api/posts/{target['id']}/comments", {
        "actor_id": npc["actor_id"], "content": content, "parent_id": None,
        "gen_task_id": f"npc:{npc['name']}:{target['id']}"})
    print(f"[ok] {display} 已评论：{content}")
    from homeland_ingest import homeland_ingest
    homeland_ingest(char_config, "moment_npc_comment", "朋友圈NPC评论",
                    f"{display}评论了我的朋友圈「{target['content'][:40]}」", content)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--actor", default="xiazhou")
    ap.add_argument("--api", default="http://127.0.0.1:7842")
    ap.add_argument("--ga-root", default=DEFAULT_GA_ROOT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="当日已发也强制发")
    ap.add_argument("--interact", action="store_true", help="回评主人模式")
    ap.add_argument("--cross", action="store_true", help="互评模式")
    ap.add_argument("--npc-comment", action="store_true", help="剧情NPC评论模式")
    ap.add_argument("--lore-file", help="世界知识文件路径（注入模板 {{lore}}，位于 persona 之后；不传则置空）")
    args = ap.parse_args()
    if args.actor not in CHAR_NAME:
        raise SystemExit(f"未知角色 {args.actor}，可选：{'/'.join(CHAR_NAME)}")
    llmcore, char_config, persona = setup_ga(args.ga_root, args.actor)
    args.actor_id = actor_id_of(args.api, args.actor)
    if args.interact:
        return run_interact(args, llmcore, char_config, persona)
    if args.cross:
        return run_cross(args, llmcore, char_config, persona)
    if args.npc_comment:
        return run_npc_comment(args, llmcore, char_config)
    return run_post(args, llmcore, char_config, persona)


if __name__ == "__main__":
    sys.exit(main())
