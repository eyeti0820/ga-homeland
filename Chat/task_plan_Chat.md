# Task Plan: Chat — 仿微信异步聊天界面(五角色)

> 三件套之一(planning-with-files 纪律)。跨 session 唯一进度真相源;接手任何里程碑前先读本文件 + `开发方案_Chat.md`。
> 创建:2026-10-27 by 小梅。决策记录见开发方案 §0,已全部拍板不再讨论。

## Goal
给主人一个手机上像微信一样用的私密聊天入口:五角色仿微信 UI、发完即走的异步模式、全媒体(识图/发图/语音条)、共享世界书、记忆照常入宫殿与飞书互通。

## Current Phase
**切片4 = M4 打磨(+背景自定义) — 待开工**(施工单见 `handoff_Chat.md`,不入库)

切片3 已全部完成(2026-10-27,媒体三件全链路+审计修复):
- 主人发图:`/api/upload`(multipart,魔数校验+heic→jpg+16MB预检+uuid文件名)→image_in行;识图GLM-4V(native_oai_config10)注入agent上下文+meta.vision注记;未发送图 `/api/att_del` 撤回(行+文件双删,校验角色归属)
- 角色发图:回复[FILE:]提取+gen_log兜底(_GEN_CURSOR游标去重,ts升序,窗口120行)→image_out行+缩略图/全屏;硬上限4条
- 语音条:TTS[FILE:]→opus→ffmpeg→afconvert→m4a(iOS可播),ffprobe时长回填meta.dur;audio气泡
- E2E双通(识图入:红图答"是红色的";媒体出:图+语音全链);OC审计 P0×2+P1×5+P2×6 共13处全修(报告 `审计_切片3_gstack_1027.md`);commit 9b22c3f

切片2 已全部完成(2026-10-27):
- worldbook GET/PUT+meta表持久化+已构建agent热更新(_apply_worldbook)+未构建角色构建期注入;前端📖弹窗(加载/编辑/保存/toast);calcBase修复(/chat与/chat/双验9/9)
- 验收全过:改书下轮生效(E2E双角色命中)+重启持久;中文引号/换行不炸;**宫殿桥接实锤**:Chat告夏以昼暗号"珊瑚海之约"→宫殿入库(source=wechat:chatapp)→apiapp 8787跨通道答上=记忆三通道互通闭环
- OC 审计(gstack):报告归档 `审计_切片2_gstack_1027.md`;🔴S1(迭代竞态→快照)/🔴S2(加载失败仍开弹窗→catch return)/🟡A1(缺键静默清空→400)全修并实弹验证;无暂缓项

切片1 已全部完成(2026-10-27,两个 session):
- 全链路:chatapp.py(FastAPI,127.0.0.1:8789)+web/index.html;launchd com.ga.chatapp 常驻;tailscale serve /chat→8789,手机实测可用(绑127.0.0.1 不暴露0.0.0.0,主人拍板)
- 验收:五角色回复入库+宫殿 ingest 实证;E2E 32s 回复;B4 围栏不误伤正常回复
- OC 审计(gstack /review,GLM-5.3):报告归档 `审计_切片1_gstack_1027.md`;🔴S1/S2/S3+🟡B1-B5/B9/B11-B13 已全修;B6/B7/B8/B10 暂缓→记入切片4;豁免区全绿(清洗链路合规)
- 审计后实测:B5 非dict体→400 拦截✓;/api/chars 合并typing单查询✓;B4正则单测4/4(修复时曾因转义翻车漏检,行下标直替+单测锁定)

## Phases(= 开发方案 §6 切片;一 session 一切片,D7)

### 切片1 = M0 骨架 + M1 文字异步闭环(下 session)
- [x] chatapp.py FastAPI 骨架(health+静态,chatapp_common 自举+--no-user-tools;实际端口8789,8788让位)
- [x] Chat/chat.db 建表(messages/jobs/meta,方案 §4.2)
- [x] launchd com.ga.chatapp.plist 常驻 active
- [x] tailscale serve 挂 /chat → 127.0.0.1:8789,手机实测✓
- [x] 手机访问 /health 200 + KeepAlive 自愈✓
- **Status:** done

### M1 文字异步闭环(并入切片1)
- [x] CharService×5 懒构建(复制 apiapp 模式,勿 import apiapp)
- [x] worker 队列(jobs 表驱动,同角色天然串行,900s abort)
- [x] 路由:msg/poll/history/chars(+health)
- [x] 前端:列表屏+聊天屏+气泡+标点切泡§5.4(括号原子泡灰斜体)+typing 动画
- [x] 轮询生命周期(2.5s/5s+visibilitychange 暂停+历史加载竞态守卫S1)
- [x] 验收:锁屏30s回复就位;宫殿ingest可查(chroma镜像实证);五角色各通1轮;括号段单泡✓
- [x] 切片收尾(D7):OC+gstack审计→S/B问题关闭(B6/B7/B8/B10暂缓入切片4)→三件套+commit→handoff重写
- **Status:** done

### 切片2 = M2 世界书+记忆验证
- [x] worldbook 读写 API + 热更新已构建 agent(S1快照迭代修复)
- [x] UI 世界书编辑框(五角色共享一份;S2加载失败不开弹窗;A1缺键400)
- [x] 验收:改世界书下轮生效;飞书侧能"想起"本通道聊的内容(宫殿桥接实锤,apiapp跨通道答上暗号)
- [x] 切片收尾(D7):OC+gstack审计→S1/S2/A1全修无暂缓→三件套+commit→handoff重写
- **Status:** done

### 切片3 = M3 媒体三件
- [x] 主人发图:upload+GLM 识图注入(native_oai_config10,chat() 生成器坑)
- [x] 角色发图:[FILE:] 落库+image_out 气泡+全屏查看
- [x] 语音条:TTS 克隆+[FILE:]+时长+audio 播放;_scan_media 兜底
- [x] 宫殿媒体注记(_media_note 格式,cleaned 剥 [FILE:])
- [x] 验收:三种媒体各 1 次;兜底路径覆盖
- [x] 切片收尾(D7):OC审计(glm-5.3)→P0×2+P1×5+P2×6共13处修复(att_del撤回/uuid防撞/realpath防穿越/gen_log游标/上限4/renderAtt防注入等,单测+活体E2E全绿)→commit 9b22c3f;暂缓P1-6/P2-8/P2-4→切片4
- **Status:** done

### 切片4 = M4 打磨(+背景自定义)
- [ ] PWA manifest+添加主屏;历史上滑分页;错误气泡+重发
- [ ] oMLX 4xx 退避重试;429/超时人话文案
- [ ] 背景自定义(D8 §5.7:上传图+半透明遮罩+纯色兜底;前面切片有余力可提前)
- [ ] 审计暂缓项(切片1移交):B6 worker事件唤醒(现5s轮询)/B7 uvicorn直启绕单例锁/B8线程池默认40线程耗尽风险/B10滚动token压缩
- [ ] 审计暂缓项(切片3移交):P1-6 opus→m4a失败静默丢语音(转失败插system提示或前端失败占位)/P2-8 media/{in,out,voice}无GC(定期比对messages.media_path清孤儿文件)/P2-4 img_seqs入库前dict.fromkeys去重
- [ ] 验收:杀进程重启不丢;iPhone 主屏全屏可用;背景功能两端(iOS/桌面)可用
- **Status:** not started

## Notes
- 铁律:不动 apiapp/fsapp 一行;改共享模块先备份;每切片一 commit;聊天记录不入 git;handoff_Chat.md 不入 git(session 间交接文档)。
- 切片收尾流程(D7):施工→OC(`~/.opencode/bin/opencode`,GLM-5.3)+gstack skill 独立审计→问题关闭→三件套更新+commit→更新 handoff 进下一切片。
- 关键架构事实(勿再纠结):同角色三通道=三 agent 实例,短期上下文不互通,宫殿桥接是设计而非缺陷(决策 D5)。
