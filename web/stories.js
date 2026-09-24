/* 我们的故事 · 前端（无框架，hash 路由） */
"use strict";
const API = "/api/stories";
const CAST = { xiazhou: "夏以昼", shenxinghui: "沈星回", qinche: "秦彻", lishen: "黎深", qiyu: "祁煜" };
const view = document.getElementById("view");
const metaEl = document.getElementById("st-meta");

function el(tag, cls, html) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
async function jget(url) { const r = await fetch(url); if (!r.ok) throw r.status; return r.json(); }
async function jsend(method, url, body) {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : null });
  if (!r.ok) throw (await r.text().catch(() => r.status));
  return r.status === 204 ? null : r.json().catch(() => null);
}
function fmtTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts.replace(" ", "T"));
  return isNaN(d) ? ts : `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/* ---------------- 路由 ---------------- */
function route() {
  const h = location.hash || "#/";
  const m = h.match(/^#\/(\d+)$/, "");
  if (h === "#/new") return renderNew();
  if (m) return renderDetail(+m[1]);
  return renderList();
}
window.addEventListener("hashchange", route);

/* ---------------- 列表 ---------------- */
async function renderList() {
  view.innerHTML = "";
  let list;
  try { list = await jget(API); } catch (e) {
    view.appendChild(el("p", "st-empty", "加载失败，稍后再试"));
    return;
  }
  const bar = el("div", "st-topbar");
  bar.appendChild(el("span", "hint", list.length ? `共 ${list.length} 部` : ""));
  const nb = el("button", "st-new-btn", "＋ 开新故事");
  nb.onclick = () => (location.hash = "#/new");
  bar.appendChild(nb);
  view.appendChild(bar);

  if (!list.length) {
    view.appendChild(el("p", "st-empty", "书架还空着<br>开一部属于你们的故事吧"));
    return;
  }
  for (const s of list) {
    const card = el("article", "st-card glass");
    card.appendChild(el("h2", null, esc(s.title)));
    if (s.background) card.appendChild(el("p", "bg", esc(s.background)));
    const row = el("div", "st-meta-row");
    for (const slug of (s.cast || [])) row.appendChild(el("span", "st-chip cast", esc(CAST[slug] || slug)));
    row.appendChild(el("span", "st-chip", `${s.chapter_by_chars || 0} 章`));
    row.appendChild(el("span", "st-chip", s.last_chapter_at ? `更新 ${fmtTime(s.last_chapter_at)}` : "未开笔"));
    row.appendChild(el("span", "st-chip " + (s.enabled ? "on" : "off"), s.enabled ? "更新中" : "已停更"));
    card.appendChild(row);
    card.onclick = () => (location.hash = `#/${s.id}`);
    view.appendChild(card);
  }
  metaEl.textContent = `— 我们的故事 · ${list.length} 部 —`;
}

/* ---------------- 详情 ---------------- */
async function renderDetail(sid) {
  view.innerHTML = "";
  let s;
  try { s = await jget(`${API}/${sid}`); } catch (e) {
    view.appendChild(el("p", "st-empty", e === 404 ? "这个故事不存在（可能已被删除）" : "加载失败，稍后再试"));
    return;
  }
  let bible = {};
  try { bible = JSON.parse(s.bible || "{}"); } catch (e) {}

  const back = el("a", "st-back", "← 书架");
  back.href = "#/";
  view.appendChild(back);

  const head = el("section", "st-head glass");
  head.appendChild(el("h1", null, esc(s.title)));
  head.appendChild(el("div", "fields",
    `${s.background ? `背景：<b>${esc(s.background)}</b><br>` : ""}` +
    `${s.user_identity ? `你的身份：<b>${esc(s.user_identity)}</b><br>` : ""}` +
    `执笔：${(s.cast || []).map(c => `<b>${esc(CAST[c] || c)}</b>`).join(" · ")}`));
  if (s.background === "" && s.user_identity === "") head.querySelector(".fields").innerHTML =
    `执笔：${(s.cast || []).map(c => `<b>${esc(CAST[c] || c)}</b>`).join(" · ")}`;
  const bv = el("div", "st-bible");
  bv.appendChild(el("span", "tag", "剧情圣经"));
  bv.appendChild(document.createTextNode(
    ` ${(bible.chronicle || []).slice(-2).join("／") || "尚无大事记"}` +
    `${(bible.foreshadow || []).length ? " ｜ 伏笔：" + bible.foreshadow.slice(0, 3).join("；") : ""}`));
  head.appendChild(bv);
  view.appendChild(head);

  const tl = el("section", "st-timeline");
  const chaps = s.chapters || [];
  if (!chaps.length) tl.appendChild(el("p", "st-empty", "第一章还在路上"));
  const lazy = [];   // 列表接口不带正文（省流量），逐章懒加载
  for (const c of chaps) {
    let a = null;
    if (c.kind === "chapter") {
      a = el("article", "chap body glass");
      a.appendChild(el("div", "ch-top",
        `<span><span class="ch-no">第${c.idx}章</span><span class="ch-title">${esc(c.title || "")}</span></span>` +
        `<span class="ch-time">${esc(CAST[c.author_slug] || "")} · ${fmtTime(c.created_at)}</span>`));
    } else if (c.kind === "master_note") {
      a = el("article", "chap note");
      a.appendChild(el("div", "ch-top", `<span class="ch-title">🎬 导演纸条</span><span class="ch-time">${fmtTime(c.created_at)}${c.consumed ? " · 已落实" : ""}</span>`));
    } else if (c.kind === "master_seg") {
      a = el("article", "chap seg glass");
      a.appendChild(el("div", "ch-top", `<span><span class="ch-no">插入</span><span class="ch-title">${esc(c.title || "")}</span></span><span class="ch-time">你 · ${fmtTime(c.created_at)}</span>`));
    }
    if (!a) continue;
    a.appendChild(el("div", "ch-text", "…"));
    lazy.push([c.id, a]);
    tl.appendChild(a);
  }
  lazy.forEach(([cid, card]) => {
    jget(`${API}/${sid}/chapters/${cid}`).then(full => {
      const t = card.querySelector(".ch-text");
      if (t) t.textContent = full.content || "（本章内容为空）";
      if (full.summary && t) t.after(el("div", "ch-sum", "本章梗概：" + esc(full.summary)));
    }).catch(() => { const t = card.querySelector(".ch-text"); if (t) t.textContent = "（加载失败，刷新试试）"; });
  });
  view.appendChild(tl);

  /* 导演操作台 */
  const dir = el("section", "director glass");
  dir.appendChild(el("h3", null, "导演操作台"));
  const ops = el("div", "ops");
  const bToggle = el("button", "d-btn", s.enabled ? "⏸ 停更" : "▶ 恢复更新");
  bToggle.onclick = async () => {
    try { await jsend("PATCH", `${API}/${sid}`, { enabled: s.enabled ? 0 : 1 }); renderDetail(sid); }
    catch (e) { alert("操作失败：" + e); }
  };
  const bNote = el("button", "d-btn primary", "🎬 贴纸条");
  const bSeg = el("button", "d-btn", "✍️ 插一段正文");
  const bDel = el("button", "d-btn warn", "🗑 删除故事");
  ops.append(bToggle, bNote, bSeg, bDel);
  dir.appendChild(ops);

  const noteP = el("div", "d-panel");
  noteP.innerHTML = `<label style="font-size:12.5px;color:var(--ink-soft);letter-spacing:.08em">给执笔者的批注（下一章会落实）</label>
    <textarea rows="3" placeholder="例：让他在雨夜登场；这章要有一次争吵"></textarea>
    <div class="d-row"><span class="d-tip">可贴多张，会按顺序消化</span><button class="d-btn primary">贴上去</button></div>`;
  const noteErr = el("div", "d-err");
  noteP.appendChild(noteErr);
  noteP.querySelector(".d-btn.primary").onclick = async () => {
    const t = noteP.querySelector("textarea").value.trim();
    if (!t) { noteErr.textContent = "纸条内容不能为空"; noteErr.style.display = "block"; return; }
    try { await jsend("POST", `${API}/${sid}/chapters`, { kind: "master_note", content: t }); renderDetail(sid); }
    catch (e) { noteErr.textContent = "失败：" + esc(e); noteErr.style.display = "block"; }
  };
  bNote.onclick = () => { noteP.classList.toggle("show"); segP.classList.remove("show"); };

  const segP = el("div", "d-panel");
  segP.innerHTML = `<input type="text" placeholder="这段的小标题（可空）">
    <textarea rows="6" placeholder="以你的视角直接写一段，插进故事里" style="margin-top:8px"></textarea>
    <div class="d-row"><span class="d-tip">插入后，角色会接着你的段落继续写</span><button class="d-btn primary">插进去</button></div>`;
  const segErr = el("div", "d-err");
  segP.appendChild(segErr);
  segP.querySelector(".d-btn.primary").onclick = async () => {
    const title = segP.querySelector("input").value.trim();
    const content = segP.querySelector("textarea").value.trim();
    if (!content) { segErr.textContent = "正文不能为空"; segErr.style.display = "block"; return; }
    try { await jsend("POST", `${API}/${sid}/chapters`, { kind: "master_seg", title, content }); renderDetail(sid); }
    catch (e) { segErr.textContent = "失败：" + esc(e); segErr.style.display = "block"; }
  };
  bSeg.onclick = () => { segP.classList.toggle("show"); noteP.classList.remove("show"); };

  bDel.onclick = async () => {
    if (!confirm(`确定删除《${s.title}》？所有章节一并删除，不可恢复。`)) return;
    try { await jsend("DELETE", `${API}/${sid}`); location.hash = "#/"; }
    catch (e) { alert("删除失败：" + e); }
  };
  dir.append(noteP, segP);
  view.appendChild(dir);
  metaEl.textContent = `— ${s.title} · ${s.chapter_by_chars || 0} 章 · ${s.enabled ? "更新中" : "停更"} —`;
}

/* ---------------- 新建 ---------------- */
function renderNew() {
  view.innerHTML = "";
  const back = el("a", "st-back", "← 书架");
  back.href = "#/";
  view.appendChild(back);

  const f = el("form", "st-form glass");
  f.innerHTML = `
    <h1>开新故事</h1>
    <label>书名</label>
    <input type="text" name="title" maxlength="60" placeholder="例：雨夜航灯" required>
    <label>故事背景（这段 IF 平行线的设定，一两句话）</label>
    <textarea name="background" rows="2" placeholder="例：临空市暴雨季，你是航管局新来的管制员"></textarea>
    <label>你的身份（你想以什么身份出场，可空）</label>
    <input type="text" name="user_identity" maxlength="200" placeholder="例：刚调来星港的管制员">
    <label>执笔阵容（点选角色，可多选）</label>
    <div class="cast-pick">${Object.entries(CAST).map(([k, v]) => `<span class="c-chip" data-slug="${k}">${v}</span>`).join("")}</div>
    <label>写作偏好（可空，例：节奏慢一点 / 多写日常）</label>
    <input type="text" name="pace" placeholder="自由发挥就填空">
    <div class="f-actions">
      <label style="display:flex;align-items:center;gap:6px;margin:0;font-size:13px">
        <input type="checkbox" name="enabled" checked> 建好就开始更新
      </label>
      <button type="submit" class="st-new-btn" style="border:none">开书</button>
    </div>
    <div class="d-err"></div>`;
  f.querySelectorAll(".c-chip").forEach(ch => ch.onclick = () => ch.classList.toggle("on"));
  f.onsubmit = async (ev) => {
    ev.preventDefault();
    const err = f.querySelector(".d-err");
    const cast = [...f.querySelectorAll(".c-chip.on")].map(c => c.dataset.slug);
    if (!cast.length) { err.textContent = "至少选一位执笔角色"; err.style.display = "block"; return; }
    const body = {
      title: f.elements.title.value.trim(),
      background: f.elements.background.value.trim(),
      user_identity: f.elements.user_identity.value.trim(),
      cast_json: JSON.stringify(cast),
      style_json: JSON.stringify({ note: f.elements.pace.value.trim() }),
      enabled: f.enabled.checked ? 1 : 0,
    };
    if (!body.title) { err.textContent = "书名不能为空"; err.style.display = "block"; return; }
    try {
      const s = await jsend("POST", API, body);
      location.hash = `#/${s.id}`;
    } catch (e) { err.textContent = "创建失败：" + esc(e); err.style.display = "block"; }
  };
  view.appendChild(f);
  metaEl.textContent = "— 开新书 —";
}

route();
