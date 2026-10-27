# 切片3 M3媒体 代码审计报告

- 审计对象: `chatapp.py` / `web/index.html` 中 M3 新增代码(识图/发图/语音条/上传)
- 模式: 只读审计, 2026-10-27; 旧代码(M0/M1/M2)仅作上下文, 不在本报告范围
- 分级: **P0 严重(应修) / P1 建议(可选) / P2 存疑(说明)**

---

## P0 严重(应修)

### P0-1 上传即永久入库: 取消/未发送的图片仍会显示在聊天里
- **文件+行号**: `chatapp.py:612-618`(`_store_img` 在 upload 时即 `_insert_msg`); `web/index.html:382`(`dropAtt` 只删本地数组)、`web/index.html:333`(`closeChat` 清 pending)
- **问题**: `/api/upload` 落盘的同时就写入了 `messages(image_in)` 行。用户点 × 取消或切换角色后, 前端仅清空 `pending`, 但 DB 行已存在且无任何删除接口; 下次 `openChat` 全量 poll 会把这些"没发出去"的图片当已发消息渲染, 且 `_digest` 预览也会显示"[图片]"。数据永久残留。
- **修法**: 上传阶段只落盘暂存(返回路径不写库), 改由 `/api/msg` 在事务内把 `image_seqs` 对应暂存文件转正为 `image_in` 行; 或补 DELETE 接口并在 `dropAtt` 调用。

### P0-2 发图文件名毫秒时间戳无随机后缀, 同回复多图同名互覆
- **文件+行号**: `chatapp.py:400-401`
- **问题**: `name = f"{int(time.time()*1003)}{ext}"`。`_emit_reply_media` 在同一线程连续搬运多张图(多 `[FILE:]` 或 gen_log 补漏), `copy2`+insert 每张远小于 1ms 时两次取毫秒值极易落在同一 bucket → 生成同名文件, `shutil.copy2` 静默覆盖 → 两条 `image_out` 行指向同一文件, 前一张图丢失、气泡显示重复图。
- **修法**: 文件名追加随机段(如 `uuid.uuid4().hex[:6]`, 与 `chatapp.py:614` 上传命名同款)。

---

## P1 建议(可选)

### P1-1 上传不校验空文件与图片魔数
- **文件+行号**: `chatapp.py:607-616`
- **问题**: 0 字节文件或任意非图片字节(改扩展名)都能通过: 会写入 `media/in` 并被 `/media` 静态服务, 识图时才以"(识图失败)"兜底, 白占存储并产生脏消息行。
- **修法**: `raw` 为空直接 400, 并校验前几字节魔数(jpg/png/webp/gif/heic)。

### P1-2 前端多选不限量, 服务端 `[:3]` 静默截断
- **文件+行号**: `web/index.html:386-391`(onPicked 无数量上限) / `chatapp.py:640`
- **问题**: 用户可选 5 张全部上传并乐观渲染 5 个气泡, 但服务端只取前 3 个进 `img_seqs` —— 聊天显示 5 张、模型只见过 3 张, 且无任何提示。
- **修法**: 前端 `pending.length >= 3` 时拦截提示; 或服务端超 3 张返回 400。

### P1-3 媒体行没有"最多4条"上限, 与注释/文档不符
- **文件+行号**: `chatapp.py:436-451`(docstring 声称最多4条, 循环无计数)
- **问题**: LLM 输出 N 个 `[FILE:]` + gen_log 补漏可产生 N 条媒体行、N 次 copy/转码, 无资源与刷屏上限。
- **修法**: emit 循环加计数器, 超 4 条 break 并打日志。

### P1-4 gen_log 时间窗 `start-2` 跨 job 重叠 → 重复发图/语音
- **文件+行号**: `chatapp.py:505`(t0=start-2)、`411-432`(`_scan_gen_log` 无跨 job 去重)
- **问题**: 同角色连续两个 job 间隔小于 2s 时(worker 轮询 1s, 很常见), 上一 job 的 gen_log 条目 ts 落在下一 job 的 `[start-2, now)` 窗口内且 char 匹配 → 同一文件被再次搬运成新的 `image_out`/`voice` 行, 用户看到重复媒体。
- **修法**: 在 meta 表记录 per-char 已处理的 gen_log ts 游标, 替代时间窗回看。

### P1-5 15MB 校验晚于 multipart 完整解析
- **文件+行号**: `chatapp.py:597-607`
- **问题**: `await request.form()` 已把整个 multipart 读完(大 part 会 spool 到磁盘)之后才检查 `len(raw) > 15MB`, 超大请求可先占用内存/临时磁盘。本服务仅 127.0.0.1+tailscale, 暴露面小, 故列 P1。
- **修法**: 读 form 前预检 `Content-Length`, 或用 Starlette multipart 流式限额。

### P1-6 opus→m4a 失败静默丢语音
- **文件+行号**: `chatapp.py:365-384`、`390-396`(`_emit_media_row` 失败返回 `""`)
- **问题**: 正文 text 行已先入库, 之后语音转换失败时无任何用户可见提示(也不产生 note), 用户只看到文字, "语音发了但没到"无从分辨。
- **修法**: 转换失败时插入一条 system 提示(或在前端 voice 行加失败占位)。

### P1-7 `_media_fp` 无路径归一化防护(加固)
- **文件+行号**: `chatapp.py:309-312`
- **问题**: 直接 `os.path.join(MEDIA_DIR, p[7:])`, 不挡 `..`。当前 `media_path` 全部由服务端生成(上传名/L614), 实际不可利用, 但一旦未来有任何来源写入该列即成穿越点。
- **修法**: `os.path.realpath` 后校验 `startswith(MEDIA_DIR + os.sep)` 再返回。

---

## P2 存疑(说明)

### P2-1 `renderAtt` 用 innerHTML 拼接 src(当前不可注入, 模式脆弱)
- **文件+行号**: `web/index.html:378`
- 说明: `p.media_path` 来自 `/api/upload` 响应, 由服务端时间戳+uuid+扩展白名单生成, 不含可注入字符, 现状安全; 但属 innerHTML 拼 URL 模式, 建议改 `createElement` + `.src` 属性赋值, 与 `appendRow` 一致。

### P2-2 `[FILE:]`/gen_log 绝对路径的信任边界(设计取舍, 留档)
- **文件+行号**: `chatapp.py:443-449`、`427`
- 说明: LLM 输出或 gen_log 指向磁盘上任意已存在的 `.jpg/.png/.webp/.opus` 绝对路径都会被复制进公开 `/media` 下。等价 fsapp 成熟方案、单用户 tailscale 内网, 风险可接受; 相对路径已被 `os.path.isabs` 正确拦截(边界符合预期)。仅作记录。

### P2-3 gen_log 扫描的三处小缺陷
- **文件+行号**: `chatapp.py:417-424`
- 说明: ① 五角色共用 gen_log, 只回看尾 40 行, 高并发下本角色条目可能被挤出窗口而漏补漏; ② `reversed` 导致媒体行按新→旧顺序入库(与正文顺序不一致); ③ `float(r.get("ts"))` 若 ts 为非数值字符串会抛 ValueError 被外层捕获, 中断本次整段扫描。修法: 扩大窗口/按 ts 升序输出/float 移入行级 try。

### P2-4 `img_seqs` 不去重
- **文件+行号**: `chatapp.py:640`(入库)、`344`(读取)
- 说明: 前端重复传 `[5,5,5]` 时校验用 `set` 能过, 但落库为 `"5,5,5"`; 读取侧 `seq IN (5,5,5)` 只识图一次, 行为无害但数据脏。修法: 入库前 `list(dict.fromkeys(...))`。

### P2-5 afconvert 失败/超时残留半截 m4a 且仍被静态服务
- **文件+行号**: `chatapp.py:371-375`
- 说明: rc!=0 或 60s 超时被 kill 后, `out` 可能已写一半, 未插入 DB 却留在 `voice/` 并可经 `/media/voice/` 访问; 另外 afinfo 失败时 `dur=0` 前端显示 `0"`。修法: 失败分支 `os.unlink(out)`(ignore errors)。注: `subprocess.run` 超时会自动 kill 子进程, 无进程泄漏; 全程 list 传参无 shell 注入。

### P2-6 语音文件名同毫秒+同 basename 跨角色可碰撞
- **文件+行号**: `chatapp.py:369`
- 说明: `{ms}_{basename}.m4a`, 不同角色 worker 经线程池并发转码, 同 ms 同源名的概率低但存在, 碰撞即覆盖。修法: 同 P0-2 加随机后缀。

### P2-7 vision 注记整列覆盖式回写
- **文件+行号**: `chatapp.py:357-359`
- 说明: `UPDATE messages SET meta=?` 直接覆盖 image_in 行原有 meta(当前该类行 meta 恒为 NULL, 无实际影响), 未来加字段会被清掉。修法: 读旧值合并 JSON。另: 增量 poll 已 rendered 的行不会重渲染, 用户自己发的图要重进聊天屏才能看到 👁 识图注记(`web/index.html:291,313`), 属可接受的表现层限制。

### P2-8 media/{in,out,voice} 无 GC, 无限增长
- **文件+行号**: `chatapp.py:612-618`(in)、`400-401`(out)、`369`(voice)
- 说明: 上传(含 P0-1 的未发送图)、发图、语音均只增不减, 无引用文件(如 P2-5 残留)永不清理。修法: 定期比对 messages.media_path 清理孤儿文件。

### P2-9 heic 上传后识图大概率失败
- **文件+行号**: `chatapp.py:322`(mime 一律按 png/jpeg 判)、`610`(允许 heic)
- 说明: iPhone 实拍 heic 能上传, 但 base64 后按 `image/jpeg` 送 GLM-4V 多半解析失败 → "(识图失败)"。修法: 入库前 `sips` 转 jpg, 或上传端拒绝 heic。

---

## 已核查无问题项(切片3范围内)

- **前端 XSS**: `appendRow` 的 `img.src`(`index.html:289`)、`audio.src`(`295`)、`zoomImg`(`393`)均为 DOM 属性赋值而非 innerHTML, `vision`/`dur` 走 `textContent`, 注入面不存在; `renderAtt` 见 P2-1。
- **media 静态路由**: `StaticFiles` 自带穿越防护, 目录列举默认关闭(`chatapp.py:732-733`); `/chat` 前缀剥离中间件与 mount 组合正常。
- **afconvert/afinfo**: list 传参无 shell 注入; `subprocess.run(timeout=)` 超时自动 kill, 无子进程泄漏; 文件句柄均用 with 管理。
- **`_take_job` 原子领取**: SELECT+带 status 条件的 UPDATE 校验 rowcount(`chatapp.py:257-268`), 同角色单 worker 串行, 无双领竞态。
- **SERVICES 迭代**: M3 未新增对 `SERVICES` 的迭代; `_svc` 的 `setdefault` 幂等(与 M2 S1 快照迭代不冲突)。
- **img_seqs 迁移**: `init_db` 建表后 `PRAGMA table_info` 探测 + `ALTER ADD COLUMN`(`chatapp.py:129-131`)对新旧库均幂等; 存量 pending job 的 NULL 经 `or ""` 兜底; 消息+job 同事务写入(B2 保持)。
- **image_seqs 归属校验**: `/api/msg` 校验本角色/user/image_in 行(`chatapp.py:645-653`), worker 侧 `_describe_job_images` 二次过滤, 防跨角色串图。
- **图片-only 消息**: content 落 `[图片]` 非空串, `_process_job` 的"user消息不存在"检查不会误杀(`chatapp.py:458-460, 659`)。
