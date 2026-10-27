# Findings & Decisions: Chat

## Requirements(主人原话归纳)
- 复用飞书端全部媒体链路(识图/发图/语音条),不是轻聊模式;
- 世界书=一个文本框,五角色共享同一份,易维护;
- UI=手写仿微信(和微信一模一样也行,个人自用),五角色各接各的;**句末标点切气泡**(微信一饼文字难看);
- 私密话题记忆:照常入宫殿,飞书里的角色也要"想起来",现有通路零改动;
- 语音条/收图/发图现在就要(一期)。

## Key Research Findings(2026-10-27 核实)
1. **否决土豆聊天 selfhost**:Docker compose(PG+Redis+后端+双前端)+OpenViking 可选,部署重;README 自述"单人自部署不建议账号系统"。手写单页(无构建链)实现量远小于改造它。
2. **复用点行号快照**(2026-10-27,变动后以函数名 grep 定位):
   - apiapp.py: L25 `--no-user-tools` 注入 / L77-107 CharService(get_agent 懒构建+persona+ctx.apply_model+daemon 线程) / L241 FILE_HINT 拼任务 / L305 断开 drain / L35 超时 900s / APIAPP_CHECK_AGENT 冒烟模式;
   - fsapp.py: L170 `_vision_describe`(GA_VISION_CFG 现役=native_oai_config10 云端 GLM-4V-flash) / L673 `_send_generated_files` / L827 `_scan_media`(gen_log.jsonl 兜底);
   - chatapp_common.py: AgentChatMixin(L307)+tidy_lines/repair_folded_tags/extract_files/strip_files/ensure_single_instance;
   - char_media.py:TTS 克隆(夏以昼/沈星回/秦彻有 profile;黎深/祁煜仅图)+生图 [FILE:] 协议。
3. **vision 调用姿势坑**(feishu_sop 坑47):`resolve_client` 按 mykey 变量名解析;`chat()` 是生成器,必须 next 到 StopIteration 取 resp.content。
4. **launchd plist 快照坑**(feishu_sop 坑47):改 env 后 kickstart -k 不重读,须 bootout+bootstrap;验证=launchctl print 的 environment 段。
5. **宫殿媒体注记格式**(坑48):ingest 拼 `[发过语音]`/`[发过图:desc]`;主人发图 vision_note 加 `[图片]` 前缀;cleaned 剥 `[FILE:...]`。
6. **记忆架构**:apiapp 注释自述"recall-only 无跨通道短期上下文桥"——chatapp 同理;三通道三 agent 实例,宫殿=唯一共享记忆,flock 跨进程串行 ingest。
7. **oMLX 现状**:前缀缓存死案(SWA@1024→RotatingKVCache 被 dflash 排除+剥思维链断前缀,684 插入 0 命中,主人另机源码锤死);反向建议:关缓存省 5.6GB 常驻+1.4GB/req 拷贝防 OOM(**待主人批准,与本任务解耦**)。长 prefill ~724 tok/s(10K≈14s)是物理成本,异步化绕开体感问题。
8. **家园 repo 惯例**:三件套命名 `*_家园系统.md` 式 → 本项目 `*_Chat.md`;commit 中文短题;头像素材在 server/web 或 seeds(M0 时定位)。

## Decisions(见开发方案 §0,全部已拍板)
D1 全媒体复用 / D2 共享世界书 / D3 手写仿微信 / D4 标点切泡(<8字并入下段,≤6泡,300ms逐泡) / D5 照常入宫殿 / D6 媒体一期交付。

## Open Questions
- 无阻塞项。二期候选:iOS 推送、轻聊模式、聊天记录导出。

## 2026-10-27 深夜补充
- **opencode环境事实**:v1.18.30装于`~/.opencode/bin`(不在PATH,调用须全路径);`~/.config/opencode/opencode.json`仅配Zhipu GLM(glm-5.3/glm-5.3-flash,bigmodel coding端点);Go套餐未装;**gstack skill在`~/.config/opencode/skills/gstack/SKILL.md`**可直接用
- 切泡括号规则本质:意识同步模式括号=大段意识/动作描写,按句号切会刷屏→括号段原子化单泡+灰斜体区分正文;嵌套括号按最外层配对,未闭合从(到末尾整体一泡
- 背景自定义复杂度:上传+background-image+半透明遮罩≈50行;iOS Safari上传若有坑降级纯色不阻塞主线
- ⚠Chat/.gitignore的!*.md白名单会捞handoff_Chat.md入库→已追加排除handoff*.md(主人要求handoff不入库)


## 2026-10-27 Session 2(切片1审计+修复)
- **tailscale serve GUI CLIError 是纸老虎**:`serve --bg --https=443 /chat http://127.0.0.1:8789` 命令行直发即成功;写命令报 CLIError≠不能 serve,别被 GUI 吓退
- **B4 正则转义坑(本次最大翻车)**:在 patch 脚本字符串里写正则,raw 串的 \w 被二次转义成字面反斜杠→围栏永远漏检。教训:补丁里带正则必须跟单测(真错误/代码块结尾/普通回复/无类围栏 4例),用 python 行下标直替比 file_patch 转义更稳
- **事件循环阻塞三件套**:懒构建(同步3-60s)必须进 asyncio.to_thread;worker 内 DB/LLM 轮询同理;FastAPI sync def 路由会自动进线程池但 async def 里千万别碰同步重活
- **jobs 表防误杀**:claim 必须带 `WHERE status='pending'` 守卫+rowcount 校验;重启恢复只回收 running(孤儿),pending 是上个进程没抢到的,留给本进程 worker
- **客户端轮询竞态**:history 拉全量与增量 poll 并发时,用 loadingHistory 标志暂停增量轮询+本地 rendered set 去重,双保险
- **format_error 特征**:GA agent 错误文本=尾随围栏 ```ExcType: msg @ file:line, fn -> `line` ```,按此识别 agent 内部错误,勿入 messages/宫殿
- OC/gstack 审计工作流跑通:opencode -e --mode plan + /review,报告落盘后 python 直读全文(stdout 会截断)


## 2026-10-27 Session 3(切片2坑)
- **前端补丁验证必先 reload**:浏览器tab若在补丁前加载,JS函数还是旧版→测试假失败(S2首测弹窗仍开,location.reload后绿)。改 index.html 后任何页面内测试先重载
- **kickstart 重启清空懒构建agent**:`updated_agents:0`≠坏,是SERVICES空;先发1条消息重建agent再PUT验热更
- **apiapp(8787) API Key 结构**:ga_config/api_keys.json = {sk-ga-*: 属主角色名};选 value==目标角色 的键,Bearer直发 /v1/chat/completions
- **OC审计标准姿势**:subprocess.Popen(cwd=repo,stdout=log文件,start_new_session=True)防管道阻塞;完成判据=进程退出+log稳定;报告去ANSI(re\x1b)再归档,python直读全文
- **桥接验证方法论**:跨通道记忆测试用"暗号"三步——通道A告知→查宫殿入库(source字段)→通道B(apiapp curl)提问;勿信单通道自证
- **curl -w %{http_code} 拼接易翻车**:python侧直接用 urllib.request 更稳(还能顺手json.loads)


## 2026-10-27 Session 4(切片3坑)
- **前端FormData必须 headers:{}**:api()默认json头,上传须让浏览器自动带multipart boundary,否则422
- **node不在裸PATH**(~/.nvm glob也空):JS语法检改用浏览器实测(location.reload后typeof四函数+行为测试)更硬
- **媒体文件名同毫秒撞名**:time*1000连发会撞→追加uuid6位(upload/voice两处都要)
- **gen_log兜底防重复搬运**:时间窗重叠会重复入库→按char记_GEN_CURSOR游标+ts升序+脏ts逐行try容错
- **meta是JSON对象勿整串覆盖**:写vision先读旧meta合并,否则dur等键互踩
- **media/in图片"红点"假警报**:img loading=lazy在视口外不加载≠404;验证比getBoundingClientRect或fresh load
- **测试断言先自查**:T3"6图→4行"断言误&&上notes token数→假FAIL;库内COUNT才是真相
- **afconvert失败留半截**:rc!=0也要unlink输出,防下次误判
- **审计报告归档命名**:audit_out.md→`审计_切片3_gstack_1027.md`(与前两切片一致,入库);research_切片3.md/temp_build_notes.md留本地不入
