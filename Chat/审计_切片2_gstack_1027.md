# OC/gstack 审计报告 · Chat切片2(世界书) · 2026-10-27

> 审计员: opencode glm/glm-5.3 (gstack /review) | 范围: git diff 切片2 = Chat/chatapp.py / Chat/web/index.html

# M2: persona之后注入共享世界书(读meta; 无则不注入)
                     self.ctx.apply_model(agent)
                     threading.Thread(target=agent.run, daemon=True).start()
                     self.agent = agent
+                    _apply_worldbook(agent)   # 竞态自愈: 构建期间若世界书被热更, 落成时再校一次
                     print(f"[chatapp] {self.char}: agent ready", flush=True)
                 except Exception as e:
                     self.build_error = str(e)
@@ -176,6 +178,51 @@ def _svc(char):
     return svc
 
 
+# ---------------- M2 共享世界书(meta表持久化 + 已构建agent热更新) ----------------
+
+WORLDBOOK_KEY = "worldbook"
+WORLDBOOK_TAG = "[共享世界书]"
+
+
+def get_worldbook():
+    with db() as conn:
+        r = conn.execute("SELECT value FROM meta WHERE key=?", (WORLDBOOK_KEY,)).fetchone()
+    return r["value"] if r else ""
+
+
+def set_worldbook(text):
+    with db() as conn:
+        conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
+                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
+                     (WORLDBOOK_KEY, text))
+
+
+def _apply_worldbook(agent):
+    """把最新世界书同步进agent.extra_sys_prompts: 替换旧段/新增/清空即删段.
+    agentmain.run 每次task重组sys_prompt都重读该列表(2026-10-27核实), 故热更新下一轮即生效."""
+    text = get_worldbook().strip()
+    prompts = agent.extra_sys_prompts
+    out, replaced = [], False
+    for p in prompts:
+        if isinstance(p, str) and p.startswith(WORLDBOOK_TAG):
+            if text and not replaced:
+                out.append(WORLDBOOK_TAG + "\n" + text)
+                replaced = True
+            # 旧[共享世界书]段一律丢弃(空=删除; 多段收敛为一段)
+        else:
+            out.append(p)
+    if text and not replaced:
+        out.append(WORLDBOOK_TAG + "\n" + text)
+    prompts[:] = out
+    return len(prompts)
+
+
+def _hot_update_worldbook():
+    """保存后同步所有已构建agent; 未构建角色在get_agent时注入(含构建期竞态自愈)"""
+    return sum(1 for svc in SERVICES.values()
+               if svc.agent is not None and _apply_worldbook(svc.agent))
+
+
 # ---------------- worker(jobs表驱动, 每角色一个串行worker) ----------------
 
 _LLM_RUNNING_RE = re.compile(r'\**LLM Running \(Turn \d+\)[^\n]*\n?')
@@ -416,6 +463,27 @@ def history(char_id: str = "", before_seq: int = 0, limit: int = 50):
     return {"msgs": [_msg_dict(r) for r in rows]}
 
 
+@app.get("/api/worldbook")
+def worldbook_get():
+    """M2: 世界书(五角色共享一份)"""
+    return {"worldbook": get_worldbook()}
+
+
+@app.put("/api/worldbook")
+async def worldbook_put(request: Request):
+    try:
+        body = await request.json()
+    except Exception:
+        return _err(400, "body须为JSON")
+    if not isinstance(body, dict) or not isinstance(body.get("worldbook", ""), str):
+        return _err(400, "body须为 {\"worldbook\": 文本}")
+    text = body.get("worldbook", "")
+    await asyncio.to_thread(set_worldbook, text)
+    updated = await asyncio.to_thread(_hot_update_worldbook)
+    print(f"[chatapp] worldbook已保存({len(text)}字), 热更新{updated}个agent", flush=True)
+    return {"ok": True, "updated_agents": updated}
+
+
 @app.get("/")
 async def index():
     return FileResponse(os.path.join(WEB_DIR, "index.html"))

$ git diff HEAD -- Chat/web/index.html
diff --git a/Chat/web/index.html b/Chat/web/index.html
index e1507d9..a8345b0 100644
--- a/Chat/web/index.html
+++ b/Chat/web/index.html
@@ -65,12 +65,22 @@ body{font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif;backgro
 #sendBtn:disabled{background:#9fd6b5}
 #toast{position:fixed;top:20%;left:50%;transform:translateX(-50%);background:rgba(0,0,0,.75);color:#fff;
   font-size:14px;border-radius:8px;padding:10px 18px;display:none;z-index:99}
+#wbBtn{position:absolute;right:12px;top:50%;transform:translateY(-50%);font-size:19px;cursor:pointer;line-height:1}
+.screen-header{position:relative}
+#wbScreen{position:fixed;inset:0;background:#fff;display:none;flex-direction:column;z-index:50}
+#wbText{flex:1;margin:12px;padding:10px;border:1px solid #ddd;border-radius:6px;font-size:15px;
+  line-height:1.6;resize:none;font-family:inherit;outline:none}
+#wbBar{display:flex;gap:10px;padding:0 12px 14px}
+#wbBar button{flex:1;border:none;border-radius:6px;padding:11px 0;font-size:16px}
+#wbCancel{background:#f2f2f2;color:#111}
+#wbSave{background:#07c160;color:#fff}
+#wbSave:disabled{background:#9fd6b5}
 </style>
 </head>
 <body>
 <div id="app">
   <div id="listScreen">
-    <div class="screen-header">Chat</div>
+    <div class="screen-header">Chat<span id="wbBtn" onclick="openWb()">📖</span></div>
     <div class="searchbox"><input id="searchInput" placeholder="搜索" oninput="renderList()"></div>
     <div id="charList"></div>
   </div>
@@ -89,6 +99,12 @@ body{font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif;backgro
   </div>
 </div>
 <div id="toast"></div>
+<div id="wbScreen">
+  <div class="screen-header"><span onclick="closeWb()" style="position:absolute;left:12px;top:50%;transform:translateY(-50%);font-size:26px;cursor:pointer;line-height:1">‹</span>共享世界书</div>
+  <textarea id="wbText" placeholder="五角色共享的世界设定, 保存后下一轮回复生效"></textarea>
+  <div id="wbBar"><button id="wbCancel" onclick="closeWb()">取消</button>
+  <button id="wbSave" onclick="saveWb()">保存</button></div>
+</div>
 <script>
 'use strict';
 const CHARS=[
@@ -112,10 +128,33 @@ function fmtTime(ts){const d=new Date(ts*1000),n=new Date();
   if(d.toDateString()===n.toDateString())return hm;
   return (d.getMonth()+1)+'-'+d.getDate()+' '+hm}
 function toast(t){const el=$('toast');el.textContent=t;el.style.display='block';setTimeout(()=>el.style.display='none',2200)}
-const BASE=location.pathname.replace(/[^/]*$/,'').replace(/\/$/,'');
+/*BASE:BEGIN 单测标记: temp下node提取本段测试, 改动须同步单测*/
+function calcBase(pathname){
+  let p=pathname.replace(/\/+$/,'');                 /* 剥尾斜杠: /chat/ -> /chat */
+  const last=p.substring(p.lastIndexOf('/')+1);
+  if(last && last.indexOf('.')>=0) p=p.substring(0,p.lastIndexOf('/')); /* 末段像文件名才剥: /index.html -> '' */
+  return p;
+}
+const BASE=calcBase(location.pathname);
+/*BASE:END*/
 async function api(path,opt){const r=await fetch(BASE+path,Object.assign({headers:{'Content-Type':'application/json'}},opt||{}));
   if(!r.ok)throw new Error((await r.json().catch(()=>({}))).error||('HTTP '+r.status));return r.json()}
 
+/* ---------- M2 世界书(五角色共享一份) ---------- */
+async function openWb(){
+  try{const r=await api('/api/worldbook');$('wbText').value=r.worldbook||''}
+  catch(e){toast('世界书加载失败')}
+  $('wbScreen').style.display='flex';
+}
+function closeWb(){$('wbScreen').style.display='none'}
+async function saveWb(){
+  const btn=$('wbSave');btn.disabled=true;btn.textContent='保存中…';
+  try{await api('/api/worldbook',{method:'PUT',body:JSON.stringify({worldbook:$('wbText').value})});
+      toast('已保存, 下一轮回复生效');closeWb()}
+  catch(e){toast('保存失败: '+e.message)}
+  finally{btn.disabled=false;btn.textContent='保存'}
+}
+
 /* ---------- 切泡 §5.4 ---------- */
 const OPEN_CLOSE={'(':')','（':'）','【':'】','[':']'};
 const END_PUNCT=new Set(['。','！','？','…','~','!','?','．','〜','\n']);

diff 已拿到,现在通读两个文件的完整上下文,重点看 `db()`、SERVICES、agent 构建流程、worker 与 meta 表。
→ Read Chat/chatapp.py
→ Read Chat/web/index.html

$ ls "/Users/potato/Library/Application Support/GenericAgent/runtime/app/frontends/" 2>/dev/null | head -20; rg -n "extra_sys_prompts" "/Users/potato/Library/Application Support/GenericAgent/runtime/app/agentmain.py" | head -20
DESKTOP_PET_README.md
__pycache__
apiapp.py
apiapp.py.bak.0920
apiapp.py.bak_1021_probe
at_complete.py
btw_cmd.py
char_config.py
char_config.py.bak.0922
char_config.py.bak.1020presplit
char_config.py.bak2_prejev
char_config.py.bak_1021_dedupe
char_config.py.bak_1022_kwfb
char_media.py
char_media.py.bak_1025
char_media.py.bak_1025_media
char_media.py.bak_1025_wximg
char_media.py.bak_1025_wxvoice_off
char_media.py.bak_1026_voice
chat_bubble.png
zsh:1: command not found: rg

diff 与两文件全文已通读。现在核实三个关键依赖声明:①agentmain 是否真的每轮 task 重读 `extra_sys_prompts`(热更新有效性前提);②`chatapp_common.build_done_text` 的清洗规范;③temp 下的 calcBase 单测是否同步。
$ rg -n "def build_done_text|tidy_lines|repair_folded_tags" "/Users/potato/Library/Application Support/GenericAgent/runtime/app/frontends/chatapp_common.py" | head; ls temp/ 2>/dev/null
zsh:1: command not found: rg
bak_1012_duty_reform

$ grep -n "extra_sys_prompts" "/Users/potato/Library/Application Support/GenericAgent/runtime/app/agentmain.py" | head -20; echo ---; grep -n "def run\|sys_prompt" "/Users/potato/Library/Application Support/GenericAgent/runtime/app/agentmain.py" | head -30
63:        self.extra_sys_prompts = []
172:            sys_prompt = get_system_prompt() + '\n'.join(self.extra_sys_prompts) + getattr(self.llmclient.backend, 'extra_sys_prompt', '')
---
36:    with open(os.path.join(script_dir, f'assets/sys_prompt{lang_suffix}.txt'), 'r', encoding='utf-8') as f: prompt = f.read()
63:        self.extra_sys_prompts = []
154:    def run(self):
172:            sys_prompt = get_system_prompt() + '\n'.join(self.extra_sys_prompts) + getattr(self.llmclient.backend, 'extra_sys_prompt', '')
173:            if self.peer_hint: sys_prompt += f"\n[Peer] 用户提及其他会话/后台任务状态时: temp/model_responses/ (只找近期修改的文件尾部)\n"
201:            gen = agent_runner_loop(self.llmclient, sys_prompt, raw_query, handler, TOOLS_SCHEMA,

$ grep -n "def build_done_text\|tidy_lines\|repair_folded_tags" "/Users/potato/Library/Application Support/GenericAgent/runtime/app/frontends/chatapp_common.py"; echo ---; find temp -name "*calc*" -o -name "*base*" 2>/dev/null; find . -maxdepth 2 -name "*.test.js" -o -maxdepth 2 -name "*calcbase*" 2>/dev/null | head
62:def repair_folded_tags(text):
71:def tidy_lines(text):
104:    text = repair_folded_tags(text)
107:    return re.sub(r"\n{3,}", "\n\n", tidy_lines(text)).strip() or "..."
254:def build_done_text(raw_text):
---

核实完毕。三项关键事实：①agentmain.py:172 确认 `run()` 循环内每次 task 重组 `sys_prompt = ... + '\n'.join(self.extra_sys_prompts) + ...`,"热更新下一轮生效"声明成立；②chatapp_common.py:104-107 的 `tidy_lines`+`repair_folded_tags` 只挂在 `build_done_text`(LLM 输出入库链)；③temp/ 下实际不存在 calcBase 单测。以下为审查报告。

---

# Chat 切片2 代码审查报告(/review · 独立审查)

**范围**: 工作区未提交 diff — `Chat/chatapp.py`(+68)、`Chat/web/index.html`(+43),忽略 duty.json。
**方法**: git diff + 两文件全文通读 + 运行时依赖核实(agentmain.py / chatapp_common.py)。

**总体评价**: 切片整体质量高——两次注入的构建期竞态自愈、`prompts[:] =` 原地收敛、WAL 短事务、前端纯 `.value`/`textContent` 渲染，均可看出设计有意识。发现严重级问题 2 条(均为低概率但后果不可自愈/不可恢复)，建议 4 条。

---

## 🔴 严重

**S1. SERVICES 字典迭代与懒构建插入的竞态，可致热更新半途失败**
- 位置: `Chat/chatapp.py:222`(`_hot_update_worldbook` 迭代 `SERVICES.values()`)对侧 `Chat/chatapp.py:177`(`_svc` 中 `SERVICES.setdefault` 新增 key)
- 场景: `PUT /api/worldbook` 在线程池迭代 SERVICES 期间，某角色**首条消息**恰好触发 worker 线程 `_svc()` → `setdefault` 插入新 key → 迭代器抛 `RuntimeError: dictionary changed size during iteration`。后果链: `worldbook_put` 500(chatapp.py:482 未捕获)，且 **DB 已写入但部分已构建 agent 未热更**，无重试机制——这些 agent 携带旧世界书直至下次保存。
- 概率: 窄(仅“新角色首次构建瞬间”与“保存”并发)，但属教科书级并发缺陷，后果静默。
- 修复(一行): `return sum(1 for svc in list(SERVICES.values()) if ...)` 迭代前快照；未及热更的 agent 本就靠 get_agent 首建注入兜底，快照即闭环。另建议 chatapp.py:481-482 的 `set_worldbook`/`_hot_update_worldbook` 分开 try,热更失败时返回 `{"ok": True, "updated_agents": -1}` 之类降级值而非 500,避免"已保存却报保存失败"误导重试。

**S2. 世界书加载失败仍打开编辑弹窗，空白保存即静默清空(不可恢复)**
- 位置: `Chat/web/index.html:144-148`(`openWb` catch 后 toast 但 `display='flex'` 照常执行)配合 `index.html:150-156`(`saveWb` 无条件 PUT)及后端 `chatapp.py:478-481`(空串合法值)
- 场景: 移动端网络抖动 → GET 失败，toast 仅 2.2 秒且不阻断 → 用户面对**空白 textarea**(与“世界书本就为空”无法区分)→ 点保存 → 服务器世界书被空串覆盖。meta 表仅存单份、无历史版本，数据**不可恢复**。
- 修复: 加载失败时不打开弹窗(`catch(e){toast('世界书加载失败');return}`);或打开但置脏标记，保存前若"加载失败 && textarea 为空"弹确认。

---

## 🟡 建议

**A1. PUT 缺 `worldbook` 键被静默当作"清空"**
- 位置: `Chat/chatapp.py:478-480`
- `body.get("worldbook", "")` 使 `PUT {"foo":1}` 通过校验并清空世界书。修复: `if not isinstance(body, dict) or not isinstance(body.get("worldbook"), str) or "worldbook" not in body: return _err(...)`(显式要求键存在)。

**A2. 世界书无长度上限，直接进 sys_prompt**
- 位置: `Chat/chatapp.py:480-481`;对比消息链路 `chatapp.py:414-415` 限 4000 字
- uvicorn 不限制请求体大小，超大文本将每个 agent 每个 task 全量注入，token/成本/上下文三重放大。建议按世界书场景设上限(如 50,000 字)并在前端 maxlength 同步提示。

**A3. `_hot_update_worldbook` 每 agent 各查一次库**
- 位置: `Chat/chatapp.py:220-223` → `chatapp.py:203`
- 一次保存 = 1 写 + 5 读(各开关一次 SQLite 连接)。量级无害(本地 WAL 毫秒级)，但语义上应读一次复用: `_apply_worldbook(agent, text=None)` 增加可选参数，`_hot_update_worldbook` 先 `text = get_worldbook()` 再传参。顺带把返回值从 `len(prompts)`(含 persona,口径混)改为布尔"是否变更"，`updated_agents` 计数更诚实(现状: persona 为 None 且世界书空时该 agent 少计)。

**A4. calcBase 单测未同步——注释与事实不符**
- 位置: `Chat/web/index.html:131`(`/*BASE:BEGIN 单测标记: temp下node提取本段测试, 改动须同步单测*/`)
- temp/ 下实测无任何 calcBase 相关测试(find 无结果)。本次恰好修改了 calcBase 却无单测护航。补齐单测(至少覆盖 `/chat`、`/chat/`、`/chat/index.html`、`/` 四例)，或删注释以免误导下个切片。

---

## ⚪ 豁免(已核实，无需改动)

| 项 | 结论 |
|---|---|
| 热更新有效性前提 | ✅ agentmain.py:172 确认 `run()` 每 task 重读 `extra_sys_prompts`,“下一轮生效”成立；在途 job 已组好的 prompt 不受影响，符合声明语义 |
| 构建期两次注入竞态(chatapp.py:157/161) | ✅ 设计自洽: 保存路径因 `agent is None` 跳过 → 落成时二次校准补上; `_apply_worldbook` 幂等收敛单段，并发 `prompts[:] =` 最后写者赢且无损; CPython 下 list 切片赋值/join 迭代无异常风险，至多一轮新旧混合，下轮自愈 |
| SQLite 多 worker 写 | ✅ 全部短事务独立连接，WAL+busy_timeout=5000; chat.db 无跨进程写者；并发 PUT 双写幂等收敛 |
| XSS | ✅ 世界书仅经 `textarea.value`(index.html:145)与后端字符串拼接，消息渲染链沿用 `esc()`/`textContent`(index.html:123/269),无 innerHTML 注入面 |
| 路径穿越 | ✅ 本切片无任何文件路径操作，worldbook 落 meta 表 |
| Prompt 注入 | ✅ 世界书属用户自编辑信任边界内的系统设定，注入目标是自家角色 agent,家庭内网场景(127.0.0.1 + tailscale)接受 |
| 清洗规范(tidy_lines+repair_folded_tags) | ✅ 边界正确: 该规范挂在 `build_done_text`(chatapp_common.py:104-107)面向 **LLM 输出**入库；世界书为**人工输入**，故意排版分行不应被 tidy_lines 合并，不套用是正确取舍。备注: 若未来加“AI 生成世界书”入口，该链路必须走 build_done_text 同款清洗 |
| calcBase 对含点目录误剥(如 `/foo.bar/`) | ✅ 实际部署 /chat 前缀不触发；四类常规路径(/、/chat、/chat/、/chat/index.html)行为均正确，旧 bug(无尾斜杠时 BASE 误剥为空)已修复 |
| 前端保存防重/双击 | ✅ `btn.disabled` 防重; openWb 并发 fetch 同值无害；描述中 `?char_id=` 参数被共享设计忽略，行为一致无害 |

---

**收口建议**: S1 一行快照 + S2 一行 return 即可消掉两个严重项，连同 A1(补键存在校验)合并为一个小补丁，不阻塞切片验收——但建议随本切片落地而非顺延。


---

## 修复记录(2026-10-27,本鸦)

| 项 | 修复 | 验证 |
|---|---|---|
| 🔴S1 | `chatapp.py` _hot_update_worldbook 改 `list(SERVICES.values())` 快照迭代 | 重启后实弹: 重建agent→PUT→`updated_agents:1`,无异常 |
| 🔴S2 | `index.html` openWb catch 分支加 `return`,加载失败不再开弹窗 | 浏览器mock fetch reject→弹窗保持 display:none(需重载页取新JS) |
| 🟡A1 | PUT 校验 `"worldbook" not in body`→400(空串=清空须显式传键) | `{"foo":1}`→400 新错误文案;`{"worldbook":...}`→200 |

结论: 🔴全修+🟡A1落地,无暂缓项。
