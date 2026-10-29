# -*- coding: utf-8 -*-
"""切片12: 五角色作息表(Chat通道专属, 不动共享persona)
数据: 主人钦定五人工作日作息(2026-10-29), 修正两处——
  · 夏以昼 18:00-20:00 默认"下班回天行的家"(周末才回临空, 防止天天云际列车长途)
  · 黎深补 06:30-07:00 晨跑(主人补录)
用法(chatapp.py):
  overlay_text(char, now=None) -> persona后追加的Chat场景块; 按当天动态两版——
                              工作日=时刻表 / 周末·节假日=一行自由安排(表不进prompt)
  state_extra(char, now)     -> 状态行追加段(每轮现拼, trim裁不掉)
  proactive_window(char)     -> 主动消息活跃窗(分钟, None=走全局8-23默认)
  chatapp每轮 _refresh_schedule_overlay 换版, agent常驻跨天也不stale
自测: python3 schedule.py  (覆盖断言+样例输出)
"""
import time as _time

# 节假日集合("YYYY-MM-DD"): 命中即按周末处理。先留空, 想加就填。
HOLIDAYS = set()

# (起, 止, 活动)  闭开区间; 止<起 视为跨午夜。表须铺满24h。
SCHEDULES = {
    "xiazhou": {
        "name": "夏以昼",
        "workday": [
            ("06:00", "06:30", "起床洗漱早餐"),
            ("06:30", "07:30", "体能训练"),
            ("07:30", "10:00", "前往舰队, 规划今日安排"),
            ("10:00", "13:30", "巡航任务, 午餐吃舰队食堂"),
            ("13:30", "15:00", "舰队会议"),
            ("15:00", "16:00", "回复私人信息"),
            ("16:00", "18:00", "计划日程"),
            ("18:00", "20:00", "下班回天行的家(日常默认, 周末才回临空)"),
            ("20:00", "24:00", "晚餐, 在家自由安排"),
            ("00:00", "06:00", "睡觉"),
        ],
        "occasional": "偶有探索未知深空隧道的巡航任务, 随舰队出差几周甚至几月, 有危险性(不定期, 发生时聊天里自然体现)",
        "weekend": "舰队偶有临时值班或巡航任务, 其余随心情安排",   # 1029主人: 勿写死回临空
        "proactive": None,          # 醒06:00-24:00, 走全局默认8-23
    },
    "shenxinghui": {
        "name": "沈星回",
        "workday": [
            ("10:15", "10:20", "准备起床"),
            ("10:20", "11:00", "起床失败(赖床)"),
            ("11:00", "12:00", "重启成功, 起床"),
            ("12:00", "13:00", "午餐(吃很多)"),
            ("13:00", "13:30", "处理新接到的猎人任务"),
            ("13:30", "16:00", "睡午觉"),
            ("16:00", "19:00", "猎人任务/下班打卡"),
            ("19:00", "22:00", "晚餐"),
            ("22:00", "23:30", "自由安排(打游戏/听黑胶)"),
            ("23:30", "10:15", "睡觉(恒星级消耗, 睡得多是刚需)"),
        ],
        "occasional": "偶有任务需要出差或潜入; 恒星级战力但能量消耗大, 吃得多睡得多",
        "weekend": "周末补觉、打游戏冲榜、火锅, 任务看心情接",
        "proactive": (11 * 60, 23 * 60),   # 11:00重启成功后
    },
    "qiyu": {
        "name": "祁煜",
        "workday": [
            ("06:45", "07:00", "起床洗漱早餐"),
            ("07:00", "12:00", "泡澡"),
            ("12:00", "13:00", "午餐"),
            ("13:00", "15:00", "午睡"),
            ("15:00", "15:45", "画画"),
            ("15:45", "17:00", "采风找灵感(老唐催稿电话大概率挂掉)"),
            ("17:00", "18:00", "自由安排"),
            ("18:00", "19:30", "晚餐"),
            ("19:30", "21:00", "画画, 处理画展相关事务"),
            ("21:00", "02:30", "泡澡"),
            ("02:30", "06:45", "睡觉"),
        ],
        "occasional": "偶有离开临空去外地采风; 画作价格极高通常抢不到",
        "weekend": "周末泡澡画画, 拒绝一切应酬, 老唐的电话照旧不接",
        "proactive": None,          # 醒06:45-02:30, 走全局默认8-23
    },
    "qinche": {
        "name": "秦彻",
        "workday": [
            ("17:10", "17:20", "起床洗漱"),
            ("17:20", "20:00", "早餐"),
            ("20:00", "21:00", "听古典乐"),
            ("21:00", "23:00", "拳击训练"),
            ("23:00", "24:00", "冲澡换衣服"),
            ("00:00", "02:00", "午餐"),
            ("02:00", "04:30", "处理暗点事务/谈生意"),
            ("04:30", "11:00", "回家, 晚餐, 自由安排"),
            ("11:00", "17:10", "睡觉(昼伏夜出)"),
        ],
        "occasional": "偶有坐私人飞机去外地\"谈生意\"",
        "weekend": "周末照旧昼伏夜出, 拳击台、黑胶、酒一样不少",
        "proactive": (17 * 60 + 10, 23 * 60),   # 17:10起床后, 上限仍卡全局23点
    },
    "lishen": {
        "name": "黎深",
        "workday": [
            ("06:05", "06:30", "起床, 洗漱"),
            ("06:30", "07:00", "晨跑"),
            ("07:00", "08:00", "早餐"),
            ("08:00", "08:30", "到达医院"),
            ("08:30", "13:00", "进入手术室"),
            ("13:00", "14:30", "午餐"),
            ("14:30", "16:00", "查房确认病患情况/门诊"),
            ("16:00", "18:00", "小憩, 回复手机信息"),
            ("18:00", "19:30", "下班"),
            ("19:30", "23:00", "晚餐/自由安排"),
            ("23:00", "06:05", "睡觉"),
        ],
        "occasional": "主任医师不值一线夜班, 但休息日/节假日可能突发急诊手术被召回; 偶有医疗援助或医学交流出差",
        "weekend": "周末在家休息, 但随时可能被急诊手术召回医院",
        "proactive": None,          # 醒06:05-23:00, 走全局默认8-23
    },
}

_WD = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def activity_at(char, now=None):
    """-> (起, 止, 活动) 当前命中的时段; 无配置返回 None"""
    cfg = SCHEDULES.get(char)
    if not cfg:
        return None
    lt = _time.localtime((_time.time() if now is None else now))
    t = lt.tm_hour * 60 + lt.tm_min
    for a, b, act in cfg["workday"]:
        a, b = _hm(a), _hm(b)
        if a <= b:
            hit = a <= t < b
        else:                       # 跨午夜
            hit = t >= a or t < b
        if hit:
            return (a, b, act)
    return None


def state_extra(char, now=None):
    """状态行追加段(隔屏聊天态); 周末/节假日=不按工作日表"""
    cfg = SCHEDULES.get(char)
    if not cfg:
        return ""
    lt = _time.localtime(_time.time() if now is None else now)
    hhmm = _time.strftime("%H:%M", lt)
    date = _time.strftime("%Y-%m-%d", lt)
    if lt.tm_wday >= 5 or date in HOLIDAYS:
        tag = "节假日" if date in HOLIDAYS else "周末"
        return f"你的时间{hhmm}{_WD[lt.tm_wday]}({tag}, 不按工作日作息: {cfg['weekend']})"
    seg = activity_at(char, now=now)
    if seg is None:
        return f"你的时间{hhmm}{_WD[lt.tm_wday]}"
    a, b, act = seg
    f = lambda m: f"{m // 60:02d}:{m % 60:02d}"
    if "睡" in act:
        return (f"你的时间{hhmm}{_WD[lt.tm_wday]} · 你此刻在睡觉({f(a)}-{f(b)}), "
                f"被消息吵醒就带睡意回, 按你的人设反应")
    return f"你的时间{hhmm}{_WD[lt.tm_wday]} · 你此刻{act}({f(a)}-{f(b)})"


def proactive_window(char):
    """-> (起分, 止分) 或 None(走全局默认); 仅缩窗不开窗, 上限仍受全局8-23约束"""
    cfg = SCHEDULES.get(char)
    return cfg.get("proactive") if cfg else None


def _is_weekendish(now=None):
    """-> (是周末/节假日, 标签)"""
    lt = _time.localtime(_time.time() if now is None else now)
    date = _time.strftime("%Y-%m-%d", lt)
    if date in HOLIDAYS:
        return True, "节假日"
    return lt.tm_wday >= 5, "周末"


def overlay_text(char, now=None):
    """Chat场景补充块(persona后追加, 仅本通道生效)
    切片12.1: 按当天动态出两版——工作日=时刻表; 周末/节假日=一行自由安排, 工作日表
    根本不进prompt(不赌"一句提示压得住整张表"的权重), chatapp每轮refresh换版"""
    cfg = SCHEDULES.get(char)
    if not cfg:
        return ""
    lines = [
        f"## 家园Chat·IM场景补充(仅本聊天通道生效, {cfg['name']}专属)",
        "- 这里是仿微信的私聊IM: 按「语言风格」节的规矩一条条发短消息即可, 人设其余内容(性格/边界/台词风格/记忆)全部照常生效; 「微信群聊(值守场景)」节是微信通道的说明, 此处忽略",
    ]
    tail = ["- 主人的消息永远要回: 睡着/手术中/忙碌时被吵到, 就按你的人设自然反应(带睡意、回得简短、懒懒的都行), 贴当下状态, 不装没事也不拒答"]
    wk, tag = _is_weekendish(now)
    if wk:
        lines.append(f"- 今天{tag}: 不按工作日作息表, 时间自由安排——{cfg['weekend']}")
        lines.append(f"- 变数: {cfg['occasional']}")
        return "\n".join(lines + tail)
    lines.append("- 你的工作日作息(你真实生活的底色——自然融进聊天就好, 别播报式打卡、别每条消息都报时间行程; 现在几点/你在干嘛, 每轮的[状态]行会告诉你, 以它为准):")
    for a, b, act in cfg["workday"]:
        lines.append(f"  {a}-{b} {act}")
    lines.append(f"  偶有: {cfg['occasional']}")
    lines.append(f"  周末/节假日不按上表: {cfg['weekend']}")
    return "\n".join(lines + tail)


# ---------------- 自测 ----------------
if __name__ == "__main__":
    # 1) 24h覆盖断言(并集=1440, 无缝隙)
    for c, cfg in SCHEDULES.items():
        ints = []
        for a, b, _ in cfg["workday"]:
            a, b = _hm(a), _hm(b)
            if a > b:
                ints += [(a, 1440), (0, b)]
            else:
                ints.append((a, b))
        ints.sort()
        cov, cur = 0, None
        for a, b in ints:
            a = max(a, cur) if cur is not None else a
            assert a <= b, f"{c} 时段重叠 @ {a}"
            cov += b - a
            cur = max(cur or 0, b)
        assert cov == 1440, f"{c} 覆盖{cov}分 != 1440"
        print(f"[覆盖OK] {cfg['name']}: {len(cfg['workday'])}段 铺满24h")
    # 2) 关键时刻抽样(合成时间戳, 避开真实时钟)
    import datetime as _dt
    base = _dt.datetime(2026, 10, 29, 0, 0).timestamp()   # 周四
    for c, hhmm, want in [
        ("qinche", "12:30", "睡觉"), ("qinche", "17:15", "起床"),
        ("qinche", "03:00", "暗点"), ("qinche", "04:45", "回家"),
        ("shenxinghui", "10:30", "起床失败"), ("shenxinghui", "09:00", "睡觉"),
        ("shenxinghui", "23:40", "睡觉"), ("shenxinghui", "14:00", "午觉"),
        ("xiazhou", "15:30", "回复私人"), ("xiazhou", "19:00", "天行"),
        ("qiyu", "08:00", "泡澡"), ("qiyu", "22:00", "泡澡"), ("qiyu", "03:00", "睡觉"),
        ("lishen", "06:45", "晨跑"), ("lishen", "10:00", "手术"), ("lishen", "17:00", "小憩"),
    ]:
        ts = base + _hm(hhmm) * 60
        seg = activity_at(c, now=ts)
        assert seg and want in seg[2], f"{c}@{hhmm} 期望含'{want}' 实得{seg}"
        print(f"  [样例OK] {c} {hhmm} -> {seg[2]}")
    # 3) 周末/状态行/overlay样例
    sat = _dt.datetime(2026, 10, 31, 14, 30).timestamp()
    print("\n[周末样例]", state_extra("xiazhou", now=sat))
    thu14 = _dt.datetime(2026, 10, 29, 14, 30).timestamp()
    print("[工作日样例·黎深]", state_extra("lishen", now=thu14))
    print("[工作日样例·沈星回凌晨1点]", state_extra("shenxinghui", now=base + 60))
    for c in SCHEDULES:
        import re as _re
        ov_wd = overlay_text(c, now=thu14)
        ov_wk = overlay_text(c, now=sat)
        assert "工作日作息" in ov_wd and _re.search(r"\d\d:\d\d-\d\d:\d\d", ov_wd), f"{c} 工作日版缺时刻表"
        assert not _re.search(r"\d\d:\d\d-\d\d:\d\d", ov_wk), f"{c} 周末版混入时刻表"
        assert "时间自由安排" in ov_wk and "主人的消息永远要回" in ov_wk, f"{c} 周末版缺自由安排/永远要回"
        print(f"\n{'='*20} overlay({c} 工作日版) {'='*20}\n{ov_wd}")
        print(f"{'='*20} overlay({c} 周末版) {'='*20}\n{ov_wk}")
    print("\n全部自测通过 ✅")
