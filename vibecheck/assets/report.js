(function () {
  "use strict";
  const D = JSON.parse(document.getElementById("vc-data").textContent);
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = (s) =>
    String(s == null ? "" : s).replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const codeify = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  const base = (p) => String(p).split("/").pop();
  const fmtDate = (ts) =>
    ts ? new Date(ts * 1000).toISOString().slice(0, 10) : "";
  const SEV = ["high", "medium", "low", "info"];
  const CAT = {
    "agent-risk": "Agent-edit risk",
    coupling: "Coupling",
    duplication: "Duplication",
    size: "God objects",
    "prompt-archaeology": "Prompt archaeology",
  };
  const TIERLBL = {
    "do-not-touch": "Review required",
    careful: "Careful",
    normal: "Normal",
    generated: "Generated",
  };
  function h(tag, attrs, html) {
    const e = document.createElement(tag);
    if (attrs)
      for (const k in attrs) {
        if (attrs[k] != null) e.setAttribute(k, attrs[k]);
      }
    if (html != null) e.innerHTML = html;
    return e;
  }
  function css(name) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
  }

  function downloadText(name, content, type) {
    const url = URL.createObjectURL(new Blob([content], { type }));
    const a = h("a", { href: url, download: name });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  $("#downloadData").addEventListener("click", () =>
    downloadText(
      "gcaa-scan.json",
      JSON.stringify(D, null, 2),
      "application/json",
    ),
  );
  const highCount = D.findings.filter((f) => f.severity === "high").length;
  const reviewCount = D.risk.filter((r) => r.tier === "do-not-touch").length;
  $("#summaryStrip").innerHTML =
    `<a href="#findings" class="high"><span>High-severity findings</span><b>${highCount}</b><small>Verify before editing</small></a><a href="#touch"><span>Files needing review</span><b>${reviewCount}</b><small>Highest impact tier</small></a><a href="#findings"><span>Total findings</span><b>${D.findings.length}</b><small>Source evidence included</small></a><a href="#method"><span>Analysis scope</span><b>${D.stats.code_files}</b><small>Code files · ${Object.keys(D.stats.languages || {}).length} languages</small></a>`;

  /* ------------------------------------------------------------------ masthead */
  (function meta() {
    const r = D.repo,
      s = D.stats;
    const agents = Object.entries(s.ai_agents || {})
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => k.replace(/\s*<.*$/, "") + " " + v)
      .join(", ");
    const rows = [
      ["Repository", esc(r.name)],
      [
        "Commit",
        `<span class="mono">${esc(r.short)}</span>${r.branch ? " · " + esc(r.branch) : ""}`,
      ],
      ["Scanned", fmtDate(r.scanned_at)],
      ["Code", `${s.code_files} files · ${s.loc.toLocaleString()} lines`],
      [
        "History",
        s.commits
          ? `${s.commits} commits, ${fmtDate(s.first_ts)} → ${fmtDate(s.last_ts)}`
          : "no history",
      ],
      [
        "Written with",
        s.commits
          ? `${s.ai_commits} of ${s.commits} commits co-authored by AI${agents ? " (" + esc(agents) + ")" : ""}`
          : "unknown",
      ],
    ];
    $("#meta").innerHTML = rows
      .map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`)
      .join("");
  })();

  /* ------------------------------------------------------------------ result */
  (function result() {
    const sc = D.score;
    const total = sc.total;
    const readings = {
      handover:
        "Lower structural risk in the checks run. Ordinary review still applies.",
      pack: "Prepare a brief with the relevant files, invariants and verification steps.",
      supervised:
        "Agent changes need a person reviewing each diff against the risks below.",
      stabilize:
        "Pay down the worst debt before handing this repository to an agent.",
    };
    $("#scorecard").innerHTML = `
    <div class="eyebrow">Architecture debt score</div>
    <div class="sc-num"><b>${Math.round(total)}</b><small>/ 100</small></div>
    <div class="stamp">${esc(sc.tier_label)}</div>
    <p class="sc-read">${readings[sc.tier] || ""} Lower is better.</p>
    <div class="tierscale" aria-label="Score scale">
      <div class="bands"><i></i><i></i><i></i><i></i></div>
      <div class="pin" style="left:calc(${Math.min(99.5, total)}% - 1px)"></div>
      <div class="labels"><span>Hand over</span><span>With a pack</span><span>Supervised</span><span>Stabilize first</span></div>
    </div>
    <div class="note" style="margin-top:12px;font-size:12px">Scoring ${esc(sc.version)} · ${sc.kloc}k lines. Heuristic, not calibrated.</div>`;

    const panel = $("#panel");
    let html = `<thead><tr><th>Measure</th><th class="num">Result</th><th>0 — 100</th><th>Flag</th><th class="ref">Weight</th></tr></thead><tbody>`;
    Object.entries(sc.categories).forEach(([key, c], i) => {
      const flag =
        c.score >= 65
          ? '<span class="flag hh" title="High: above 65">HH</span>'
          : c.score >= 45
            ? '<span class="flag h" title="Elevated: 45 to 65">H</span>'
            : c.score >= 25
              ? '<span class="flag" title="Above the 25 reference">↑</span>'
              : '<span class="flag muted" title="Within reference">—</span>';
      html += `<tr class="cat" tabindex="0" role="button" aria-expanded="false" data-i="${i}">
      <td><span class="caret">▸</span><span class="catname">${esc(c.label)}</span></td>
      <td class="num">${c.score.toFixed(0)}</td>
      <td><div class="bar"><i style="width:${c.score}%"></i><b style="left:25%"></b><b style="left:45%"></b><b style="left:65%"></b></div></td>
      <td>${flag}</td><td class="ref">${Math.round(sc.weights[key] * 100)}%</td></tr>
      <tr class="comps" data-for="${i}" hidden><td colspan="5">${c.components
        .map(
          (x) => `
        <div class="comp"><span>${esc(x.name)}</span><span class="cv">${esc(x.value)}${x.range ? ` <span class="muted">· full debt at ${esc(x.range[1])}${x.range[0] !== 0 ? ", none at " + esc(x.range[0]) : ""}</span>` : ""}</span>
        <span class="cs">${x.score == null ? "n/a" : x.score.toFixed(0)}</span><span class="minibar"><i style="width:${x.score || 0}%"></i></span></div>`,
        )
        .join("")}
        ${c.credit ? `<div class="credit">−${c.credit} credit: ${esc(c.credit_note)}</div>` : ""}</td></tr>`;
    });
    panel.innerHTML = html + "</tbody>";
    panel.querySelectorAll("tr.cat").forEach((tr) => {
      const toggle = () => {
        const row = panel.querySelector(`tr.comps[data-for="${tr.dataset.i}"]`);
        row.hidden = !row.hidden;
        tr.setAttribute("aria-expanded", String(!row.hidden));
      };
      tr.addEventListener("click", toggle);
      tr.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          toggle();
        }
      });
    });

    // attention: the most severe finding of each kind, and the two riskiest files
    const seen = new Set(),
      picks = [];
    for (const f of D.findings) {
      if (f.severity === "info" || seen.has(f.id)) continue;
      seen.add(f.id);
      picks.push(f);
      if (picks.length >= 5) break;
    }
    const ol = $("#attention");
    ol.innerHTML = picks
      .map((f) => {
        const ev = f.evidence
          .slice(0, 2)
          .map((e) => `${esc(e.file)}${e.line ? ":" + e.line : ""}`)
          .join(" · ");
        return `<li><span class="t"><span class="chip sev-${f.severity}">${f.severity}</span> ${codeify(f.title)}</span>
      <span class="d">${codeify(f.detail)}</span><span class="e">${ev} · <button class="text-link" type="button" data-review="${esc(f.id)}">Review evidence</button></span></li>`;
      })
      .join("");
    if (!picks.length)
      ol.innerHTML = `<li><span class="t">No prioritized findings in the checks run.</span><span class="d">This does not establish that the repository is free of defects. Review language coverage and limits below.</span></li>`;
    const dnt = D.risk.filter((r) => r.tier === "do-not-touch").slice(0, 3);
    if (dnt.length)
      ol.insertAdjacentHTML(
        "beforeend",
        `<li><span class="t"><span class="chip tier-do-not-touch">risk</span> Keep agents out of ${dnt.map((r) => `<code>${esc(base(r.path))}</code>`).join(", ")} without a person in the loop</span>
    <span class="d">${esc(dnt[0].reasons.slice(0, 2).join("; "))}.</span></li>`,
      );
  })();

  /* ------------------------------------------------------------------ caught in history */
  function caughtEvents() {
    const tl = D.timeline;
    if (!tl || !tl.length) return [];
    const events = [];
    for (let i = 0; i < tl.length; i++) {
      const prev = new Set(
        i ? tl[i - 1].high.map((x) => x.id + "|" + x.title) : [],
      );
      const fresh = tl[i].high.filter((x) => !prev.has(x.id + "|" + x.title));
      if (!fresh.length) continue;
      const groups = {};
      fresh.forEach((x) => (groups[x.id] = groups[x.id] || []).push(x));
      for (const id in groups) {
        const items = groups[id];
        let j = i + 1;
        while (
          j < tl.length &&
          tl[j].high.some(
            (x) => x.id === id && items.some((y) => y.title === x.title),
          )
        )
          j++;
        events.push({
          id,
          items,
          from: tl[i],
          last: tl[j - 1],
          fixed: j < tl.length ? tl[j] : null,
          span: j - i,
        });
      }
    }
    return events;
  }
  (function caught() {
    const ev = caughtEvents().filter((e) => e.fixed);
    if (!ev.length) return;
    const box = $("#caught");
    box.hidden = false;
    const label = (e) =>
      e.items.length === 1
        ? codeify(e.items[0].title)
        : `${e.items.length} findings like ${codeify(e.items[0].title)}`;
    box.innerHTML = `<h3>Findings that disappear in history</h3>
    <p class="note" style="margin:0 0 8px">Scanning each past commit as it stood, these high findings appear, then disappear in a later commit; this does not establish why they disappeared.</p>
    <ul>${ev
      .slice(0, 4)
      .map(
        (e) => `<li><div>${label(e)}</div>
      <div class="when">${esc(e.from.sha)} → fixed ${e.span > 1 ? e.span + " commits later " : ""}in ${esc(e.fixed.sha)}: “${esc(e.fixed.subject)}”</div></li>`,
      )
      .join("")}</ul>`;
  })();

  /* ------------------------------------------------------------------ findings */
  (function findings() {
    const counts = { all: D.findings.length };
    SEV.forEach(
      (sev) =>
        (counts[sev] = D.findings.filter((f) => f.severity === sev).length),
    );
    const state = { sev: "all", limit: 30, q: "", category: "all" };
    const filt = $("#ffilters"),
      search = $("#findingSearch"),
      category = $("#findingCategory");
    filt.innerHTML = ["all", ...SEV]
      .map(
        (sev) =>
          `<button class="fbtn" type="button" data-sev="${sev}" aria-pressed="${sev === "all"}">${sev === "all" ? "All" : sev[0].toUpperCase() + sev.slice(1)}<span class="n">${counts[sev]}</span></button>`,
      )
      .join("");
    [...new Set(D.findings.map((f) => f.category))]
      .sort()
      .forEach((c) =>
        category.appendChild(h("option", { value: c }, esc(CAT[c] || c))),
      );
    function draw() {
      const list = D.findings
        .map((f, i) => ({ f, i }))
        .filter(
          ({ f }) =>
            (state.sev === "all" || f.severity === state.sev) &&
            (state.category === "all" || f.category === state.category) &&
            [
              f.title,
              f.detail,
              f.id,
              ...f.evidence.map((e) => e.file + " " + (e.text || "")),
            ]
              .join(" ")
              .toLowerCase()
              .includes(state.q),
        );
      $("#findingCount").textContent =
        `${list.length} of ${D.findings.length} findings`;
      $("#flist").innerHTML =
        list
          .slice(0, state.limit)
          .map(
            ({ f, i }) => `
      <details class="fd" id="finding-${i}"><summary><span class="chip sev-${f.severity}">${f.severity}</span><span class="fd-title">${codeify(f.title)}</span><span class="fd-cat">${esc(CAT[f.category] || f.category)}</span></summary>
      <div class="fd-body"><p>${codeify(f.detail)}</p>${f.evidence.length ? `<div class="ev">${f.evidence.map((e) => `<div class="ev-line"><span class="ev-loc">${esc(e.file)}${e.line ? ":" + e.line : ""}</span><span class="ev-code">${esc(e.text || "")}</span></div>`).join("")}</div>` : ""}
      <div class="evidence-action"><button class="text-link" type="button" data-plan="${i}">Plan a change for this finding</button></div></div></details>`,
          )
          .join("") +
        (list.length > state.limit
          ? `<button class="btn ghost more" type="button" id="fmore">Show ${Math.min(30, list.length - state.limit)} more</button>`
          : "");
      if (!list.length)
        $("#flist").innerHTML =
          `<div class="empty-state">${D.findings.length ? 'No findings match these filters. <button class="text-link" id="clearFilters" type="button">Clear filters</button>' : "No findings from the checks run. Consult Method &amp; limits before treating this as a clean bill of health."}</div>`;
      const more = $("#fmore");
      if (more)
        more.addEventListener("click", () => {
          state.limit += 30;
          draw();
        });
      const clear = $("#clearFilters");
      if (clear) clear.addEventListener("click", reset);
    }
    function reset() {
      Object.assign(state, { sev: "all", limit: 30, q: "", category: "all" });
      search.value = "";
      category.value = "all";
      filt
        .querySelectorAll("button")
        .forEach((b) =>
          b.setAttribute("aria-pressed", String(b.dataset.sev === "all")),
        );
      draw();
    }
    filt.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-sev]");
      if (!b) return;
      state.sev = b.dataset.sev;
      state.limit = 30;
      filt
        .querySelectorAll("button")
        .forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      draw();
    });
    search.addEventListener("input", () => {
      state.q = search.value.trim().toLowerCase();
      state.limit = 30;
      draw();
    });
    category.addEventListener("change", () => {
      state.category = category.value;
      state.limit = 30;
      draw();
    });
    $("#attention").addEventListener("click", (e) => {
      const b = e.target.closest("[data-review]");
      if (!b) return;
      reset();
      const i = D.findings.findIndex((f) => f.id === b.dataset.review);
      state.limit = Math.max(30, i + 1);
      draw();
      const target = $("#finding-" + i);
      if (target) {
        target.open = true;
        location.hash = target.id;
        target.querySelector("summary").focus();
      }
    });
    $("#flist").addEventListener("click", (e) => {
      const b = e.target.closest("[data-plan]");
      if (!b) return;
      const f = D.findings[Number(b.dataset.plan)];
      $("#blastInput").value =
        "Fix " + f.title + " in " + f.evidence.map((x) => x.file).join(", ");
      location.hash = "blast";
      $("#blastInput").focus();
    });
    draw();
  })();

  /* ------------------------------------------------------------------ do not touch */
  (function risk() {
    let showAll = false;
    function draw() {
      const rows = showAll ? D.risk : D.risk.slice(0, 15);
      $("#risk").innerHTML =
        `<thead><tr><th>#</th><th>File</th><th>Regression risk</th><th>Tier</th><th>Why</th></tr></thead><tbody>` +
        rows
          .map(
            (
              r,
              i,
            ) => `<tr class="${r.tier === "do-not-touch" ? "dnt" : ""}"><td class="rk">${i + 1}</td><td class="file"><button type="button" class="file-button" data-file="${esc(r.path)}">${esc(r.path)}</button></td>
        <td><div class="sbar"><div class="bar"><i style="width:${r.score}%"></i><b style="left:45%"></b><b style="left:62%"></b></div><span>${Math.round(r.score)}</span></div></td>
        <td><span class="chip tier-${r.tier}">${TIERLBL[r.tier] || r.tier}</span></td>
        <td><ul>${
          r.reasons
            .slice(0, 4)
            .map((x) => `<li>${codeify(x)}</li>`)
            .join("") || "<li>nothing stands out</li>"
        }</ul></td></tr>`,
          )
          .join("") +
        "</tbody>";
      $("#riskMore").hidden = D.risk.length <= 15;
      $("#riskMore").textContent = showAll
        ? "Show the top 15"
        : `Show all ${D.risk.length} files`;
    }
    $("#risk").addEventListener("click", (e) => {
      const b = e.target.closest("[data-file]");
      if (b) {
        location.hash = "mri";
        selectNode(b.dataset.file);
        if (MRI.focus) MRI.focus(b.dataset.file);
      }
    });
    $("#riskMore").addEventListener("click", () => {
      showAll = !showAll;
      draw();
    });
    draw();
    if (D.secret_controllers && D.secret_controllers.length) {
      $("#controllers").innerHTML =
        `<h3 style="margin-top:22px">Small files with outsized reach</h3>
      <p class="note" style="margin:0">Below-median size, top-fifth reach. They look like helpers in a file tree; a signature change here lands everywhere.</p>
      <div class="controllers">${D.secret_controllers.map((s) => `<div class="ctl"><b>${esc(base(s.path))}</b><div class="muted">${s.loc} lines · ${s.fan_in} files depend on it</div></div>`).join("")}</div>`;
    }
  })();

  /* ------------------------------------------------------------------ repo MRI */
  const KINDS = [
    { k: "static", label: "Code references", color: "--s1", dash: null },
    { k: "contract", label: "Routed string keys", color: "--s2", dash: null },
    { k: "string", label: "Shared rare strings", color: "--s3", dash: "2 3" },
    { k: "cochange", label: "Change together", color: "--s0", dash: "6 4" },
  ];
  const MRI = {
    selected: null,
    on: { static: true, contract: true, string: false, cochange: true },
  };
  function tierColor(n) {
    if (n.tier === "do-not-touch") return css("--crit");
    if (n.tier === "careful") return css("--serious");
    if (n.tier === "generated") return css("--sheet");
    if (n.role === "test" || n.role === "tool") return css("--sheet");
    if (n.role === "resource") return css("--ink-3");
    return css("--s0");
  }
  function edgeKind(e) {
    const ks = Object.keys(e.kinds);
    for (const k of ["static", "contract", "cochange", "string"])
      if (ks.includes(k)) return k;
    return ks[0];
  }
  function detailFor(id) {
    const m = D.metrics[id] || {},
      r = D.risk.find((x) => x.path === id);
    const out = D.graph.edges.filter((e) => e.s === id),
      inn = D.graph.edges.filter((e) => e.t === id);
    const rel = (list, key) =>
      list
        .sort((a, b) => b.w - a.w)
        .slice(0, 12)
        .map(
          (e) =>
            `<li><button type="button" data-go="${esc(e[key])}">${esc(base(e[key]))}</button><span class="why">${esc(e.why.join("; "))}</span></li>`,
        )
        .join("");
    return `<div class="eyebrow">${esc(m.role || "")}${m.autoload ? " · autoload " + esc(m.autoload) : ""}${m.class_name ? " · class " + esc(m.class_name) : ""}</div>
    <h3>${esc(id)}</h3>
    ${r ? `<span class="chip tier-${r.tier}">${TIERLBL[r.tier]} · ${Math.round(r.score)}</span>` : ""}
    ${m.generated_by ? `<p class="note">Generated by <code>${esc(m.generated_by)}</code>.</p>` : ""}
    ${m.doc ? `<p class="note">${esc(m.doc)}</p>` : ""}
    <div class="kv"><div><b>${m.loc || 0}</b><span>lines</span></div><div><b>${m.fan_in || 0}</b><span>dependents</span></div><div><b>${m.reach == null ? "–" : m.reach}</b><span>reach</span></div>
      <div><b>${m.funcs || 0}</b><span>functions</span></div><div><b>${m.longest_func || 0}</b><span>longest fn</span></div><div><b>${m.churn_commits || 0}</b><span>commits</span></div></div>
    ${r && r.reasons.length ? `<ul class="rel">${r.reasons.map((x) => `<li>${codeify(x)}</li>`).join("")}</ul>` : ""}
    ${inn.length ? `<div class="eyebrow">Depended on by ${inn.length}</div><ul class="rel">${rel(inn, "s")}</ul>` : ""}
    ${out.length ? `<div class="eyebrow">Depends on ${out.length}</div><ul class="rel">${rel(out, "t")}</ul>` : ""}`;
  }
  function selectNode(id) {
    MRI.selected = id;
    const d = $("#detail");
    d.innerHTML = id
      ? detailFor(id)
      : `<div class="eyebrow">Select a file</div><p class="note">Click any circle to see its measurements, what depends on it and why. Circle size is lines of code; colour is regression risk.</p>`;
    d.querySelectorAll("[data-go]").forEach((b) =>
      b.addEventListener("click", () => {
        selectNode(b.dataset.go);
        if (MRI.focus) MRI.focus(b.dataset.go);
      }),
    );
    if (MRI.highlight) MRI.highlight(id);
  }
  (function mri() {
    const tools = $("#mriTools"),
      box = $("#graphbox");
    const nodes = D.graph.nodes.map((n) => ({ ...n }));
    tools.innerHTML =
      KINDS.map(
        (k) =>
          `<button class="kind" type="button" data-k="${k.k}" aria-pressed="${MRI.on[k.k]}">${k.label} <span class="muted">${D.graph.edges.filter((e) => k.k in e.kinds).length}</span></button>`,
      ).join("") +
      `<input type="search" id="mriSearch" placeholder="Find a file" aria-label="Find a file"><div class="graph-controls"><button class="btn ghost" id="zoomIn" aria-label="Zoom in" type="button">+</button><button class="btn ghost" id="zoomOut" aria-label="Zoom out" type="button">−</button><button class="btn ghost" id="resetMap" type="button">Reset view</button></div>`;
    $("#mriLegend").innerHTML =
      `<span><i class="node-dot" style="background:var(--crit)"></i>Review required</span><span><i class="node-dot" style="background:var(--serious)"></i>Careful</span><span><i class="node-dot" style="background:var(--s0)"></i>Normal</span><span>Size: lines of code · grouped by directory · drag to pan</span>`;
    selectNode(null);
    if (!nodes.length) {
      $("#graphFallback").textContent =
        "No supported files to map in this snapshot.";
      return;
    }
    const svgNS = "http://www.w3.org/2000/svg";
    const svgEl = (tag, attrs, parent, text) => {
      const el = document.createElementNS(svgNS, tag);
      Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
      if (text != null) el.textContent = text;
      parent.appendChild(el);
      return el;
    };
    const dirs = [...new Set(nodes.map((n) => n.dir))].sort();
    const groupSize = new Map(
      dirs.map((d) => [d, nodes.filter((n) => n.dir === d).length]),
    );
    // A stable directory grid makes the graph repeatable and bounds layout cost.
    const cols = Math.max(1, Math.ceil(Math.sqrt(dirs.length)));
    const cell = Math.max(
      300,
      Math.ceil(Math.sqrt(Math.max(...groupSize.values()))) * 75,
    );
    const width = cols * cell,
      height = Math.ceil(dirs.length / cols) * cell;
    box.replaceChildren();
    const svg = svgEl(
      "svg",
      {
        viewBox: `0 0 ${width} ${height}`,
        role: "group",
        "aria-label": "Dependency graph of the repository",
      },
      box,
    );
    const edgesLayer = svgEl("g", {}, svg),
      nodesLayer = svgEl("g", {}, svg);
    const view = { x: 0, y: 0, w: width, h: height };
    function updateView() {
      svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
    }
    function zoom(factor) {
      const w = Math.min(width * 2, Math.max(width / 8, view.w * factor));
      const h = (w * height) / width;
      view.x += (view.w - w) / 2;
      view.y += (view.h - h) / 2;
      view.w = w;
      view.h = h;
      updateView();
    }
    $("#zoomIn").addEventListener("click", () => zoom(0.75));
    $("#zoomOut").addEventListener("click", () => zoom(1.3));
    $("#resetMap").addEventListener("click", () => {
      Object.assign(view, { x: 0, y: 0, w: width, h: height });
      updateView();
      $("#mriSearch").value = "";
      filterFiles("");
      selectNode(null);
    });
    let drag = null;
    svg.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".graph-node") || e.button !== 0) return;
      drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
      svg.setPointerCapture(e.pointerId);
    });
    svg.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const scale = Math.max(
        view.w / svg.clientWidth,
        view.h / svg.clientHeight,
      );
      view.x = drag.vx - (e.clientX - drag.x) * scale;
      view.y = drag.vy - (e.clientY - drag.y) * scale;
      updateView();
    });
    svg.addEventListener("pointerup", () => (drag = null));
    svg.addEventListener("pointercancel", () => (drag = null));
    const topIds = new Set(D.risk.slice(0, 8).map((r) => r.path)),
      nodeElements = new Map();
    dirs.forEach((dir, i) => {
      const cx = ((i % cols) + 0.5) * cell,
        cy = (Math.floor(i / cols) + 0.5) * cell;
      const members = nodes
        .filter((n) => n.dir === dir)
        .sort((a, b) => a.id.localeCompare(b.id));
      svgEl(
        "text",
        {
          x: cx,
          y: cy - cell * 0.42,
          "text-anchor": "middle",
          "font-size": 13,
          fill: css("--ink-3"),
        },
        svg,
        dir ? dir + "/" : "repository root",
      );
      members.forEach((n, j) => {
        const angle = j * 2.399963229728653,
          radius =
            members.length === 1
              ? 0
              : Math.sqrt((j + 0.5) / members.length) * cell * 0.32;
        n.x = cx + Math.cos(angle) * radius;
        n.y = cy + Math.sin(angle) * radius;
        const group = svgEl(
          "g",
          {
            class: "graph-node",
            tabindex: 0,
            role: "button",
            "aria-label": n.id,
            "aria-pressed": "false",
          },
          nodesLayer,
        );
        svgEl(
          "circle",
          {
            cx: n.x,
            cy: n.y,
            r: Math.min(22, 5 + Math.sqrt(n.loc || 1) * 0.3),
            fill: tierColor(n),
            stroke: css("--sheet"),
            "stroke-width": 2,
          },
          group,
        );
        svgEl(
          "title",
          {},
          group,
          `${n.id} · ${n.loc} lines · ${n.fan_in} dependents`,
        );
        if (topIds.has(n.id))
          svgEl(
            "text",
            {
              x: n.x,
              y: n.y + 32,
              "text-anchor": "middle",
              "font-size": 12,
              fill: css("--ink"),
              stroke: css("--sheet"),
              "stroke-width": 3,
              "paint-order": "stroke",
            },
            group,
            base(n.id),
          );
        group.addEventListener("click", () => selectNode(n.id));
        group.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            selectNode(n.id);
          }
        });
        nodeElements.set(n.id, group);
      });
    });
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const links = D.graph.edges
      .filter((e) => byId.has(e.s) && byId.has(e.t))
      .map((e) => ({
        e,
        el: svgEl(
          "line",
          {
            x1: byId.get(e.s).x,
            y1: byId.get(e.s).y,
            x2: byId.get(e.t).x,
            y2: byId.get(e.t).y,
            "stroke-width": 1.5,
          },
          edgesLayer,
        ),
      }));
    const files = h("div", {
      class: "graph-file-list",
      "aria-label": "Files in dependency map",
    });
    box.after(files);
    nodes
      .slice()
      .sort((a, b) => a.id.localeCompare(b.id))
      .forEach((n) => {
        const b = h("button", {
          type: "button",
          "data-map-file": n.id,
          "aria-pressed": "false",
        });
        b.textContent = n.id;
        b.addEventListener("click", () => {
          selectNode(n.id);
          MRI.focus(n.id);
        });
        files.appendChild(b);
      });
    const activeKind = (e) => KINDS.find((k) => MRI.on[k.k] && k.k in e.kinds);
    MRI.highlight = (id) => {
      const neighbors = new Set([id]);
      links.forEach(({ e }) => {
        if (!activeKind(e)) return;
        if (e.s === id) neighbors.add(e.t);
        if (e.t === id) neighbors.add(e.s);
      });
      nodeElements.forEach((el, key) => {
        el.setAttribute("opacity", !id || neighbors.has(key) ? 1 : 0.18);
        el.setAttribute("aria-pressed", String(id === key));
      });
      links.forEach(({ e, el }) => {
        const kind = activeKind(e);
        el.setAttribute("display", kind ? "inline" : "none");
        if (kind) {
          el.setAttribute("stroke", css(kind.color));
          el.setAttribute("stroke-dasharray", kind.dash || "");
          el.setAttribute(
            "opacity",
            !id ? 0.35 : e.s === id || e.t === id ? 0.85 : 0.04,
          );
        }
      });
      files
        .querySelectorAll("button")
        .forEach((b) =>
          b.setAttribute("aria-pressed", String(b.dataset.mapFile === id)),
        );
    };
    MRI.focus = (id) => {
      const n = byId.get(id);
      if (!n) return;
      view.w = Math.min(width, cell * 1.8);
      view.h = (view.w * height) / width;
      view.x = n.x - view.w / 2;
      view.y = n.y - view.h / 2;
      updateView();
    };
    tools.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-k]");
      if (!b) return;
      MRI.on[b.dataset.k] = !MRI.on[b.dataset.k];
      b.setAttribute("aria-pressed", String(MRI.on[b.dataset.k]));
      MRI.highlight(MRI.selected);
    });
    function filterFiles(q) {
      files
        .querySelectorAll("button")
        .forEach(
          (b) => (b.hidden = !b.dataset.mapFile.toLowerCase().includes(q)),
        );
    }
    $("#mriSearch").addEventListener("input", (e) => {
      const q = e.target.value.trim().toLowerCase();
      filterFiles(q);
      const hit = q ? nodes.find((n) => n.id.toLowerCase().includes(q)) : null;
      selectNode(hit ? hit.id : null);
      if (q && !hit) $("#detail").textContent = "No files match this search.";
    });
    MRI.highlight(null);
  })();

  /* ------------------------------------------------------------------ blast radius (a port of blast.py) */
  const Blast = createBlast(D);

  const RINGCOL = {
    seed: "--ring0",
    breaks: "--ring1",
    ripples: "--ring2",
    "changes-with": "--ring3",
    "shares-strings": "--ring3",
  };
  function ringMap(b) {
    const S = 560,
      C = S / 2;
    const rings = { seed: 58, breaks: 150, ripples: 210, other: 256 };
    const place = (items, r, offset) =>
      items.map((it, i) => {
        const a =
          offset + (i / Math.max(1, items.length)) * Math.PI * 2 - Math.PI / 2;
        return Object.assign({}, it, {
          x: C + Math.cos(a) * r,
          y: C + Math.sin(a) * r,
          a,
        });
      });
    const seeds =
      b.seeds.length === 1
        ? [
            {
              path: b.seeds[0].path,
              kind: "seed",
              tier: b.seeds[0].tier,
              x: C,
              y: C,
              a: 0,
            },
          ]
        : place(
            b.seeds.map((s) => ({ path: s.path, kind: "seed", tier: s.tier })),
            rings.seed,
            0,
          );
    const br = place(
      b.impacted.filter((i) => i.kind === "breaks"),
      rings.breaks,
      0.15,
    );
    const rp = place(
      b.impacted.filter((i) => i.kind === "ripples"),
      rings.ripples,
      0.4,
    );
    const ot = place(
      b.impacted.filter(
        (i) => i.kind === "changes-with" || i.kind === "shares-strings",
      ),
      rings.other,
      0.7,
    );
    const all = seeds.concat(br, rp, ot);
    const pos = new Map(all.map((n) => [n.path, n]));
    let s = `<svg viewBox="-146 -8 ${S + 292} ${S + 16}" role="img" aria-label="Blast radius rings">`;
    [
      ["breaks", rings.breaks, "depends directly"],
      ["ripples", rings.ripples, "through another file"],
      ["other", rings.other, "history · shared strings"],
    ].forEach(([k, r, label]) => {
      s += `<circle cx="${C}" cy="${C}" r="${r}" fill="none" stroke="${css("--rule")}" stroke-dasharray="3 4"/>`;
      s += `<text x="${C}" y="${C - r - 6}" text-anchor="middle" font-size="11" fill="${css("--ink-3")}">${label}</text>`;
    });
    b.impacted.forEach((i) => {
      const to = pos.get(i.path);
      if (!to) return;
      const fromPath = i.why.length ? i.why[i.why.length - 1].from : null;
      const from = pos.get(fromPath) || seeds[0];
      s += `<line x1="${from.x}" y1="${from.y}" x2="${to.x}" y2="${to.y}" stroke="${css("--ink-3")}" stroke-opacity=".55" stroke-width="1.2"/>`;
    });
    // labels: keep each side's labels from landing on top of one another
    const sides = { L: [], R: [] };
    all.forEach((n) => {
      const right =
        n.kind === "seed" && b.seeds.length === 1 ? true : Math.cos(n.a) >= 0;
      n.right = right;
      n.r = n.kind === "seed" ? 12 : 9;
      sides[right ? "R" : "L"].push(n);
    });
    ["L", "R"].forEach((k) => {
      const list = sides[k].sort((p, q) => p.y - q.y);
      let lastY = -1e9;
      list.forEach((n) => {
        let y = n.y + 4;
        if (y - lastY < 16) y = lastY + 16;
        n.ly = y;
        lastY = y;
      });
    });
    all.forEach((n) => {
      const col = css(RINGCOL[n.kind]);
      const dnt = n.tier === "do-not-touch" || n.tier === "generated";
      const lx = n.x + (n.right ? n.r + 8 : -(n.r + 8));
      s += `<g><title>${esc(n.path)}${n.tier ? " · " + esc(n.tier) : ""}</title>`;
      if (Math.abs(n.ly - (n.y + 4)) > 7)
        s += `<line x1="${n.x + (n.right ? n.r + 2 : -(n.r + 2))}" y1="${n.y}" x2="${lx}" y2="${n.ly - 4}" stroke="${css("--rule")}" stroke-width="1"/>`;
      if (dnt)
        s += `<circle cx="${n.x}" cy="${n.y}" r="${n.r + 4.5}" fill="none" stroke="${css("--crit")}" stroke-width="2" ${n.tier === "generated" ? 'stroke-dasharray="3 2"' : ""}/>`;
      s += `<circle cx="${n.x}" cy="${n.y}" r="${n.r}" fill="${col}" stroke="${css("--sheet")}" stroke-width="2"/>`;
      s += `<text x="${lx}" y="${n.ly}" text-anchor="${n.right ? "start" : "end"}" font-size="${n.kind === "seed" ? 14 : 12.5}" font-weight="${n.kind === "seed" ? 700 : 500}" fill="${css("--ink")}" paint-order="stroke" stroke="${css("--sheet")}" stroke-width="3.5">${esc(base(n.path).replace(/\.(gd|py|tscn|ts|js|cs|gdshader)$/, ""))}</text></g>`;
    });
    return s + "</svg>";
  }

  function renderBlast(b, origin) {
    const out = $("#blastOut");
    const conf = b.constraints.filter((c) => c.conflict);
    const kindLbl = {
      breaks: "breaks",
      ripples: "ripples",
      "changes-with": "history",
      "shares-strings": "strings",
    };
    out.innerHTML = `
    <p class="verdict">${esc(b.summary)}</p>
    ${
      conf.length
        ? `<div class="stop"><div class="stamp">Stop · contradicts the project's rules</div>${conf
            .slice(0, 4)
            .map(
              (c) =>
                `<blockquote>${codeify(c.text)}<cite>${esc(c.line ? c.doc + ":" + c.line : c.doc)}</cite></blockquote>`,
            )
            .join("")}</div>`
        : ""
    }
    ${
      b.seeds.length
        ? `<div class="blast-grid">
      <div class="ringbox">${ringMap(b)}
        <div class="legend" style="padding:0 6px 6px">${[
          ["seed", "where it lands"],
          ["breaks", "breaks"],
          ["ripples", "ripples"],
          ["changes-with", "history or strings"],
        ]
          .map(
            ([k, l]) =>
              `<span><i class="node-dot" style="background:${css(RINGCOL[k])}"></i>${l}</span>`,
          )
          .join(
            "",
          )}<span><i class="node-dot" style="background:transparent;box-shadow:0 0 0 2px ${css("--crit")}"></i>do not touch</span></div>
      </div>
      <div class="lists">
        <h3>Where the change goes</h3>
        <ul class="ilist">${b.seeds.map((s) => `<li><span class="p">${esc(s.path)}</span>${s.tier === "do-not-touch" || s.tier === "generated" ? `<span class="chip tier-${s.tier}">${TIERLBL[s.tier]}</span>` : "<span></span>"}<span class="w">matched ${esc((s.matched || []).slice(0, 5).join(", ") || "—")}</span></li>`).join("")}</ul>
        <h3>Files to verify</h3>
        <ul class="ilist">${
          b.impacted
            .map(
              (i) =>
                `<li><span class="p">${esc(i.path)}</span><span class="kindtag"><i style="background:${css(RINGCOL[i.kind])}"></i>${kindLbl[i.kind]}</span><span class="w">${esc(
                  i.kind === "breaks"
                    ? i.why[0].because.join("; ")
                    : i.why.length
                      ? "via " +
                        [i.why[0].from]
                          .concat(i.why.map((w) => w.to))
                          .map(base)
                          .join(" → ") +
                        ": " +
                        i.why[i.why.length - 1].because.join("; ")
                      : "",
                )}</span></li>`,
            )
            .join("") || "<li><span class='w'>No dependents found.</span></li>"
        }</ul>
      </div>
    </div>
    <div class="packs">
      ${b.invariants.length ? `<details class="box"><summary>Invariants to preserve (${b.invariants.length})</summary><div class="inner"><ul>${b.invariants.map((x) => `<li>${codeify(x.text)} <span class="mono muted">${esc(x.line ? x.file + ":" + x.line : x.file)}</span></li>`).join("")}</ul></div></details>` : ""}
      ${b.rules.length ? `<details class="box"><summary>Rules the docs already state (${b.rules.length})</summary><div class="inner">${b.rules.map((x) => `<blockquote>${codeify(x.text)}<cite>${esc(x.doc)}:${x.line}</cite></blockquote>`).join("")}</div></details>` : ""}
      ${b.stop.length ? `<details class="box"><summary>Stop and ask before touching (${b.stop.length})</summary><div class="inner"><ul>${b.stop.map((x) => `<li><code>${esc(x.path)}</code> — ${codeify(x.reasons.join("; "))}</li>`).join("")}</ul></div></details>` : ""}
    </div>
    <div class="packbox">
      <div class="packhead"><div><b>Agent Handoff Pack</b> <span class="muted">${origin === "cli" ? "precomputed by the CLI" : "computed in your browser from the scan"}</span></div>
        <div class="pack-actions"><button class="btn ghost" type="button" id="downloadPack">Download brief</button><button class="btn" type="button" id="copyPack">Copy for Claude or Codex</button></div></div>
      <pre class="pack" id="packText" tabindex="0">${esc(b.pack)}</pre>
    </div>`
        : ""
    }`;
    const download = $("#downloadPack");
    if (download)
      download.addEventListener("click", () =>
        downloadText("gcaa-handoff.md", b.pack, "text/markdown"),
      );
    const copy = $("#copyPack");
    if (copy)
      copy.addEventListener("click", () => {
        const text = b.pack;
        const done = (msg) => {
          copy.textContent = msg;
          setTimeout(
            () => (copy.textContent = "Copy for Claude or Codex"),
            2200,
          );
        };
        const select = () => {
          const r = document.createRange();
          r.selectNodeContents($("#packText"));
          const s = getSelection();
          s.removeAllRanges();
          s.addRange(r);
          done("Selected: press Ctrl/Cmd+C");
        };
        if (navigator.clipboard && navigator.clipboard.writeText)
          navigator.clipboard
            .writeText(text)
            .then(() => done("Copied"), select);
        else select();
      });
  }
  (function blastUI() {
    const pre = D.blasts || [];
    const presets = $("#presets");
    const examples = pre.map((b) => b.request);
    (D.repo.godot
      ? ["refactor the save system", "rename a screen"]
      : ["refactor the report renderer", "change dependency resolution"]
    ).forEach((x) => {
      if (!examples.includes(x)) examples.push(x);
    });
    examples.forEach((x) => {
      const b = h("button", { type: "button" });
      b.textContent = x;
      b.addEventListener("click", () => go(x));
      presets.appendChild(b);
    });
    function go(text) {
      text = text.trim();
      if (!text) return;
      $("#blastInput").value = text;
      const cached = pre.find(
        (b) => b.request.toLowerCase() === text.toLowerCase(),
      );
      if (cached) {
        renderBlast(cached, "cli");
        return;
      }
      try {
        renderBlast(Blast.run(text), "browser");
      } catch (err) {
        $("#blastOut").innerHTML =
          `<p class="note">The in-page analysis failed on this request (${esc(err.message)}). The CLI gives the same answer: <code>vibecheck blast . "${esc(text)}"</code></p>`;
      }
    }
    $("#blastForm").addEventListener("submit", (e) => {
      e.preventDefault();
      go($("#blastInput").value);
    });
    if (pre.length) go(pre[0].request);
    else
      $("#blastOut").innerHTML =
        `<p class="empty-state">Describe a concrete change above. Analysis runs locally against this saved snapshot.</p>`;
  })();

  /* ------------------------------------------------------------------ history */
  (function history() {
    const tl = D.timeline,
      body = $("#historyBody");
    if (!tl || !tl.length) {
      body.innerHTML = `<p class="note">History was not included. Run <code>python3 -m vibecheck scan . --timeline</code> to score every commit.</p>`;
      return;
    }
    const W = 1000,
      H = 300,
      m = { l: 40, r: 132, t: 26, b: 34 };
    const x = (i) =>
      m.l + (tl.length === 1 ? 0 : (i * (W - m.l - m.r)) / (tl.length - 1));
    const y = (v) => m.t + ((100 - v) * (H - m.t - m.b)) / 100;
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Architecture debt score at each commit">`;
    [
      [0, ""],
      [25, "hand over"],
      [45, "with a pack"],
      [65, "supervised"],
      [100, "stabilize first"],
    ].forEach(([v, lbl]) => {
      s += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" stroke="${css(v === 0 ? "--rule" : "--grid")}" />`;
      s += `<text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${v}</text>`;
      if (lbl && v < 100)
        s += `<text x="${W - m.r + 8}" y="${y(v) - 5}" class="lbl">${lbl} ↑</text>`;
    });
    let lastDay = "";
    tl.forEach((c, i) => {
      const d = fmtDate(c.ts);
      if (d !== lastDay) {
        s += `<line x1="${x(i)}" x2="${x(i)}" y1="${H - m.b}" y2="${H - m.b + 5}" stroke="${css("--rule")}"/><text x="${x(i)}" y="${H - m.b + 18}" text-anchor="middle">${d.slice(5)}</text>`;
        lastDay = d;
      }
    });
    const pts = tl.map((c, i) => [x(i), y(c.score)]);
    s += `<path d="M${pts[0][0]},${y(0)} ${pts.map((p) => "L" + p[0] + "," + p[1]).join(" ")} L${pts[pts.length - 1][0]},${y(0)} Z" fill="${css("--area")}"/>`;
    s += `<path d="${pts.map((p, i) => (i ? "L" : "M") + p[0] + "," + p[1]).join(" ")}" fill="none" stroke="${css("--s1")}" stroke-width="2"/>`;
    tl.forEach((c, i) => {
      const hi = (c.findings.high || 0) > 0;
      if (hi)
        s += `<circle cx="${x(i)}" cy="${y(c.score)}" r="7.5" fill="none" stroke="${css("--crit")}" stroke-width="2"/>`;
      s += `<circle cx="${x(i)}" cy="${y(c.score)}" r="${i === tl.length - 1 ? 5 : 3}" fill="${css("--s1")}" stroke="${css("--sheet")}" stroke-width="1.5"/>`;
    });
    const last = tl[tl.length - 1];
    s += `<text x="${x(tl.length - 1) + 10}" y="${y(last.score) + 4}" class="ink" font-weight="700">${last.score.toFixed(0)}</text>`;
    const minI = tl.reduce((a, c, i) => (c.score < tl[a].score ? i : a), 0);
    if (minI !== tl.length - 1 && minI < tl.length - 3)
      s += `<text x="${x(minI)}" y="${y(tl[minI].score) + 24}" text-anchor="middle" class="lbl">lowest ${tl[minI].score.toFixed(0)}</text>`;
    tl.forEach((c, i) => {
      s += `<rect x="${x(i) - (W - m.l - m.r) / tl.length / 2}" y="${m.t}" width="${(W - m.l - m.r) / tl.length}" height="${H - m.t - m.b}" fill="transparent" data-i="${i}"/>`;
    });
    s += `<line id="xhair" x1="0" x2="0" y1="${m.t}" y2="${H - m.b}" stroke="${css("--ink-3")}" stroke-dasharray="3 3" visibility="hidden"/></svg>`;
    const events = caughtEvents();
    body.innerHTML = `<div class="chart" id="tlChart">${s}<div class="tip" id="tlTip" hidden></div></div>
    <div class="legend"><span><svg width="22" height="10" aria-hidden="true"><line x1="0" y1="5" x2="22" y2="5" stroke="${css("--s1")}" stroke-width="2"/></svg>Architecture debt score</span>
      <span><svg width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="5" fill="none" stroke="${css("--crit")}" stroke-width="2"/></svg>commit with a high finding</span></div>
    ${
      events.length
        ? `<h3 style="margin-top:22px">High findings through history</h3><div class="scroll-x"><table class="plain"><thead><tr><th>Finding</th><th>First seen</th><th>Fixed in</th><th>Evidence</th></tr></thead><tbody>
      ${events
        .map(
          (
            e,
          ) => `<tr><td>${e.items.length > 1 ? `<b>${e.items.length}×</b> ` : ""}${codeify(e.items[0].title)}${e.items.length > 1 ? ` <span class="muted">and ${e.items.length - 1} more of the same kind</span>` : ""}</td>
        <td class="mono">${esc(e.from.sha)}<div class="muted">${esc(e.from.subject.slice(0, 60))}</div></td>
        <td class="mono">${e.fixed ? esc(e.fixed.sha) + `<div class="muted">${esc(e.fixed.subject.slice(0, 60))}</div>` : "still present"}</td>
        <td class="mono">${(e.items[0].evidence || [])
          .slice(0, 1)
          .map((v) => esc(v.file) + ":" + v.line)
          .join("")}</td></tr>`,
        )
        .join("")}</tbody></table></div>`
        : ""
    }
    <details class="box" style="margin-top:16px"><summary>Every commit (${tl.length})</summary><div class="inner scroll-x"><table class="plain"><thead><tr><th>Commit</th><th>Subject</th><th>Written with</th><th class="n">Score</th><th class="n">Δ</th><th class="n">Lines</th></tr></thead><tbody>
      ${tl
        .map((c, i) => {
          const dlt = i ? c.score - tl[i - 1].score : 0;
          return `<tr><td class="mono">${esc(c.sha)}<div class="muted">${fmtDate(c.ts)}</div></td><td>${esc(c.subject)}</td><td>${esc((c.ai || "").replace(/\s*<.*$/, ""))}</td><td class="n">${c.score.toFixed(1)}</td><td class="n">${i ? (dlt > 0 ? "+" : "") + dlt.toFixed(1) : ""}</td><td class="n">${c.loc.toLocaleString()}</td></tr>`;
        })
        .join("")}
    </tbody></table></div></details>`;
    const chart = $("#tlChart"),
      tip = $("#tlTip"),
      xh = chart.querySelector("#xhair");
    chart.querySelectorAll("rect[data-i]").forEach((r) => {
      r.addEventListener("mouseenter", () => {
        const i = +r.dataset.i,
          c = tl[i];
        xh.setAttribute("x1", x(i));
        xh.setAttribute("x2", x(i));
        xh.setAttribute("visibility", "visible");
        tip.innerHTML = `<b>${esc(c.sha)}</b> · ${fmtDate(c.ts)} · score ${c.score.toFixed(1)}<br>${esc(c.subject)}${c.high.length ? `<br><span style="opacity:.8">${c.high.length} high: ${esc(c.high[0].title)}</span>` : ""}`;
        tip.hidden = false;
        const box = chart.getBoundingClientRect(),
          px = (x(i) / W) * box.width;
        tip.style.left = Math.max(4, Math.min(box.width - 290, px + 10)) + "px";
        tip.style.top = "8px";
      });
      r.addEventListener("mouseleave", () => {
        tip.hidden = true;
        xh.setAttribute("visibility", "hidden");
      });
    });
  })();

  /* ------------------------------------------------------------------ backtest */
  (function backtest() {
    const bt = D.backtest,
      body = $("#backtestBody");
    if (!bt || !bt.samples) {
      body.innerHTML = `<p class="note">Backtest was not included. Run <code>python3 -m vibecheck scan . --backtest</code> to measure the blast radius against this repository's history.</p>`;
      return;
    }
    const vs = bt.summary.seeded.vibecheck,
      rnd = bt.summary.seeded.random,
      rq = bt.summary.request.vibecheck;
    const chart = (mode, title) => {
      const rows = Object.entries(bt.summary[mode]).sort(
        (a, b) => b[1]["recall@5"] - a[1]["recall@5"],
      );
      const W = 520,
        rowH = 34,
        m = { l: 124, r: 64, t: 26, b: 26 },
        H = m.t + m.b + rows.length * rowH;
      const x = (v) => m.l + v * (W - m.l - m.r);
      let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)}">`;
      [0, 0.25, 0.5, 0.75, 1].forEach((v) => {
        s += `<line x1="${x(v)}" x2="${x(v)}" y1="${m.t - 6}" y2="${H - m.b}" stroke="${css(v === 0 ? "--rule" : "--grid")}"/><text x="${x(v)}" y="${H - m.b + 16}" text-anchor="middle">${v === 0 ? "0" : v === 1 ? "all" : v * 100 + "%"}</text>`;
      });
      rows.forEach(([name, v], i) => {
        const yy = m.t + i * rowH + rowH / 2,
          main = name === "vibecheck";
        const ink = css(main ? "--ink" : "--ink-3");
        s += `<text x="${m.l - 10}" y="${yy + 4}" text-anchor="end" ${main ? 'class="ink" font-weight="700"' : 'class="lbl"'}>${esc(name === "vibecheck" ? "VibeCheck" : name)}</text>`;
        s += `<line x1="${x(v["recall@5"])}" x2="${x(v["recall@10"])}" y1="${yy}" y2="${yy}" stroke="${ink}" stroke-width="${main ? 2.5 : 1.5}"/>`;
        s += `<circle cx="${x(v["recall@5"])}" cy="${yy}" r="${main ? 6 : 5}" fill="${ink}" stroke="${css("--sheet")}" stroke-width="2"><title>${esc(name)} recall@5 ${v["recall@5"].toFixed(2)}</title></circle>`;
        s += `<circle cx="${x(v["recall@10"])}" cy="${yy}" r="${main ? 5.5 : 4.5}" fill="${css("--sheet")}" stroke="${ink}" stroke-width="2"><title>${esc(name)} recall@10 ${v["recall@10"].toFixed(2)}</title></circle>`;
        s += `<text x="${W - m.r + 8}" y="${yy + 4}" ${main ? 'class="ink" font-weight="700"' : 'class="lbl"'}>${v["recall@5"].toFixed(2)} · ${v["recall@10"].toFixed(2)}</text>`;
      });
      s += `<text x="${m.l}" y="14" class="lbl" font-weight="600">${esc(title)}</text></svg>`;
      return `<div class="chart">${s}</div>`;
    };
    body.innerHTML = `
    <div class="kpis">
      <div><b>${Math.round(vs["recall@5"] * 100)}%</b><span>of the files a commit also changed are in the top 5, given its main file (random: ${Math.round(rnd["recall@5"] * 100)}%)</span></div>
      <div><b>${Math.round(rq["hit@5"] * 100)}%</b><span>of commits have a changed file in the top 5 from the subject line alone</span></div>
      <div><b>${bt.samples}</b><span>commits replayed of ${bt.commits_total}; ${bt.seconds}s</span></div>
    </div>
    <div class="bt">${chart("seeded", "Given the file the commit changed most")}${chart("request", "Given only the commit's subject line")}</div>
    <div class="legend"><span><svg width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="5" fill="${css("--ink")}"/></svg>share of changed files found in the top 5</span><span><svg width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="4" fill="none" stroke="${css("--ink")}" stroke-width="2"/></svg>in the top 10</span></div>
    <p class="note" style="margin-top:12px">${esc(bt.method_note)} The graph's weights were set before this backtest was run and were not tuned on it. Results cover ${bt.samples} eligible commits from this repository. Differences between methods are descriptive; this report does not calculate statistical significance or establish generalization to other repositories.</p>
    <details class="box" style="margin-top:12px"><summary>Every replayed commit</summary><div class="inner scroll-x"><table class="plain"><thead><tr><th>Commit</th><th>Main file</th><th class="n">Also changed</th><th class="n">VibeCheck top 10</th><th class="n">Static only</th><th class="n">Co-change only</th></tr></thead><tbody>
      ${bt.details
        .map(
          (
            d,
          ) => `<tr><td class="mono">${esc(d.sha)}<div class="muted">${esc(d.subject.slice(0, 64))}</div></td><td class="mono">${esc(base(d.seed))}</td><td class="n">${d.truth.length}</td>
        <td class="n">${Math.round(d.seeded.vibecheck["recall@10"] * 100)}%</td><td class="n">${Math.round(d.seeded["static only"]["recall@10"] * 100)}%</td><td class="n">${Math.round(d.seeded["co-change only"]["recall@10"] * 100)}%</td></tr>`,
        )
        .join("")}
    </tbody></table></div></details>`;
  })();

  /* ------------------------------------------------------------------ method */
  (function method() {
    const sc = D.score;
    $("#methodBody").innerHTML = `
    <div>
      <h3>Scan coverage</h3>
      <p>${D.stats.code_files} code files across ${
        Object.keys(D.stats.languages || {})
          .map(esc)
          .join(", ") || "no supported languages"
      }. ${D.stats.tests.length} test files identified by naming conventions; references to tests are not measured test execution coverage.</p>
      ${(D.stats.parse_errors || []).length ? `<div class="stop"><b>Incomplete parsing: ${D.stats.parse_errors.length} files</b><ul>${D.stats.parse_errors.map((e) => `<li><code>${esc(e.file)}</code>: ${esc(e.error)}</li>`).join("")}</ul></div>` : "<p>No parse failures recorded by the available adapters. Tokenizer-only languages can still miss unsupported syntax.</p>"}
      ${
        D.stats.skipped
          ? `<p>Excluded files: ${
              Object.entries(D.stats.skipped)
                .map(([reason, count]) => `${count} ${esc(reason)}`)
                .join(", ") || "none"
            }.</p>`
          : ""
      }
      <h3>What is read</h3>
      <p>Every text file at the scanned commit, straight from git, with nothing checked out. GDScript and Godot scenes and settings are parsed; Python uses its own parser; other languages get a tokenizer. The commit log supplies which files change together. No code leaves the machine, and no model is called unless you ask for a review.</p>
      <h3>What is checked</h3>
      <ul>
        <li><b>String contracts.</b> Keys routed through dictionaries and <code>match</code> statements, followed across files; payload keys a handler reads against what each sender sends; signals, meta keys, groups and input actions; members of globals that no longer exist.</li>
        <li><b>Documented rules.</b> Boundaries written in agent instructions (“no <code>UI</code>, no <code>Game</code>”) checked against the dependency graph, transitively.</li>
        <li><b>Build and export.</b> Compile-time dependencies on files the export leaves out; resource paths that do not exist.</li>
        <li><b>Prompt archaeology.</b> Stale names in the docs agents read first, prompts marked dead that are still runnable, elision markers and chat voice left in code.</li>
        <li><b>Structure.</b> Duplicated logic on normalized tokens, large files and long functions, cycles, hubs, and co-change with no reference.</li>
      </ul>
    </div>
    <div>
      <h3>How the score is built</h3>
      <p>Six categories, weighted: ${Object.entries(sc.weights)
        .map(
          ([k, w]) => `${esc(sc.categories[k].label)} ${Math.round(w * 100)}%`,
        )
        .join(
          ", ",
        )}. Each category is the mean of its components; each component maps a measured value onto 0 to 100 between two hand-set thresholds, shown in the result panel. Agent instructions and an architecture test each earn a 10-point credit on agent-edit risk. These thresholds are judgment, not yet calibrated against a corpus of repositories.</p>
      <h3>What it cannot see</h3>
      <ul>
        <li>Behaviour. A dependency that exists only at runtime through an untyped variable is invisible; calls through such variables are linked only when the name is unambiguous.</li>
        <li>Intent. A <code>has()</code>-guarded read of a key nothing writes is reported as dead, even when data arrives from outside the repository.</li>
        <li>Design. Whether a hub deserves to be a hub. That is what the optional review by Claude is for, written from the context pack.</li>
      </ul>
      <h3>Run it</h3>
      <pre class="cli">python3 -m vibecheck scan . --ask "add multiplayer" --timeline --backtest
python3 -m vibecheck blast . "replace the inventory system"
python3 -m vibecheck check . --fail-on high     # CI gate
python3 -m vibecheck scan . --review            # needs: pip install anthropic</pre>
    </div>`;
    $("#foot").innerHTML =
      `GCAA ${esc(D.tool.version)} · scoring ${esc(sc.version)} · ${esc(D.repo.name)} @ ${esc(D.repo.short)} · scanned in ${D.stats.timings.total || 0}s`;
  })();

  /* the scan and the blast-radius engine, for the console and for checking against the CLI */
  window.VibeCheck = { data: D, blast: Blast.run };

  /* ------------------------------------------------------------------ section index */
  (function toc() {
    const links = [...document.querySelectorAll(".toc a")];
    const secs = links.map((a) =>
      document.querySelector(a.getAttribute("href")),
    );
    const onScroll = () => {
      let cur = 0;
      secs.forEach((s, i) => {
        if (s && s.getBoundingClientRect().top < 120) cur = i;
      });
      links.forEach((a, i) => a.classList.toggle("on", i === cur));
    };
    document.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  })();
})();
