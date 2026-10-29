#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切片12(五角色作息注入)单元测试 — B4式: DB隔离+正反样本+import后直调

跑法: python3 tests/test_slice12_schedule.py  (cwd=Chat/)
覆盖: SCHEDULE加载 / _state_line隔屏拼时段(工作日·跨午夜·周末·睡眠) /
      _state_line见面分支不受作息污染 / _proactive_gates per-char缩窗 /
      overlay_text五角色非空且含作息节
"""
import datetime as _dt
import os, sys, tempfile

CHAT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CHAT_DIR)
sys.path.insert(0, os.path.dirname(CHAT_DIR))

import chatapp

_tmp = tempfile.mkdtemp(prefix="chat_slice12_test_")
chatapp.DB_PATH = os.path.join(_tmp, "test.db")
chatapp.init_db()

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok  {name}")
    else:    FAIL += 1; print(f" FAIL {name}  {detail}")

def ts(y, mo, d, h, mi):
    return _dt.datetime(y, mo, d, h, mi).timestamp()

# ---- 1. SCHEDULE 模块加载 ----
check("schedule已加载(5角色)", bool(chatapp.SCHEDULE) and len(chatapp.SCHEDULE.SCHEDULES) == 5,
      str(getattr(chatapp, "SCHEDULE", None))[:80])

# ---- 2. _state_line 隔屏分支拼作息 ----
line = chatapp._state_line("lishen", now=ts(2026, 10, 29, 14, 30))   # 周四下午=查房/门诊
check("黎深周四14:30含时刻", "14:30" in line and "周四" in line, line)
check("黎深周四14:30含时段活动", "查房" in line or "门诊" in line, line)
check("黎深周四14:30为隔屏态", "隔屏聊天" in line, line)

line = chatapp._state_line("shenxinghui", now=ts(2026, 10, 29, 0, 1))  # 周四00:01=睡觉(跨午夜段)
check("沈星回00:01判定睡觉", "睡" in line, line)

line = chatapp._state_line("qinche", now=ts(2026, 10, 29, 12, 30))   # 周四午=昼伏夜出
check("秦彻12:30判定睡觉", "睡" in line, line)

line = chatapp._state_line("xiazhou", now=ts(2026, 10, 29, 19, 0))   # 周四晚=回天行的家
check("夏以昼19:00回天行的家", "天行" in line, line)
check("夏以昼19:00不提云际列车(防天天长途)", "云际列车" not in line, line)

line = chatapp._state_line("xiazhou", now=ts(2026, 10, 31, 14, 30))  # 周六=周末模式
check("周六注入周末态", "周末" in line, line)
check("周六状态行无工作日时刻段", ("-" not in line.split("(")[-1]), line)   # 1029主人: 周末可值班巡航, 断言只卡"不拼工作日时段"

line = chatapp._state_line("lishen", now=ts(2026, 10, 29, 6, 45))    # 晨跑段
check("黎深06:45晨跑(主人补录段)", "晨跑" in line, line)

# ---- 3. 见面分支不受作息污染 ----
_orig = chatapp._get_meet_state
chatapp._get_meet_state = lambda c: {"mode": "meet", "scene": "客厅", "since": 0, "note": ""}
try:
    line = chatapp._state_line("lishen", now=ts(2026, 10, 29, 14, 30))
    check("见面态=意识同步中", "意识同步中" in line and "场景:客厅" in line, line)
    check("见面态不拼作息时段", "查房" not in line, line)
finally:
    chatapp._get_meet_state = _orig

# ---- 4. _proactive_gates per-char 缩窗(小时短路不触DB) ----
ok, why = chatapp._proactive_gates("shenxinghui", ts(2026, 10, 29, 9, 0))
check("沈星回09:00被作息窗拦下", not ok and "作息活跃段" in why, why)
ok, why = chatapp._proactive_gates("qinche", ts(2026, 10, 29, 12, 0))
check("秦彻12:00被作息窗拦下", not ok and "作息活跃段" in why, why)
ok, why = chatapp._proactive_gates("shenxinghui", ts(2026, 10, 29, 12, 0))
check("沈星回12:00过小时门(后续门reason≠作息)", not ("作息活跃段" in why), why)  # 12:00在活跃窗
ok, why = chatapp._proactive_gates("xiazhou", ts(2026, 10, 29, 5, 0))
check("夏以昼05:00全局门拦(8点前)", not ok and "非活跃时段" in why and "作息活跃段" not in why, why)

# ---- 5. overlay 五角色 ----
for c in ["xiazhou", "shenxinghui", "qiyu", "qinche", "lishen"]:
    ov = chatapp.SCHEDULE.overlay_text(c)
    check(f"overlay({c})非空含作息节", bool(ov) and "作息" in ov and "主人的消息永远要回" in ov,
          (ov or "")[:60])



# ---- 6. 切片12.1: _refresh_schedule_overlay 每轮换版 ----
class _FakeAgent:
    def __init__(self, ps): self.extra_sys_prompts = ps

_ov = chatapp.SCHEDULE.overlay_text("lishen")
_a = _FakeAgent([_ov])                       # 已是当天版 -> 原样不动
chatapp._refresh_schedule_overlay(_a, "lishen")
check("refresh当天版原样", _a.extra_sys_prompts == [_ov], str(_a.extra_sys_prompts))
_b = _FakeAgent([_ov + "\n- 过期行"])        # stale版 -> 被换新
chatapp._refresh_schedule_overlay(_b, "lishen")
check("refresh换掉stale版", _b.extra_sys_prompts == [_ov], str(_b.extra_sys_prompts))
_c = _FakeAgent([])                          # 未注 -> 补注
chatapp._refresh_schedule_overlay(_c, "lishen")
check("refresh补注缺失", _c.extra_sys_prompts == [_ov], str(_c.extra_sys_prompts))
_d = _FakeAgent(["- 别的prompt不动", _ov])
chatapp._refresh_schedule_overlay(_d, "lishen")
check("refresh不伤邻位", _d.extra_sys_prompts == ["- 别的prompt不动", _ov], str(_d.extra_sys_prompts))
print(f"\n[PASS {PASS} / FAIL {FAIL}]")
sys.exit(1 if FAIL else 0)
