#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切片9(见面屏)后端补丁单测 — 四补丁: mode_evt标签 / digest过滤

跑法: python3 tests/test_slice9_be.py  (cwd=Chat/)
b补丁(/api/msg user行meta.mode)与c补丁(worker assistant行meta.mode)属落库路径,
   由实弹API阶段验证(见 开发方案_见面模式.md §3.5/§4.5), 本文件只测DB层可直调逻辑。
"""
import asyncio, json, os, sys, tempfile

CHAT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CHAT_DIR)
sys.path.insert(0, os.path.dirname(CHAT_DIR))

import chatapp

# ---- DB隔离: 指到临时库, 绝不碰真chat.db ----
_tmp = tempfile.mkdtemp(prefix="chat_slice9_test_")
chatapp.DB_PATH = os.path.join(_tmp, "test.db")
chatapp.init_db()

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok  {name}")
    else:    FAIL += 1; print(f" FAIL {name}  {detail}")

class FakeRequest:
    def __init__(self, body): self._b = body
    async def json(self): return self._b

def post_mode(body):
    r = asyncio.run(chatapp.mode_post(FakeRequest(body)))
    return (r, r.status_code) if hasattr(r, "status_code") else (r, 200)

def _last_rows(char, n=3):
    with chatapp.db() as conn:
        return conn.execute("SELECT seq,kind,content,meta FROM messages WHERE char_id=? "
                            "ORDER BY seq DESC LIMIT ?", (char, n)).fetchall()

def dig_of(char):
    return next((x for x in chatapp._digest() if x["char_id"] == char), None)

def _meta_of(row):
    return json.loads(row["meta"]) if row["meta"] else {}

C = "shenxinghui"   # 测试专用角色(与切片8一致)

print("== 1. 补丁a: mode系统行打mode_evt标 ==")
post_mode({"char_id": C, "mode": "meet", "scene": "星轨天文台"})
rows = _last_rows(C, 1); m = _meta_of(rows[0])
check("进入行带mode_evt=1", m.get("mode_evt") == 1, str(m))
check("进入行带mode=meet", m.get("mode") == "meet", str(m))
check("进入行仍是system kind", rows[0]["kind"] == "system")

post_mode({"char_id": C, "mode": "chat"})
rows = _last_rows(C, 1); m = _meta_of(rows[0])
check("退出行带mode_evt=1", m.get("mode_evt") == 1, str(m))
check("退出行带mode=chat", m.get("mode") == "chat", str(m))

print("== 2. 补丁d: digest过滤(meet行不进聊天列表) ==")
chatapp._insert_msg(C, "user", "text", "聊天最后一条", meta={"mode": "chat"})
chatapp._insert_msg(C, "assistant", "text", "(他抬头看你)见见面聊聊？", meta={"mode": "meet"})
dig = dig_of(C) or {}
check("digest跳过meet行→回chat行", dig["last_seq"] and dig["preview"] == "聊天最后一条",
      f"preview={dig.get('preview')!r}")
check("digest带mode字段(M1契约不破)", "mode" in dig, str(list(dig.keys())))

print("== 3. 补丁d: mode_evt系统行不进digest ==")
chatapp._insert_msg(C, "user", "text", "再聊一句", meta={"mode": "chat"})
# 模拟再次进入meet: 手插一条带标系统行(与端点产物同构)
chatapp._insert_msg(C, "system", "system", "—— 意识同步开始 · 场景:书房 ——",
                    meta={"mode_evt": 1, "mode": "meet"})
dig = dig_of(C) or {}
check("digest跳过mode_evt行", dig["preview"] == "再聊一句", f"preview={dig.get('preview')!r}")

print("== 4. 补丁d: 历史无meta同步行兜底(M1期残留) ==")
chatapp._insert_msg(C, "user", "text", "兜底前一句", meta={"mode": "chat"})
with chatapp.db() as conn:   # 直接裸插无meta老格式行
    conn.execute("INSERT INTO messages(char_id, role, kind, content, ts) "
                 "VALUES (?, 'system', 'system', '—— 意识同步开始 · 场景:旧版 ——', 1.0)", (C,))
dig = dig_of(C) or {}
check("无meta老同步行也被过滤", dig["preview"] == "兜底前一句", f"preview={dig.get('preview')!r}")

print("== 5. 补丁d: chat态行不受影响 ==")
chatapp._insert_msg(C, "assistant", "text", "普通聊天回复", meta={"mode": "chat"})
dig = dig_of(C) or {}
check("chat行正常进digest", dig["preview"] == "普通聊天回复", f"preview={dig.get('preview')!r}")
pro = chatapp._insert_msg(C, "assistant", "text", "[主动]在吗", meta={"proactive": True})
dig = dig_of(C) or {}
check("chat态proactive行正常显示", dig["last_seq"] == pro, f"seq={dig.get('last_seq')} vs {pro}")


# ==== D7审计修复回归 (1028): P1-2/P2-1/P2-3/P2-6/P2-9 ====
print("== D7-1. P1-2: assistant行mode锚定user发送行 ==")
u1 = chatapp._insert_msg(C, "user", "text", "见面时说的话", meta={"mode": "meet"})
u2 = chatapp._insert_msg(C, "user", "text", "回chat后说的话", meta={"mode": "chat"})
check("user行meta=meet→取meet", chatapp._mode_of_user_row(u1) == "meet")
check("user行meta=chat→取chat", chatapp._mode_of_user_row(u2) == "chat")
u3 = chatapp._insert_msg(C, "user", "text", "无meta老行")
check("无meta→None(现读兜底)", chatapp._mode_of_user_row(u3) is None)
check("不存在的seq→None", chatapp._mode_of_user_row(999999) is None)
with chatapp.db() as conn:
    conn.execute("UPDATE messages SET meta='{bad json' WHERE seq=?", (u3,))
check("坏meta→None不炸", chatapp._mode_of_user_row(u3) is None)

print("== D7-2. P2-3: digest遇坏meta JSON不炸 ==")
chatapp._insert_msg(C, "assistant", "text", "坏meta正文行", meta="{bad")
dig = dig_of(C)
check("坏meta行被digest跳过不raise", dig is not None, "crash?")
chatapp._insert_msg(C, "assistant", "text", "好meta行", meta='{"mode": "chat"}')
dig2 = dig_of(C)
check("坏meta后digest仍工作", dig2 and dig2["preview"] == "好meta行", str(dig2 and dig2.get("preview")))

print("== D7-3. P2-1: note控制字符被拒 ==")
r, code = post_mode({"char_id": C, "mode": "meet", "scene": "星轨天文台", "note": "备注\u0000带控制符"})
check("note含控制符→400", code == 400, f"code={code}")

print("== D7-4. P2-6: digest带scene(供openMeet恢复) ==")
check("digest含scene字段", dig2 and "scene" in dig2, str(list(dig2.keys()) if dig2 else None))
post_mode({"char_id": C, "mode": "meet", "scene": "星轨天文台"})
dig_m = dig_of(C)
check("meet态scene=星轨天文台", dig_m and dig_m.get("scene") == "星轨天文台", str(dig_m and dig_m.get("scene")))
post_mode({"char_id": C, "mode": "chat"})
dig3 = dig_of(C)
check("chat态scene=空串", dig3 and dig3.get("scene") == "", str(dig3 and dig3.get("scene")))

print("== D7-5. P2-6b: system行落库带mode标 ==")
chatapp._insert_msg(C, "system", "system", "(一条语音转码失败, 内容丢失)",
                    meta={"mode": chatapp._mode_of(C)})
rows = _last_rows(C, 1); m = _meta_of(rows[0])
check("system行带mode标", m.get("mode") in ("chat", "meet"), str(m))

print("== D7-6. P2-9: 状态行自带作废声明+坏state回退chat ==")
chatapp._meta_set(chatapp._meet_key(C), json.dumps({"mode": "meet", "scene": "静室"}))
line = asyncio.run(chatapp._state_line(C))
check("meet态状态行含作废声明", "作废" in line and "静室" in line, line)
chatapp._meta_set(chatapp._meet_key(C), json.dumps({"scene": "只有场景没mode"}))
check("无mode坏state→回退chat行", "隔屏聊天" in asyncio.run(chatapp._state_line(C)), "no fallback")
with chatapp.db() as conn:
    conn.execute("DELETE FROM meta WHERE key=?", (chatapp._meet_key(C),))

print("== D7-7. 1030-TE3: /api/history mode=chat 过滤+游标分页(与digest口径共用) ==")
H = "shenxinghui"
with chatapp.db() as conn:
    conn.execute("DELETE FROM messages WHERE char_id=?", (H,))
chatapp._insert_msg(H, "user", "text", "chat甲")
chatapp._insert_msg(H, "assistant", "text", "meet乙", meta={"mode": "meet"})
chatapp._insert_msg(H, "assistant", "text", "evt丙", meta={"mode_evt": "meet"})
chatapp._insert_msg(H, "system", "system", "—— 意识同步开始 ——(琴房)")
chatapp._insert_msg(H, "user", "text", "chat戊")
hist = chatapp.history(char_id=H, before_seq=0, limit=50, mode="chat")
conts = [m["content"] for m in hist["msgs"]]
check("mode=chat只回chat行(meet/evt/横幅全滤)", conts == ["chat甲", "chat戊"], str(conts))
check("mode=chat返回seq升序", hist["msgs"][0]["seq"] < hist["msgs"][-1]["seq"], "应升序")
raw = [m["content"] for m in chatapp.history(char_id=H, before_seq=0, limit=50)["msgs"]]
check("默认原始行含meet/evt/横幅", "meet乙" in raw and "evt丙" in raw and any("—— 意识同步开始" in c for c in raw), str(raw))
seq_wu = hist["msgs"][-1]["seq"]
page1 = [m["content"] for m in chatapp.history(char_id=H, before_seq=seq_wu, limit=1, mode="chat")["msgs"]]
check("游标分页(before_seq分支)同样滤meet", page1 == ["chat甲"], str(page1))

print("=" * 40)
print(f"PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
