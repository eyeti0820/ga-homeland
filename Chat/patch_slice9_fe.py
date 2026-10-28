#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切片9 见面屏前端补丁: CSS块 + meetScreen HTML + JS逻辑块 + 三处改造
锚点全部assert唯一, 备份 web/index.html.bak_1028_s9pre
"""
import os, re, subprocess, sys

D = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(D, 'web', 'index.html')
BAK = os.path.join(D, 'web', 'index.html.bak_1028_s9pre')
if not os.path.exists(BAK):
    open(BAK, 'w').write(open(P).read())
src = open(P).read()

def rep(old, new, cnt=1):
    global src
    n = src.count(old)
    assert n == cnt, f"锚点非唯一({n}处): {old[:60]!r}"
    src = src.replace(old, new)

# ============ 1) CSS 块: 插在 </style> 前 ============
CSS = r'''
/* ---------- 切片9: 见面屏(小说排版·羊皮纸细方格·五主题) ---------- */
#meetScreen{position:fixed;inset:0;display:none;flex-direction:column;z-index:60;
  font-family:"Songti SC","Noto Serif SC",serif;
  background:
    repeating-linear-gradient(0deg,transparent 0 21px,var(--m-grid) 21px 22px),
    repeating-linear-gradient(90deg,transparent 0 21px,var(--m-grid) 21px 22px),
    var(--m-paper);
  --m-paper:#f6eedb;--m-grid:rgba(150,110,55,.13);--m-ink:#3c2f1e;--m-ink2:#8a7a5e;
  --m-ac:#e8823a;--m-ac2:#3a6ea8;--m-saybg:rgba(255,255,255,.55);--m-bandc:#a08a5f;}
#meetScreen[data-char="shenxinghui"]{--m-paper:#f3eefa;--m-grid:rgba(105,80,160,.12);--m-ink:#37304a;--m-ink2:#837699;--m-ac:#7b5ea7;--m-ac2:#c99b2e;--m-saybg:rgba(255,255,255,.6);--m-bandc:#8a7ba8}
#meetScreen[data-char="qinche"]{--m-paper:#2b2422;--m-grid:rgba(214,172,110,.09);--m-ink:#e9ddc9;--m-ink2:#a3937c;--m-ac:#c7515a;--m-ac2:#d9a441;--m-saybg:rgba(255,255,255,.05);--m-bandc:#9a8468}
#meetScreen[data-char="lishen"]{--m-paper:#eef3f9;--m-grid:rgba(90,120,160,.13);--m-ink:#2f3a4a;--m-ink2:#7a8aa0;--m-ac:#4a7fb5;--m-ac2:#7fa8cf;--m-saybg:rgba(255,255,255,.6);--m-bandc:#8296ad}
#meetScreen[data-char="qiyu"]{--m-paper:#f9eef6;--m-grid:rgba(180,110,160,.12);--m-ink:#43303f;--m-ink2:#9c7f92;--m-ac:#d98ab8;--m-ac2:#7a5ea8;--m-saybg:rgba(255,255,255,.6);--m-bandc:#b78aa8}
#meetScreen::after{content:'';position:absolute;inset:0;pointer-events:none;z-index:1;opacity:.35;mix-blend-mode:soft-light;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='140' height='140'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/></filter><rect width='140' height='140' filter='url(%23n)' opacity='0.55'/></svg>")}
#meetHeader{position:relative;z-index:2;display:flex;align-items:center;gap:10px;padding:0 12px;
  min-height:52px;background:rgba(40,32,24,.93);color:#f3ecda;border-bottom:2px solid var(--m-ac);flex:none}
#meetScreen[data-char="qinche"] #meetHeader{background:#1c1613}
#meetBack{font-size:26px;line-height:1;cursor:pointer;padding:4px 10px;color:#f3ecda}
#meetTitle{font-size:17px;letter-spacing:2px}
#meetHint{display:none;font-size:12px;color:var(--m-ac2);letter-spacing:1px}
#meetHint .d{animation:blink 1.2s infinite}
#meetSceneBtn{margin-left:auto;font-size:13px;color:#f3ecda;border:1px solid var(--m-ac);border-radius:12px;
  padding:3px 12px;cursor:pointer;letter-spacing:1px;background:transparent}
#meetArea{position:relative;z-index:1;flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch}
#meetPaper{max-width:680px;margin:0 auto;padding:18px 16px 26px}
.m-band{display:flex;align-items:center;gap:10px;margin:18px 0;color:var(--m-bandc);font-size:12px;letter-spacing:2px}
.m-band::before,.m-band::after{content:'';flex:1;height:1px;background:linear-gradient(90deg,transparent,var(--m-bandc));opacity:.5}
.m-band::after{background:linear-gradient(90deg,var(--m-bandc),transparent)}
.m-msg{margin:14px 0;animation:mIn .28s ease both}
@keyframes mIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.m-name{font-size:12px;color:var(--m-ac);letter-spacing:2px;margin:0 0 4px 2px}
.m-msg.u{text-align:right}
.m-msg.u .m-name{color:var(--m-ink2)}
.m-say{display:inline-block;max-width:86%;text-align:left;font-weight:700;font-size:15.5px;line-height:1.75;
  color:var(--m-ink);background:var(--m-saybg);border:1.5px solid var(--m-ac);border-radius:10px;
  padding:8px 14px;margin:2px 0}
.m-msg.u .m-say{border-color:var(--m-ac2)}
.m-nar{display:block;max-width:96%;font-weight:400;font-size:14px;line-height:1.8;color:var(--m-ink2);margin:4px 2px}
.m-msg.u .m-nar{margin-left:auto;text-align:right;max-width:86%}
#meetBar{position:relative;z-index:2;display:none;align-items:flex-end;gap:8px;padding:8px 10px calc(8px + env(safe-area-inset-bottom));
  background:rgba(40,32,24,.93);flex:none}
#meetScreen[data-char="qinche"] #meetBar{background:#1c1613}
#meetInput{flex:1;background:rgba(255,252,245,.96);border:1px solid var(--m-ac);border-radius:8px;
  color:var(--m-ink);font-family:inherit;font-size:15px;line-height:1.5;padding:8px 10px;resize:none;max-height:120px;outline:none}
#meetSendBtn{background:var(--m-ac);color:#fff;border:none;border-radius:8px;font-family:inherit;
  font-size:15px;letter-spacing:3px;padding:9px 16px;cursor:pointer}
#meetSendBtn:disabled{opacity:.5}
#meetSceneMenu{display:none;position:absolute;top:50px;right:10px;z-index:5;background:#faf5e8;border:1px solid var(--m-ac);
  border-radius:8px;box-shadow:0 4px 14px rgba(0,0,0,.25);overflow:hidden;min-width:130px}
#meetSceneMenu div{padding:9px 16px;font-size:14px;color:#3c2f1e;cursor:pointer;border-bottom:1px solid rgba(0,0,0,.06)}
#meetSceneMenu div:hover{background:rgba(232,130,58,.12)}
.meet-entry{font-family:"Songti SC","Noto Serif SC",serif;font-size:11px;color:#666;letter-spacing:1px;
  border:1px solid #bbb;border-radius:10px;padding:1px 8px;margin-top:4px;cursor:pointer;display:inline-block}
.meet-entry:active{opacity:.6}
'''
rep('</style>', CSS + '</style>')

# ============ 2) HTML: #meetScreen 插在 listScreen 前 ============
HTML = '''<div id="meetScreen" data-char="">
    <div id="meetHeader">
      <div id="meetBack" onclick="closeMeet()">&#8249;</div>
      <div id="meetTitle"></div>
      <div id="meetHint">正在输入<span class="d">.</span><span class="d">.</span><span class="d">.</span></div>
      <div id="meetSceneBtn" onclick="toggleSceneMenu(event)">&#9670; <span id="meetSceneName"></span></div>
    </div>
    <div id="meetArea"><div id="meetPaper"></div></div>
    <div id="meetSceneMenu"></div>
    <div id="meetBar">
      <textarea id="meetInput" rows="1" autocomplete="off" oninput="autoGrow(this)" onkeydown="meetKey(event)"></textarea>
      <button id="meetSendBtn" onclick="meetSend()">落笔</button>
    </div>
  </div>
  <div id="listScreen">'''
rep('<div id="listScreen">', HTML)

# ============ 3) JS 块: 插在 sendMsg 结束后(L634区域锚) ============
JS = r'''
/* ---------- 切片9: 见面屏(意识同步·小说排版) ---------- */
const MEET_SCENES=['静室','天台·夜','雨夜·窗边','咖啡馆','老宅·院里'];
let meetChar=null,meetScene='',meetSince={},meetRendered=new Set();

function mMeta(m){if(!m.meta)return{};if(typeof m.meta==='object')return m.meta;try{return JSON.parse(m.meta)}catch(e){return{}}}
function meetFilter(m){const mt=mMeta(m);   /* meet态相关行才进见面屏 */
  return !!(mt.mode_evt||mt.mode==='meet'||/^—— (意识同步|同步)/.test(m.content||''));}
function chatFilter(m){const mt=mMeta(m);   /* 聊天屏滤掉meet行(切片9) */
  return !(mt.mode_evt||mt.mode==='meet'||/^—— (意识同步|同步)/.test(m.content||''));}
function meetParse(text){   /* 行级解析: 括号段=旁白, 其余=台词 */
  const out=[];
  (text||'').split(/\n/).forEach(line=>{
    const s=line.trim();if(!s)return;
    const re=/[（(][^（）()]*[)）]/g;let last=0,m,any=false;
    while((m=re.exec(s))){
      const pre=s.slice(last,m.index).trim();
      if(pre)out.push({t:'say',s:pre});
      out.push({t:'nar',s:m[0]});any=true;last=m.index+m[0].length;
    }
    const tail=s.slice(last).trim();
    if(tail)out.push({t:'say',s:tail});
    if(!any&&!tail)out.push({t:'say',s});
  });
  return out.length?out:[{t:'say',s:text||'……'}];
}
function meetNovel(cid,msg){
  if(meetRendered.has(msg.seq))return;meetRendered.add(msg.seq);
  const paper=$('meetPaper');if(!paper)return;
  const wrap=document.createElement('div');
  if(msg.role==='system'){wrap.className='m-band';wrap.textContent=msg.content||'';paper.appendChild(wrap);return}
  wrap.className='m-msg '+(msg.role==='user'?'u':'a');
  const nm=document.createElement('div');nm.className='m-name';
  nm.textContent=msg.role==='user'?'我':(CMAP[cid]?CMAP[cid].name:cid);wrap.appendChild(nm);
  meetParse(msg.content).forEach(seg=>{
    const el=document.createElement(seg.t==='say'?'div':'span');
    el.className=seg.t==='say'?'m-say':'m-nar';el.textContent=seg.s;wrap.appendChild(el);
  });
  paper.appendChild(wrap);
}
function meetScroll(){const a=$('meetArea');if(a)a.scrollTop=a.scrollHeight}
async function openMeet(cid){
  meetChar=cid;meetScene=MEET_SCENES[0];meetRendered=new Set();
  $('listScreen').style.display='none';$('chatScreen').style.display='none';
  const ms=$('meetScreen');ms.style.display='flex';ms.dataset.char=cid;
  $('meetTitle').textContent=CMAP[cid].name;$('meetSceneName').textContent=meetScene;
  $('meetBar').style.display='flex';$('meetPaper').innerHTML='';
  try{await api('/api/mode',{method:'POST',body:JSON.stringify({char_id:cid,mode:'meet',scene:meetScene})})}catch(e){}
  try{
    const r=await api(`/api/history?char_id=${encodeURIComponent(cid)}&limit=50`);
    let msgs=(r.msgs||[]).filter(meetFilter);
    for(let i=msgs.length-1;i>=0;i--){if(msgs[i].role==='system'){msgs=msgs.slice(i);break}}   /* 只渲染最近一轮同步 */
    msgs.forEach(m=>meetNovel(cid,m));
    const last=msgs[msgs.length-1];
    meetSince[cid]=last?last.seq:0;
    if(last)setReadSeq(cid,last.seq);
    if(!msgs.length){const b=document.createElement('div');b.className='m-band';b.textContent='意识空间已连接 · 开始你们的对话';$('meetPaper').appendChild(b)}
    meetScroll();
  }catch(e){toast('加载失败: '+e.message)}
}
async function closeMeet(){
  const cid=meetChar;meetChar=null;
  $('meetScreen').style.display='none';$('listScreen').style.display='flex';
  if(cid){try{await api('/api/mode',{method:'POST',body:JSON.stringify({char_id:cid,mode:'chat'})})}catch(e){}}
  refreshDigest();
}
function toggleSceneMenu(ev){if(ev)ev.stopPropagation();
  const m=$('meetSceneMenu');
  if(m.style.display==='block'){m.style.display='none';return}
  m.innerHTML=MEET_SCENES.map(s=>`<div onclick="pickScene('${s}')">${s}</div>`).join('');
  m.style.display='block';
}
async function pickScene(s){
  $('meetSceneMenu').style.display='none';
  if(!meetChar||s===meetScene)return;meetScene=s;
  $('meetSceneName').textContent=s;
  const b=document.createElement('div');b.className='m-band';b.textContent=`场景 · ${s}`;$('meetPaper').appendChild(b);meetScroll();
  try{await api('/api/mode',{method:'POST',body:JSON.stringify({char_id:meetChar,mode:'meet',scene:s})})}catch(e){}
}
async function meetPoll(){
  if(!meetChar)return;
  try{
    const r=await api(`/api/poll?char_id=${encodeURIComponent(meetChar)}&since=${meetSince[meetChar]||0}`);
    const msgs=(r.msgs||[]).filter(meetFilter);
    msgs.forEach(m=>meetNovel(meetChar,m));
    const last=msgs[msgs.length-1];
    if(last){meetSince[meetChar]=last.seq;setReadSeq(meetChar,last.seq);meetScroll()}
    let t=(r.typing!==undefined)?r.typing:false;
    if(r.typing===undefined){const d=digest.find(x=>x.id===meetChar);t=!!(d&&d.typing)}
    $('meetHint').style.display=t?'inline':'none';
  }catch(e){}
}
async function meetSend(){
  if(!meetChar||sending)return;
  const el=$('meetInput'),text=el.value.trim();if(!text)return;
  sending=true;$('meetSendBtn').disabled=true;
  try{
    const r=await api('/api/msg',{method:'POST',body:JSON.stringify({char_id:meetChar,text})});
    meetNovel(meetChar,{seq:r.seq,role:'user',kind:'text',content:text,ts:r.ts||Date.now()/1000,meta:null});
    meetSince[meetChar]=Math.max(meetSince[meetChar]||0,r.seq);
    el.value='';autoGrow(el);meetScroll();setTimeout(meetPoll,400);
  }catch(e){toast('发送失败: '+e.message)}
  sending=false;$('meetSendBtn').disabled=false;
}
function meetKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();meetSend()}}
document.addEventListener('click',()=>{const m=$('meetSceneMenu');if(m)m.style.display='none'});
'''
rep("sending=false;$('sendBtn').disabled=false;\n}\n", "sending=false;$('sendBtn').disabled=false;\n}\n" + JS)

# ============ 4) applyMsgs 聊天屏过滤 ============
rep('''  msgs.forEach(m=>{if(!rendered.has(m.seq))appendRow(cid,m,stagger)});
  const last=msgs[msgs.length-1];''',
'''  const list=msgs.filter(chatFilter);   /* 切片9: 聊天屏滤meet行/mode_evt行 */
  list.forEach(m=>{if(!rendered.has(m.seq))appendRow(cid,m,stagger)});
  const last=list[list.length-1];''')

# ============ 5) renderList 双入口 ============
rep('''      <div class="crow-right"><div class="crow-time">${c.last_ts?fmtTime(c.last_ts):''}</div>
      ${unread>0?`<div class="badge">${unread>99?'99+':unread}</div>`:''}</div>`;''',
'''      <div class="crow-right"><div class="crow-time">${c.last_ts?fmtTime(c.last_ts):''}</div>
      <div class="meet-entry" style="border-color:${CMAP[c.id].color||'#b5a'}" onclick="event.stopPropagation();openMeet('${c.id}')">&#9670; 见面</div>
      ${unread>0?`<div class="badge">${unread>99?'99+':unread}</div>`:''}</div>`;''')

# ============ 6) 主循环+visibility 改造 ============
rep('''setInterval(()=>{
  if(document.hidden)return;
  tick++;
  pollOnce();
  if(tick%2===1||!activeChar)refreshDigest();
},2500);
document.addEventListener('visibilitychange',()=>{if(!document.hidden){pollOnce();refreshDigest()}});''',
'''setInterval(()=>{
  if(document.hidden)return;
  tick++;
  if(meetChar)meetPoll();else pollOnce();   /* 切片9: 见面屏独立轮询 */
  if(tick%2===1||!(activeChar||meetChar))refreshDigest();
},2500);
document.addEventListener('visibilitychange',()=>{if(!document.hidden){if(meetChar)meetPoll();else pollOnce();refreshDigest()}});''')

# ============ 7) openChat 时若残留meet态→纠偏回chat ============
rep("  applyBg();\n\n  /* 切片7: 骨架秒开",
"""  applyBg();
  if(CMAP[cid].mode==='meet'){try{api('/api/mode',{method:'POST',body:JSON.stringify({char_id:cid,mode:'chat'})})}catch(e){}}   /* 切片9: 异常退出meet的纠偏 */

  /* 切片7: 骨架秒开""")

open(P, 'w').write(src)
print("前端7处补丁落地 OK, 大小:", len(src))

# ============ 语法验证: 提取<script>合并 node --check ============
blocks = re.findall(r'<script>(.*?)</script>', src, re.S)
js = '\n;\n'.join(blocks)
tmp = '/tmp/slice9_check.js'
open(tmp, 'w').write(js)
r = subprocess.run(['node', '--check', tmp], capture_output=True, text=True)
print("node --check:", "PASS" if r.returncode == 0 else "FAIL\n" + r.stderr[:800])
sys.exit(0 if r.returncode == 0 else 1)
