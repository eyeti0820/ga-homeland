/* 家园朋友圈 · 前端逻辑（vanilla，无构建链）
   API 约定见 server/app/routers/moments.py
   渲染策略同便签墙：增量更新，不打断正在输入的卡片；
   首屏 15 条 + 下滑加载更早（before_id），轮询拉最新做增量同步。 */
"use strict";

const API = {
  actors: "/api/actors",
  posts: "/api/posts",
  post: (id) => `/api/posts/${id}`,
  comments: (id) => `/api/posts/${id}/comments`,
  like: (id) => `/api/posts/${id}/likes`,
};

const $ = (s, el = document) => el.querySelector(s);
const feed = $("#feed");
let masterId = null;
let actorsById = {};            // id -> {name, type}
let postsCache = new Map();     // id -> post 对象
let oldestId = null;            // 无限加载游标
let loading = false, done = false;

function fmtTime(s) {
  if (!s) return "";
  const d = new Date(s.replace(" ", "T"));
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (sameDay) return hm;
  return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}
async function jsend(url, method, body) {
  const r = await fetch(url, {
    method, headers: { "Content-Type": "application/json" },
    body: body == null ? null : JSON.stringify(body),
  });
  if (!r.ok && !(method === "DELETE" && r.status === 404)) {
    const t = await r.text(); throw new Error(`${r.status} ${t}`);
  }
  return r.status === 204 ? null : r.json();
}

/* ---------- 渲染 ---------- */
function renderCard(p) {
  const liked = (p.likes || []).some((l) => l.actor_id === masterId);
  const likeN = (p.likes || []).length;
  const card = document.createElement("article");
  card.className = "m-card";
  card.dataset.id = p.id;
  card.dataset.actor = p.actor_id;
  card.innerHTML = `
    <div class="m-head">
      ${p.author_type === "master" ? `<button class="del-x" data-del-post="${p.id}" title="删除这条动态">×</button>` : ""}
      <div class="m-avatar">${esc((p.author_name || "?").slice(0, 1))}</div>
      <div class="m-who">
        <span class="m-name">${esc(p.author_name || "")}${p.author_type === "master" ? " 👑" : ""}</span>
        <span class="m-time">${fmtTime(p.created_at)}</span>
      </div>
    </div>
    <div class="m-content">${esc(p.content)}</div>
    <div class="m-acts">
      <button class="m-act like${liked ? " on" : ""}">
        <span class="z">${liked ? "♥" : "♡"}</span><span class="lc">${likeN}</span>
      </button>
      <span class="grow"></span>
      <button class="m-act cmt-btn">💬 评论</button>
    </div>
    <div class="m-comments" ${p.comments && p.comments.length ? "" : "hidden"}></div>
    <form class="m-form" hidden>
      <input type="text" maxlength="140" placeholder="说点什么…">
      <button type="submit">发送</button>
    </form>`;
  renderComments(card, p);
  wireCard(card, p);
  return card;
}

function renderComments(card, p) {
  const box = $(".m-comments", card);
  const all = p.comments || [];
  const byId = new Map(all.map((c) => [c.id, c]));
  // 楼中楼归属：沿 parent_id 链上溯到顶层评论
  const rootOf = (c) => {
    let n = c, guard = 0;
    while (n && n.parent_id && byId.has(n.parent_id) && guard++ < 20)
      n = byId.get(n.parent_id);
    return n && !n.parent_id ? n : null;
  };
  const tops = all.filter((c) => !c.parent_id);
  const kids = all.filter((c) => c.parent_id);
  box.innerHTML = "";
  for (const c of tops) {
    const div = cmtNode(c, false);
    const floor = document.createElement("div");
    floor.className = "m-cmt-floor";
    for (const k of kids) {
      if (rootOf(k) !== c) continue;
      const parentC = byId.get(k.parent_id);   // 直接被回复者
      floor.appendChild(cmtNode(k, true, parentC || c));
    }
    if (floor.childElementCount) div.appendChild(floor);
    box.appendChild(div);
  }
}

function cmtNode(c, isFloor, parent = null) {
  const div = document.createElement("div");
  div.className = "m-cmt"; div.dataset.id = c.id;
  const whoCls = c.author_type === "master" ? "who master" : "who";
  const rep = isFloor && parent
    ? `<span class="reply-to">回复 ${esc(parent.author_name)}：</span>` : "";
  div.innerHTML =
    `<span class="${whoCls}">${esc(c.author_name)}</span>${rep}${esc(c.content)}` +
    (c.author_type === "master" ? `<button class="c-del" data-del-cmt="${c.id}" title="删除评论">删</button>` : "") +
    `<button class="c-reply" title="回复 TA">回复</button>`;
  return div;
}

/* ---------- 交互 ---------- */
function cardBusy(card) {   // 输入中/聚焦 → 轮询跳过重渲染
  return card.contains(document.activeElement) &&
         ["INPUT", "TEXTAREA"].includes(document.activeElement.tagName);
}

function wireCard(card, p) {
  const likeBtn = $(".like", card);
  likeBtn.onclick = async () => {
    const liked = likeBtn.classList.toggle("on");
    const z = $(".z", likeBtn); z.textContent = liked ? "♥" : "♡";
    const lc = $(".lc", likeBtn);
    lc.textContent = +lc.textContent + (liked ? 1 : -1);
    try {
      if (liked) await jsend(API.like(p.id), "POST", { actor_id: masterId });
      else await jsend(`${API.like(p.id)}?actor_id=${masterId}`, "DELETE", null);
    } catch (e) { alert("操作失败：" + e.message); }
    refreshOne(p.id);
  };

  $(".cmt-btn", card).onclick = () => {
    const f = $(".m-form", card); f.hidden = !f.hidden;
    if (!f.hidden) { f.dataset.parent = ""; $("input", f).focus(); }
  };
  card.addEventListener("click", async (ev) => {
    const dc = ev.target.closest("[data-del-cmt]");
    if (dc) {
      if (!confirm("删掉这条评论？（楼中楼一起）")) return;
      try { await jsend(`/api/comments/${dc.dataset.delCmt}`, "DELETE", null); await refreshOne(p.id); }
      catch (e) { alert("没删掉：" + e.message); }
      return;
    }
    const dp = ev.target.closest("[data-del-post]");
    if (dp) {
      if (!confirm("删除这条动态？（评论和赞一起消失）")) return;
      try {
        await jsend(`/api/posts/${dp.dataset.delPost}`, "DELETE", null);
        postsCache.delete(p.id); card.remove();
        $("#feed-empty").style.display = postsCache.size ? "none" : "";
      } catch (e) { alert("没删掉：" + e.message); }
      return;
    }
    const r = ev.target.closest(".c-reply"); if (!r) return;
    const node = r.closest(".m-cmt");
    const f = $(".m-form", card); f.hidden = false;
    f.dataset.parent = node.dataset.id;
    const inp = $("input", f);
    inp.placeholder = `回复 ${$(".who", node).textContent}…`;
    inp.focus();
  });

  const f = $(".m-form", card);
  f.onsubmit = async (ev) => {
    ev.preventDefault();
    const inp = $("input", f); const content = inp.value.trim();
    if (!content || masterId == null) return;
    const btn = $("button", f); btn.disabled = true;
    try {
      await jsend(API.comments(p.id), "POST", {
        actor_id: masterId, content,
        parent_id: f.dataset.parent ? +f.dataset.parent : null,
      });
      inp.value = ""; f.dataset.parent = ""; inp.placeholder = "说点什么…";
      await refreshOne(p.id);
    } catch (e) { alert("发送失败：" + e.message); }
    btn.disabled = false;
  };
}

async function refreshOne(id) {
  const p = await jget(API.post(id));
  postsCache.set(id, p);
  const card = feed.querySelector(`.m-card[data-id="${id}"]`);
  if (card && !cardBusy(card)) {
    const fresh = renderCard(p);
    card.replaceWith(fresh);
    if (!$(".m-form", fresh).hidden) {}   // 重渲染后收起输入（内容已发出）
  }
}

/* ---------- 加载 ---------- */
function mergePosts(list) {
  let changed = false;
  for (const p of list) {
    if (!postsCache.has(p.id)) changed = true;
    postsCache.set(p.id, p);
  }
  return changed;
}

async function loadFirst() {
  const d = await jget(`${API.posts}?limit=15`);
  mergePosts(d.posts || []);
  paintAll();
  $("#feed-empty").style.display = postsCache.size ? "none" : "";
  if (d.posts && d.posts.length) {
    oldestId = Math.min(...postsCache.keys());
    $("#sentinel").hidden = !d.has_more;
    watchSentinel();
  }
}

function paintAll() {
  const ids = [...postsCache.keys()].sort((a, b) => b - a);
  for (const id of ids) {
    if (!feed.querySelector(`.m-card[data-id="${id}"]`)) {
      feed.insertBefore(renderCard(postsCache.get(id)), $("#feed-empty"));
    }
  }
}

async function loadMore() {
  if (loading || done || oldestId == null) return;
  loading = true;
  const d = await jget(`${API.posts}?limit=15&before_id=${oldestId}`);
  const list = d.posts || [];
  if (!list.length) { done = true; $("#sentinel").hidden = true; }
  else {
    mergePosts(list); paintAll();
    oldestId = Math.min(...postsCache.keys());
    $("#sentinel").hidden = !d.has_more;
  }
  loading = false;
}

function watchSentinel() {
  new IntersectionObserver((es) => { if (es[0].isIntersecting) loadMore(); },
    { rootMargin: "200px" }).observe($("#sentinel"));
}

/* ---------- 轮询（增量：只拉比现有最新的，不打断输入） ---------- */
setInterval(async () => {
  try {
    const maxId = Math.max(0, ...postsCache.keys());
    const d = await jget(`${API.posts}?limit=15&after_id=${maxId}`);
    for (const p of d.posts || []) {
      postsCache.set(p.id, p);
      const card = feed.querySelector(`.m-card[data-id="${p.id}"]`);
      if (card) { if (!cardBusy(card)) card.replaceWith(renderCard(p)); }
      else feed.insertBefore(renderCard(p), $("#feed-empty"));
    }
    $("#feed-empty").style.display = postsCache.size ? "none" : "";
  } catch (e) { /* 静默，下轮再试 */ }
}, 15000);

(async function init() {
  try {
    const actors = await jget(API.actors);
    for (const a of actors) {
      actorsById[a.id] = a;
      if (a.type === "master") masterId = a.id;
    }
    await loadFirst();
    $("#feed-meta").textContent = `共 ${postsCache.size} 条 · 15s 自动刷新`;
  } catch (e) {
    $("#feed-meta").textContent = "加载失败：" + e.message;
  }
})();
