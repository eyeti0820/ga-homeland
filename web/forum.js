/* forum.js — 临空市论坛前端：hash 路由三视图 + 暗点暗号门 + 主人马甲（墨迹未干）发言 */
"use strict";

const API = "";
const DARK_KEY = "f_darkspot_ok";        // 暗点通行证
const MASTER_MASK = "墨迹未干";           // 主人在论坛的固定马甲（不暴露他人对照）
const PASSCODE = "N109";

const view = document.getElementById("view");
const sentinel = document.getElementById("sentinel");
const meta = document.getElementById("view-meta");

let io = null;
let state = { board: null, threadId: null, beforeId: null, loading: false };

/* ---------- API helpers ---------- */
async function jget(p) {
  const r = await fetch(API + p);
  if (!r.ok) throw new Error(`GET ${p} -> ${r.status}`);
  return r.json();
}
async function jpost(p, body) {
  const r = await fetch(API + p, { method: "POST", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || `POST ${p} -> ${r.status}`);
  return d;
}

/* ---------- utils ---------- */
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const ts = (s) => (s || "").replace("T", " ").slice(5, 16);

/* ---------- views ---------- */
const BOARD_ICO = { city: "🏙", hunters: "🛡", fleet: "🚀", darkspot: "🌑", asko: "🩺", gallery: "🎨" };
async function renderBoards() {
  document.title = "临空市论坛";
  view.dataset.board = "";
  const d = await jget("/api/forum/boards");
  const grid = el("div", "f-boards");
  for (const b of d.items) {
    const a = el("a", "f-board");
    a.href = `#/b/${b.key}`;
    a.dataset.key = b.key;
    a.appendChild(el("span", "f-ico", BOARD_ICO[b.key] || "📌"));
    const h = el("h3", null, b.name);
    if (b.access === "secret") h.appendChild(el("span", "f-lock", "🔒 暗号板"));
    a.appendChild(h);
    a.appendChild(el("p", null, b.description));
    a.appendChild(el("div", "f-meta", `${b.thread_count} 帖 · 最近 ${ts(b.last_activity) || "安静得像凌晨三点"}`));
    grid.appendChild(a);
  }
  view.replaceChildren(grid);
  meta.textContent = `六板 ${d.count} · 全城潜水员俱乐部`;
}

async function renderBoard(key) {
  state.board = key; state.beforeId = null;
  view.dataset.board = key;
  const [bd] = (await jget("/api/forum/boards")).items.filter((b) => b.key === key);
  if (!bd) { view.replaceChildren(el("p", "m-empty", "没有这块板")); return; }
  if (bd.access === "secret" && localStorage.getItem(DARK_KEY) !== "1") { renderPassgate(key); return; }

  const crumb = el("p", "f-crumb");
  crumb.appendChild(el("a", null, "« 临空市论坛")).href = "#/";
  crumb.appendChild(document.createTextNode(` / ${bd.name}`));

  const btn = el("button", "f-new-thread", "✎ 发帖");
  btn.onclick = () => renderNewThread(key, bd);

  const list = el("div");
  view.replaceChildren(crumb, btn, list);
  document.title = `${bd.name} · 临空市论坛`;
  meta.textContent = bd.description;

  await loadThreads(key, list);
  io = new IntersectionObserver((ents) => {
    if (ents[0].isIntersecting && state.board === key) loadThreads(key, list);
  });
  io.observe(sentinel);
}

async function loadThreads(key, list) {
  if (state.loading) return;
  state.loading = true;
  const q = `/api/forum/threads?board_key=${key}` + (state.beforeId ? `&before_id=${state.beforeId}` : "");
  const d = await jget(q);
  for (const t of d.threads) {
    const a = el("a", "f-thread");
    a.href = `#/t/${t.id}`;
    const h = el("p", "f-t-title");
    if (t.is_pinned) h.appendChild(el("span", "f-pin", "置顶"));
    h.appendChild(el("span", null, t.title));
    a.appendChild(h);
    const sub = el("div", "f-t-sub");
    sub.appendChild(el("span", null, `${t.author} · ${ts(t.created_at)}`));
    sub.appendChild(el("span", null, `${t.reply_count} 回复 · ${t.views} 浏览`));
    if (t.author === MASTER_MASK) {
    const dx = el("button", "f-del-x", "×"); dx.title = "删除这个主题";
    dx.onclick = async (ev) => {
      ev.preventDefault(); ev.stopPropagation();
      if (!confirm("删除这个主题？（整栋楼一起塌）")) return;
      try { await jdelForum(`/api/forum/threads/${t.id}`); a.remove(); }
      catch (e) { alert("没删掉：" + e.message); }
    };
    a.appendChild(dx);
  }
  a.appendChild(sub);
    list.appendChild(a);
  }
  state.beforeId = d.threads.length ? d.threads[d.threads.length - 1].id : state.beforeId;
  if (!d.has_more) { if (io) io.disconnect(); sentinel.hidden = true; }
  else sentinel.hidden = false;
  if (!list.children.length) list.appendChild(el("p", "m-empty", "这块板还空着，第一帖由你来写"));
  state.loading = false;
}

async function renderThread(id) {
  if (io) io.disconnect();
  state.threadId = id;
  const d = await jget(`/api/forum/threads/${id}`);
  const t = d.thread, posts = d.posts || [];
  view.dataset.board = t.board_key || "";
  const crumb = el("p", "f-crumb");
  crumb.appendChild(el("a", null, "« 临空市论坛")).href = "#/";
  const b = el("a", null, ` ${t.board_name} `); b.href = `#/b/${t.board_key}`;
  crumb.appendChild(b); crumb.appendChild(document.createTextNode("/ 帖子"));

  const op = el("article", "f-op");
  const h = el("h2", null, (t.is_pinned ? "📌 " : "") + t.title);
  const who = el("p", "f-who");
  who.appendChild(document.createTextNode("楼主 "));
  who.appendChild(el("b", null, t.author));
  who.appendChild(document.createTextNode(` · ${ts(t.created_at)} · ${t.views} 浏览`));
  if (t.author === MASTER_MASK) {
    const dx = el("button", "f-del-x", "×"); dx.title = "删除这个主题";
    dx.onclick = async () => {
      if (!confirm("删除这个主题？（整栋楼一起塌）")) return;
      try { await jdelForum(`/api/forum/threads/${t.id}`); location.hash = "#/"; }
      catch (e) { alert("没删掉：" + e.message); }
    };
    who.appendChild(dx);
  }
  op.append(h, who, el("div", "f-op-content", t.content));

  const floors = el("div");
  posts.forEach((p, i) => {
    const f = el("article", "f-floor");
    const w = el("p", "f-who");
    w.appendChild(el("span", "f-fl-no", `${i + 1}楼`));
    w.appendChild(el("b", null, p.author));
    w.appendChild(document.createTextNode(` · ${ts(p.created_at)}`));
    if (p.author === MASTER_MASK) {
      const dx = el("button", "f-del-x", "×"); dx.title = "删除这层楼";
      dx.onclick = async () => {
        if (!confirm("删掉这层楼？")) return;
        try { await jdelForum(`/api/forum/thread-posts/${p.id}`); renderThread(threadId); }
        catch (e) { alert("没删掉：" + e.message); }
      };
      w.appendChild(dx);
    }
    f.append(w, el("div", "f-fl-content", p.content));
    floors.appendChild(f);
  });

  const form = replyForm(id);
  view.replaceChildren(crumb, op, floors, form);
  document.title = `${t.title} · 临空市论坛`;
  meta.textContent = `${t.board_name} · ${posts.length} 楼`;
}

/* ---------- 主人发言（固定马甲：墨迹未干） ---------- */
async function myMaskId() {
  const d = await jget("/api/forum/masks");
  const m = d.items.find((x) => x.forum_username === MASTER_MASK);
  return m ? m.id : null;
}
function replyForm(threadId) {
  const f = el("div", "f-form");
  f.appendChild(el("p", "f-hint", `以「${MASTER_MASK}」的身份回一帖（其他人的马甲就别扒啦）`));
  const ta = document.createElement("textarea");
  ta.placeholder = "接一句……";
  const btn = el("button", null, "回复");
  btn.onclick = async () => {
    if (!ta.value.trim()) return;
    btn.disabled = true;
    try {
      const mid = await myMaskId();
      await jpost(`/api/forum/threads/${threadId}/posts`, { mask_id: mid, content: ta.value.trim() });
      renderThread(threadId);
    } catch (e) { alert(e.message); btn.disabled = false; }
  };
  f.append(ta, btn);
  return f;
}
function renderNewThread(key, bd) {
  const f = el("div", "f-form");
  f.appendChild(el("p", "f-hint", `在「${bd.name}」开新帖 · 马甲：${MASTER_MASK}`));
  const ti = document.createElement("input"); ti.type = "text"; ti.placeholder = "标题（80字内）"; ti.maxLength = 80;
  const ta = document.createElement("textarea"); ta.placeholder = "正文（2000字内）";
  const btn = el("button", null, "发布");
  btn.onclick = async () => {
    if (!ti.value.trim() || !ta.value.trim()) return;
    btn.disabled = true;
    try {
      const mid = await myMaskId();
      await jpost("/api/forum/threads", { board_key: key, mask_id: mid,
        title: ti.value.trim(), content: ta.value.trim() });
      // hash 未变时赋同值不触发 hashchange，需手动刷新视图
      if (location.hash === `#/b/${key}`) await renderBoard(key);
      else location.hash = `#/b/${key}`;
    } catch (e) { alert(e.message); btn.disabled = false; }
  };
  f.append(ti, ta, btn);
  view.replaceChildren(el("p", "f-crumb", `« ${bd.name}`), f);
}

/* ---------- 暗点暗号门 ---------- */
function renderPassgate(key) {
  const g = el("div", "f-pass");
  g.appendChild(el("h3", null, "暗 点"));
  g.appendChild(el("p", null, "这块板只对知道门牌号的人开放。"));
  const inp = document.createElement("input"); inp.placeholder = "暗号"; inp.maxLength = 12;
  const btn = el("button", null, "进去");
  const err = el("p", "f-err", "");
  const tryOpen = () => {
    if (inp.value.trim().toUpperCase() === PASSCODE) {
      localStorage.setItem(DARK_KEY, "1");
      renderBoard(key);
    } else { err.textContent = "……不是这个。再想想。"; inp.value = ""; }
  };
  btn.onclick = tryOpen;
  inp.onkeydown = (e) => { if (e.key === "Enter") tryOpen(); };
  g.append(inp, btn, err);
  view.replaceChildren(g);
  meta.textContent = "🔒 你需要一个暗号";
  inp.focus();
}

/* ---------- router ---------- */
function route() {
  if (io) io.disconnect();
  sentinel.hidden = true;
  state.loading = false;
  const h = location.hash || "#/";
  const mBoard = h.match(/^#\/b\/(\w+)$/), mThread = h.match(/^#\/t\/(\d+)$/);
  const p = mBoard ? renderBoard(mBoard[1]) : mThread ? renderThread(+mThread[1]) : renderBoards();
  p.catch((e) => view.replaceChildren(el("p", "m-empty", `加载失败：${e.message}`)));
}
window.addEventListener("hashchange", route);
route();


/* ---------- 主人删除（M4.5） ---------- */
async function jdelForum(url) {
  const r = await fetch(url, { method: "DELETE" });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
}
