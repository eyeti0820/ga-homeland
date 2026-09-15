# -*- coding: utf-8 -*-
"""家园产出回写记忆宫殿 — 主人2026-09-15拍板: 方案A原文直存+场景标签包装
调用: homeland_ingest(char_config, scene_key, scene_cn, detail, content)
  char_config: 已按角色scoped的 frontends.char_config 模块(脚本先 env GA_CHAR 再 import 拿到的那份)
  scene_key  : 场景英文key → 拼进 sessionId(如 note/moment/moment_reply/moment_cross/forum_thread/forum_floor/diary)
  scene_cn   : 场景中文名(标签前缀, 如 朋友圈)
  detail     : 一句话场景上下文(谁说了啥/在哪发的/心情)
  content    : 角色原话全文(直存不总结, 由 char_config 内部截断至4000字)
失败只打印 [MP], 绝不抛异常影响发帖主流程。
"""
from datetime import date


def homeland_ingest(char_config, scene_key, scene_cn, detail, content):
    try:
        reply = (content or "").strip()
        if char_config is None or not reply or not (detail or "").strip():
            return
        user_text = f"【家园·{scene_cn}】{date.today().isoformat()} {detail}"
        char_config.ingest_sync(f"homeland_{scene_key}", user_text, reply)
        print(f"[MP] 家园入库✓ {scene_key} ({len(reply)}字)")
    except Exception as e:
        print(f"[MP] 家园入库失败({scene_key}): {e}")
