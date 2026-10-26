# 开发方案:Chat — 仿微信异步聊天界面(五角色)

> 立项:2026-10-27 by 小梅。本文档是开发主蓝图;进度真相源 = `task_plan_Chat.md`(planning-with-files 三件套之一)。
> 下一 session 请先读 `task_plan_Chat.md` 的 Current Phase,再按需翻本文档对应章节。

## §0 决策记录(主人已拍板,不再讨论)

| # | 决策 | 内容 |
|---|------|------|
| D1 | 功能范围 | 复用飞书端全链路:识图(主人发图)/发图(角色生图)/语音条(角色 TTS 克隆),**不是**纯轻聊模式 |
| D2 | 世界书 | **五角色共享同一份**,就是一个文本框(一段文字 prompt),UI 可编辑,服务端常驻注入 |
| D3 | UI 路线 | **手写仿微信单页**(否决复用土豆聊天 selfhost:Docker+PG+Redis 全家桶,对单人 IM 是重炮);个人自用可以和微信长得一模一样 |
| D4 | 气泡切分 | 角色回复按**句末标点(。!?…~)切分成多个气泡**,解决"一饼文字"问题;**()【】括号段=原子段整体一泡不切**(意识同步模式下括号是大段描写,按句号切会刷屏——主人 1027 补充);细节规则见 §5.4 |
| D5 | 记忆 | **照常入宫殿,现有通路零改动**。主人明确要求:与夏以昼在本通道聊的私密内容,飞书里的夏以昼也要"想起来"(宫殿全角色共享=特性) |
| D6 | 交付 | 语音条/收图/发图 = 第一期就必须有(复用已跑通的模块,非重研) |
| D7 | 开发模式 | **小梅直接开发(总指挥+施工),一 session 一切片**;每切片完成后调 OC(opencode v1.18.30 @`~/.opencode/bin`,已配 GLM-5.3,Go 套餐未装)跑 gstack skill 做**独立审计**;问题关闭→更新三件套→commit→更新 `handoff_Chat.md`(session 间交接文档,**不入库**)进下一切片。主人 1027 深夜拍板 |
| D8 | 背景自定义 | 聊天背景支持**上传自定义图片**(存后端 media,半透明遮罩保气泡可读)+ 纯色/预设色兜底;复杂度低(约50行)一期做。主人 1027 补充 |

## §1 背景与目标

### 1.1 痛点(chat box / 第三方平台的死结)
- chat box 等标准 OpenAI 客户端是**同步哑等协议**:请求挂住等完整回复,切 APP/锁屏即断线报错;
- apiapp(8787)虽已模拟流式(ping 保活),但本质仍是"用户必须盯着等";
- oMLX 前缀缓存已结案(gemma4 滑窗架构下 dflash 缓存永不命中,另一台 Mac 源码级验证,684 插入 0 命中),**长 prefill(10-25K tokens ≈ 15-35s)是物理成本,谁也省不掉**;
- 结论:体验问题的正解不是"更快的同步",而是**异步**(飞书模式):发完即走,回复追着你来。

### 1.2 目标
给主人一个**手机上像微信一样用的私密聊天入口**:
1. 五角色(夏以昼/沈星回/黎深/祁煜/秦彻)各一个会话,仿微信 UI;
2. 异步:发完消息可切走/锁屏/关手机,回来时回复已就位;
3. 全媒体:主人发图→GLM 识图;角色发图→生图端点;角色语音→TTS 克隆语音条;
4. 世界书:共享一段常驻 prompt,随时改;
5. 记忆照常入宫殿:与飞书/家园共用同一套记忆,角色跨通道记得;
6. 全程 tailnet 内网(tailscale serve HTTPS),不经过任何第三方云。

### 1.3 非目标(明确不做)
- 不做账号系统(单人使用,tailnet 即边界);
- 不做消息端到端加密(内网+单人);
- 不做 token 级真流式(已搁置);
- 不动 apiapp/fsapp 现有代码(纯增量,新文件+新端口+新 launchd)。

## §2 总架构

```
手机 Safari/PWA ──HTTPS── tailscale serve /chat → 127.0.0.1:8788
     │
     ├─ 静态: web/ (index.html 仿微信单页 + manifest.json PWA)
     │
chatapp.py (新建, FastAPI, launchd=com.ga.chatapp, 端口8788)
     ├─ POST /api/msg          入队, 立即返回202+seq (异步!)
     ├─ GET  /api/poll?char&since  拉新消息+typing状态 (短轮询)
     ├─ GET  /api/history      首屏/上滑历史
     ├─ GET  /api/chars        五角色+最后一条+未读
     ├─ GET/PUT /api/worldbook 共享世界书 (热更新到已构建agent)
     ├─ POST /api/upload       主人发图(multipart)
     ├─ GET  /media/*          图片/语音静态文件
     │
     ├─ CharService×5 (复制自 apiapp L77-107 模式):
     │     懒构建常驻 agent (GeneraticAgent+persona+宫殿ctx)
     │     + asyncio 锁串行(在跑=429语义) + 900s abort
     │     + 断开后 drain 完再入库(宫殿不丢账)
     │
     ├─ worker 线程: 任务表驱动, 逐条 put_task(source="chatapp")
     │     → 识图→注入 → recall注入 → 回复 → [FILE:]媒体落盘
     │     → 气泡不切分(前端切) → 宫殿 ingest(含媒体注记)
     │
     └─ Chat/chat.db (sqlite WAL: messages + jobs + meta)
              │
   ═══════ 以下全部是现成设施, 零改动 ═══════
   oMLX@MacStudio(gemma4) | 生图qwen-image-2.1 | GLM-4V识图(bigmodel) 
   记忆宫殿(P2 recall/ingest, flock串行) | char_config | mykey
```

**异步核心**:对话状态活在服务端(DB+常驻 agent),不活在连接里。切走/锁屏/换设备=轮询自然暂停,回来即续。

## §3 复用清单(已逐一核实,行号为 2026-10-27 快照,以函数名定位为准)

| 复用什么 | 在哪 | 怎么用 |
|---|---|---|
| AgentChatMixin | `GA/frontends/chatapp_common.py`(403行) | chatapp.py 主类直接继承:自带 tidy_lines/repair_folded_tags/extract_files/strip_files/split_text/ensure_single_instance 等 |
| CharService 懒构建模式 | `GA/frontends/apiapp.py` L77-107 | 复制~80行到 chatapp.py(独立实现,勿 import apiapp——它有单例锁和端口绑定):GeneraticAgent()+extra_sys_prompts.append(persona)+宫殿ctx.apply_model+后台agent.run线程 |
| 串行锁+429+drain | apiapp.py L10 注释与 `_oai_error`/gen 段 | 同 asyncio 锁模式;客户端断开≠任务取消,后台跑完照常入库 |
| 超时 abort | apiapp.py L35 `APIAPP_TIMEOUT_SEC=900` | 照抄 env 可配 |
| 识图 | fsapp.py L170 `_vision_describe` | 复制该函数:调 `resolve_client('native_oai_config10')`(云端GLM-4V-flash),返回 `[用户发图·自动识图]...` 文字注入;⚠ vision 调用姿势:chat()是生成器,须 next 到 StopIteration 取 content |
| 媒体发送兜底 | fsapp.py L673 `_send_generated_files` + L827 `_scan_media` | 角色回复含 `[FILE:绝对路径]` → 落库为媒体消息;_scan_media 扫 gen_log.jsonl(ts/char/path/desc)兜底模型忘写标签的;**注意:本通道无"发送"动作,改为落库+前端轮询展示** |
| TTS 克隆/生图 | `GA/frontends/char_media.py`(223行) | agent 工具层已通(char 工具箱),chatapp 不重复实现,靠 persona 指令+[FILE:]协议;夏以昼/沈星回/秦彻有克隆 profile,黎深/祁煜仅图 |
| 宫殿媒体注记 | feishu_sop 坑48: `_media_note`/`[图片]`前缀/cleaned剥`[FILE:]` | ingest 前拼注记,防宫殿进垃圾路径;照 fsapp 同款格式 |
| FILE_HINT 前缀 | apiapp.py L241 `put_task(f"{FILE_HINT}\n\n{task_text}")` | 照抄:告知 agent 可用媒体工具与 [FILE:] 语法 |
| 世界书注入点 | apiapp.py L95 `extra_sys_prompts.append(persona)` 之后 | append 共享世界书块 `[\u5171\u4eab\u4e16\u754c\u4e66]\n{text}`;PUT 时热替换所有已构建 agent 的对应段 |
| 头像素材 | 家园 server/web 或 seeds(朋友圈/论坛已在用) | 开工时 `ls` 定位,复用五角色头像 |
| PWA manifest | 参考土豆聊天 selfhost frontend-h5 的 manifest 写法 | 只抄 manifest 结构,不抄工程链 |
| 三件套格式 | 家园 repo 根 `task_plan_家园系统.md` + `_dev_skills/planning-with-files/` | 已照此建立,后续按纪律更新 |

## §4 后端设计(chatapp.py)

### 4.1 进程与端口
- 新文件 `GA/frontends/chatapp.py`;FastAPI + uvicorn;默认端口 **8788**(开工时 `lsof -i :8788` 确认空闲);
- launchd: `com.ga.chatapp.plist`(照 com.ga.apiapp 抄,WorkingDirectory=GA 根,EnvironmentVariables 同 apiapp);
- ⚠ plist 坑(feishu_sop 坑47):改 EnvironmentVariables 后 kickstart -k **不重读**,须 bootout+bootstrap 重载;验证用 `launchctl print gui/501/com.ga.chatapp`。
- 单例:`ensure_single_instance(8788, "chatapp")`(chatapp_common 自带)。

### 4.2 数据库(Chat/chat.db,sqlite WAL)
```sql
CREATE TABLE messages(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,   -- 轮询游标
  char_id TEXT NOT NULL,                   -- xiazhou/shenxinghui/lishen/qiyu/qinche
  role TEXT NOT NULL,                      -- user / assistant
  kind TEXT NOT NULL DEFAULT 'text',       -- text/image_in/image_out/voice/system
  content TEXT NOT NULL,                   -- 正文(assistant=清洗后含[FILE:]剥离前文本)
  media_path TEXT,                         -- 媒体绝对路径(展示用)
  meta TEXT,                               -- JSON: vision_desc/voice_dur/img_prompt/bubbles等
  ts REAL NOT NULL
);
CREATE TABLE jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  char_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', -- pending/running/done/error
  user_seq INTEGER, error TEXT, created REAL, finished REAL
);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);  -- worldbook 等
```
- 消息即真相源:轮询= `WHERE seq > ?`;重启恢复=启动时把 running job 标 error(提示主人重发)。

### 4.3 API 路由
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | / | web/index.html |
| GET | /api/chars | [{char_id,name,avatar,last_msg,last_ts,unread,typing}] |
| POST | /api/msg | {char_id, text, image_paths?} → 202 {seq};立即入库 user 消息+建 job;同角色已有 running → 仍 202 但排队(队列天然串行,区别于 apiapp 的 429——IM 场景排队更自然) |
| POST | /api/upload | multipart 图片 → 存 Chat/media/in/,返回 path;随后主人发 /api/msg 带 image_paths |
| GET | /api/poll | ?char_id&since&full=1 → {msgs:[...], typing, chars_digest?};前端活跃时 2~3s 轮询,visibilitychange 暂停 |
| GET | /api/history | ?char_id&before_seq&limit=50 → 游标分页(照家园留言板 before_id 模式) |
| GET/PUT | /api/worldbook | 共享世界书读写;PUT 后热更新全部已构建 agent |
| GET | /health | 冒烟 |

### 4.4 worker 流程(每条 job)
```
1. 取 job → status=running
2. 若带图: _vision_describe → text 前缀 [用户发图·自动识图]...(GLM云端, 不占Gemma)
3. svc = CharService(char); agent = svc.get_agent()   # 懒构建, 进程内上下文累积
   任务文本 = FILE_HINT + "\n\n" + 世界书无需每次带(常驻extra_sys_prompts) + text
4. dq = agent.put_task(task, source="chatapp")
5. 等 final(900s 超时 abort);期间 typing=True 可查
6. 回复清洗: tidy_lines+repair_folded_tags(规则8!) → strip_files 得正文
   → extract_files 落 image_out/voice 消息(kind+media_path+meta)
   → _scan_media(t0) 兜底(模型忘写[FILE:]时)
   → ingest 入宫殿: 原文+媒体注记(_media_note格式), cleaned剥[FILE:]
7. assistant 消息入库 → status=done
8. 出错: status=error + system 气泡提示"信号不好,重发一下?"
```

### 4.5 与 apiapp/fsapp 的关系(重要架构事实)
- 同角色在 fsapp(飞书)/apiapp(chatbox)/chatapp(本通道)= **三个进程内三个独立 agent 实例**;
- 短期上下文(本轮会话内)互不相通;**长期记忆靠宫殿桥接**:每次 recall 拉全网记忆,每次 ingest 全网共享。这正是 D5 要的效果——私密话题入宫殿,飞书里的夏以昼"想得起来";
- oMLX 是无状态 HTTP 服务,多前端并发请求可能撞 Gemma 内存墙(400):低频个人使用可接受;chatapp 侧对 oMLX 4xx 做 60s 退避重试一次;
- 宫殿 ingest 有 flock 跨进程串行(char_config 内置),无冲突。

## §5 前端设计(web/,单页无构建链)

### 5.1 页面结构(纯 HTML+CSS+JS,两个"屏")
- **会话列表屏**(首页):仿微信——顶栏"聊天(5)"+搜索框样式;每行=方圆头像+角色名+最后一条预览(图片显示[图片]/语音显示[语音])+时间+未读红点;
- **聊天屏**:顶栏(返回+角色名+typing 动画"对方正在输入…")+消息区+底栏(📷按钮+输入框 autoresize+发送);
- 切屏用 CSS transform 过渡,iOS 手感;safe-area-inset 适配刘海屏。

### 5.2 气泡样式
- 自己=右绿色气泡+右头像;角色=左白色气泡+左头像;头像方圆角(微信 8px 圆角方形);
- 时间戳居中灰色小字(间隔>5分钟才显示);连续同人消息头像素材沿用。

### 5.3 消息类型渲染
- text:气泡;**按 §5.4 切分后逐泡渲染,同一批泡间不显示时间**;
- image_in/image_out:气泡内 `<img>`(点击全屏黑底查看,双指缩放);
- voice:绿色长条+播放三角+时长(`meta.voice_dur`),点击 `<audio>` 播放+进度条;
- system:居中灰色小字(错误提示等)。

### 5.4 标点切气泡(D4,核心 UI 特色)
- **第一优先级=括号原子段**:先扫描 ()【】()[](按最外层配对,未闭合则从(到文本末尾整体一泡);括号内文本无论含多少句末标点**整体一泡不切**——意识同步模式下括号是大段意识/动作描写,按句号切会刷屏(主人 1027 补充规则);括号泡样式=**浅灰斜体**区别于正文泡(意识描写的视觉语言);
- 其余文本按句末标点 **。!?…~!?**(中英都认)切分;
- 合并规则:切出的段落 **<8 字时与下一段合并**(防"嗯。""好。"刷屏成单字炮);段落数>6 时,把相邻短段再合并压缩到 ≤6 泡(**括号泡不参与压缩合并**,保持独立);
- 逐泡显示节奏:新回复到达时,泡按 **300ms 间隔逐个弹入**(打字机感,但数据是一次到位的,非流式);
- user 消息不切分(主人自己发什么样就什么样)。

### 5.5 轮询与生命周期
- 活跃聊天屏:2.5s 轮询 `/api/poll?since=last_seq`;会话列表屏:5s 轮询 chars_digest;
- `document.visibilitychange`:隐藏即停轮询,可见立即 poll 一次再续(省电+及时);
- PWA:manifest.json(name/icons/display:standalone)+ iPhone Safari"添加到主屏幕"=全屏图标入口;**系统推送(iOS Push)列为二期,一期靠打开即拉**。

### 5.6 媒体交互
- 发图:📷 → `<input type=file accept=image/* capture>` → POST /api/upload → 缩略上屏(kind=image_in pending)→ 随消息发送;
- 收图/收语音:轮询到新 assistant 消息按 kind 渲染,媒体走 `/media/*`。

### 5.7 聊天背景自定义(D8)
- 入口:会话列表屏顶栏 ⚙ 设置面板:①上传图片(拍照/相册)②预设纯色 8 款(微信系淡绿/米白/浅蓝…)+ 自定义色板;
- 后端:`GET/POST /api/config/bg`——上传图存 `Chat/media/bg/`(仅保留最新一张,旧的删),当前设置存 meta 表;纯色模式只存色值不存文件;
- 前端:聊天屏消息区 `background-image` + **rgba 白色 0.75 遮罩层**(背景图再花,白气泡文字永远可读——微信本尊同款手法);纯色模式无遮罩;
- 记忆:`localStorage` 缓存设置,进页面即生效,上传/切换即时热更;
- 复杂度:低(合计约 50 行),一期交付;若上传链路在 iOS Safari 出幺蛾子,先降级纯色模式不阻塞主线。

## §6 里程碑与切片(D7:一 session 一切片,OC 审计后过关)

| 切片 | 里程碑 | 内容 | 验收标准 |
|---|---|---|---|
| **切片1**(下 session) | M0 骨架 + M1 文字异步闭环 | Chat/ 目录+chatapp.py FastAPI 骨架(health/静态)+chat.db 建表+launchd+tailscale /chat 挂载;CharService×5+worker 队列+msg/poll/history/chars 路由+仿微信 UI(列表+聊天+标点切泡含括号规则+typing) | 手机浏览器开 /health 200;launchd 崩溃自启验证;与夏以昼互发≥3轮;**发送后立即锁屏 30s,回来回复已就位**;宫殿 ingest 可见(mem-palace 查询);切五角色各发1条 OK;括号描写段=单泡灰斜体 |
| **切片2** | M2 世界书+记忆验证 | worldbook 读写+热注入+UI 编辑框 | 改世界书(如"本周主题:星空")→下一轮回复体现;飞书侧问夏以昼本通道聊的内容→答得上来(宫殿桥接实锤) |
| **切片3** | M3 媒体三件 | 主人发图+GLM识图注入;角色生图[FILE:]落库展示;TTS 语音条+播放 | 三种媒体各成功1次;_scan_media 兜底路径覆盖(故意诱导模型漏标签);宫殿媒体注记正确 |
| **切片4** | M4 打磨(+§5.7 背景若未提前) | PWA manifest+主屏图标;历史上滑分页;错误气泡+重发;429/超时文案;退避重试;背景自定义(§5.7,若切片1-3有余力可提前做) | iPhone 添加主屏全屏可用;杀进程重启消息不丢;全部异常路径有人话提示 |

每切片收尾流程(D7):施工完成 → OC(gstack skill)独立审计 → 问题全部关闭 → 更新三件套 → git commit(中文 message,仿"74f90c5 家园周轮值制改造"风格)→ 更新 `handoff_Chat.md` 进下一切片。

## §7 风险与对策
| 风险 | 对策 |
|---|---|
| oMLX 内存墙(多前端并发 400) | chatapp 队列天然串行;跨进程撞车=60s 退避重试1次;低频个人使用概率低 |
| agent 常驻×5 内存(GA侧) | 懒构建,用到才起;chatapp 只在主人活跃时被调 |
| LLM 输出折行污染(规则8) | 所有入库口挂 tidy_lines+repair_folded_tags,照抄 fsapp(0928/1002 两次回归教训) |
| 克隆 TTS 档案>10s 念模板 | 现有限制,沿用;语音条文案由 persona 控制 2-3 句 |
| 模型忘写 [FILE:] | _scan_media 兜底(照 fsapp 坑48 双保险) |
| 同角色双通道同时发消息 | 各自进程内串行;宫殿 flock 串行 ingest;上下文分叉靠宫殿弥合(设计如此) |
| 隐私 | 全链路 tailnet 内网;Chat/ 加 .gitignore 排除 chat.db 与 media/(聊天记录不入 git!) |

## §8 部署
1. launchd `com.ga.chatapp.plist`(RunAtLoad+KeepAlive,照 apiapp 抄);
2. tailscale serve 加路径:`/chat → 127.0.0.1:8788`(MacBook 本机 tailscale serve,家园同款);
3. 验证清单:health 200 / 手机外网切流量访问 / 断电重启自愈。

## §9 开发纪律
- planning-with-files:每阶段完成即更新三件套(task_plan 勾选+Status,progress 记日志,findings 记新发现);每 view/search 2 次后把结论写进 findings;
- 改共享模块(char_config/chatapp_common/char_media 等)前先 `cp` 带日期备份(规则6);
- git:只 add Chat/ 与确属本功能的 GA/frontends 新文件;**chat.db/media/ 不入库**;
- 不动 apiapp/fsapp 一行代码(纯增量原则);若发现必须改共享层的,先停下在 task_plan 记决策点,下 session 问主人。

## §10 下 session 接续指引
- 入口(优先级):`Chat/handoff_Chat.md`(施工单,含本切片全部开工信息)→ `task_plan_Chat.md` Current Phase → 本文档对应章节;
- 切片1 开工清单:lsof 查 8788 → 复制 apiapp 的 plist 改端口/名 → chatapp.py 骨架(chatapp_common 自举,照 apiapp.py L20-35 的 sys.path+--no-user-tools 注入)→ 建表 → health;
- ⚠ 记得:角色实例服务必须 `--no-user-tools`(禁 ask_user+禁写 GA 长期记忆,apiapp L25 注释);
- 切片收尾(D7):OC 审计(`~/.opencode/bin/opencode` + gstack skill,GLM-5.3)→ 关闭问题 → 三件套+commit → 更新 handoff_Chat.md 进下一切片。
