#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「我们的故事」章节生成器（排班/手动触发）。

用法（GA 系统 python，非 server venv）：
    python3 ops/gen_story.py --actor xiazhou                  # 自动挑一部值日故事续写
    python3 ops/gen_story.py --actor xiazhou --all            # 逐本续写所有可写故事（每本独立调一次LLM，互不串）
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
    CHAR_NAME, DEFAULT_GA_ROOT, api, setup_ga, resolve_char_session, ask_llm, tidy_lines,
)
from homeland_ingest import homeland_ingest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / "prompts" / "story_chapter.md").read_text(encoding="utf-8")
VOICE_DIR = ROOT / "恋与深空故事素材" / "LoRA训练资料"
DEFAULT_MIN_HOURS = 18
# 冷却硬地板：上号班每天固定时刻触发，24h 地板会让 gap 永远差几分钟、隔天才能写
# （踩坑实证 09-25：秦彻 22:12 写入 → 次日 22:09 班 gap=23.95h 被拦），主人拍板降为 22h
COOLDOWN_FLOOR_H = 22
BIBLE_CHRONICLE_CAP = 12

# 默认全书文风（stories.style_json.tone 可按书覆盖；多行纯文本，注入模板 {tone_block}）
DEFAULT_TONE = """\
基调：顾漫式恋爱轻喜剧（《微微一笑很倾城》《杉杉来吃》），阳光治愈、生活流心动，日常小事撑戏，拒绝沉重虐恋。
叙事呼吸与场景驻留：一章只把 1~2 个核心生活场景写透（如一顿饭、一次走廊偶遇、一场自习），绝不赶场打卡。允许有大段的闲笔、气氛渲染与感官余韵。
严禁分镜排版：全章写成真正的小说叙事大段（15~30段），严禁一人一句台词连续空行换行的剧本分镜流。对白必须包裹在动作、神态与心理块中。
灵魂技法＝反差萌暗涌：每位男主都是"外表X/内心Y"的两面人（高冷vs幼稚吃醋、毒舌vs心软、慵懒vs暗中紧张）。行动云淡风轻，微动作和眼神早已出卖心思；禁公式化套用"面上…心里…"字眼，靠真实的生理反应与细节表达心动。
旁白自带幽默：叙述者像个风趣的观察者，可以温和吐槽角色，但点到为止。
对话生活化：接地气的日常斗嘴、机锋往来、互怼拆台；没有翻译腔，没有抒情长独白。
多写男主之间的插科打诨、损友互怼、争风吃醋；NPC 配角放心给戏份，制造笑料和烟火气。
心动藏在细节里：拧松的瓶盖、多带的一份早饭、不动声色挡开的人群、一次没接住的对视；甜而不腻，撩而不油。
男主的"霸道"只体现在智商碾压和无微不至的安排，绝无肢体强迫；女主天然呆、嘴笨都可爱。
误会与小波折要可爱向，三段内解开，最好以乌龙/虚惊一场收场；禁车祸失忆绝症第三者等狗血。
科幻设定只做背景板和笑料素材（Evol 用来制造名场面，不用来考据世界观）；严禁剧情滑向悬疑、危机、阴谋主线，禁大段科幻术语轰炸。
允许偶尔的严肃/深情桥段，但必须短，且为下一处笑点或心动瞬间服务。"""


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
                    seg = raw[start:i + 1]
                    seg = re.sub(r"\\\n(?=[nrtu])", r"\\", seg)  # 0929: 渠道折行腰斩转义(\+换行+n → \n), 先重组防游离字母
                    blk = seg.replace("\\\n", "\\n")  # 0928: 修断错转义(\+真实换行 → 字面\n, gemini长文偶发)
                    return json.loads(blk, strict=False)  # strict=False: 容字符串内裸控制字符(gemini长文排版倾向,0928)
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


def write_chapter(story, args, char_cn, sess, char_config, persona, llmcore, sess_name):
    """为单本书续一章：独立组 prompt、独立调用一次 LLM、独立落库。
    dry-run 只打印 prompt；输出不合格抛 SystemExit（由调用方决定中止还是继续下一本）。"""
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
    # 0925修: 列表端点章节dict无summary键(仅单章full端点有), len(chaps)>=3时直接取会KeyError
    prev_summary = None
    if len(chaps) >= 3:
        c3 = chaps[-3]
        prev_summary = c3.get("summary")
        if not prev_summary:
            try:
                prev_summary = api(args.api, "GET", f"/api/stories/{sid}/chapters/{c3['id']}").get("summary")
            except Exception:
                prev_summary = None
    prev_summary = prev_summary or "（无；你就是开篇）"

    if not chaps:
        mode_block = ("【本章是全书开篇】\n由你为这部 IF 小说写下第一章："
                      "交代切入局面，让读者（user 身份）自然登场，埋下第一个钩子。")
    else:
        mode_block = f"【本章是续写（第 {idx} 章）】\n接住上一章章末的钩子推进剧情。"


    recall_key = (bible.get("current_scene") or "") + " " + (chaps[-1]["title"] if chaps else "") + " 我们的故事"
    memory = char_config.recall(recall_key)
    prompt = TEMPLATE.format(
        persona=persona, title=story["title"], background=story["background"] or "（自由发挥）",
        char_cn=char_cn, user_identity=story["user_identity"] or "与 cast 关系由你定义",
        voice_profile=voice_profile(args.actor), bible_block=bible_block(bible),
        tone_block=story.get("style", {}).get("tone") or DEFAULT_TONE,
        prev_summary=prev_summary, recent_block=recent_block, notes_block=notes_block,
        mode_block=mode_block, recall_mem=memory or "（无相关记忆）",
        idx=idx, words_range=words_range,
    )
    if args.dry_run:
        print(f"[dry-run] {char_cn} · 故事{sid}《{story['title']}》第{idx}章 · {words_range}字 · 纸条{len(notes)}条")
        print(prompt)
        return 0

    raw = ask_llm(llmcore, args.ga_root, args.actor, prompt, sess=sess, sess_name=sess_name)
    if raw is None:
        print("[fail] 模型双渠道失败，本章不写")
        return 0
    out = parse_json_out(raw)
    if out is None:  # 一次纠偏机会
        raw2 = ask_llm(llmcore, args.ga_root, args.actor, [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "上一条输出无法解析。请严格只输出一个合法 JSON 对象（不要代码围栏/解释），字段照契约。"},
        ], sess=sess, sess_name=sess_name)
        if raw2 is None:
            print("[fail] 纠偏双渠道失败，本章不写")
            return 0
        out = parse_json_out(raw2)
    if not isinstance(out, dict) or not (out.get("content") or "").strip() or len(out["content"]) < 300:
        head = ((out.get("content") or "") if isinstance(out, dict) else raw)[:300]
        raise SystemExit(f"[gen_story] 模型输出不合格（content<300字或解析失败）。头300字：\n{head}")

    # 0930修: title/summary同样过tidy_lines(渠道90B定宽折行让title带裸换行: #44"灯\n市辨草糖栗香"/#47"会诊在挑\n台"实证)
    title = tidy_lines(_denorm(out.get("title") or f"第{idx}章").strip())[:60]
    # 0929修: 渠道偶发按~90字节定宽折行长JSON, strict=False让裸换行存活进content(半截句提行)
    # 入库前tidy_lines合并句中断裂换行(0928朋友圈同款修复,补齐story链路)
    content = tidy_lines(_denorm(out["content"]).strip())
    summary = tidy_lines(_denorm(out.get("summary") or content[:120]).strip())
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



def main():
    ap = argparse.ArgumentParser(description="我们的故事 · 章节生成器")
    ap.add_argument("--actor", required=True, help="personas 文件名（slug）")
    ap.add_argument("--story-id", type=int, help="指定故事 id（默认挑你参加的第一个可写故事）")
    ap.add_argument("--all", dest="all_stories", action="store_true",
                    help="逐本续写你参加的所有可写故事（每本独立调用一次 LLM，互不共享上下文；默认只写第一本可写的）")
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

    llmcore, char_config, persona = setup_ga(args.ga_root, args.actor)
    sess, sess_name = resolve_char_session(llmcore, args.ga_root, args.actor)  # 1012: 故事跟随角色主聊天模型(char_models.json); 旧_story固定键退役(备份在temp/bak_1012_qinche_lora)

    written, skipped, failed = [], [], []
    for cand in duty:
        detail = api(args.api, "GET", f"/api/stories/{cand['id']}")
        # 共写模式：cast 内谁上号谁写自己那章；门禁=该作者在本书的上一章冷却
        mine = [c for c in detail["chapters"]
                if c["kind"] == "chapter" and c["author_slug"] == args.actor]
        if mine and not args.force:
            min_h = detail.get("style", {}).get("min_hours", DEFAULT_MIN_HOURS)
            gap = hours_since(mine[-1]["created_at"])
            if gap < max(min_h, COOLDOWN_FLOOR_H):
                skipped.append(detail["title"])
                print(f"[gen_story] 故事{detail['id']}《{detail['title']}》你已写过/冷却中"
                      f"（{gap:.1f}h/{max(min_h, COOLDOWN_FLOOR_H)}h），跳过")
                continue
        try:
            write_chapter(detail, args, char_cn, sess, char_config, persona, llmcore, sess_name)
            written.append(detail["title"])
        except SystemExit as e:
            if not args.all_stories:
                raise  # 默认模式：保持旧行为，失败即中止
            failed.append(detail["title"])
            print(f"[gen_story] ✗ 故事{detail['id']}《{detail['title']}》本轮失败，继续下一本：{e}")
            continue
        except Exception as e:
            if not args.all_stories:
                raise
            failed.append(detail["title"])
            print(f"[gen_story] ✗ 故事{detail['id']}《{detail['title']}》异常，继续下一本：{e}")
            continue
        if not args.all_stories:
            break  # 默认模式：写完第一本可写的即收工（含跳过冷却书后自然推进）

    if args.all_stories:
        print(f"[gen_story] --all 收工：成功 {len(written)} 本（{'/'.join(written) or '无'}）；"
              f"冷却跳过 {len(skipped)} 本；失败 {len(failed)} 本")
        return 1 if (failed and not written) else 0
    if written:
        return 0
    print("[gen_story] 本轮无可写故事，收工")
    return 0


if __name__ == "__main__":
    sys.exit(main())
