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
