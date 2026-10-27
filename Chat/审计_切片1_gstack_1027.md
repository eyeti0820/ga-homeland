
> build · glm-5.3

→ Read /Users/potato/.config/opencode/skills/gstack/SKILL.md
✱ Glob "Chat/**" 11 matches
→ Read Chat/chatapp.py
→ Read Chat/web/index.html

$ ls "$HOME/Library/Application Support/GenericAgent/runtime/app" 2>/dev/null | head -50; ls "$HOME/Library/Application Support/GenericAgent/runtime/app/frontends" 2>/dev/null | head -30
CONTRIBUTING.md
LICENSE
README.md
TMWebDriver.py
__pycache__
agent_loop.py
agentmain.py
agentmain.py.bak_0927_mephisto
assets
frontends
ga
ga.cmd
ga.py
ga_cli
ga_config
ga_patches
hub.pyw
launch.pyw
llmcore.py
llmcore.py.bak_1026_thinkfix
logs
memory
mykey.py
mykey.py.bak.0926b
mykey.py.bak_1020_gemma
mykey.py.bak_1026_1402
mykey.py.bak_1026_thinkfix
mykey_template.py
mykey_template_en.py
plugins
pyproject.toml
reflect
sche_tasks
simphtml.py
temp
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
chatapp_common.py
chatapp_common.py.bak_1005_gemfold
chatapp_common.py.bak_1025_abc
conductor.html
conductor.py
conductor_im_plugins
continue_cmd.py
cost_tracker.py
data_backup.py
dcapp.py

→ Read Chat/开发方案_Chat.md

$ grep -n "def build_done_text\|def tidy_lines\|def repair_folded_tags\|def ensure_single_instance\|def extract_files\|def strip_files\|FILE_HINT" -A 30 "$HOME/Library/Application Support/GenericAgent/runtime/app/frontends/chatapp_common.py" | head -150
39:FILE_HINT = "If you need to show files to user, use [FILE:filepath] in your response."
40-TAG_PATS = [r"<" + t + r">.*?</" + t + r">" for t in ("think", "thinking", "summary", "tool_use", "file_content")]
41-PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
42-RESTORE_GLOBS = (
43-    os.path.join(PROJECT_ROOT, "temp", "model_responses", "model_responses_*.txt"),
44-    os.path.join(PROJECT_ROOT, "temp", "model_responses_*.txt"),
45-)
46-RESTORE_BLOCK_RE = re.compile(
47-    r"^=== (Prompt|Response) ===.*?\n(.*?)(?=^=== (?:Prompt|Response) ===|\Z)",
48-    re.DOTALL | re.MULTILINE,
49-)
50-HISTORY_RE = re.compile(r"<history>\s*(.*?)\s*</history>", re.DOTALL)
51-SUMMARY_RE = re.compile(r"<summary>\s*(.*?)\s*</summary>", re.DOTALL)
52-
53-
54-# --- 2026-10-05: Gemini中转(反重力量等)token级折行防御 ---
55-# 渠道会在正文/标签中间插裸\n: 句子被拦腰折断, <summary>被折成"<\nsummary>"躲过TAG_PATS剥除
56-_FOLD_TAG_NAMES = ("think", "thinking", "summary", "tool_use", "file_content")
57-_TERM_END = set("。！？!?…；;：:）)】]」』\"'”’~～—")
58-_LINE_START_OK = re.compile(r"^[\u4e00-\u9fffA-Za-z]")
59-_STRUCT_PREFIX = ("```", "|", "#", "- ", "* ", ">")
60-
61-
62:def repair_folded_tags(text):
63-    """先把标签内部被渠道折行插入的空白抹掉, 恢复完整<summary>等标签再剥除。"""
64-    text = text or ""
65-    for t in _FOLD_TAG_NAMES:
66-        pat = re.compile(r"<\s*/?\s*" + t + r"\s*>", re.IGNORECASE)
67-        text = pat.sub(lambda m: re.sub(r"\s+", "", m.group(0)), text)
68-    return text
69-
70-
71:def tidy_lines(text):
72-    """把折行插进句子中间的裸\\n拼回去: 只合并相邻两行, 空行分段保留。
73-    判据: 上一行非空且不以句读收尾, 且本行以中文/字母开头(数字/标点/结构行不并);
74-    ```代码块/表格行内部永不合并。"""
75-    out = []
76-    in_fence = False
77-    for ln in (text or "").split("\n"):
78-        s = ln.strip()
79-        if not s:
80-            out.append("")
81-            continue
82-        if s.startswith("```"):
83-            in_fence = not in_fence
84-            out.append(ln.rstrip())
85-            continue
86-        if in_fence:
87-            out.append(ln.rstrip())
88-            continue
89-        if out and out[-1]:
90-            prev = out[-1].rstrip()
91-            if (prev and prev[-1] not in _TERM_END
92-                    and _LINE_START_OK.match(s)
93-                    and not s.startswith(_STRUCT_PREFIX)
94-                    and not prev.lstrip().startswith(("```", "|"))):
95-                sep = " " if (prev[-1].isascii() and prev[-1].isalnum()
96-                              and s[0].isascii() and s[0].isalnum()) else ""
97-                out[-1] = prev + sep + s
98-                continue
99-        out.append(ln.rstrip())
100-    return "\n".join(out)
101-
--
110:def extract_files(text):
111-    return re.findall(r"\[FILE:([^\]]+)\]", text or "")
112-
113-
114:def strip_files(text):
115-    return re.sub(r"\[FILE:[^\]]+\]", "", text or "").strip()
116-
117-
118-def split_text(text, limit):
119-    text, parts = (text or "").strip() or "...", []
120-    while len(text) > limit:
121-        cut = text.rfind("\n", 0, limit)
122-        if cut < limit * 0.6:
123-            cut = limit
124-        parts.append(text[:cut].rstrip())
125-        text = text[cut:].lstrip()
126-    return parts + ([text] if text else []) or ["..."]
127-
128-
129-def _restore_log_files():
130-    files = []
131-    for pattern in RESTORE_GLOBS:
132-        files.extend(glob.glob(pattern))
133-    return sorted(set(files))
134-
135-
136-def _restore_text_pairs(content):
137-    users = re.findall(r"=== USER ===\n(.+?)(?==== |$)", content, re.DOTALL)
138-    resps = re.findall(r"=== Response ===.*?\n(.+?)(?==== Prompt|$)", content, re.DOTALL)
139-    restored = []
140-    for u, r in zip(users, resps):
141-        u, r = u.strip(), r.strip()[:500]
142-        if u and r:
143-            restored.extend([f"[USER]: {u}", f"[Agent] {r}"])
144-    return restored
--
185:    if text.startswith(FILE_HINT):
186:        text = text[len(FILE_HINT):].lstrip()
187-    if "### 用户当前消息" in text:
188-        text = text.split("### 用户当前消息", 1)[-1].strip()
189-    return text
190-
191-
192-def _native_response_summary(response_body):
193-    try:
194-        blocks = ast.literal_eval((response_body or "").strip())
195-    except Exception:
196-        return ""
197-    if not isinstance(blocks, list):
198-        return ""
199-    text_parts = []
200-    for block in blocks:
201-        if isinstance(block, dict) and block.get("type") == "text":
202-            text = block.get("text", "")
203-            if isinstance(text, str) and text:
204-                text_parts.append(text)
205-    match = SUMMARY_RE.search("\n".join(text_parts))
206-    return (match.group(1).strip() if match else "")[:500]
207-
208-
209-def _restore_native_history(content):
210-    blocks = RESTORE_BLOCK_RE.findall(content or "")
211-    if not blocks:
212-        return []
213-    pairs = []
214-    pending_prompt = None
215-    for label, body in blocks:
216-        if label == "Prompt":
--
254:def build_done_text(raw_text):
255-    files = [p for p in extract_files(raw_text) if os.path.exists(p)]
256-    body = strip_files(clean_reply(raw_text))
257-    if files:
258-        body = (body + "\n\n" if body else "") + "\n".join(f"生成文件: {p}" for p in files)
259-    return body or "..."
260-
261-
262-def public_access(allowed):
263-    return not allowed or "*" in allowed
264-
265-
266-def to_allowed_set(value):
267-    if value is None:
268-        return set()
269-    if isinstance(value, str):
270-        value = [value]

$ grep -n "def put_task\|def abort\|def run\b\|class GeneraticAgent\|def continue_cmd\|extra_sys_prompts" "$HOME/Library/Application Support/GenericAgent/runtime/app/agentmain.py" | head -30
63:        self.extra_sys_prompts = []
112:    def abort(self):
133:    def put_task(self, query, source="user", images=None):
154:    def run(self):
172:            sys_prompt = get_system_prompt() + '\n'.join(self.extra_sys_prompts) + getattr(self.llmclient.backend, 'extra_sys_prompt', '')

$ sed -n 150,190p "$HOME/Library/Application Support/GenericAgent/runtime/app/frontends/chatapp_common.py"; echo ----; sed -n 100,200p "$HOME/Library/Application Support/GenericAgent/runtime/app/agentmain.py"
except Exception:
        return None
    if not isinstance(prompt, dict) or prompt.get("role") != "user":
        return None
    if not isinstance(prompt.get("content"), list):
        return None
    return prompt


def _native_prompt_text(prompt):
    texts = []
    for block in prompt.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str) and text.strip():
                texts.append(text)
    return "\n".join(texts).strip()


def _native_history_lines(prompt_text):
    match = HISTORY_RE.search(prompt_text or "")
    if not match:
        return []
    restored = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if line.startswith("[USER]: ") or line.startswith("[Agent] "):
            restored.append(line)
    return restored


def _native_first_user_line(prompt_text):
    text = (prompt_text or "").strip()
    if not text or "<history>" in text or text.startswith("### [WORKING MEMORY]"):
        return ""
    if text.startswith(FILE_HINT):
        text = text[len(FILE_HINT):].lstrip()
    if "### 用户当前消息" in text:
        text = text.split("### 用户当前消息", 1)[-1].strip()
    return text

----
        self.llmclient.last_tools = ''
        load_tool_schema()
    def list_llms(self): 
        self.load_llm_sessions()
        return [(i, self.get_llm_name(b), i == self.llm_no) for i, b in enumerate(self.llmclients)]
    def get_llm_name(self, b=None, model=False):
        b = self.llmclient if b is None else b
        if isinstance(b, dict): return 'BADCONFIG_MIXIN'
        if model: return b.backend.model.lower()
        return f"{type(b.backend).__name__.replace('Session', '')}/{b.backend.name}"
    def get_ctx_multiplier(self): return getattr(self.llmclient.backend, 'maxlen_multiplier', 1.0)

    def abort(self):
        if not self.is_running: return
        print('Abort current task...')
        self.stop_sig = True
        if self.handler is not None: self.handler.code_stop_signal.append(1)
        for sess in getattr(self.llmclient.backend, '_sessions', [self.llmclient.backend]):
            sess.should_stop = lambda: self.stop_sig  # live read; cleared by run()'s finally
            try:  # wake a recv() blocked in another thread. Verified on Windows: shutdown()/close() do NOT
                  # wake it (makefile refcount defers real closesocket); _real_close() does -> ChunkedEncodingError
                import socket as _socket
                raw = sess.active_response.raw
                fp = getattr(getattr(raw, '_fp', None), 'fp', None)  # http.client response -> buffered socket file
                sock = fp.raw._sock if fp else raw.connection.sock   # SocketIO._sock (SSL-wrapped OK); fallback urllib3 conn
                try: sock.shutdown(_socket.SHUT_RDWR)  # for non-Windows semantics
                except OSError: pass
                try: sock._real_close()  # CPython internal; bypasses refcount -> actual closesocket
                except AttributeError: sock.close()
            except Exception: pass
            try: sess.active_response.close()
            except Exception: pass
            
    def put_task(self, query, source="user", images=None):
        display_queue = queue.Queue()
        self.task_queue.put({"query": query, "source": source, "images": images or [], "output": display_queue})
        return display_queue

    # i know it is dangerous, but raw_query is dangerous enough it doesn't enlarge
    def _handle_slash_cmd(self, raw_query, display_queue):
        if not raw_query.startswith('/'): return raw_query
        if _sm := re.match(r'/session\.(\w+)=(.*)', raw_query.strip()):
            k, v = _sm.group(1), _sm.group(2)
            vfile = os.path.join(script_dir, 'temp', v)
            if os.path.isfile(vfile): v = open(vfile, encoding='utf-8').read().strip()
            try: v = json.loads(v)  # cover number parsing
            except (json.JSONDecodeError, ValueError): pass
            setattr(self.llmclient.backend, k, v)
            display_queue.put({'done': smart_format(f"✅ session.{k} = {repr(v)}", max_str_len=500), 'source': 'system'})
            return None
        if raw_query.strip() == '/resume':
            return r'帮我看看最近有哪些会话可以恢复。读model_responses/目录，按修改时间取最近10个文件，从每个文件里找最后一个<history>...</history>块，用一句话总结每个会话在聊什么，列表给我选。注意读文件后要把字面的\n替换成真换行才能正确匹配。'
        return raw_query

    def run(self):
        while True:
            task = self.task_queue.get()
            if isinstance(task, str): break
            raw_query, source, display_queue = task["query"], task["source"], task["output"]
            task_images = task.get("images") or []  # P1 直读图: 取出被丢弃的图片(仅vision模型走直传, 其余模型保持原工具读图链路)
            raw_query = self._handle_slash_cmd(raw_query, display_queue)
            if raw_query is None:
                self.task_queue.task_done(); continue
            self.is_running = True; self._current_queue = display_queue
            if len(raw_query) > 2000:
                task_file = os.path.join(script_dir, 'temp', f'user_prompt_{os.getpid()}_{time.time_ns()}.md')
                with open(task_file, 'w', encoding='utf-8') as f: f.write(raw_query)
                raw_query = f'Long user prompt saved to {task_file}. Read and execute.'
            self.all_outputs.append({"input": raw_query, "outputs": []})
            if len(self.all_outputs) > 10000: self.all_outputs = self.all_outputs[-5000:]
            rquery = smart_format(raw_query.replace('\n', ' '), max_str_len=200)
            self.history.append(f"[USER]: {rquery}")
            sys_prompt = get_system_prompt() + '\n'.join(self.extra_sys_prompts) + getattr(self.llmclient.backend, 'extra_sys_prompt', '')
            if self.peer_hint: sys_prompt += f"\n[Peer] 用户提及其他会话/后台任务状态时: temp/model_responses/ (只找近期修改的文件尾部)\n"
            handler = GenericAgentHandler(self, self.history, os.path.join(script_dir, 'temp'))
            if getattr(self, 'no_print', False): handler.print = lambda *a, **k: None
            if self.handler and 'key_info' in self.handler.working: 
                ki = re.sub(r'\n\[SYSTEM\] 此为.*?工作记忆[。\n]*', '', self.handler.working['key_info'])  # 去旧
                handler.working['key_info'] = ki
                handler.working['passed_sessions'] = ps = self.handler.working.get('passed_sessions', 0) + 1
                if ps > 0: handler.working['key_info'] += f'\n[SYSTEM] 此为 {ps} 个对话前设置的key_info，若已在新任务，先更新或清除工作记忆。\n'
            self.handler = handler  # although new handler, the **full** history is in llmclient, so it is full history!
            self.llmclient.log_path = self.log_path
            if self.force_non_stream:
                self.llmclient.backend.stream = False
                self.llmclient.backend.read_timeout = max(self.llmclient.backend.read_timeout, 1200)
            # P1 直读图: vision模型(Gemini/Claude/GPT系等)把图片直接嵌入首条user消息, 模型直接看图不调工具(中转Gemini带tool_result会空回)
            iuc = None
            if task_images:
                _m = str(getattr(self.llmclient.backend, 'model', '')).lower()
                if any(k in _m for k in ('gemini', 'claude', 'gpt-4', 'gpt-5', 'o3', 'o4', '-vl', 'vision')):
                    import base64 as _b64, mimetypes as _mt
                    _blocks = [{"type": "text", "text": raw_query + "\n\n(系统注: 请勿调用任何图像裁剪/缩放/搜索等内置工具,直接依据完整图片作答。)"}]
                    for _p in task_images[:6]:
                        try:
                            _mime = _mt.guess_type(str(_p))[0] or 'image/jpeg'
                            _b = _b64.b64encode(open(_p, 'rb').read()).decode()
                            _blocks.append({"type": "image_url", "image_url": {"url": f"data:{_mime};base64,{_b}"}})
                        except Exception as _e:
                            print(f"[直读图] 图片读取失败 {_p}: {_e}")
                    if len(_blocks) > 1: iuc = _blocks

$ grep -n "def recall\|def ingest\|def apply_model\|def load_persona\|flock\|class CharCtx" "$HOME/Library/Application Support/GenericAgent/runtime/app/frontends/char_config.py" | head -20
11:2026-09-20: CharCtx多角色化(apiapp单进程服务全家角色); _ingest_now加跨进程flock
12:           (fsapp/wxclaw/apiapp多进程可能并发sweep同一chroma库, flock串行化防写坏)
188:class CharCtx:
203:    def load_persona(self):
215:    def apply_model(self, agent):
245:    def recall(self, query, n=4, max_chars=900):
368:    def ingest(self, user_id, user_text, reply_text):
372:    def ingest_sync(self, user_id, user_text, reply_text):
404:            # 跨进程flock: fsapp/wxclaw/apiapp并发sweep同一chroma库时串行化(2026-09-20)
406:                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
418:                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
438:def load_persona():
442:def apply_model(agent):
446:def recall(query, n=4, max_chars=900):
450:def ingest(user_id, user_text, reply_text):
454:def ingest_sync(user_id, user_text, reply_text):

$ grep -n "def clean_reply" -A 25 "$HOME/Library/Application Support/GenericAgent/runtime/app/frontends/chatapp_common.py"; grep -n "repair_folded_tags\|tidy_lines" "$HOME/Library/Application Support/GenericAgent/runtime/app/frontends/chatapp_common.py" | head; echo ----; grep -n "def ensure_single_instance" -A 15 "$HOME/Library/Application Support/GenericAgent/runtime/app/frontends/chatapp_common.py"
103:def clean_reply(text):
104-    text = repair_folded_tags(text)
105-    for pat in TAG_PATS:
106-        text = re.sub(pat, "", text or "", flags=re.DOTALL)
107-    return re.sub(r"\n{3,}", "\n\n", tidy_lines(text)).strip() or "..."
108-
109-
110-def extract_files(text):
111-    return re.findall(r"\[FILE:([^\]]+)\]", text or "")
112-
113-
114-def strip_files(text):
115-    return re.sub(r"\[FILE:[^\]]+\]", "", text or "").strip()
116-
117-
118-def split_text(text, limit):
119-    text, parts = (text or "").strip() or "...", []
120-    while len(text) > limit:
121-        cut = text.rfind("\n", 0, limit)
122-        if cut < limit * 0.6:
123-            cut = limit
124-        parts.append(text[:cut].rstrip())
125-        text = text[cut:].lstrip()
126-    return parts + ([text] if text else []) or ["..."]
127-
128-
62:def repair_folded_tags(text):
71:def tidy_lines(text):
104:    text = repair_folded_tags(text)
107:    return re.sub(r"\n{3,}", "\n\n", tidy_lines(text)).strip() or "..."
----
278:def ensure_single_instance(port, label):
279-    try:
280-        lock_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
281-        lock_sock.bind(("127.0.0.1", port))
282-        return lock_sock
283-    except OSError:
284-        print(f"[{label}] Another instance is already running, skipping...")
285-        sys.exit(1)
286-
287-
288-def require_runtime(agent, label, **required):
289-    missing = [k for k, v in required.items() if not v]
290-    if missing:
291-        print(f"[{label}] ERROR: please set {', '.join(missing)} in mykey.py or mykey.json")
292-        sys.exit(1)
293-    if agent.llmclient is None:

$ grep -n "display_queue.put\|output.put\|queue.put" "$HOME/Library/Application Support/GenericAgent/runtime/app/agentmain.py" "$HOME/Library/Application Support/GenericAgent/runtime/app/agent_loop.py" 2>/dev/null | head -20; echo ----; cat "/Users/potato/土小豆/系统代码/GA家园系统/Chat/.gitignore"; echo ----; sed -n 245,262p "$HOME/Library/Application Support/GenericAgent/runtime/app/frontends/char_config.py"; sed -n 368,378p "$HOME/Library/Application Support/GenericAgent/runtime/app/frontends/char_config.py"
/Users/potato/Library/Application Support/GenericAgent/runtime/app/agentmain.py:135:        self.task_queue.put({"query": query, "source": source, "images": images or [], "output": display_queue})
/Users/potato/Library/Application Support/GenericAgent/runtime/app/agentmain.py:148:            display_queue.put({'done': smart_format(f"✅ session.{k} = {repr(v)}", max_str_len=500), 'source': 'system'})
/Users/potato/Library/Application Support/GenericAgent/runtime/app/agentmain.py:212:                        display_queue.put({'next': full_resp[last_pos:] if self.inc_out else full_resp, 
/Users/potato/Library/Application Support/GenericAgent/runtime/app/agentmain.py:216:                    display_queue.put({'next': full_resp[last_pos:], 'source': source,
/Users/potato/Library/Application Support/GenericAgent/runtime/app/agentmain.py:218:                display_queue.put({'done': full_resp, 'source': source, 'turn': curr_turn, 'outputs': turn_resps.copy()})
/Users/potato/Library/Application Support/GenericAgent/runtime/app/agentmain.py:222:                display_queue.put({'done': full_resp + f'\n```\n{format_error(e)}\n```', 'source': source, 'turn': curr_turn, 'outputs': turn_resps.copy()})
----
# 运行时数据不入库
chat.db*
media/
__pycache__/
*.log

# 白名单: 本项目文档入库长期维护(覆盖根.gitignore过程产物排除, 主人1027拍板)
!*.md

# handoff=session间交接文档,不入库(主人1027定)
handoff*.md
----
    def recall(self, query, n=4, max_chars=900):
        """检索本角色宫殿, 返回注入用记忆块; 无结果/异常返回''(绝不阻塞聊天)"""
        if not _mp_ready() or not query.strip() or not os.path.exists(self.palace_path):
            return ""
        try:
            from mempalace.searcher import search_memories
            res = search_memories(query.strip()[:200], self.palace_path, n_results=n)
            hits = []
            seen = set()
            for h in (res.get("results") or []):
                t = (h.get("text") or "").strip().replace("\n", " ")
                if t:
                    k = t[:80]  # 1021: 近重复条目去重(同对话多版本入库只留首条)
                    if k in seen:
                        continue
                    seen.add(k)
                    hits.append(f"- {t[:160]}")
            # 1022 P12: 中文专名字面兜底通道, 精确命中排在向量结果前(专名query向量必miss)
    def ingest(self, user_id, user_text, reply_text):
        """后台线程: 一问一答追加转录 → 增量sweep入库"""
        threading.Thread(target=self._ingest_now, args=(user_id, user_text, reply_text), daemon=True).start()

    def ingest_sync(self, user_id, user_text, reply_text):
        """同步版: 短命进程用, 进程退出前sweep必然完成"""
        self._ingest_now(user_id, user_text, reply_text)

    def _ingest_now(self, user_id, user_text, reply_text):
        if not (user_text or "").strip() or not (reply_text or "").strip():
            return

# Chat 切片1 代码审查报告(gstack /review)

**审查范围**:`Chat/chatapp.py`(413行)、`Chat/web/index.html`(323行);对照 `开发方案_Chat.md` §4/§5/§7 与 `chatapp_common.py`/`agentmain.py`/`char_config.py` 实现核实。只审查未修改任何文件。

**总体结论**：架构符合方案(jobs表异步队列/单例锁/端口事由/气泡切分D4全部规则均落地)；LLM入库清洗链路**合规**(见豁免区结论)。发现 1 个前端严重竞态、2 个后端严重稳定性问题。

---

## 🔴 严重

**S1. 前端：历史加载与轮询竞态导致消息永久丢失(显示层)**
`web/index.html:276-293`(scroll handler)× `index.html:240-244`(applyMsgs)× `index.html:265-274`(pollOnce)
时序：滚动触发历史加载 → `await api('/api/history')` 挂起期间，2.5s 定时器 `pollOnce` 照常执行 → 新消息 append 进 DOM 且 `sinceSeq[cid]` 推进到最新 → history 响应返回后 `area.innerHTML=''` + `rendered=new Set()` **把刚拉到的新消息连同 DOM 一起清掉**，而 `sinceSeq` 已推进 → 后续 poll 的 `WHERE seq>?` 永远查不回它们 → 消息从界面消失，直到重进聊天屏。
**修复**：① `pollOnce`/`applyMsgs` 开头加 `if(loadingHistory) return;`;或 ② history 重建时按 `rendered` 差集合并(重建后把已被 poll 渲染的 seq 一并补回)；或 ③ 重建期间暂存新消息、重建后回放。

**S2. 后端：阻塞 SQLite 调用直接跑在 asyncio 事件循环里**
`chatapp.py:324-392`(全部 async 端点内直接调 `_digest()`/`db()`)、`chatapp.py:257/275-281`(worker 内 `_insert_msg`/`_finish_job` 直调)、`chatapp.py:70-80`(`db()` 每次新建连接)
所有端点是 `async def`,其中的 `sqlite3.connect+execute` 是阻塞调用;`busy_timeout=5000` 意味着写锁竞争时**整个事件循环(所有角色的轮询/超时计时/HTTP 响应)最多冻结5秒**。另外每次 `db()` 新建连接+双 PRAGMA,`_digest` 一次请求开 11 个连接(5条last消息+5次`_is_typing`+主体)，churn 严重。
**修复**：读端点改 `def`(FastAPI 自动进线程池)或统一 `asyncio.to_thread` 包裹;`db()` 改线程局部连接复用;`_digest` 的 typing 合成一条 `GROUP BY char_id` 查询。

**S3. 后端：agent 懒构建在事件循环内执行，可冻结全服务数秒**
`chatapp.py:229`(`svc.get_agent()` 直调)× `chatapp.py:148-165`
`GeneraticAgent()` 构建(加载工具schema/llm配置/persona)是重操作，同步跑在事件循环；五角色首次来信各冻结一次，期间所有 HTTP/worker 计时停摆。
**修复**:`agent = await asyncio.to_thread(svc.get_agent)`(`_build_lock` 已是线程锁，天然兼容)。

---

## 🟡 建议

**B1. 重启恢复把 pending 也标 error,违背方案 §4.2 且丢排队消息**
`chatapp.py:114-115`(`status IN ('running','pending')`);方案 §4.2 与模块docstring(L9)均只要求 running→error。pending job 完全可恢复(worker 会正常消费)，现实现让排队消息作废+连发多条“重发一下”系统提示。
**修复**：改为 `WHERE status='running'`。

**B2. user消息与job插入分两个事务，崩溃窗口产生“永不回复且无提示”的消息**
`chatapp.py:351-355`。**修复**：合并进同一个 `db()` 事务(两次 INSERT 一次 commit),job insert 用同一连接拿 `lastrowid`。

**B3. `/media` 挂载在 import 时求值，冷部署永久 404**
`chatapp.py:400-401`(`if os.path.isdir(MEDIA_DIR)`)在 `init_db()`(L406,main里才跑)建目录**之前**执行；且 `media/` 被 .gitignore 排除，新机器首跑不挂载，重启才恢复——切片3媒体的定时炸弹。**修复**：模块顶先 `os.makedirs(MEDIA_DIR, exist_ok=True)` 再 mount,或 mount 时用 `check_dir=False` 并在 startup 建目录。

**B4. agent 内部错误文本被当作角色回复入库并 ingest 进宫殿**
`chatapp.py:245-247` 只认 `"done" in item`;而 `agentmain.py:222` 出错时也 put `{'done': full_resp + '\n```\n{format_error(e)}\n```'}` → 堆栈错误文案清洗后成为角色气泡并污染宫殿记忆。**修复**：done 项带 `turn/outputs` 字段，检测末尾 code fence 里的 format_error 则按 job error 处理，不入库不 ingest。

**B5. `POST /api/msg` 对非对象 JSON 返回 500**
`chatapp.py:340-344`:`request.json()` 合法但为数组/字符串时 `body.get` 抛 AttributeError → 500。**修复**:`if not isinstance(body, dict): return _err(400, ...)`。(顺带：无请求体大小上限，内网可接受，但可加 `Content-Length` 上限。)

**B6. 消息拾取延迟上限1s + 每秒5次空轮询连接churn**
`chatapp.py:268-270`。**修复**：post_msg 成功后 `asyncio.Event.set()` 唤醒对应角色 worker,轮询间隔放宽到 5s 作兜底。

**B7. `uvicorn chatapp:app` 方式启动会绕过 init_db/recover/单例锁**
`chatapp.py:404-412`(main 独占关键初始化)。**修复**：初始化迁入 lifespan/startup(`@app.on_event` 也已弃用)，main 只留 uvicorn.run。

**B8. to_thread 依赖默认线程池，5个常驻 `dq.get` 等待与 recall/ingest 突发竞争**
`chatapp.py:240-242`;默认池 `min(32, cpu+4)`,4核机=8线程，五角色同时等回复+ingest 可能排队延迟。**修复**：自建 `ThreadPoolExecutor(max_workers=16)` 传 `loop.run_in_executor`,或改 `get_nowait`+`await asyncio.sleep(0.2)` 纯异步轮询。

**B9. 前端：发送回显用本地时钟，post_msg 未返回服务端 ts**
`index.html:305`(`ts:Date.now()/1000`)× `chatapp.py:356`(响应只含 seq/job_id)。手机时钟偏差会导致时间胶囊错序。**修复**：响应体加 `"ts": time.time()`,前端采用。

**B10. 前端：气泡 stagger 期间反复强制滚底 + 切屏后定时器仍触发**
`index.html:232-233`:300ms 逐泡弹出时用户上滑阅读会被 `scrollBottom()` 连续拽回；切到别的聊天屏后遗留 `setTimeout` 仍操作新屏。**修复**：stagger 前记录 `area` 世代 token,回调先校验；仅当用户原本就贴底时才自动滚。

**B11. 前端：未读数把主人自己发的消息也计入**
`index.html:188`(`last_seq` 含 user 消息)+ `index.html:243`(`setReadSeq` 只在 poll 到才更新)→ 自己刚发的消息短暂给自己加未读红点。**修复**：digest 按 role 过滤 last_seq,或 sendMsg 成功后立即 `setReadSeq(activeChar, r.seq)`。

**B12. 前端：URL 拼接未 encode,当前安全但脆弱**
`index.html:253/269/283`(`api(\`...char_id=${cid}...\`)`)。当前 cid 是固定白名单值无风险；切片3加 `text` 等自由参数入 query 时会变注入点。**修复**：统一 `encodeURIComponent` 或 URLSearchParams。

**B13. SQLite 细节：可加 `PRAGMA synchronous=NORMAL`**(`chatapp.py:75-76` 附近)——WAL 下默认 FULL，个人 IM 场景 NORMAL 足够且写延迟显著降低。

---

## ⚪ 豁免(核实过，不构成问题)

| 项 | 结论 |
|---|---|
| **规则8清洗合规(审查重点)** | ✅ `chatapp.py:255` → `build_done_text` → `clean_reply`(chatapp_common.py:103-107)内部**先 `repair_folded_tags` 再剥 TAG_PATS 再 `tidy_lines`**,顺序正确;`_LLM_RUNNING_RE` 预剥进度条幅不破坏该链。ingest 入宫殿的 `final` 也是清洗后文本，符合方案 §4.4 step6“cleaned剥[FILE:]”。唯一注记：B4 的错误文本同样走了清洗入库，见建议。 |
| SQL注入 | 全部语句参数化(`?`),无拼接。 |
| XSS | `esc()`(index.html:107)只用于文本节点上下文，&/</> 转义足够；所有 style/color 取自硬编码 CMAP;气泡用 `textContent`(L230)。无属性注入面。 |
| SSRF | 服务端无任何用户驱动的出站 HTTP(recall/ingest 为本地宫殿)；切片3识图接入时需复审。 |
| 路径穿越 | `/media` 由 StaticFiles 托管(自动防 `..`);`/` 返回固定路径；无用户可控文件参数。 |
| 无鉴权/无CORS | 方案 §1.3 拍板：tailnet 即边界，单人内网。 |
| 端口/单例锁 8788 冲突 | 已用 GA_LOCK_PORT=19544 规避，事由文档化(chatapp.py:14-15)。 |
| `_take_job` SELECT-UPDATE 竞态 | 每角色仅一个 worker 消费，且 UPDATE 带 `status='pending'` 条件+rowcount 校验(L190-193),双保险成立。 |
| display 队列 item 类型 | agentmain 只 put dict(`next`/`done` 键)，`"done" in item` 是键检查，无误匹配风险。 |
| 同角色并发/跨进程宫殿写 | jobs 表 per-char 天然串行；宫殿 ingest 有跨进程 flock(char_config.py:404-418),符合方案 §4.5。 |
| 超时 abort 后 agent 状态 | `stop_sig` 由 run() finally 清(agentmain.py:112 注释)，下一 job 不受污染。 |
| worldbook/upload/bg、媒体消息 | 方案切片2/3范围，本次未实现属计划内；`meta` 表已预建。 |
| `.gitignore` | 已正确排除 `chat.db*`、`media/`,聊天记录不入 git(方案 §7 隐私项)。 |
| D4 气泡切分规则 | 括号原子段/未闭合整体一泡/<8字合并/>6泡压缩/括号不参与合并/300ms逐泡/user不切，全部按 §5.4 落地(index.html:120-179, 228-234)。 |
| IME 回车 | `!ev.isComposing` 已处理(index.html:296),中文输入法不误发。 |

---

**修复优先级建议**：S1(用户可直接感知的消息丢失)→ S3/S2(一行 to_thread 改动，收益大)→ B4(宫殿污染不可逆)→ B1/B2/B3 → 其余随切片4打磨收尾。
