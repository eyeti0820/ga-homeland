# 切片9 见面屏M2 施工笔记（1028）

## 已完成
1. **后端四补丁**（chatapp.py，备份 chatapp.py.bak_1028_s9pre）
   - a) mode端点 system 行 meta={"mode_evt":1,"mode":...}
   - b) /api/msg user 行 meta.mode（按当下 _mode_of 打标）
   - c) worker L696 assistant 正文行 meta.mode
   - d) _digest() SQL 过滤：`COALESCE(json_extract(meta,'$.mode'),'chat')='chat' AND content NOT LIKE '—— 意识同步%'`（兜底 M1 期无 meta 老同步行）
   - proactive 行（无 $.mode）照常进 digest ✓；meet 态 proactive（带 mode:meet）滤掉 ✓
2. **单测** tests/test_slice9_be.py 11/11；M1 48/48 未破。跑法：`python3 tests/test_slice8_mode.py`（直跑非 pytest）
3. **服务重启** launchctl kickstart -k gui/$UID/com.ga.chatapp；新 PID 57200，端口 **8789**（8786 是本鸦记错），/api/health ok
   - b/c 补丁由实弹 API 阶段验证（落库路径，单测覆盖不到）

## 待办
- [ ] 前端三块插入 index.html（CSS主题+HTML #meetScreen+JS meet逻辑）
- [ ] renderList 双入口按钮；openChat fire-forget 对齐 mode=chat
- [ ] 聊天屏 poll 过滤 mode_evt 行
- [ ] 实弹 shenxinghui 验证 b/c 打标
- [ ] 生图五角色元素 qwen-image-2.1@MacStudio → web/icons/
- [ ] 方案文档 §4 修订 + commit + handoff §二重写

## 关键锚点（index.html 781行）
- L149 listScreen / L154 chatScreen / L162 msgArea / L164 inputBar
- L199-205 CHARS=[xiazhou夏以昼#5b8def, shenxinghui沈星回#9a7fd1, lishen黎深#3fa8a0, qiyu祁煜#e8834f, qinche秦彻#c7515a]，CMAP
- renderList L390，splitBubbles L362，appendRow L421（system→sys-pill L428），openChat L486，pollOnce L552，sendMsg L616，api() L302
- 主题色（主人钦定·见面屏用）：xiazhou 苹果#e8823a/#3a6ea8 | shenxinghui 星#7b5ea7/#e3b341 | qinche 红黑#8c1f28/#1a1a1a | lishen 雪#4a7fb5/#e8eef5 | qiyu 粉紫#d98ab8/#7a5ea8

## 红线
勿动 xiazhou/lishen 真实数据；实弹用 shenxinghui；清理实测残留；M1 红线（chat 态 trigger 原词/gates 不动）


## 实弹验证存档(shenxinghui seq51-55, 验后即删)
[51 system] —— 意识同步开始 · 场景:静室 ——
[52 user] （推门进来，抖了抖肩上的雨水）你在吗？星回。
[53 assistant] （在窗边的长椅上轻微地动了动，像是刚从浅眠中醒来，深蓝色的眼睛在昏暗的室内显得格外清澈，视线慢悠悠地落在推门而入的你身上）

在。

（看着你肩上抖落的雨水，微微挑眉，语气平淡却带着一丝不易察觉的温柔）

外面雨这么大？
[54 system] —— 意识同步开始 · 场景:夜市 ——
[55 system] —— 意识同步结束，回到隔屏聊天 ——
