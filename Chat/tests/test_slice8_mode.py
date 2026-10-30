#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切片8(见面模式L2状态机)单元测试 — B4式: DB隔离+正反样本+import后直调函数(不TestClient防worker)

跑法: python3 tests/test_slice8_mode.py  (cwd=Chat/)
覆盖: _MODE_RE校验 / _get_meet_state坏值自愈 / _fmt_since跨天边界 / _state_line两态 /
      mode_post全校验+meet再入保since+退出存chat态+ended_at(1030-TE) / _digest带mode /
      mode_get单查全量 / _state_line退出事件只注一次(noted翻位,1030-TE2) / 共享协议块(1030-TE2)
"""
import asyncio, json, os, sys, sqlite3, tempfile, time

CHAT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CHAT_DIR)
sys.path.insert(0, os.path.dirname(CHAT_DIR))   # 共享模块(ensure_single_instance等)

import chatapp

# ---- DB隔离: 指到临时库, 绝不碰真chat.db ----
_tmp = tempfile.mkdtemp(prefix="chat_slice8_test_")
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

def sl(char, now=None):   # 1030-TE2: _state_line改async(注入即翻位noted)
    return asyncio.run(chatapp._state_line(char, now=now))

print("== 1. _MODE_RE 校验(B4式正反样本) ==")
for good in ("chat", "meet"):
    check(f"mode={good!r} 应过", bool(chatapp._MODE_RE.match(good)))
for bad in ("Chat", "chat ", " meet", "meet2", "", "offline", "CHAT"):
    check(f"mode={bad!r} 应拒", not chatapp._MODE_RE.match(bad))

print("== 2. _get_meet_state 坏值自愈 ==")
check("无记录→chat", chatapp._get_meet_state("xiazhou") == {"mode": "chat"})
chatapp._meta_set("meet_state:xiazhou", "not-json{{{")
check("坏JSON→chat", chatapp._get_meet_state("xiazhou") == {"mode": "chat"})
chatapp._meta_set("meet_state:xiazhou", json.dumps({"mode": "evil"}))
check("mode=evil→chat", chatapp._get_meet_state("xiazhou") == {"mode": "chat"})
chatapp._meta_set("meet_state:xiazhou", json.dumps({"mode": "meet", "scene": "书房"}))
st = chatapp._get_meet_state("xiazhou")
check("mode=meet原样", st.get("mode") == "meet" and st.get("scene") == "书房", str(st))
check("_mode_of", chatapp._mode_of("xiazhou") == "meet" and chatapp._mode_of("qiyu") == "chat")

print("== 3. _fmt_since 跨天边界(固定now) ==")
NOW = time.mktime(time.strptime("2026-10-28 15:00:00", "%Y-%m-%d %H:%M:%S"))
cases = [
    (NOW - 30,           "始于今天14:59·刚开始"),
    (NOW - 5 * 60,       "始于今天14:55·已持续5分钟"),
    (NOW - 3 * 3600,     "始于今天12:00·已持续3小时"),
    (NOW - 14 * 3600,    "始于今天01:00·已持续14小时"),
    (time.mktime(time.strptime("2026-10-28 09:05:00", "%Y-%m-%d %H:%M:%S")), "始于今天09:05·已持续5小时"),
    (time.mktime(time.strptime("2026-10-27 23:05:00", "%Y-%m-%d %H:%M:%S")), "始于昨晚23:05·已持续15小时"),
    (time.mktime(time.strptime("2026-10-27 10:00:00", "%Y-%m-%d %H:%M:%S")), "始于昨天10:00·已持续29小时"),
    (time.mktime(time.strptime("2026-10-26 07:30:00", "%Y-%m-%d %H:%M:%S")), "始于前天07:30·已持续55小时"),
    (time.mktime(time.strptime("2026-10-23 08:00:00", "%Y-%m-%d %H:%M:%S")), "始于10-23 08:00·已持续127小时"),
]
for ts, want in cases:
    got = chatapp._fmt_since(ts, NOW)
    check(f"{want}", got == want, f"got={got!r}")

print("== 4. _state_line 两态 ==")
l = sl("qiyu", NOW)
check("chat态", l.startswith("[状态: 隔屏聊天 · 你们不在同一空间, 通过手机联系") and l.endswith("]"),
      repr(l))   # 切片12: 隔屏行尾部现拼作息时段(前缀断言兼容)
chatapp._meta_set("meet_state:qiyu", json.dumps(
    {"mode": "meet", "scene": "书房", "since": NOW - 14 * 3600, "note": "她已睡着"}))
l = sl("qiyu", NOW)
want = "[状态: 意识同步中(你们在同一空间, 面对面) · 场景:书房 · 始于今天01:00·已持续14小时 · 备注:她已睡着 · 历史旧状态行一律作废,以本行为准]"
check("meet态全字段", l == want, repr(l))
chatapp._meta_set("meet_state:qiyu", json.dumps({"mode": "meet"}))
l = sl("qiyu", NOW)
check("meet态裸记录", l == "[状态: 意识同步中(你们在同一空间, 面对面) · 历史旧状态行一律作废,以本行为准]", repr(l))

print("== 5. mode_post 校验(全400路径) ==")
for name, body in [
    ("无char_id", {"mode": "meet"}),
    ("坏char_id", {"char_id": "nobody", "mode": "meet"}),
    ("无mode", {"char_id": "xiazhou"}),
    ("坏mode", {"char_id": "xiazhou", "mode": "video"}),
    ("scene超12字", {"char_id": "xiazhou", "mode": "meet", "scene": "一二三四五六七八九十一二三"}),
    ("scene带换行", {"char_id": "xiazhou", "mode": "meet", "scene": "书房\n客厅"}),
    ("note超40字", {"char_id": "xiazhou", "mode": "meet", "note": "长" * 41}),
]:
    _, sc = post_mode(body)
    check(name, sc == 400, f"status={sc}")
_, sc = post_mode(
    {"char_id": "xiazhou", "mode": "meet", "scene": "一二三四五六七八九十一二"})
check("scene恰12字应过", sc == 200, f"status={sc}")

print("== 6. mode_post 切换闭环 + since保留 + 退出删KV ==")
with chatapp.db() as conn:
    conn.execute("DELETE FROM meta WHERE key LIKE 'meet_state:%'")
    conn.execute("DELETE FROM messages WHERE char_id='shenxinghui'")
r, sc = post_mode({"char_id": "shenxinghui", "mode": "meet", "scene": "琴房", "note": "试音中"})
check("入meet返回200", sc == 200, f"status={sc}")
kv = chatapp._meta_get("meet_state:shenxinghui")
check("meta KV落库", kv and json.loads(kv)["scene"] == "琴房", str(kv))
with chatapp.db() as conn:
    row = conn.execute("SELECT content FROM messages WHERE char_id='shenxinghui' "
                       "AND kind='system' ORDER BY seq DESC LIMIT 1").fetchone()
check("system分隔行", row and row["content"] == "—— 意识同步开始 · 场景:琴房 ——", str(row))
since1 = json.loads(kv)["since"]
time.sleep(1.1)
r2 = post_mode({"char_id": "shenxinghui", "mode": "meet", "scene": "客厅"})   # 再入=换场景
since2 = json.loads(chatapp._meta_get("meet_state:shenxinghui"))["since"]
check("再入meet保since", since2 == since1, f"{since1} vs {since2}")
check("换场景生效", json.loads(chatapp._meta_get("meet_state:shenxinghui"))["scene"] == "客厅")
r3, sc3 = post_mode({"char_id": "shenxinghui", "mode": "chat"})
check("退chat返回200", sc3 == 200)
kv3 = json.loads(chatapp._meta_get("meet_state:shenxinghui") or "null")   # 1030-TE: 退出不再删KV
check("退出存chat态", bool(kv3) and kv3.get("mode") == "chat", str(kv3))
check("退出stamp ended_at", kv3.get("ended_at", 0) > 0, str(kv3))
check("退出清scene", kv3.get("scene") == "", str(kv3))
post_mode({"char_id": "shenxinghui", "mode": "chat"})   # 纯chat切换不应覆盖旧stamp
kv3c = json.loads(chatapp._meta_get("meet_state:shenxinghui") or "null")
check("纯chat切换不覆盖ended_at", kv3c.get("ended_at") == kv3.get("ended_at"), str(kv3c))
with chatapp.db() as conn:
    row = conn.execute("SELECT content FROM messages WHERE char_id='shenxinghui' "
                       "AND kind='system' ORDER BY seq DESC LIMIT 1").fetchone()
check("结束分隔行", row and row["content"] == "—— 意识同步结束，回到隔屏聊天 ——", str(row))

print("== 7. _digest 带mode / mode_get 单查全量 ==")
post_mode({"char_id": "lishen", "mode": "meet", "scene": "医院走廊"})
chatapp._meta_set("meet_state:qinche", "garbage{{{")   # 坏KV应自愈为chat
dig = {o["char_id"]: o for o in chatapp._digest()}
check("digest meet", dig["lishen"]["mode"] == "meet")
check("digest chat", dig["xiazhou"]["mode"] == "chat" and dig["shenxinghui"]["mode"] == "chat")
check("digest 坏KV→chat", dig["qinche"]["mode"] == "chat")
g = chatapp.mode_get(char_id="lishen")
check("mode_get单查", g["mode"] == "meet" and g["scene"] == "医院走廊", str(g))
check("mode_get坏char", chatapp.mode_get(char_id="nobody").status_code == 400)
allm = chatapp.mode_get()
check("mode_get全量", allm["chars"]["lishen"] == "meet" and allm["chars"]["xiazhou"] == "chat", str(allm))

print("== 8. 1030-TE2: 退出meet后'同步已结束'只注一次(noted翻位) ==")
now = time.time()
chatapp._meta_set("meet_state:shenxinghui", json.dumps(
    {"mode": "chat", "scene": "", "since": 0.0, "note": "", "ended_at": now - 1800}))
l1 = sl("shenxinghui")
check("首次:同步已结束事件行", "意识同步已于" in l1 and "不要用括号" in l1
      and "不要责怪她的离开" in l1, l1)
check("首次:作废句", "历史旧状态行一律作废" in l1, l1)
kv = chatapp._get_meet_state("shenxinghui")
check("首次:KV已翻位noted且ended不丢", kv.get("noted") is True and kv.get("ended_at") == now - 1800, str(kv))
l2 = sl("shenxinghui", now=now + 3600)
check("第二次:回基础行不重复事件", l2.startswith("[状态: 隔屏聊天") and "意识同步已于" not in l2, l2)
check("第二次:noted保持", chatapp._get_meet_state("shenxinghui").get("noted") is True, "")
chatapp._meta_set("meet_state:shenxinghui", json.dumps(
    {"mode": "chat", "scene": "", "since": 0.0, "note": "", "ended_at": "bad"}))
check("坏ended_at自愈回基础行", "隔屏聊天" in sl("shenxinghui"), "")

print("== 9. 1030-TE2: 意识同步协议上收共享块(_meet_format.md随load_persona注入) ==")
from char_config import CharCtx
_PDIR = os.path.join(chatapp.GA_ROOT, "assets", "personas")
for _ch in ("xiazhou", "shenxinghui", "lishen", "qiyu", "qinche"):
    _body = open(os.path.join(_PDIR, _ch + ".md"), encoding="utf-8").read()
    check(f"{_ch}人设无共享行", "她发出" not in _body and "不用括号写动作/心理/场景描写" not in _body, "")
_full = CharCtx("xiazhou").load_persona()
check("load_persona拼通用协议", "意识同步·通用协议" in _full and "只能由她发起与结束" in _full, "")
check("load_persona仍拼abilities", "通讯台" in _full, "")
check("顺序:人设→abilities→通用协议", 0 < _full.index("通讯台") < _full.index("意识同步·通用协议"), "")
with chatapp.db() as conn:   # 残留清零(1029教训): 测试KV全清防污染生产状态行
    conn.execute("DELETE FROM meta WHERE key LIKE 'meet_state:%'")

print(f"\n{'='*40}\nPASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
