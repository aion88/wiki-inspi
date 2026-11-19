const $ = (id) => document.getElementById(id);
const out = (m) => { $("out").textContent = m; };
chrome.storage.local.get(["token", "port"]).then((c) => { if (c.port) $("port").value = c.port; if (c.token) $("token").value = c.token; });
const base = () => `http://127.0.0.1:${parseInt($("port").value, 10) || 18765}`;
$("save").onclick = async () => { await chrome.storage.local.set({ token: $("token").value.trim(), port: parseInt($("port").value, 10) || 18765 }); out("Saved."); };
$("pair").onclick = async () => {
  try {
    const r = await fetch(base() + "/pair", { method: "POST" });
    const js = await r.json();
    if (!r.ok || !js.token) return out("Pair refused: " + (js.error || r.status) + " (run setup-token.sh, then click within 60 s)");
    $("token").value = js.token;
    await chrome.storage.local.set({ token: js.token, port: parseInt($("port").value, 10) || 18765 });
    out("Paired. Token stored.");
  } catch (e) { out("Receiver unreachable: " + e); }
};
$("test").onclick = async () => {
  try {
    const r = await fetch(base() + "/health", { method: "POST", headers: { Authorization: "Bearer " + $("token").value.trim() } });
    out(r.ok ? "OK: receiver reachable, token accepted." : "Refused: HTTP " + r.status + " " + JSON.stringify(await r.json().catch(() => ({}))));
  } catch (e) { out("Receiver unreachable: " + e); }
};
