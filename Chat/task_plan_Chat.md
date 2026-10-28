# Task Plan: Chat — 仿微信异步聊天界面(五角色)

> 三件套之一(planning-with-files 纪律)。跨 session 唯一进度真相源;接手任何里程碑前先读本文件 + `开发方案_Chat.md`。
> 创建:2026-10-27 by 小梅。决策记录见开发方案 §0,已全部拍板不再讨论。

## Goal
给主人一个手机上像微信一样用的私密聊天入口:五角色仿微信 UI、发完即走的异步模式、全媒体(识图/发图/语音条)、共享世界书、记忆照常入宫殿与飞书互通。

## Current Phase
**切片8 立项完成 (2026-10-28),待施工 = 见面模式(L2状态机) M1 后端状态机**
切片7已交付(长按删除//new/背景WebP+手机加速,见 handoff_Chat.md §⓪)。切片8~12=见面模式双入口(聊天UI不动+见面小说流),设计方案见 **`开发方案_见面模式.md`**(唯一真相源,§0已拍板D-M1~M6)。原可选方向(语音波形/导出/宫殿面板)顺延,不再列切片8。

切片4 已全部完成(2026-10-27,打磨+背景+审计修复):
- PWA:manifest.json+icons+theme-color(#111118 与聊天底色对齐),iPhone 可加主屏;历史上滑分页(游标 before_seq+scrollTop<60 触底拉 50 条,重建 DOM 保滚动位置)
- 失败重试全链:红!pill(mark_user_error meta.error)+/api/retry(B6 事件唤醒,空转零轮询);**审计 P0-1/P0-2 修复**:poll 携带 err_seqs=[{seq,error}] 前端 syncErrPills 双向对齐(他端重试成功即撤 pill)+retry 双守卫(无 error 标 400/同 user_seq 在途 job 409)+retryMsg 防抖
- oMLX 退避:可重试错误 5s/15s 自动重试共 3 次,429/超时人话文案;4xx 不重试
- 背景自定义:none/纯色(#hex 正则 3/4/6/8 位)/上传图(jpg/png,预检+读后 8MB 双限,media/bg 只留最新);openChat 失败回退+bgImgPick 失败本地回滚
- 性能与移交项:B6 worker 事件唤醒(5s 空轮询→await _wake 零轮询)/B7 单例锁/B8 重活全 to_thread/B10 滚动压缩(_agent_ctx_chars 超限留档 _RETIRED 不再复用)/P1-6 转码失败插 system 提示/P2-8 媒体 GC 24h 比对 messages 清孤儿/P2-4(切片3)img_seqs 入库前去重
- 审计(gstack GLM-5.3,全材料内联防越界):报告 `审计_切片4_gstack_1027.md`;P0×2+P1×2 全修+P2×9 修 7 留 2(见 Notes 永久取舍);离线沙盒打靶 5/5(400/409/202/err_seqs 正反例)+浏览器实测 pill 注入/撤销全绿

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
- [x] PWA manifest+添加主屏;历史上滑分页;错误气泡+重发
- [x] oMLX 退避重试(5s/15s×3);429/超时人话文案
- [x] 背景自定义(none/纯色/上传图;遮罩裁剪不做,见 Notes)
- [x] 审计暂缓项(切片1移交):B6事件唤醒/B7单例锁/B8 to_thread/B10滚动压缩——全落
- [x] 审计暂缓项(切片3移交):P1-6转码失败提示/P2-8媒体GC/P2-4 img_seqs去重——全落
- [x] 验收:杀进程重启不丢(recover只回收running);背景API级验证;桌面浏览器 pill 实测;iPhone 主屏/背景两端=人工验收清单(见 handoff)
- [x] 切片收尾(D7):gstack审计(材料内联)→P0×2+P1×2全修+P2修7留2→离线靶5/5+浏览器全绿→三件套+commit
- **Status:** done

## Notes
- 铁律:不动 apiapp/fsapp 一行;改共享模块先备份;每切片一 commit;聊天记录不入 git;handoff_Chat.md 不入 git(session 间交接文档)。
- **永久取舍(切片4审计后拍板,不再流转)**:①P2-4 并发 bg_set 互删(单用户概率低,说明即可);②P2-9 meta 读写竞态(单写者+短事务,实测无伤);③无 Service Worker(iOS 主屏 PWA 不受影响,离线缓存灰度风险>收益);④背景半透明遮罩未做(纯色即兜底,要再加≈20行)。
- 切片收尾流程(D7):施工→OC(`~/.opencode/bin/opencode`,GLM-5.3)+gstack skill 独立审计→问题关闭→三件套更新+commit→更新 handoff 进下一切片。
- 关键架构事实(勿再纠结):同角色三通道=三 agent 实例,短期上下文不互通,宫殿桥接是设计而非缺陷(决策 D5)。

### 切片5R(1027下午) 纯色退役+注释泄漏+GC时序 ✅
- 验收反馈闭环: 头像行注释文字泄漏(模板内嵌注释)摘除; 纯色退役(mode 只认 none|image,前端8刀+后端4刀+DB五桶洗none,遗留color桶读取降级none); GC时序bug修复(keep从落库前移到落库后+无条件GC,旧图/孤儿真回收)
- UI行为: bgMode('image')直接弹文件框; image态#bgImgBtn=换图入口
- 验证: API+浏览器DataTransfer全链路(上传→分桶隔离→祁煜屏上屏→复原→GC目录空), color 400
- 详见 progress_Chat.md 切片5R 节; 备份 GA/temp/bak_slice5_1027

## 2026-10-28 切片8+9 见面模式M1+M2 ✅(commit c165561)
- **M1 后端(切片8)**: meta KV `meet_state:{char}` + `POST /api/mode`(mode/scene,插mode_evt横幅行,不调LLM) + `_process_job` 每轮注入`[状态:...]`运行时行(自然语时间) + `_proactive_once` meet态trigger/recall分支 + B4式单测 tests/test_slice8_mode.py(48绿) + 重启自愈
- **M2 见面屏(切片9)**: 列表屏"◆见面"入口 → #meetScreen 小说流(旁白正体m-nar/台词600字重主题色竖条气泡m-say/mode_evt横幅m-band/场景切换菜单pickScene/typing次色闪烁) + 五主题CSS变量(主人钦定色)+羊皮纸方格纸面 + **双流隔离**(聊天屏&digest滤meet行[SQL meta.mode+LIKE兜底],见面屏滤chat行) + meet态下行meta.mode打标(user行按当下mode/assistant行同)
- 验收: 浏览器实弹8项全绿(入口/开屏/双端排版/DB打标/换景横幅/退出行+KV清理/聊天屏过滤rows=0/真实数据零伤); tests/test_slice9_be.py 11绿; 实测行已清(星回回复留档notes_slice9.md)
- 方案修订: 开发方案_见面模式.md §4.5(双流隔离推翻§2共享原则/主人钦定"同步别混进聊天界面";聊天屏完全零改动)
- 遗留→切片10: 五皮肤肉眼验收+暗光可读(主题CSS已在切片9提前落地); 切片11: 生图五角色徽标替换"◆见面"文字按钮+空屏插画

- 用户投诉根因三连: ①上次清库只删seq>=47, seq1-46全量测试残留 ②清手机缓存后LS_READ空, 全局递增seq被当未读→红标46/26 ③服务端空历史时前端不清DOM+骨架缓存→幽灵消息常驻
- 处置: DB全清42msg/19jobs/meta+seq归零(备份/tmp重启即失); 测试媒体6删(bg/avatars保留); 宫殿5角色删30条测试(裸对话对=测试, USER带【家园·前缀=朋友圈生产流保留; qinche10-22真实互动2对+柠檬水孤条存疑保留); 前端补丁A首装以digest.last_seq播种已读基线+B空历史清屏清缓存; 服务kill精确PID重启(切片7起有内存态); API验证5角色last_seq=0


## 2026-10-28晚 切片9-D7 审计修复 ✅
- OC 审计(GLM-5.3+gstack)核出 4×P1+9×P2,逐条人工复核后修 9 条(2条不成立/3条微): 关键=P1-2 打标锚定 user 发送行(新 `_mode_of_user_row`,实弹铁证: meet中发送→秒切chat→assistant行仍标meet); P1-1 历史分页丢 mode 过滤; P2-3 digest json_valid 护栏; P2-2 mode_post 同步写包 to_thread; P2-1 note 控制字符 400; P2-6 digest 带 scene+system 行补标; P2-9 状态行作废声明; P2-4/5/7 前端微修(P1-3 toast 化)
- 实弹: note 400 / digest{mode,scene} / meet_state KV 清净; tests 11→25 绿; DB 清残留(56-58+死KV,星回曾卡 meet 态); 服务精确 PID 重启
- 审计报告留档 /tmp/oc_audit_out.txt(重启即失,结论已并入本文件+notes_slice9)
- 遗留: 切片10=素材接入+**场景系统两级级联重构**(1028已拍板,规格@handoff_Chat.md文末)+五主题验收 / 切片11=剩余生图素材

## 2026-10-28晚 切片10 场景两级级联重构+手帐化UI ✅ (commit 09ee0ed)
- **场景重构(纯前端)**: 选景改底部抽屉弹层 `#meetSceneSheet`(mss*系): 最近去过×3(localStorage `meet_recent_scenes`,去重保序挤旧)+约会点COMMON(兔咪火锅/甜品店/Twinkle潮玩店/奶茶店)+角色地盘两级级联(CHAR_SCENES: 一级地盘→浮出BASE+特有房间chips,可不选=整屋)+底部常驻自定义输入;落库格式"一级·二级";后端 scene 仍自由文本零改动,旧场景名不迁移
- **手帐化UI**: LXGW文楷字体(web/fonts/,见面屏局部font-family)+浅淡方格纹纸面+左列装订孔(34px芯白描边,qinche深芯rgba(0,0,0,.5))+和纸胶带贴条(58×17 主题色 旋转-4°)+贴纸感台词气泡+“— 第N幕 —”分隔带+五角色CSS主题变量化(openMeet幂等切换注入,chat屏fontFamily实测系统栈零泄漏)
- **验收(全过)**: 字体A/B对照卡vision实锤;装订孔+胶带 PIL 按CSS几何×dpr像素采样实锤(橙带y560-598);五角色 computed 主题变量轮验全对(xiazhou橙/shenxinghui紫/lishen蓝/qiyu粉/qinche暗);chat屏回归82条消息正常;浮层数=0——vision曾报"面板遮挡/胶带不存在"均为低清幻觉,DOM+像素双证翻案(经验已录 vision_sop)
- **测试**: slice8 48/48 + slice9 25/25;slice8 两期望串对齐 D7-6 作废声明后缀(预存漂移,chatapp.py 本片未动)
- ⚠ commit msg 欠准确: 只提手帐化,实含场景级联重构(同一diff 191行,以本条目为准)
- hotfix `1a41fb6`(1028晚): 切片10加字体link时误写字面\n于head→body左上角渲染"/n/n"符号,已改真换行+实弹复查清零
- 遗留: 五皮肤手机端暗光肉眼验收+主人UI小调优(下session主任务)/切片11=生图五徽标替换"◆见面"+空屏插画/切片12=persona注入
