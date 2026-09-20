// Run with jsdom installed as a development-only dependency.
// This checks DOM behavior, not browser layout or paint.
const fs = require("node:fs");
const assert = require("node:assert/strict");
const { JSDOM, VirtualConsole } = require("jsdom");
const errors = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (error) => errors.push(error.message));
const dom = new JSDOM(fs.readFileSync(process.argv[2], "utf8"), {
  runScripts: "dangerously",
  virtualConsole,
  url: "https://report.invalid/",
});
const { window: w } = dom,
  d = w.document;
const input = (id, value) => {
  const el = d.getElementById(id);
  el.value = value;
  el.dispatchEvent(new w.Event("input", { bubbles: true }));
};
assert.equal(errors.length, 0, errors.join("\n"));
assert.match(d.title, /GCAA/);
assert.equal(d.querySelectorAll("#panel tr.cat").length, 6);
assert.ok(d.querySelector("#scorecard .sc-num b").textContent);
assert.equal(d.querySelectorAll("[src],link[rel=stylesheet]").length, 0);
const data = JSON.parse(d.getElementById("vc-data").textContent);
assert.equal(d.querySelectorAll(".graph-node").length, data.graph.nodes.length);
input("findingSearch", "certainly-no-matching-findings-123456");
assert.equal(d.querySelectorAll("#flist .fd").length, 0);
assert.ok(d.querySelector("#clearFilters"));
d.querySelector("#clearFilters").click();
assert.equal(
  d.querySelectorAll("#flist .fd").length,
  Math.min(30, data.findings.length),
);
if (data.findings.length) {
  d.querySelector('[data-sev="high"]').click();
  assert.equal(
    d.querySelectorAll("#flist .fd").length,
    Math.min(30, data.findings.filter((f) => f.severity === "high").length),
  );
  d.querySelector('[data-sev="all"]').click();
  d.querySelector("[data-plan]").click();
  assert.match(d.getElementById("blastInput").value, /^Fix /);
  assert.equal(w.location.hash, "#blast");
}
input("blastInput", "refactor the report renderer");
d.getElementById("blastForm").dispatchEvent(
  new w.Event("submit", { bubbles: true, cancelable: true }),
);
assert.ok(d.querySelector("#blastOut .verdict"));
assert.ok(
  !d.getElementById("blastOut").textContent.includes("analysis failed"),
);
if (data.graph.nodes.length) {
  const first = d.querySelector(".graph-node");
  first.dispatchEvent(
    new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
  );
  assert.equal(first.getAttribute("aria-pressed"), "true");
  assert.ok(d.querySelector("#detail h3").textContent);
  const svg = d.querySelector("#graphbox svg"),
    original = svg.getAttribute("viewBox");
  d.getElementById("zoomIn").click();
  assert.notEqual(svg.getAttribute("viewBox"), original);
  d.getElementById("resetMap").click();
  assert.equal(svg.getAttribute("viewBox"), original);
  input("mriSearch", "no-such-file-xyz");
  assert.match(d.getElementById("detail").textContent, /No files match/);
  assert.equal(
    [...d.querySelectorAll("[data-map-file]")].filter((b) => !b.hidden).length,
    0,
  );
  input("mriSearch", "");
  assert.equal(
    [...d.querySelectorAll("[data-map-file]")].filter((b) => !b.hidden).length,
    data.graph.nodes.length,
  );
}
d.querySelector("#panel tr.cat").click();
assert.equal(d.querySelector("#panel tr.comps").hidden, false);
assert.equal(errors.length, 0, errors.join("\n"));
console.log(
  "PASS: report startup, offline assets, filters, finding → planner, analysis, keyboard graph selection, search, zoom/reset, score detail",
);
w.close();
