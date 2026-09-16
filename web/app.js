/* 家园便签墙 · 前端逻辑（vanilla，无构建链）
   API 约定见 server/app/routers/notes.py
   渲染策略：diff 增量更新（不整墙重建，避免打断 hover/输入），
   首屏 20 条 + 下滑无限加载更早（before_id 游标），
   轮询只拉最新 12 条做增量同步（新便签 + 旧便签新回复）。 */
"use strict";

const API = {
  actors: "/api/actors",
  notes: "/api/notes",
  note: (id) => `/api/notes/${id}`,
  reply: (id) => `/api/notes/${id}/replies`,
};

const PALETTE = ["butter", "terracotta", "rose", "sage", "mist", "lavender"];
const HEX = {
  butter: "#f7ddb0", terracotta: "#e9b9a0", rose: "#e8c8c6",
  sage: "#ccd6bd", mist: "#c3d2da", lavender: "#d2c9de",
};

const PAGE_FIRST = 20;   // 首屏/同步窗口
const POLL_WIN = 12;     // 轮询窗口（最新 N 条做 diff）

let masterId = null;
let actorBySlug = {};        // slug -> {id,name,type}
let lastSeenIds = new Set(); // 已见便签 id（驱动 fresh 动画）
let newestId = 0;            // 已渲染的最大 id
let oldestId = Infinity;     // 已渲染的最小 id
let hasMore = false;         // 是否还有更旧一页
let loadingOlder = false;

const $ = (s) => document.querySelector(s);

/* ---------- 工具 ---------- */
function slugOf(actor) {
  const m = (actor.persona_ref || "").match(/personas\/(\w+)\.md/);
  return m ? m[1] : (actor.type === "master" ? "master" : "mephisto");
}
/* 伪随机倾角：同一便签永远同一角度 */
function tiltOf(id) {
  const a = Math.sin(id * 12.9898) * 43758.5453;
  const r = a - Math.floor(a);                     // 0~1
  return ((r * 6 - 3) * 0.9).toFixed(2);           // ±2.7°
}
function esc(s) {
  return String(s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtTime(t) { return (t || "").slice(5, 16); }

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}
async function jpost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${url} → ${r.status} ${await r.text()}`);
  return r.json();
}

/* ---------- 渲染 ---------- */
function stripHtml(r) {
  const rslug = r.author_type === "master" ? "master" : slugOf(r);
  return `
      <div class="reply-strip">
        <div class="r-meta">
          <span class="avatar-dot d-${rslug}">${esc((r.author_name || "?").slice(0, 1))}</span>
          <span class="r-name">${esc(r.author_name)}</span>
          <span>${fmtTime(r.created_at)}</span>
          ${r.author_type === "master" ? `<button class="del-x rx" data-del-reply="${r.id}" title="删除这条回复">×</button>` : ""}
        </div>
        ${esc(r.content)}
      </div>`;
}
/* 回复区指纹：变了才重渲染（保住正在打字的输入框） */
function rSig(n) {
  return (n.replies || []).map((r) => `${r.id}:${r.content.length}`).join(",");
}
function updateNoteEl(el, n) {
  const sig = rSig(n);
  if (el.dataset.rs === sig) return;
  el.dataset.rs = sig;
  el.querySelectorAll(".reply-strip").forEach((x) => x.remove());
  const row = el.querySelector(".reply-row");
  const tpl = document.createElement("template");
  tpl.innerHTML = (n.replies || []).map(stripHtml).join("");
  [...tpl.content.childNodes].forEach((x) => el.insertBefore(x, row)); // 快照迭代: childNodes是live列表,边移边迭代会跳档丢条(踩坑:主人回复不显示)
}

function noteEl(n, fresh) {
  const slug = slugOf(n);
  const el = document.createElement("article");
  el.className = `note k-${n.color || "butter"} by-${n.author_type}` + (fresh ? " fresh" : "");
  el.dataset.id = n.id;
  el.dataset.rs = rSig(n);
  el.style.setProperty("--tilt", `${tiltOf(n.id)}deg`);
  el.style.setProperty("--tape-tilt", `${((n.id * 7) % 5 - 2) * 1.5}deg`);

  el.innerHTML = `
    ${n.author_type === "master" ? `<button class="del-x" data-del-note="${n.id}" title="删除这张便签">×</button>` : ""}
    <div class="note-body">${esc(n.content)}</div>
    <div class="note-meta">
      <span class="who">${esc(n.author_name)}</span>
      <span>#${n.id} · ${fmtTime(n.created_at)}</span>
    </div>
    ${(n.replies || []).map(stripHtml).join("")}
    <div class="reply-row">
      <input maxlength="300" placeholder="回一句…" data-note="${n.id}">
      <button data-note="${n.id}">回</button>
    </div>`;
  return el;
}

/* 增量同步：已存在→只更新回复区；不存在→按 id 序插入（prepend/append 自动落位） */
/* ---------- 瀑布流布局：新便签放最短列顶（避开 Safari 多列碎片化 bug） ---------- */
function layoutWall() {
  const wall = $("#wall");
  const ae = document.activeElement;
  if (ae && wall.contains(ae) && (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA")) return; // 主人正在打字，跳过搬动
  const wallEmpty = $("#wall-empty");
  const els = [...wall.querySelectorAll(".note")].sort((a, b) => +b.dataset.id - +a.dataset.id);
  if (wallEmpty && !els.length) { wall.appendChild(wallEmpty); return; }
  const GAP = 22, MIN = 272;
  const colNum = Math.max(1, Math.floor((wall.clientWidth + GAP) / (MIN + GAP)));
  while (wall.querySelectorAll(".col").length < colNum) {
    const c = document.createElement("div"); c.className = "col"; wall.appendChild(c);
  }
  const cols = [...wall.querySelectorAll(".col")];
  while (cols.length > colNum) cols.pop().remove();
  const heights = new Array(cols.length).fill(0);
  for (const el of els) {
    let k = 0;
    for (let i = 1; i < heights.length; i++) if (heights[i] < heights[k]) k = i;
    cols[k].appendChild(el); // move
    heights[k] += el.offsetHeight + GAP;
  }
}

function syncNotes(notes) {
  const wall = $("#wall");
  for (const n of notes) {
    let el = wall.querySelector(`.note[data-id="${n.id}"]`);
    if (el) { updateNoteEl(el, n); continue; }
    el = noteEl(n, lastSeenIds.size > 0 && !lastSeenIds.has(n.id));
    wall.appendChild(el); // 顺序无意义，位置由 layoutWall() 统一分配
  }
  notes.forEach((n) => {
    lastSeenIds.add(n.id);
    newestId = Math.max(newestId, n.id);
    if (n.id < oldestId) oldestId = n.id;
  });
  const alive = new Set(notes.map((n) => n.id));
  wall.querySelectorAll(".note").forEach((el) => {
    const id = +el.dataset.id;
    if (!alive.has(id)) { el.remove(); lastSeenIds.delete(id); }
  });
  renderMeta();
  layoutWall();
}
function renderMeta() {
  const count = $("#wall").querySelectorAll(".note").length;
  $("#wall-empty").style.display = count ? "none" : "block";
  const tail = hasMore ? "下滑加载更早" : (count ? "已经到底啦" : "");
  $("#wall-meta").textContent =
    `墙上 ${count} 张 · 每 10 秒悄悄看一眼` + (tail ? ` · ${tail}` : "");
  $("#sentinel").hidden = !hasMore;
}

function renderPresence(actors) {
  const chars = actors.filter((a) => a.type === "character");
  const names = chars.map((a) => a.name).join(" · ");
  $("#presence").textContent = names ? `在家里：${names}` : "";
}

/* ---------- 交互 ---------- */
function buildSwatches() {
  const box = $("#swatches");
  let picked = null;
  PALETTE.forEach((k, i) => {
    const d = document.createElement("div");
    d.className = "swatch" + (i === 0 ? " on" : "");
    d.style.background = HEX[k];
    d.dataset.k = k;
    if (i === 0) picked = k;
    d.onclick = () => {
      box.querySelectorAll(".swatch").forEach((x) => x.classList.remove("on"));
      d.classList.add("on");
      picked = k;
    };
    box.appendChild(d);
  });
  box._pick = () => picked;
}

async function postNote() {
  const input = $("#note-input");
  const btn = $("#post-btn");
  const content = input.value.trim();
  if (!content || masterId == null) return;
  btn.disabled = true;
  try {
    await jpost(API.notes, {
      actor_id: masterId,
      content,
      color: $("#swatches")._pick(),
      pos_x: 0.08 + Math.random() * 0.8,
      pos_y: 0.08 + Math.random() * 0.8,
    });
    input.value = "";
    await syncNow();
  } catch (e) {
    alert("贴不上去…试试刷新？" + e.message);
  } finally {
    btn.disabled = false;
  }
}

async function sendReply(noteId, input) {
  const content = input.value.trim();
  if (!content) return;
  input.disabled = true;
  try {
    await jpost(API.reply(noteId), { actor_id: masterId, content });
    input.value = "";
    const n = await jget(API.note(noteId));   // 定点刷新这一张，不动全墙
    const el = $("#wall").querySelector(`.note[data-id="${noteId}"]`);
    if (el) updateNoteEl(el, n);
  } catch (e) {
    alert("回复失败：" + e.message);
  } finally {
    input.disabled = false;
    input.focus();
  }
}

/* 拉最新一页并增量同步（首屏/发帖后用大窗口） */
async function syncNow() {
  const data = await jget(`${API.notes}?limit=${PAGE_FIRST}`);
  hasMore = data.has_more;
  syncNotes(data.notes);
}
/* 轮询：只看最新窗口，新便签 + 最近便签的新回复 */
async function pollTick() {
  const data = await jget(`${API.notes}?limit=${POLL_WIN}`);
  syncNotes(data.notes);
}
async function loadOlder() {
  if (loadingOlder || !hasMore || oldestId === Infinity) return;
  loadingOlder = true;
  try {
    const data = await jget(`${API.notes}?limit=${PAGE_FIRST}&before_id=${oldestId}`);
    hasMore = data.has_more;
    syncNotes(data.notes);
  } finally {
    loadingOlder = false;
  }
}
async function refresh() {
  try {
    await syncNow();
  } catch (e) {
    $("#wall-meta").textContent = "连不上家里的墙…（服务在 127.0.0.1:7842 吗？）";
  }
}

/* ---------- 启动 ---------- */
(async function init() {
  buildSwatches();
  try {
    const actors = await jget(API.actors);
    renderPresence(actors);
    const master = actors.find((a) => a.type === "master");
    masterId = master ? master.id : null;
    actors.forEach((a) => { actorBySlug[slugOf(a)] = a; });
  } catch (e) { /* presence 缺席不致命 */ }

  $("#post-btn").addEventListener("click", postNote);
  $("#note-input").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) postNote();
  });

  $("#wall").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && ev.target.matches(".reply-row input")) {
      sendReply(+ev.target.dataset.note, ev.target);
    }
  });
  $("#wall").addEventListener("click", (ev) => {
    const dn = ev.target.closest("[data-del-note]");
    if (dn) { delNote(+dn.dataset.delNote); return; }
    const dr = ev.target.closest("[data-del-reply]");
    if (dr) { delReply(+dr.dataset.delReply); return; }
    if (ev.target.matches(".reply-row button")) {
      const input = document.querySelector(`.reply-row input[data-note="${ev.target.dataset.note}"]`);
      if (input) sendReply(+ev.target.dataset.note, input);
    }
  });

  await refresh();
  let resizeT = 0;
  window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(layoutWall, 120); });
  setInterval(async () => {
    try { await pollTick(); } catch (e) { /* 单次轮询失败不打扰 */ }
  }, 10000);

  if ("IntersectionObserver" in window) {
    new IntersectionObserver((ents) => {
      if (ents.some((x) => x.isIntersecting)) loadOlder();
    }, { rootMargin: "400px" }).observe($("#sentinel"));
  } else {
    window.addEventListener("scroll", () => {
      if (innerHeight + scrollY > document.body.scrollHeight - 500) loadOlder();
    }, { passive: true });
  }
})();


/* ---------- 主人删除（M4.5） ---------- */
async function jdel(url) {
  const r = await fetch(url, { method: "DELETE" });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
}
async function delNote(id) {
  if (!confirm("撕掉这张便签？（连回复一起消失）")) return;
  try {
    await jdel(`/api/notes/${id}`);
    const el = $("#wall").querySelector(`.note[data-id="${id}"]`);
    if (el) el.remove();
    lastSeenIds.delete(id);
    layoutWall();
  } catch (e) { alert("没撕掉：" + e.message); }
}
async function delReply(id) {
  if (!confirm("删掉这条回复？")) return;
  try {
    await jdel(`/api/note-replies/${id}`);
    await syncNow();
  } catch (e) { alert("没删掉：" + e.message); }
}
