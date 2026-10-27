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

## 2026-10-27 深夜 · 开发模式定案(D7)+两条新需求
- Go套餐未装实锤(opencode v1.18.30@~/.opencode/bin 只配GLM-5.3)→主人拍板:**小梅直接开发**,一session一切片+handoff_Chat.md交接(不入库);每切片OC+gstack审计→关闭→三件套+commit→更新handoff进下一切片
- 切泡新规则(主人补充):()【】=原子段整体一泡不切(意识同步模式括号是大段描写,按句号切会刷屏),灰斜体样式,不参与压缩合并→方案§5.4已重写
- D8背景自定义(主人新增):上传图+rgba白0.75遮罩+纯色兜底,评估~50行一期做→方案§5.7新增
- 切片划分落定:M0+M1=切片1(下session)/M2=切片2/M3=切片3/M4+背景=切片4→方案§6+task_plan已同步
- 本轮产出:方案6处patch+task_plan切片化+handoff_Chat.md施工单就位+本commit


## 2026-10-27 Session 2(切片1收口:审计+修复+验收+commit)
- serve 卡点破案:GUI CLIError=误信;用 `tailscale serve --bg --https=443 /chat http://127.0.0.1:8789` 直接成功;服务绑127.0.0.1(不暴露0.0.0.0,主人拍板),手机 /chat 实测全可用
- OC+gstack 审计(GLM-5.3):报告 `审计_切片1_gstack_1027.md`;1🔴前端(历史×轮询竞态S1)+2🔴后端(事件循环阻塞S2/懒构建冻结S3)+13🟡;豁免区(清洗/tidy_lines/agentmain)全绿=清洗链路合规
- 修复:chatapp.py 18处(S1守卫/S2合并查询+S3懒构建+worker重活进asyncio.to_thread/B1 claim守卫/B2单事务/B3挂载content/B4错误围栏_ERR_FENCE_RE/B5非dict→400/B13 synchronous=NORMAL)+web 6处(S1/B9服务器ts/B11自误未读/B12 URL编码)
- B4 惊魂:补丁正则转义翻车(\w写成\w字面)漏检真错误,单测4例抓获→行下标直替+单测锁定→重启复检✓
- 运行验收:E2E夏以昼32s回复「收到了。」/B5拦截400/chars合并typing/health ok;服务 kickstart 后自动恢复
- 三件套翻页:切片1→done;B6(worker事件唤醒)/B7(uvicorn直启绕锁)/B8(线程池耗尽)/B10(滚动token压缩)暂缓→切片4清单
- commit:chatapp.py+web+审计报告+三件套(中文message);handoff_Chat.md 重写=切片2施工单(不入库)


## 2026-10-27 Session 3(切片2收口:世界书+桥接+审计+修复+commit)
- 施工:worldbook GET/PUT(meta表)+_apply_worldbook热更+构建期注入;前端📖弹窗;calcBase修复(无尾斜杠BASE误剥空→保留目录前缀,9/9)
- E2E:v1星轨夜市命中(seq18)→PUT蓝洞v2→qiyu追问+shenxinghui新构建双命中;中文引号/换行/emoji不炸
- **宫殿桥接实锤**:Chat告夏以昼暗号"珊瑚海之约"→宫殿2条入库(source=wechat:chatapp)→apiapp 8787跨通道(8787 key=ga_config/api_keys.json,键值=属主角色名)答上→D5设计闭环
- OC+gstack审计(GLM-5.3):报告`审计_切片2_gstack_1027.md`;🔴S1热更迭代SERVICES竞态(list快照)/🔴S2加载失败仍开弹窗致空覆盖(catch return)/🟡A1缺worldbook键静默清空(400)——全修实弹验证(updated_agents=1/mock fetch弹窗none/400文案)
- 验收:重启持久✓/chars健康✓/9项回归全绿
- 三件套翻页:切片2→done,Current Phase→切片3;handoff重写=切片3施工单(不入库)


## 2026-10-27 Session 4(切片3收口:M3媒体三件+审计修复+commit)
- 施工:`/api/upload`(魔数+heic sips转jpg+16MB预检+uuid文件名)→image_in;`/api/msg`挂img_seqs→worker识图注入+meta.vision;回复[FILE:]+gen_log兜底(游标去重/升序/120行)→image_out|voice行;opus→ffmpeg→afconvert→m4a(iOS可播)+ffprobe时长;前端+按钮/attBar预览/×撤回/四类渲染/lazy;新增`/api/att_del`(撤回:行+文件双删,校验角色归属)
- E2E双通:识图入(红图→"是红色的"+GLM-4V注记)/媒体出(图+语音m4a dur气泡);DOM断言全绿;media/in"红点"=lazy非bug
- OC审计(GLM-5.3):P0×2+P1×7+P2×9;修P0全+P1×5+P2×6共13处(att_del双消/uuid防撞/realpath防穿越/魔数+预检/gen_log游标/硬上限4/meta合并/半截m4a清理/renderAtt createElement/限3张/closeChat孤儿清理);暂缓P1-6/P2-8/P2-4→切片4
- 验证:py_compile+新鲜库单测(穿越拒/游标去重/上限封顶)+活体E2E(假图400/空400/att_del双删/越权false/文本冒烟202)+浏览器行为测试(DataTransfer注入File→×撤回→库行+文件双消 seq37)
- commit 9b22c3f(416+/13-);审计报告归档`审计_切片3_gstack_1027.md`;handoff重写=切片4施工单(不入库)
