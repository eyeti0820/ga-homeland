#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M1.5 交换日记写页生成器（手动触发版；22:30 窗口定时接入 M2）。

用法（GA 系统 python，非 server venv）：
    python3 ops/gen_diary_page.py --actor xiazhou          # 有主人回信→翻回信写新页
    python3 ops/gen_diary_page.py --actor qinche --force   # 无回信也强制写新页
    python3 ops/gen_diary_page.py --actor shenxinghui --dry-run  # 只看 prompt

骨架复用 gen_note_reply.py（persona/会话解析/API/记忆召回）；写页节奏：
- 有 pending 主人回信 → 必写（回应回信 + 写新页），并标记 unread done
- 无回信 → 距上页 <36h 跳过（防写太密，目标 2~3 天一页），--force 可越过
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_note_reply import (  # noqa: E402  复用 M1 骨架
    CHAR_NAME, DEFAULT_GA_ROOT, api, setup_ga, resolve_char_session,
)

MIN_HOURS_BETWEEN_PAGES = 36
PAGE_LEN = (120, 320)   # 日记成篇：字数区间（比便签 40~90 长）


def parse_mood(raw):
    """从末行『心情：xxx』解析心情标签；没有则返回 (正文, None)。"""
    m = re.search(r"\n\s*心情[：:]\s*([^\n]{1,12})\s*$", raw.strip())
    if not m:
        return raw.strip(), None
    body = raw.strip()[: m.start()].strip()
    return body, m.group(1).strip()


def clean_page(text):
    t = text.strip().strip('「」"“”\'').strip()
    t = re.sub(r"\n{3,}", "\n\n", t)          # 收敛空行，保留段落感
    return t[:2000]


def last_page(base, char_id):
    r = api(base, "GET", f"/api/diary/entries?actor_id={char_id}&limit=1")
    return (r.get("entries") or [None])[0]


def hours_since(ts):
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return 9999.0
    return (datetime.now() - dt).total_seconds() / 3600



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

def build_prompt(persona, memory, char_cn, last_pages, master_replies, lore=""):
    mem = (f"\n{memory}\n" if memory else "")
    if last_pages:
        p = last_pages[0]
        ctx = f"\n你上一页日记（{p['created_at'][:16]}）写的是：\n{p['content'][:400]}\n"
    else:
        ctx = "\n这是这个日记本的第一页。\n"
    if master_replies:
        quoted = "\n".join(
            f"你上一页下面，小墨回了信（{r['content']}）" for r in master_replies
        )
        ctx += f"\n今晚翻开日记本，你读到了小墨的回信：\n{quoted}\n新的一页要自然地回应这封信——像真的翻到她的回信后提笔回应那样。\n"
    else:
        ctx += "\n今晚小墨没有新的回信，你就写平常的一页。\n"
    lore_seg = (f"\n【补充设定（世界知识参考）】\n{lore}\n" if lore else "")
    return (
        f"{persona}{lore_seg}\n"
        "——— 以下是「交换日记」场景 ———\n"
        "你和主人（她叫小墨，你的恋人）共有一本交换日记本，放在家里。"
        "你隔两三天会在深夜写一页：写日常、写心事、也会偷偷写下关于她的事。"
        f"现在轮到{char_cn}写新的一页。\n"
        f"{mem}\n"
        f"{ctx}\n"
        f"请以{char_cn}本人身份写这一页日记：\n"
        "- 口吻、称呼、性格、说话习惯严格按你的人设来\n"
        "- 成篇日记体：{PAGE_LEN[0]}~{PAGE_LEN[1]} 字，可分 2~3 段，第一人称\n"
        "- 延续你上一页的叙事（若有），不要重复已写过的事\n"
        "- 夜深人静写日记最出真情：可以有情绪积累、小脆弱、小心思，但不过火\n"
        "- 只输出日记正文；最后单独一行输出『心情：xx』（2~6 字心情标签）\n"
        "- 不要旁白、不要引号、不要署名\n"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--actor", default="xiazhou", help="personas 文件名")
    ap.add_argument("--api", default="http://127.0.0.1:7842")
    ap.add_argument("--ga-root", default=DEFAULT_GA_ROOT)
    ap.add_argument("--force", action="store_true", help="无回信/间隔不足也强制写")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--lore-file", help="世界知识文件路径（persona 之后注入；不传则置空）")
    args = ap.parse_args()

    if args.actor not in CHAR_NAME:
        raise SystemExit(f"[gen_diary_page] 未知角色 {args.actor}，可选：{list(CHAR_NAME)}")
    char_cn = CHAR_NAME[args.actor]

    actors = api(args.api, "GET", "/api/actors")
    me = next((a for a in actors if f"personas/{args.actor}.md" in (a.get("persona_ref") or "")), None)
    if not me:
        raise SystemExit(f"[gen_diary_page] 服务端找不到 {char_cn}，先跑 seeds/seed_actors.py")

    pend = api(args.api, "GET", f"/api/diary/pending-replies/{me['id']}").get("replies", [])
    last = last_page(args.api, me["id"])
    gap = hours_since(last["created_at"]) if last else 9999.0
    if pend:
        print(f"[gen_diary_page] {char_cn}：翻到 {len(pend)} 封小墨的回信，提笔。")
    elif not args.force and gap < MIN_HOURS_BETWEEN_PAGES:
        print(f"[gen_diary_page] {char_cn}：无新回信，上页仅 {gap:.1f}h 前（<{MIN_HOURS_BETWEEN_PAGES}h），跳过。")
        return 0
    elif not args.force:
        print(f"[gen_diary_page] {char_cn}：无新回信但距上页 {gap:.1f}h，写平常一页。")

    llmcore, char_config, persona = setup_ga(args.ga_root, args.actor)
    sess, sess_name = resolve_char_session(llmcore, args.ga_root, args.actor)
    print(f"[gen_diary_page] {char_cn}(#{me['id']}) 模型≈{sess_name}")

    pages = api(args.api, "GET", f"/api/diary/entries?actor_id={me['id']}&limit=2").get("entries", [])
    recall_key = (pages[0]["content"][:120] if pages else "") + " " + \
                 " ".join(r["content"] for r in pend)
    memory = char_config.recall(recall_key)
    prompt = build_prompt(persona, memory, char_cn, pages, pend, lore_block(args.lore_file))
    if args.dry_run:
        print(prompt)
        return 0

    raw = "".join(sess.raw_ask([{"role": "user", "content": prompt}]))
    body, mood = parse_mood(raw)
    body = clean_page(body)
    print(f"生成（{len(body)}字，心情={mood}）：\n{body}")
    if len(body) < PAGE_LEN[0] // 2:
        print("过短，跳过写入（防串味）")
        return 1

    out = api(args.api, "POST", "/api/diary/entries",
              {"actor_id": me["id"], "content": body, "mood": mood})
    print(f"已写页 ✓ entry#{out['id']}")
    from homeland_ingest import homeland_ingest
    homeland_ingest(char_config, "diary", "日记",
                    f"写了新日记（心情：{mood}；翻了 {len(pend)} 封回信）", body)
    for r in pend:   # 翻过的回信 → 已阅
        api(args.api, "POST", f"/api/unread/{r['unread_id']}/done", {})
    print(f"回信已翻（unread done ×{len(pend)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
