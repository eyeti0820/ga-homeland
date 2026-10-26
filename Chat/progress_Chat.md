# Progress Log: Chat

## Session: 2026-10-27(立项)

### Phase: 立项调研+方案+三件套
- **Status:** complete
- 背景:chat box 同步哑等痛点(切 APP 必断)+ oMLX 前缀缓存死案结案(gemma4 滑窗架构 dflash 永不命中)→ 主人拍板自建异步聊天界面,仿飞书机制。
- 调研动作:
  - 评估土豆聊天 selfhost(`~/Downloads/土豆系统/土豆聊天-本地部署版/potato-chat-selfhost`):Docker+PG+Redis+双前端工程链 → **否决**(对单人 IM 过重),仅参考其 PWA manifest 与自部署文档思路;
  - 核实 GA 复用底牌:chatapp_common.py(403行,AgentChatMixin)/apiapp.py(340行,CharService 懒构建+串行锁+宫殿+drain)/fsapp.py(1172行,识图/_send_generated_files/_scan_media)/char_media.py(223行,TTS克隆+生图);行号快照记入 findings;
  - 确认 _dev_skills/planning-with-files v2.3.0 在本 repo,三件套照家园惯例命名;
  - 确认记忆架构:同角色多通道=多 agent 实例,宫殿是唯一共享记忆(主人拍板 D5 要的就是这个)。
- 产出:`开发方案_Chat.md`(§0 决策/§2 架构/§3 复用清单/§4-5 前后端设计/§6 M0-M4/§7 风险)+ 本三件套;git commit 一版。
- 未决事项:无(六项决策全部拍板)。二期候选:iOS 系统推送、轻聊模式(单轮直出提速)。

### Phase: M0 骨架
- **Status:** not started
