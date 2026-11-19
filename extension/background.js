// Wiki Inspi: right-click capture. Fetches the image BYTES itself, posts them to the local receiver.
const DEFAULT_PORT = 18765;
const MAX_BYTES = 25 * 1024 * 1024;
const MENU_ID = "wiki-inspi-send";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: MENU_ID, title: "Send to Wiki Inspi", contexts: ["image"] }, () => void chrome.runtime.lastError);
});

function notify(ok, message) {
  chrome.notifications.create({
    type: "basic", iconUrl: "icon128.png",
    title: ok ? "Wiki Inspi: saved" : "Wiki Inspi: failed",
    message: String(message).slice(0, 300),
  });
}

async function getConfig() {
  const c = await chrome.storage.local.get(["token", "port"]);
  return { token: c.token || "", port: c.port || DEFAULT_PORT };
}

// ---- runs IN the page (serialised by executeScript: must be self-contained)
async function pageFetchBytes(srcUrl, maxBytes) {
  try {
    const r = await fetch(srcUrl, { credentials: "include" });
    if (!r.ok) return { error: "page fetch HTTP " + r.status };
    const blob = await r.blob();
    if (blob.size > maxBytes) return { error: "image too large (" + blob.size + " bytes)" };
    const dataUrl = await new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = () => rej(fr.error); fr.readAsDataURL(blob); });
    return { dataUrl };
  } catch (e) { return { error: String(e) }; }
}

function pageMetadata() {
  const meta = (sel) => (document.querySelector(sel) || {}).content || "";
  const host0 = location.hostname;
  let author = "";
  if (/(^|\.)pinterest\.[a-z.]+$/i.test(host0)) { // pin creator first: ld+json / meta author is the generic "Pinterest" or the board owner
    const c = document.querySelector('[data-test-id="creator-profile-name"]');
    if (c) author = c.textContent.replace(/\s+/g, " ").trim();
  }
  author = author || meta('meta[name="author"]') || meta('meta[property="article:author"]');
  const siteName = meta('meta[property="og:site_name"]');
  const nameOf = (v) => !v ? "" : Array.isArray(v) ? nameOf(v[0]) : typeof v === "string" ? v : (v.name || "");
  const walk = (o) => {
    if (!o || typeof o !== "object") return "";
    if (Array.isArray(o)) { for (const x of o) { const r = walk(x); if (r) return r; } return ""; }
    const a = nameOf(o.creator) || nameOf(o.author);
    if (a) return a;
    return walk(o["@graph"]);
  };
  if (!author) {
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      try { const a = walk(JSON.parse(s.textContent)); if (a) { author = a; break; } } catch (e) {}
    }
  }
  const host = location.hostname;
  if (/(^|\.)pinterest\.[a-z.]+$/i.test(host) && /^pinterest$/i.test(author.trim())) author = "";
  if (!author && /(^|\.)behance\.net$/.test(host)) {
    author = meta('meta[property="og:title"]').split(" by ").pop() || "";
    const owner = document.querySelector('a[href*="behance.net/"][class*="Owner"], .Project-owners a, [class*="ProjectInfo-owner"] a');
    if (owner) author = owner.textContent.trim() || author;
  }
  if (!author && /(^|\.)are\.na$/.test(host)) {
    const u = document.querySelector('a[href^="/"][class*="user" i], [class*="Attribution"] a, [data-testid*="user" i]');
    if (u) author = u.textContent.trim();
    if (!author) { // block owner: a top-level /<slug> profile link in the page body (not nav/header/footer, not a site section)
      const reserved = /^\/(login|sign_up|signup|explore|about|pricing|tools|blog|terms|privacy|help|search|settings|notifications|feed|new|api|channels?|blocks?|ai|guide|premium|jobs|press|careers|support|contact|home)$/i;
      for (const a of document.querySelectorAll('a[href^="/"]')) {
        const h = (a.getAttribute("href") || "").split(/[?#]/)[0];
        if (!/^\/[a-z0-9][a-z0-9_.-]*$/i.test(h) || reserved.test(h) || a.closest("header,nav,footer")) continue;
        const t = a.textContent.replace(/\s+/g, " ").trim();
        if (t && t.length <= 80) { author = t; break; }
      }
    }
  }
  if (!author && /(^|\.)wikimedia\.org$/.test(host)) { // file info table: Author / Artist / Creator row
    const label = [...document.querySelectorAll("#fileinfotable td, #fileinfotable th, td.fileinfo-paramfield")].find((c) => /^(author|artist|creator|auteur|artiste)\b/i.test(c.textContent.trim()) && c.textContent.trim().length < 30);
    const cell = label && label.nextElementSibling;
    if (cell) {
      const best = cell.querySelector("#creator bdi, .fn bdi, bdi, .fn a, a:not(.new)");
      let t = best ? best.textContent : "";
      if (!t.trim()) { const c = cell.cloneNode(true); c.querySelectorAll("style,script,table table").forEach((e) => e.remove()); t = c.textContent; }
      author = t.replace(/\s+/g, " ").trim().slice(0, 120);
    }
  }
  return { author: author.slice(0, 200), site_name: siteName.slice(0, 200), page_title: document.title || "" };
}

function pinUrlOf(pageUrl) {
  try {
    const u = new URL(pageUrl);
    if (/(^|\.)pinterest\.[a-z.]+$/i.test(u.hostname) && /^\/pin\/\d+/.test(u.pathname)) return u.origin + u.pathname;
  } catch (e) {}
  return "";
}

async function dataUrlToBlob(dataUrl) { return (await fetch(dataUrl)).blob(); }

async function getBytes(info, tab) {
  const frameIds = info.frameId !== undefined ? [info.frameId] : [0];
  // 1) in the page: same origin / blob: / data: / CORS-enabled, with the page's cookies
  try {
    const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id, frameIds }, func: pageFetchBytes, args: [info.srcUrl, MAX_BYTES] });
    if (res && res.result && res.result.dataUrl) return await dataUrlToBlob(res.result.dataUrl);
    var pageErr = res && res.result && res.result.error;
  } catch (e) { pageErr = String(e); }
  // 2) the worker (host_permissions bypass CORS), cookies included. blob: only exists in the page.
  if (/^blob:/.test(info.srcUrl)) throw new Error("blob: unreadable (" + pageErr + ")");
  const r = await fetch(info.srcUrl, { credentials: "include" });
  if (!r.ok) throw new Error("fetch HTTP " + r.status + " (page: " + pageErr + ")");
  const blob = await r.blob();
  if (blob.size > MAX_BYTES) throw new Error("image too large (" + blob.size + " bytes)");
  return blob;
}

async function send(info, tab) {
  const { token, port } = await getConfig();
  if (!token) throw new Error("no token: open the extension options and pair");
  const pageUrl = info.pageUrl || tab.url || "";
  let meta = { author: "", site_name: "", page_title: tab.title || "" };
  try {
    const [m] = await chrome.scripting.executeScript({ target: { tabId: tab.id, frameIds: [0] }, func: pageMetadata });
    if (m && m.result) meta = m.result;
  } catch (e) { /* restricted page: keep tab title */ }
  const pinUrl = pinUrlOf(pageUrl);
  let blob, bytesError = "";
  try { blob = await getBytes(info, tab); }
  catch (e) { blob = null; bytesError = String(e && e.message || e); }
  // No bytes (CORS / hotlink / 403 / size): a pin page still gets the HD original from the receiver;
  // any http(s) image is saved display-only (note embeds the remote URL) instead of losing the capture.
  const httpSrc = /^https?:\/\//i.test(info.srcUrl);
  if (!blob && !pinUrl && !httpSrc) throw new Error(bytesError || "image unreadable");
  const fd = new FormData();
  fd.append("src_url", info.srcUrl.startsWith("data:") ? "data:" : info.srcUrl);
  fd.append("page_url", pageUrl);
  fd.append("page_title", meta.page_title);
  fd.append("alt", "");
  fd.append("author", meta.author);
  fd.append("site_name", meta.site_name);
  if (pinUrl) { fd.append("pinterest", "1"); fd.append("pin_url", pinUrl); }
  if (blob) fd.append("image", blob, "image");
  else if (httpSrc) fd.append("display_only", "1");
  let resp;
  try {
    resp = await fetch(`http://127.0.0.1:${port}/capture`, { method: "POST", headers: { Authorization: "Bearer " + token }, body: fd });
  } catch (e) { throw new Error("receiver unreachable on 127.0.0.1:" + port + " (is the receiver running?)"); }
  let js = {};
  try { js = await resp.json(); } catch (e) {}
  if (!resp.ok || !js.ok) throw new Error(`receiver HTTP ${resp.status}: ${js.error || "unknown"}`);
  return js;
}

// Same code path as the context-menu click; also what the e2e tests call.
async function handleClick(info, tab) {
  try {
    const js = await send(info, tab);
    const remote = js.mode === "remote";
    const message = (js.status === "duplicate" ? "Already saved, source added" : remote
      ? "saved (display only, image not downloaded)" : "saved") + " : " + js.note;
    notify(true, message);
    return { ok: true, message, result: js };
  } catch (e) {
    const message = e && e.message ? e.message : String(e);
    notify(false, message);
    return { ok: false, message };
  }
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== MENU_ID || !tab) return;
  handleClick(info, tab);
});
