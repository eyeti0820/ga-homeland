#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「我们的故事」章节生成器（排班/手动触发）。

用法（GA 系统 python，非 server venv）：
    python3 ops/gen_story.py --actor xiazhou                  # 自动挑一部值日故事续写
    python3 ops/gen_story.py --actor xiazhou --story-id 1
    python3 ops/gen_story.py --actor xiazhou --dry-run        # 只打印 prompt 不写库
    python3 ops/gen_story.py --actor xiazhou --force          # 忽略冷却/轮次（仍须 enabled+cast 内）

节奏：
    - 多人 cast → 按 cast 顺序轮笔（最后执笔者的下一位写）；
    - 单人 cast → 距上章 min_hours 小时（默认 18，style_json.min_hours 覆盖）；
    - 开篇章由 cast[0] 执笔（开篇模式 prompt）。
写完：章节入库 → bible 滚动摘要回写 → 消化导演纸条 → 记忆宫殿入库（scene=story_if_{sid}）。
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from gen_note_reply import (
    CHAR_NAME, DEFAULT_GA_ROOT, api, setup_ga, resolve_char_session,
)
from homeland_ingest import homeland_ingest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / "prompts" / "story_chapter.md").read_text(encoding="utf-8")
VOICE_DIR = ROOT / "恋与深空故事素材" / "LoRA训练资料"
DEFAULT_MIN_HOURS = 18
BIBLE_CHRONICLE_CAP = 12


def voice_profile(actor: str) -> str:
    """口吻档案（素材库精选层 L1，只读；缺失不阻塞）。"""
    cn = CHAR_NAME[actor]
    p = VOICE_DIR / cn / f"{cn}整理后素材" / "03_通信与日常" / "通信与日常口吻档案.md"
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return "（口吻档案缺失，按本格人设发挥）"


def hours_since(ts: str) -> float:
    try:
        return (datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
    except (TypeError, ValueError):
        return 1e9


def parse_json_out(raw: str):
    """容错解析：剥代码围栏 → 首个配平的 {...}。失败返回 None。"""
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    start = raw.find("{")
    depth = 0
    for i, ch in enumerate(raw[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None



def _denorm(s):
    """模型偶发把换行双转义成字面 \\n 串(json.loads 后仍是两个字符), 统一还原为真换行。"""
    if isinstance(s, str) and "\\n" in s:
        s = s.replace("\\r\\n", "\n").replace("\\n", "\n")
    return s

def bible_block(bible: dict) -> str:
    if not bible:
        return "（故事刚开始，圣经为空：你是第一位执笔者）"
    lines = []
    if bible.get("chronicle"):
        lines.append("大事记：\n" + "\n".join(f"- {x}" for x in bible["chronicle"]))
    if bible.get("relations"):
        lines.append(f"人物关系：{bible['relations']}")
    if bible.get("foreshadow"):
        lines.append("未回收伏笔：\n" + "\n".join(f"- {x}" for x in bible["foreshadow"]))
    if bible.get("current_scene"):
        lines.append(f"当前局面：{bible['current_scene']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="我们的故事 · 章节生成器")
    ap.add_argument("--actor", required=True, help="personas 文件名（slug）")
    ap.add_argument("--story-id", type=int, help="指定故事 id（默认挑你参加的第一个可写故事）")
    ap.add_argument("--api", default="http://127.0.0.1:7842")
    ap.add_argument("--ga-root", default=DEFAULT_GA_ROOT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="忽略冷却与轮次（仍须 enabled 且在 cast 内）")
    args = ap.parse_args()

    if args.actor not in CHAR_NAME:
        raise SystemExit(f"[gen_story] 未知角色 {args.actor}，可选：{list(CHAR_NAME)}")
    char_cn = CHAR_NAME[args.actor]

    duty = api(args.api, "GET", f"/api/stories/duty/{args.actor}").get("stories", [])
    if not duty:
        raise SystemExit("[gen_story] 没有你参加的开放故事（enabled=1 且在 cast）")
    if args.story_id:
        duty = [s for s in duty if s["id"] == args.story_id]
        if not duty:
            raise SystemExit(f"[gen_story] 故事 {args.story_id} 不存在/未开/不在 cast")

    story = None
    for cand in duty:
        detail = api(args.api, "GET", f"/api/stories/{cand['id']}")
        # 共写模式：cast 内谁上号谁写自己那章；门禁=该作者在本书的上一章冷却
        mine = [c for c in detail["chapters"]
                if c["kind"] == "chapter" and c["author_slug"] == args.actor]
        if mine and not args.force:
            min_h = detail.get("style", {}).get("min_hours", DEFAULT_MIN_HOURS)
            gap = hours_since(mine[-1]["created_at"])
            if gap < max(min_h, 24):
                print(f"[gen_story] 故事{detail['id']}《{detail['title']}》你已写过/冷却中"
                      f"（{gap:.1f}h/{max(min_h, 24)}h），跳过")
                continue
        story = detail
        break
    if story is None:
        print("[gen_story] 本轮无可写故事，收工")
        return 0

    sid, cast = story["id"], story["cast"]
    chaps = [c for c in story["chapters"] if c["kind"] == "chapter"]
    idx = max((c["idx"] for c in story["chapters"] if c["kind"] != "master_note"), default=0) + 1
    words = int(story.get("style", {}).get("chapter_words", 1500))
    words_range = f"约{int(words*0.8)}~{int(words*1.2)}"

    notes = api(args.api, "GET", f"/api/stories/{sid}/pending-notes").get("notes", [])
    notes_block = "\n".join(f"- {n['content']}" for n in notes) or "（本章没有纸条，按剧情惯性推进）"

    bible = json.loads(story["bible"] or "{}")
    recent = []
    for c in chaps[-2:]:
        full = api(args.api, "GET", f"/api/stories/{sid}/chapters/{c['id']}")
        recent.append(f"—— 第{full['idx']}章《{full['title']}》（{CHAR_NAME.get(full['author_slug'],'')} 执笔）——\n{full['content']}")
    recent_block = "\n\n".join(recent) or "（没有更早章节）"
    prev_summary = (chaps[-3]["summary"] if len(chaps) >= 3 else None) or "（无；你就是开篇）"

    if not chaps:
        mode_block = ("【本章是全书开篇】\n由你为这部 IF 小说写下第一章："
                      "交代切入局面，让读者（user 身份）自然登场，埋下第一个钩子。")
    else:
        mode_block = f"【本章是续写（第 {idx} 章）】\n接住上一章章末的钩子推进剧情。"

    llmcore, char_config, persona = setup_ga(args.ga_root, args.actor)
    sess, sess_name = resolve_char_session(llmcore, args.ga_root, args.actor)
    recall_key = (bible.get("current_scene") or "") + " " + (chaps[-1]["title"] if chaps else "") + " 我们的故事"
    memory = char_config.recall(recall_key)

    prompt = TEMPLATE.format(
        persona=persona, title=story["title"], background=story["background"] or "（自由发挥）",
        char_cn=char_cn, user_identity=story["user_identity"] or "与 cast 关系由你定义",
        voice_profile=voice_profile(args.actor), bible_block=bible_block(bible),
        prev_summary=prev_summary, recent_block=recent_block, notes_block=notes_block,
        mode_block=mode_block, recall_mem=memory or "（无相关记忆）",
        idx=idx, words_range=words_range,
    )
    if args.dry_run:
        print(f"[dry-run] {char_cn} · 故事{sid}《{story['title']}》第{idx}章 · {words_range}字 · 纸条{len(notes)}条")
        print(prompt)
        return 0

    raw = "".join(sess.raw_ask([{"role": "user", "content": prompt}]))
    out = parse_json_out(raw)
    if out is None:  # 一次纠偏机会
        raw2 = "".join(sess.raw_ask([
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "上一条输出无法解析。请严格只输出一个合法 JSON 对象（不要代码围栏/解释），字段照契约。"},
        ]))
        out = parse_json_out(raw2)
    if not isinstance(out, dict) or not (out.get("content") or "").strip() or len(out["content"]) < 300:
        head = ((out.get("content") or "") if isinstance(out, dict) else raw)[:300]
        raise SystemExit(f"[gen_story] 模型输出不合格（content<300字或解析失败）。头300字：\n{head}")

    title = _denorm(out.get("title") or f"第{idx}章").strip()[:60]
    content = _denorm(out["content"]).strip()
    summary = _denorm(out.get("summary") or content[:120]).strip()
    chapter = api(args.api, "POST", f"/api/stories/{sid}/chapters", {
        "kind": "chapter", "author_slug": args.actor, "title": title,
        "content": content, "summary": summary,
    })

    delta = out.get("bible_delta") or {}
    bible.setdefault("chronicle", [])
    bible["chronicle"] = (bible["chronicle"] + [_denorm(str(x)) for x in delta.get("chronicle", [])])[-BIBLE_CHRONICLE_CAP:]
    if delta.get("relations"):
        bible["relations"] = str(delta["relations"])
    if isinstance(delta.get("foreshadow"), list):
        bible["foreshadow"] = [_denorm(str(x)) for x in delta["foreshadow"]]
    if delta.get("current_scene"):
        bible["current_scene"] = _denorm(str(delta["current_scene"]))
    api(args.api, "PATCH", f"/api/stories/{sid}", {"bible": json.dumps(bible, ensure_ascii=False)})

    for n in notes:
        api(args.api, "PATCH", f"/api/stories/{sid}/chapters/{n['id']}", {"consumed": 1})

    try:
        homeland_ingest(char_config, f"story_if_{sid}", "我们的故事",
                        f"【IF线·{story['title']}】第{idx}章《{title}》（{char_cn}执笔）：{summary}",
                        content)
    except Exception as e:
        print(f"[MP] 家园入库失败(story_if_{sid}): {e}")

    print(f"[gen_story] ✓ 故事{sid}《{story['title']}》第{idx}章《{title}》已入库（{len(content)}字，chapter_id={chapter['id']}）")
    print(f"[gen_story] 圣经已更新（chronicle {len(bible['chronicle'])} 条）；纸条消化 {len(notes)} 条；宫殿 scene=story_if_{sid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
