#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切片12(1102-A seamless见面延续)单元测试 — B4式: DB隔离+直调mode_post(不TestClient防worker)

跑法: python3 tests/test_slice12_seamless.py  (cwd=Chat/)
覆盖(审计P1-2): seamless位语义全链——
  quiet纠偏切换写1 / 显式exit清0 / 非quiet真切换=真结束 / 同段承since+scene(无时限) /
  新段since重置 / 旧KV无seamless键兼容(meet态承=同段, chat态=新段) / scene边界(13字过32字拒)
注: post_msg清位(chat态真发消息)涉及LLM不可直调, 由在线自证覆盖(1102已过)
"""
import asyncio, json, os, sys, tempfile, time

CHAT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CHAT_DIR)
sys.path.insert(0, os.path.dirname(CHAT_DIR))   # 共享模块

import chatapp

# ---- DB隔离: 指到临时库, 绝不碰真chat.db ----
_tmp = tempfile.mkdtemp(prefix="chat_slice12_test_")
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

def kv(char):
    return json.loads(chatapp._meta_get(f"meet_state:{char}") or "null")

C = "shenxinghui"

print("== 1. quiet纠偏切换: KV写seamless=1 ==")
post_mode({"char_id": C, "mode": "meet", "scene": "书房"})
k1 = kv(C); since1 = k1["since"]
time.sleep(0.05)
r, sc = post_mode({"char_id": C, "mode": "chat", "quiet": 1})   # 手机PWA重载→前端纠偏
k2 = kv(C)
check("quiet切换200", sc == 200, f"code={sc}")
check("KV留chat态", k2.get("mode") == "chat", str(k2))
check("seamless=1", k2.get("seamless") == 1, str(k2))
check("ended_at落定", k2.get("ended_at", 0) > 0, str(k2))

print("== 2. 重进meet(无时限同段): 承since+scene ==")
time.sleep(2.2)   # 隔任意久(旧300s窗此时必断——事件判据不受影响)
post_mode({"char_id": C, "mode": "meet", "quiet": 1})   # 不带scene=界面重进
k3 = kv(C)
check("同段承since(2.2s前, 旧窗早已超)", k3.get("since") == since1, f"{since1} vs {k3.get('since')}")
check("同段承scene", k3.get("scene") == "书房", str(k3))

print("== 3. 显式exit=真结束: seamless清0+下回新段 ==")
post_mode({"char_id": C, "mode": "chat", "quiet": 1, "exit": 1})   # 前端closeMeet
k4 = kv(C)
check("exit后seamless=0", k4.get("seamless") == 0, str(k4))
t0 = time.time(); post_mode({"char_id": C, "mode": "meet", "quiet": 1}); t1 = time.time()
k5 = kv(C)
check("exit后重进=新段", k5.get("since", 0) >= t0 and k5.get("since", 0) <= t1, str(k5))
check("新段scene空", k5.get("scene") == "", str(k5))

print("== 4. 非quiet真切换(落横幅)=真结束(审计P2-2) ==")
time.sleep(0.05)
post_mode({"char_id": C, "mode": "chat"})   # 不带quiet
k6 = kv(C)
check("非quiet切换seamless=0", k6.get("seamless") == 0, str(k6))

print("== 5. 旧KV无seamless键兼容(生产存量) ==")
chatapp._meta_set(f"meet_state:{C}", json.dumps({"mode": "chat", "scene": "旧居", "since": 1.0, "ended_at": 2.0}))
t0 = time.time(); post_mode({"char_id": C, "mode": "meet", "quiet": 1}); t1 = time.time()
k7 = kv(C)
check("chat态旧KV→新段", t0 <= k7.get("since", 0) <= t1, str(k7))
chatapp._meta_set(f"meet_state:{C}", json.dumps({"mode": "meet", "scene": "旧居", "since": 42.0}))
post_mode({"char_id": C, "mode": "meet", "quiet": 1})
check("meet态旧KV→同段(锁屏任意久)", kv(C).get("since") == 42.0, str(kv(C)))

print("== 6. scene边界(SCENE_MAX=30, 1101放宽) ==")
r13, sc13 = post_mode({"char_id": C, "mode": "meet", "scene": "一二三四五六七八九十一二三"})
check("13字通过", sc13 == 200, f"code={sc13}")
r32, sc32 = post_mode({"char_id": C, "mode": "meet", "scene": "一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十一二"})
check("32字拒绝", sc32 == 400, f"code={sc32}")

print("== 7. exit幂等: chat态重复exit不刷新ended_at ==")
post_mode({"char_id": C, "mode": "chat", "quiet": 1, "exit": 1})
e1 = kv(C).get("ended_at")
time.sleep(0.1)
post_mode({"char_id": C, "mode": "chat", "quiet": 1, "exit": 1})
check("ended_at承旧不刷", kv(C).get("ended_at") == e1, f"{e1} vs {kv(C).get('ended_at')}")

with chatapp.db() as conn:   # 残留清零(1029教训)
    conn.execute("DELETE FROM meta WHERE key LIKE 'meet_state:%'")

print(f"\n{'='*40}\nPASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
