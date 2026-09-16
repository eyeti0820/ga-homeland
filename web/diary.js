/* 交换日记 · 前端逻辑（vanilla 自包含；API 见 server/app/routers/diary.py）
   一人一本：顶部角色标签切换；纸页时间线新→旧；主人在页下夹笔回信；
   15s 轮询增量（打字/聚焦时不打断），下滑翻更早（before_id 游标）。 */
"use strict";

const API = {
  actors: "/api/actors",
  entries: (id, q) => `/api/diary/entries?actor_id=${id}` + (q ? `&${q}` : ""),
  reply: (id) => `/api/diary/entries/${id}/replies`,
};

const $ = (s) => document.querySelector(s);
let ACTORS = [], CHARS = [], masterId = null, cur = null;
let oldestId = null, typing = false;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

async function jget(url) { const r = await fetch(url); if (!r.ok) throw r.status; return r.json(); }
async function jpost(url, body) {
  const r = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)});
  if (!r.ok) throw (await r.text()) || r.status;
  return r.json();
}

const WEEK = ["日","一","二","三","四","五","六"];
function fmtTime(s) {           // "2026-09-14 21:33:12" → "9月14日 周一 21:33"
  const m = /^(\d+)-(\d+)-(\d+) (\d+:\d+)/.exec(s || "");
  if (!m) return s || "";
  const d = new Date(+m[1], +m[2] - 1, +m[3]);
  return `${+m[2]}月${+m[3]}日 周${WEEK[d.getDay()]} ${m[4]}`;
}

function pageEl(e) {
  const el = document.createElement("article");
  el.className = "diary-page"; el.dataset.id = e.id; el.dataset.actor = e.actor_id;
  el.style.setProperty("--tilt", ((e.id % 3) - 1) * 0.4 + "deg");
  el.innerHTML = `
    <div class="diary-page-head">
      <span class="avatar-dot d-${slugOf(e.actor_id)}">${esc((e.author_name || "?").slice(0,1))}</span>
      <b>${esc(e.author_name)}</b><span>的日记</span>
      <span>${fmtTime(e.created_at)}</span>
      ${e.mood ? `<span class="diary-mood">${esc(e.mood)}</span>` : ""}
      ${masterId && e.actor_id === masterId ? `<button class="del-x" data-del-entry="${e.id}" title="撕掉这一页">×</button>` : ""}
    </div>
    <div class="diary-body">${esc(e.content)}</div>
    <div class="diary-letters" data-letters="${e.id}">${e.replies.map(letterHtml).join("")}</div>
    ${masterId ? `
    <div class="diary-letter-form" data-form="${e.id}">
      <textarea maxlength="500" placeholder="在这一页夹一张回信……（署名：小墨）"></textarea>
      <button class="diary-send">夹进去</button>
    </div>` : ""}`;
  return el;
}

function letterHtml(r) {
  return `
    <div class="diary-letter">
      <div class="diary-letter-who">
        <span class="avatar-dot d-${r.author_type === "master" ? "master" : slugOf(r.author_id)}">
          ${esc((r.author_name || "?").slice(0,1))}</span>
        <b>${esc(r.author_name)}</b><span>夹了一页回信 · ${fmtTime(r.created_at)}</span>
        ${r.author_type === "master" ? `<button class="del-x" data-del-letter="${r.id}" title="收回这封回信">×</button>` : ""}
      </div>
      <div class="diary-letter-body">${esc(r.content)}</div>
    </div>`;
}

function slugOf(actorId) {       // actors 里非 master 的 slug（头像色板用）
  const a = ACTORS.find((x) => x.id === actorId);
  return a && a.type !== "master" ? a.slug : "master";
}

/* ---------- 渲染 ---------- */
async function loadActor(actor, append = false) {
  cur = actor;
  const q = append && oldestId ? `limit=8&before_id=${oldestId}` : "limit=8";
  const d = await jget(API.entries(actor.id, q));
  const book = $("#diary-book");
  if (!append) book.innerHTML = "";
  $("#diary-empty").hidden = d.count > 0 || append;
  for (const e of d.entries) book.appendChild(pageEl(e));
  if (d.entries.length) oldestId = d.entries[d.entries.length - 1].id;
  $("#diary-more").hidden = !d.has_more;
  document.querySelectorAll(".diary-tab").forEach((t) =>
    t.classList.toggle("on", +t.dataset.actor === actor.id));
}

/* ---------- 事件 ---------- */
function wire() {
  $("#diary-tabs").addEventListener("click", (ev) => {
    const t = ev.target.closest(".diary-tab");
    if (t) { oldestId = null; loadActor(CHARS.find((c) => c.id === +t.dataset.actor)); }
  });
  $("#diary-more").addEventListener("click", () => loadActor(cur, true));
  $("#diary-book").addEventListener("input", (ev) => { if (ev.target.tagName === "TEXTAREA") typing = true; });
  $("#diary-book").addEventListener("focusin", (ev) => { if (ev.target.tagName === "TEXTAREA") typing = true; });
  $("#diary-book").addEventListener("focusout", (ev) => {
    if (ev.target.tagName === "TEXTAREA" && !ev.target.value.trim()) typing = false;
  });
  $("#diary-book").addEventListener("click", async (ev) => {
    const dl = ev.target.closest("[data-del-letter]");
    if (dl) {
      if (!confirm("收回这封回信？")) return;
      try { await jdelDiary(`/api/diary/replies/${dl.dataset.delLetter}`); dl.closest(".diary-letter").remove(); }
      catch (e) { alert("没收回：" + e.message); }
      return;
    }
    const de = ev.target.closest("[data-del-entry]");
    if (de) {
      if (!confirm("撕掉这一页日记？（回信一起消失）")) return;
      try { await jdelDiary(`/api/diary/entries/${de.dataset.delEntry}`); de.closest(".diary-page").remove(); }
      catch (e) { alert("没撕掉：" + e.message); }
      return;
    }
    const btn = ev.target.closest(".diary-send"); if (!btn) return;
    const form = btn.closest(".diary-letter-form"), ta = form.querySelector("textarea");
    const content = ta.value.trim();
    if (!content) { ta.focus(); return; }
    btn.disabled = true;
    try {
      const r = await jpost(API.reply(+form.dataset.form), {actor_id: masterId, content});
      form.previousElementSibling.insertAdjacentHTML("beforeend", letterHtml(r));
      ta.value = ""; typing = false;
    } catch (e) { alert("没夹进去……再试一次？(" + e + ")"); }
    btn.disabled = false;
  });
}

/* ---------- 轮询（打字不打断） ---------- */
async function poll() {
  if (typing || !cur) return;
  try {
    const d = await jget(API.entries(cur.id, "limit=6"));
    if (!d.entries.length) return;
    const book = $("#diary-book");
    for (const e of d.entries) {          // 新页 prepend；旧页只补新回信
      const old = book.querySelector(`.diary-page[data-id="${e.id}"]`);
      if (!old) book.prepend(pageEl(e));
      else {
        const box = old.querySelector(`[data-letters="${e.id}"]`);
        if (box.children.length < e.replies.length)
          box.insertAdjacentHTML("beforeend", letterHtml(e.replies[e.replies.length - 1]));
      }
    }
    $("#diary-empty").hidden = true;
  } catch (_) {}
}

(async function init() {
  ACTORS = await jget(API.actors);          // 裸数组 [ {id,name,type,...} ]
  CHARS = ACTORS.filter((a) => a.type === "character");
  masterId = (ACTORS.find((a) => a.type === "master") || {}).id || null;
  $("#diary-tabs").innerHTML = CHARS.map((c) =>
    `<button class="diary-tab" data-actor="${c.id}">${esc(c.name)}</button>`).join("");
  wire();
  const want = +new URLSearchParams(location.search).get("actor");   // ?actor=N 直达某一本
  await loadActor(CHARS.find((c) => c.id === want) || CHARS[0]);
  setInterval(poll, 15000);
})();


/* ---------- 主人删除（M4.5） ---------- */
async function jdelDiary(url) {
  const r = await fetch(url, { method: "DELETE" });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
}
