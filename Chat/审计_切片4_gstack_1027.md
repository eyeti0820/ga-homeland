# Chat 项目切片4 (M4: PWA/聊天背景/失败重试/性能) 只读审计报告

**审计对象**： `Chat/chatapp.py`(1141行, diff基准 bak_s4)、`Chat/web/index.html`(511行)、`Chat/web/manifest.json`(新增)
**分级说明**： P0=严重缺陷， 应修复； P1=建议修复， 可选； P2=存疑/低危， 附说明供取舍。

---

## P0 严重(应修)

### P0-1 失败红!pill 在实时会话中永远不会出现， M4 重试入口对发送者不可达
- **文件**： `web/index.html:323-327` + `chatapp.py:950-954`、`chatapp.py:819-821`
- **问题**： pill 只在 `appendRow` 首次渲染某 seq 时创建(`if(msg.role==='user'&&mt.error){...ep.onclick=()=>retryMsg(...)}`)。但终败时后端只做 `_update_msg_meta(user_seq, lambda m: m.update(error=...))` —— UPDATE 既有行， **seq 不变**； 增量轮询 `/api/poll` 是 `WHERE char_id=? AND seq>?`, 永远不会重新投递该 user 行。结果： 用户当场只收到一条不可点击的灰 system 行 `f"{_friendly_err(e)}, 点红色感叹号可重试"`, 提示他去点一个本会话中根本不存在的按钮； 必须退出重进会话(走 /api/history)才能看到可点的 pill。重试再失败同理， pill 不会重现。
- **修法**： `/api/poll` 响应附带 `err_seqs`(如 `SELECT seq FROM messages WHERE char_id=? AND role='user' AND meta LIKE '%"error"%'`), 前端维护 seq→pill 映射做差量增删(同时覆盖“另一设备已重试成功需撤 pill”的场景)； 或后端打标/清标时同步插入一条引用 user_seq 的可交互行。

### P0-2 /api/retry 无在途 job 守卫， 双击/重开即产生同消息重复回复
- **文件**： `chatapp.py:1051-1074`、`web/index.html:496-498`
- **问题**： `_store()` 无条件 `INSERT INTO jobs(... VALUES (?, 'pending', ?, ?, ?))`, 既不校验该 user 行上次 job 是否终败， 也不校验是否已有 pending/running job 挂在同一 `user_seq`。前端 `retryMsg` 是 async 无防抖， 移动端双击 pill(响应返回前)会建两个 job; 另一个窗口: 重试 job 在途期间 `meta.error` 尚未清除， 重进会话 history 又渲染出 pill, 再点一次 → 两个 job 串行被 worker 消费 → **同一条用户消息得到两次完整 agent 回复**(双倍上下文/费用/媒体产出)。另注意该接口对任何 user 行(含已成功)都能建新 job, 等于隐藏的“重发”。
- **修法**： `_store()` 内先查 `SELECT id FROM jobs WHERE char_id=? AND user_seq=? AND status IN ('pending','running') LIMIT 1`, 命中则直接返回该 job(或 409); 顺手校验最近 job 状态为 error 才允许。前端 `retryMsg` 点击后立即置灰/标记防双击。

---

## P1 建议(可选)

### P1-1 背景图上传缺“读后”大小校验， chunked 编码可绕过 content-length 无界读内存
- **文件**： `chatapp.py:993-995`、`chatapp.py:1011`
- **问题**： 只有前置检查 `cl = int(request.headers.get("content-length") or 0); if cl > 8*1024*1024: return _err(413,...)`, 随后 `raw = await f.read()` 后仅校验魔数不校验 `len(raw)`。Transfer-Encoding: chunked 请求无 content-length(header 为 0)即可绕过， 任意大文件整读进内存。对比同文件 `/api/upload` 在 688 行读后有 `if len(raw) > 15*1024*1024` 双检， 此处遗漏。
- **修法**： 读后补 `if len(raw) > 8*1024*1024: return _err(413, "背景图过大(>8MB)")`。

### P1-2 B10 滚动重建泄漏旧 agent 线程且内存目的落空(依赖 agentmain 行为， 见说明)
- **文件**： `chatapp.py:602-606`、`chatapp.py:182`、`chatapp.py:962-968`
- **问题**： `SERVICES[char] = CharService(char)` 仅丢掉字典引用， 但旧 agent 是 `threading.Thread(target=agent.run, daemon=True).start()` 常驻线程， Thread 持有 bound method → agent 对象及其 150k+ 上下文**不会被回收**， 重建既没省内存又每次多泄漏一个常驻线程(长期运行累积)。另 `_agent_ctx_chars` 里 `sum(... for m in hist)` 遍历 agent 线程可能并发 append 的列表， 触发 RuntimeError 被 except 吞掉恒返回 0 → B10 可能静默永不触发。
- **修法**： 重建前先让旧 agent 优雅退出(发终止 task/调 stop, 视 agentmain 能力)； 至少在替换处 join/回收并打印线程数监控。若确认 agentmain 无停止机制， 建议本功能缓发。

---

## P2 存疑(说明)

- **P2-1 openChat 历史加载失败后退化渲染“最旧”消息**： `web/index.html:366-370` 失败时 `sinceSeq[cid]=0`, 随后 `pollOnce` 走 `chatapp.py:819-821` `WHERE ... seq>? ORDER BY seq ASC LIMIT 200` → 渲染最旧 200 条而非最新(旧实现 poll full=1 取的是最新 200)。修法： history 失败时回退 `full=1` 或提示重试。
- **P2-2 worker except 路径二次异常会把 job 卡在 running**： `chatapp.py:598-599` 若 `_finish_job(error)`/`_mark_user_error` 自身抛错(SQLite 长锁)， 直接被外层兜底吞掉， job 永久 running 直至重启 recover。修法： 终败落库再包一层 try/except。
- **P2-3 错误 system 行永久残留误导**： `chatapp.py:954` “点红色感叹号可重试”文案在重试成功(`_clear_error_hint` 只清 meta, 957-959)后仍留在历史流里。修法： 清标时软删/更新该 system 行。
- **P2-4 并发 bg_set 互删致背景 404**: `chatapp.py:1022-1027` “只留最新一张”在两个设备并发设图时互相 unlink 对方刚写、meta 尚未提交的文件 → meta 指向已删文件。单用户场景概率低， 说明即可。
- **P2-5 颜色正则放行非法位数**： `chatapp.py:1005` `#[0-9a-fA-F]{3,8}` 允许 5/7 位非法 hex, 前端赋值被忽略， 背景静默不变。收紧为 `{3}|{4}|{6}|{8}`。
- **P2-6 bgImgPick 失败后本地状态先行偏离服务端**： `web/index.html:493-495` 上传前就写 localStorage mode=image, 失败仅 toast; 下次 openCfg/fetchBgCfg 才拉平。可在失败回调里回滚。
- **P2-7 PWA 细节**： ① 无 Service Worker → Android Chrome 可能不弹安装提示(iOS A2HS 不受影响， 与“手机经 tailscale”主场景兼容)； ② `web/index.html:6` theme-color `#111116` 与 `manifest.json:10` `theme_color:#111118` 不一致(纯外观)。start_url/scope 用相对路径在根挂载与 /chat 前缀下均能正确解析， 此点无问题。
- **P2-8 可重试错误与“错误前媒体抢救”交织可能重复发媒体**： `chatapp.py:542-549` 先 `_emit_reply_media` 入库媒体行再 `raise RuntimeError("上游模型错误...")`(可重试)， 重试若再产出同路径 [FILE:] 媒体会二次入库； gen_log 兜底路径有 `_GEN_CURSOR` 游标缓解， 显式 [FILE:] 无缓解。低概率， 说明。
- **P2-9 `_update_msg_meta` 读改写无并发防护**： `chatapp.py:930-947` SELECT→UPDATE 非原子； 当前写者按角色 worker 串行， 实际无害， 若未来多写者需改 UPSERT/版本号。

---

**总体结论**： M4 切片工程质量总体良好(B6 唤醒无丢job竞态、img_seqs 去重、/api/retry 图片重校验、manifest 相对路径等均正确)， 但“失败重试”这一主打特性存在两个应修缺陷——实时会话中红！pill 不可见(P0-1)与重试无在途守卫可重复消费(P0-2)——修掉这两处并补上背景图读后限长后即可上线。
