/* Shared by the gallery page and tests: the same rules as inspi_saved.py (checked on one fixture file). */
(function (root) {
  function lin(c) { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }
  function lab(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim()); if (!m) return null;
    const n = parseInt(m[1], 16), r = lin(n >> 16), g = lin((n >> 8) & 255), b = lin(n & 255);
    const x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047;
    const y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b;
    const z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883;
    const f = t => t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116;
    const fx = f(x), fy = f(y), fz = f(z);
    return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
  }
  function colorDist(it, hex) {
    const t = lab(hex); if (!t) return null;
    let best = null;
    [it.c, ...(it.pal || [])].forEach(c => { const l = lab(c); if (!l) return; const d = Math.hypot(l[0] - t[0], l[1] - t[1], l[2] - t[2]); if (best === null || d < best) best = d; });
    return best;
  }
  function hay(it, famLabel) {
    const fv = it.fa ? Object.values(it.fa).flat().join(" ") : "";
    return [it.t, it.b, (it.s || []).join(" "), (it.m || []).join(" "), fv, famLabel || "", it.txt || ""].join(" ").toLowerCase();
  }
  function queryOk(it, q, famLabel) {
    q = String(q || "").trim().toLowerCase(); if (!q) return true;
    const h = hay(it, famLabel); return q.split(/\s+/).every(w => h.includes(w));
  }
  function facetsOk(it, facets) {
    for (const k in facets || {}) { const want = facets[k] || []; if (want.length && !(it.fa?.[k] || []).some(v => want.includes(v))) return false; }
    return true;
  }
  function colorOk(it, hex, tol) { if (!hex) return true; const d = colorDist(it, hex); return d !== null && d <= tol; }
  function savedOk(it, s, famLabel) { return queryOk(it, s.query, famLabel) && facetsOk(it, s.facets) && colorOk(it, s.color, s.tol || 25); }
  const api = { lab, colorDist, queryOk, facetsOk, colorOk, savedOk };
  if (typeof module !== "undefined") module.exports = api; else root.InspiFilter = api;
})(this);
