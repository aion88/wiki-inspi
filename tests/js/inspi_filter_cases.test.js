// node inspi_filter_cases.test.js : the page filter gives the same answers as inspi_saved.py
const F = require("../../src/inspi_filter.js"), C = require("./inspi_filter_cases.json");
let bad = 0;
for (const { s, want } of C.searches) {
  const got = C.items.filter(it => F.savedOk(it, s, "")).map(it => it.i);
  if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.error("FAIL", JSON.stringify(s), got, want); }
}
console.log(bad ? `${bad} failed` : `ok ${C.searches.length}`); process.exit(bad ? 1 : 0);
