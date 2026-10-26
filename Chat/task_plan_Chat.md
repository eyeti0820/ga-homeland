# Task Plan: Chat — 仿微信异步聊天界面(五角色)

> 三件套之一(planning-with-files 纪律)。跨 session 唯一进度真相源;接手任何里程碑前先读本文件 + `开发方案_Chat.md`。
> 创建:2026-10-27 by 小梅。决策记录见开发方案 §0,已全部拍板不再讨论。

## Goal
给主人一个手机上像微信一样用的私密聊天入口:五角色仿微信 UI、发完即走的异步模式、全媒体(识图/发图/语音条)、共享世界书、记忆照常入宫殿与飞书互通。

## Current Phase
**切片1 = M0 骨架 + M1 文字异步闭环 — 未开工**(2026-10-27 立项+深夜拍板完成;开发模式已定 D7:小梅直接开发,一切片一 session,OC 审计后过。下 session 开工入口 = `Chat/handoff_Chat.md` 施工单)

## Phases(= 开发方案 §6 切片;一 session 一切片,D7)

### 切片1 = M0 骨架 + M1 文字异步闭环(下 session)
- [ ] lsof 确认 8788 空闲 → chatapp.py FastAPI 骨架(health+静态,chatapp_common 自举+--no-user-tools)
- [ ] Chat/chat.db 建表(messages/jobs/meta,方案 §4.2)
- [ ] launchd com.ga.chatapp.plist(照 apiapp 抄)+ bootout/bootstrap 坑规避
- [ ] tailscale serve 挂 /chat → 127.0.0.1:8788
- [ ] 手机访问 /health 200 + KeepAlive 自愈验证
- **Status:** not started

### M1 文字异步闭环(并入切片1)
- [ ] CharService×5 懒构建(复制 apiapp L77-107 模式,勿 import apiapp)
- [ ] worker 队列(jobs 表驱动,同角色天然串行,900s abort)
- [ ] 路由:msg/poll/history/chars
- [ ] 前端:会话列表屏+聊天屏+气泡+**标点切泡(§5.4:括号段()【】=原子一泡灰斜体不参与合并;其余按句末标点切,<8字并入下段,≤6泡,300ms逐泡弹入)**+typing 动画
- [ ] 轮询生命周期(2.5s/5s+visibilitychange 暂停)
- [ ] 验收:锁屏 30s 回复已就位;宫殿 ingest 可查;五角色各通 1 轮;括号描写段单泡不刷屏
- [ ] 切片收尾(D7):OC+gstack 独立审计→关闭问题→三件套+commit→更新 handoff_Chat.md 进切片2
- **Status:** not started

### 切片2 = M2 世界书+记忆验证
- [ ] worldbook 读写 API + 热更新已构建 agent
- [ ] UI 世界书编辑框(五角色共享一份)
- [ ] 验收:改世界书下轮生效;飞书侧能"想起"本通道聊的内容(宫殿桥接实锤)
- **Status:** not started

### 切片3 = M3 媒体三件
- [ ] 主人发图:upload+GLM 识图注入(native_oai_config10,chat() 生成器坑)
- [ ] 角色发图:[FILE:] 落库+image_out 气泡+全屏查看
- [ ] 语音条:TTS 克隆+[FILE:]+时长+audio 播放;_scan_media 兜底
- [ ] 宫殿媒体注记(_media_note 格式,cleaned 剥 [FILE:])
- [ ] 验收:三种媒体各 1 次;兜底路径覆盖
- **Status:** not started

### 切片4 = M4 打磨(+背景自定义)
- [ ] PWA manifest+添加主屏;历史上滑分页;错误气泡+重发
- [ ] oMLX 4xx 退避重试;429/超时人话文案
- [ ] 背景自定义(D8 §5.7:上传图+半透明遮罩+纯色兜底;前面切片有余力可提前)
- [ ] 验收:杀进程重启不丢;iPhone 主屏全屏可用;背景功能两端(iOS/桌面)可用
- **Status:** not started

## Notes
- 铁律:不动 apiapp/fsapp 一行;改共享模块先备份;每切片一 commit;聊天记录不入 git;handoff_Chat.md 不入 git(session 间交接文档)。
- 切片收尾流程(D7):施工→OC(`~/.opencode/bin/opencode`,GLM-5.3)+gstack skill 独立审计→问题关闭→三件套更新+commit→更新 handoff 进下一切片。
- 关键架构事实(勿再纠结):同角色三通道=三 agent 实例,短期上下文不互通,宫殿桥接是设计而非缺陷(决策 D5)。
