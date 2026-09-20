"use strict";
function createBlast(D) {
  const base = (p) => String(p).split("/").pop();
  const BC = D.blast_config,
    STOP = new Set(BC.stop),
    NEG = new Set(BC.neg_words),
    GENERIC = new Set(BC.generic);
  const CONSTRAINT = new RegExp(BC.constraint_re, "i");
  const SPLIT = /[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+/g;
  const roles = {};
  D.graph.nodes.forEach((n) => (roles[n.id] = n.role));
  const riskBy = {};
  D.risk.forEach((r) => (riskBy[r.path] = r));
  const edgeMap = new Map(D.graph.edges.map((e) => [e.s + "\u0000" + e.t, e]));
  function stem(w) {
    w = w.toLowerCase();
    if (w.length > 4 && w.endsWith("ies")) return w.slice(0, -3) + "y";
    if (w.length > 5 && /(sses|shes|ches|xes)$/.test(w)) return w.slice(0, -2);
    if (w.length > 3 && w.endsWith("s") && !/(ss|us|is)$/.test(w))
      return w.slice(0, -1);
    if (w.length > 5 && w.endsWith("ing")) return w.slice(0, -3);
    if (w.length > 4 && w.endsWith("ed")) return w.slice(0, -2);
    return w;
  }
  function words(text) {
    const out = [];
    for (const chunk of String(text).split(/[^A-Za-z0-9]+/)) {
      for (const part of chunk.match(SPLIT) || []) {
        const p = part.toLowerCase();
        if (p.length >= 2 && !/^\d+$/.test(p)) out.push(p);
      }
    }
    return out;
  }
  function parse(text) {
    const low = text.toLowerCase();
    let intent = "change";
    for (const [name, pat] of BC.intents) {
      if (new RegExp(pat).test(low)) {
        intent = name;
        break;
      }
    }
    const raw = words(text).filter((w) => !STOP.has(w));
    const terms = {},
      concepts = [];
    for (const w of raw) {
      const s = stem(w);
      terms[s] = Math.max(terms[s] || 0, 1.0);
      const syn = BC.concepts[w] || BC.concepts[s];
      if (syn) {
        concepts.push(w);
        for (const x of syn.split(" ")) {
          const sx = stem(x);
          if (!(sx in terms)) terms[sx] = 0.45;
        }
      }
    }
    return { text, intent, raw: raw.map(stem), terms, concepts };
  }
  function bm25(index, query) {
    const k1 = 1.2,
      b = 0.6,
      paths = Object.keys(index),
      n = paths.length || 1,
      df = {},
      lens = {};
    for (const p of paths) {
      let L = 0;
      for (const t in index[p]) {
        L += index[p][t];
        df[t] = (df[t] || 0) + 1;
      }
      lens[p] = L;
    }
    const avg = paths.length ? paths.reduce((a, p) => a + lens[p], 0) / n : 1;
    const out = {};
    for (const p of paths) {
      const tf = index[p];
      let s = 0;
      const hits = [];
      for (const t in query) {
        const f = tf[t];
        if (!f) continue;
        const idf = Math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5));
        s +=
          (query[t] * idf * f * (k1 + 1)) /
          (f + k1 * (1 - b + (b * lens[p]) / avg));
        hits.push(t);
      }
      if (s > 0) out[p] = [s, hits];
    }
    return out;
  }
  function conflicts(sentence, q) {
    if (q.intent !== "add" && q.intent !== "change") return false;
    const toks = (sentence.match(/[A-Za-z]+/g) || []).map((w) =>
      w.toLowerCase(),
    );
    const stems = toks.map(stem),
      negs = [],
      hits = [];
    toks.forEach((t, i) => {
      if (NEG.has(t)) negs.push(i);
    });
    toks.forEach((t, i) => {
      if (t === "false" && i > 0) negs.push(i);
    });
    const floor = q.novel ? 0.45 : 1.0;
    stems.forEach((s, i) => {
      if ((q.terms[s] || 0) >= floor && !GENERIC.has(s)) hits.push(i);
    });
    return negs.some((i) =>
      hits.some(
        (j) =>
          (j - i > 0 && j - i <= 6) ||
          (toks[i] === "false" && i - j > 0 && i - j <= 4),
      ),
    );
  }
  function constraints(q, seeds) {
    const seedNames = new Set();
    seeds.forEach((s) => {
      seedNames.add(base(s));
      const cn = (D.metrics[s] || {}).class_name;
      if (cn) seedNames.add(cn);
    });
    const found = [];
    for (const d of D.docs_index) {
      const ws = new Set(words(d.text).map(stem));
      let overlap = 0;
      ws.forEach((t) => {
        if (t in q.terms) overlap += q.terms[t];
      });
      const mentions = [...seedNames].filter((nm) => d.text.includes(nm));
      if (overlap <= 0 && !mentions.length) continue;
      if (!CONSTRAINT.test(d.text)) continue;
      found.push({
        doc: d.doc,
        line: d.line,
        text: d.text,
        score: (overlap + 0.8 * mentions.length) * (1 + d.weight),
        conflict: conflicts(d.text, q) && !mentions.length,
        weight: d.weight,
      });
    }
    found.sort((a, b) => b.conflict - a.conflict || b.score - a.score);
    const out = [],
      seen = new Set();
    for (const c of found) {
      const k = c.text.slice(0, 80);
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(c);
      if (out.length >= 8) break;
    }
    const net = [
      "network",
      "internet",
      "online",
      "server",
      "multiplayer",
      "socket",
      "http",
      "sync",
      "cloud",
      "analytics",
      "login",
      "account",
    ];
    if (net.some((t) => t in q.terms) && q.intent !== "remove") {
      const p = (D.godot.presets || []).find(
        (p) => String(p.internet).toLowerCase() === "false",
      );
      if (p)
        out.unshift({
          doc: "export_presets.cfg",
          line: 0,
          text: `Export preset "${p.name}" ships with permissions/internet=false.`,
          score: 99,
          conflict: true,
          weight: 3,
        });
    }
    return out;
  }
  function relevantMembers(path, q) {
    const rel = new Set();
    (D.symbols[path] || []).forEach(([name, st]) => {
      const ts = new Set(words(name).map(stem).concat(st));
      for (const t of ts) {
        if ((q.terms[t] || 0) >= 0.45) {
          rel.add(name);
          break;
        }
      }
    });
    return rel;
  }
  function impactNeighbors(seeds, q, hubs) {
    const nb = new Map();
    const passive = new Set(
      D.graph.nodes
        .filter((n) => n.role === "test" || n.role === "tool")
        .map((n) => n.id),
    );
    const relevant = {};
    hubs.forEach((s) => (relevant[s] = relevantMembers(s, q)));
    for (const e of D.graph.edges) {
      if (passive.has(e.s)) continue;
      let w = e.w;
      if (hubs.has(e.t) && e.skind && e.kinds.static) {
        const members = e.members || [];
        const share = members.length
          ? members.filter((m) => relevant[e.t].has(m)).length / members.length
          : 0;
        w =
          e.kinds.static * (0.2 + 0.8 * Math.min(1, 2 * share)) +
          (e.w - e.kinds.static);
      }
      if (!nb.has(e.t)) nb.set(e.t, []);
      nb.get(e.t).push([e.s, w, e]);
    }
    return nb;
  }
  function ppr(nb, seeds) {
    const alpha = 0.55,
      p0 = new Map(seeds.map((s) => [s, 1 / seeds.length]));
    let r = new Map(p0);
    for (let it = 0; it < 40; it++) {
      const nxt = new Map();
      for (const [u, mass] of r) {
        const out = nb.get(u) || [];
        let tot = 0;
        out.forEach((o) => (tot += o[1]));
        if (tot <= 0) continue;
        for (const [v, w] of out)
          nxt.set(v, (nxt.get(v) || 0) + (alpha * mass * w) / Math.max(tot, 1));
      }
      for (const s of seeds)
        nxt.set(s, (nxt.get(s) || 0) + (1 - alpha) * p0.get(s));
      r = nxt;
    }
    return r;
  }
  function paths(nb, seeds) {
    const dist = new Map(seeds.map((s) => [s, 0])),
      prev = new Map(),
      heap = seeds.map((s) => [0, s]);
    const less = (a, b) => a[0] < b[0] || (a[0] === b[0] && a[1] < b[1]);
    const push = (x) => {
      heap.push(x);
      let i = heap.length - 1;
      while (i > 0) {
        const p = (i - 1) >> 1;
        if (less(heap[i], heap[p])) {
          [heap[i], heap[p]] = [heap[p], heap[i]];
          i = p;
        } else break;
      }
    };
    const pop = () => {
      const top = heap[0],
        last = heap.pop();
      if (heap.length) {
        heap[0] = last;
        let i = 0;
        for (;;) {
          const l = 2 * i + 1,
            r = l + 1;
          let m = i;
          if (l < heap.length && less(heap[l], heap[m])) m = l;
          if (r < heap.length && less(heap[r], heap[m])) m = r;
          if (m === i) break;
          [heap[i], heap[m]] = [heap[m], heap[i]];
          i = m;
        }
      }
      return top;
    };
    heap.sort((a, b) => (less(a, b) ? -1 : 1));
    while (heap.length) {
      const [d, u] = pop();
      if (d > (dist.has(u) ? dist.get(u) : 1e9)) continue;
      for (const [v, w, e] of nb.get(u) || []) {
        const nd = d + 0.35 - Math.log(Math.min(1, Math.max(w, 1e-3)));
        if (nd < (dist.has(v) ? dist.get(v) : 1e9)) {
          dist.set(v, nd);
          prev.set(v, [u, e]);
          push([nd, v]);
        }
      }
    }
    const out = new Map();
    for (const v of prev.keys()) {
      const chain = [];
      let cur = v;
      while (prev.has(cur) && chain.length < 6) {
        const [u, e] = prev.get(cur);
        chain.push([u, cur, e]);
        cur = u;
      }
      out.set(v, chain.reverse());
    }
    return out;
  }
  function run(request) {
    const q = parse(request);
    const indexAll = D.index;
    const idx = {};
    for (const p in indexAll)
      if (["code", "generated", "resource"].includes(roles[p]))
        idx[p] = indexAll[p];
    const ranking = bm25(idx, q.terms);
    const ordered = Object.entries(ranking).sort((a, b) => b[1][0] - a[1][0]);
    const known = new Set();
    for (const p in indexAll) for (const t in indexAll[p]) known.add(t);
    const missing = q.raw.filter((t) => !known.has(t));
    const top = ordered.length ? ordered[0][1][0] : 0;
    const seeds = ordered
      .filter(([p, v]) => v[0] >= 0.4 * top)
      .slice(0, 5)
      .map(([p]) => p);
    const novel = q.raw.length > 0 && missing.length === q.raw.length;
    q.novel = novel;
    const cons = constraints(q, seeds);
    if (!seeds.length)
      return {
        request,
        intent: q.intent,
        missing_terms: missing,
        novel: true,
        seeds: [],
        impacted: [],
        context: [],
        tests: [],
        constraints: cons,
        invariants: [],
        rules: [],
        stop: [],
        summary: "Nothing in the code matches this request.",
        pack: "",
      };
    const hubs = new Set(
      seeds.filter((s) => ((D.metrics[s] || {}).fan_in || 0) >= 6),
    );
    const relevant = {};
    seeds.forEach((s) => (relevant[s] = relevantMembers(s, q)));
    const nb = impactNeighbors(seeds, q, hubs);
    const r = ppr(nb, seeds),
      chains = paths(nb, seeds);
    const seedSet = new Set(seeds);
    const cands = [...r.entries()]
      .filter(
        ([p]) =>
          !seedSet.has(p) &&
          ["code", "generated", "resource"].includes(roles[p]),
      )
      .sort((a, b) => b[1] - a[1]);
    const best = cands.length ? cands[0][1] : 0;
    const impacted = [];
    for (const [p, v] of cands.slice(0, 12)) {
      if (v < 0.12 * best) break;
      const chain = chains.get(p) || [];
      const kinds = new Set(
        chain.length ? Object.keys(chain[chain.length - 1][2].kinds) : [],
      );
      const direct = [];
      for (const s of seeds) {
        const ge = edgeMap.get(p + "\u0000" + s);
        if (!ge || !(ge.kinds.static || ge.kinds.contract)) continue;
        const used = ge.skind ? ge.members : [];
        if (
          hubs.has(s) &&
          !ge.kinds.contract &&
          !used.some((m) => relevant[s].has(m))
        )
          continue;
        if (
          ge.skind &&
          ["preload", "load", "ext_resource", "scene-script"].includes(
            ge.skind,
          ) &&
          !used.length &&
          !["remove", "rename", "replace"].includes(q.intent)
        )
          continue;
        direct.push([
          s,
          ge,
          hubs.has(s)
            ? used.filter((m) => relevant[s].has(m)).sort()
            : used.slice().sort(),
        ]);
      }
      if (roles[p] === "resource" && !direct.length) continue;
      let kind, why;
      if (direct.length) {
        kind = "breaks";
        const [s, ge, used] = direct.reduce((a, b) =>
          b[1].w > a[1].w ? b : a,
        );
        why = [
          {
            from: s,
            to: p,
            because: used.length
              ? [`uses ${used.slice(0, 5).join(", ")} of ${base(s)}`]
              : ge.why.slice(0, 2),
          },
        ];
      } else {
        kind =
          kinds.has("static") || kinds.has("contract")
            ? "ripples"
            : kinds.has("cochange")
              ? "changes-with"
              : "shares-strings";
        why = chain.map(([a, b, e]) => ({
          from: a,
          to: b,
          because: e.why.slice(0, 2),
        }));
      }
      const rr = riskBy[p] || {};
      impacted.push({
        path: p,
        score: v,
        kind,
        tier: rr.tier,
        risk: rr.score,
        why,
      });
    }
    const order = {
      breaks: 0,
      ripples: 1,
      "changes-with": 2,
      "shares-strings": 3,
    };
    impacted.sort((a, b) => order[a.kind] - order[b.kind] || b.score - a.score);
    const impactedSet = new Set(impacted.map((i) => i.path));
    const context = [],
      ctxSeen = new Set();
    D.graph.edges
      .filter(
        (e) =>
          seedSet.has(e.s) &&
          !seedSet.has(e.t) &&
          e.kinds.static &&
          ["code", "generated"].includes(roles[e.t]),
      )
      .sort((a, b) => b.w - a.w)
      .forEach((e) => {
        if (ctxSeen.has(e.t) || impactedSet.has(e.t)) return;
        ctxSeen.add(e.t);
        context.push({ path: e.t, because: e.why.slice(0, 2) });
      });
    const area = new Set([...seeds, ...impactedSet]);
    const testHits = new Map();
    D.graph.edges.forEach((e) => {
      if (
        roles[e.s] === "test" &&
        area.has(e.t) &&
        (e.kinds.static || e.kinds.contract)
      )
        testHits.set(
          e.s,
          (testHits.get(e.s) || 0) + (seedSet.has(e.t) ? 2 : 1),
        );
    });
    const tests = [...testHits.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([p, n]) => ({ path: p, hits: n }));
    let inv = [];
    seeds.concat(impacted.slice(0, 6).map((i) => i.path)).forEach((p) => {
      const m = D.metrics[p] || {};
      if (m.generated_by)
        inv.push({
          file: p,
          line: 1,
          text: `Generated by \`${m.generated_by}\`: change the generator and re-run it; never hand-edit.`,
        });
      if (m.persists)
        inv.push({
          file: p,
          line: 1,
          text: "Writes persistent data: existing saves must still load after the change.",
        });
    });
    seeds.forEach((p) => {
      let users = new Map();
      D.graph.edges.forEach((e) => {
        if (e.t === p && e.skind && ["code", "generated"].includes(roles[e.s]))
          (e.members || []).forEach((mn) => {
            if (!users.has(mn)) users.set(mn, new Set());
            users.get(mn).add(e.s);
          });
      });
      const m = D.metrics[p] || {};
      const name = m.class_name || m.autoload || base(p);
      let entries = [...users.entries()];
      const byUse = (a, b) =>
        b[1].size - a[1].size || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0);
      if (hubs.has(p)) {
        const rel = entries.filter(([mn]) => relevant[p].has(mn));
        entries = rel.length ? rel : entries.sort(byUse).slice(0, 2);
      }
      entries
        .sort(byUse)
        .slice(0, 6)
        .forEach(([mn, fs]) => {
          if (fs.size < 2) return;
          const sym = (D.symbols[p] || []).find((s) => s[0] === mn);
          inv.push({
            file: p,
            line: sym ? sym[2] : 1,
            text: `\`${name}.${mn}\` is used by ${fs.size} files (${[...fs].map(base).sort().slice(0, 4).join(", ")}): keep its signature and meaning.`,
          });
        });
    });
    seeds.forEach((p) =>
      (D.contracts.invariants[p] || []).slice(0, 5).forEach((x) => {
        if (["routing", "signal", "scene", "meta"].includes(x.kind))
          inv.push({ file: p, line: x.line, text: x.text });
      }),
    );
    if ((D.godot.export_excludes || []).length)
      inv.push({
        file: "export_presets.cfg",
        line: 0,
        text: `The export leaves out ${D.godot.export_excludes.slice(0, 4).join(", ")}: shipped code must not preload or name anything there.`,
      });
    const invSeen = new Set();
    inv = inv
      .filter((x) => (invSeen.has(x.text) ? false : invSeen.add(x.text)))
      .slice(0, 16);
    const names = new Set();
    area.forEach((p) => {
      names.add(base(p));
      const cn = (D.metrics[p] || {}).class_name;
      if (cn) names.add(cn);
    });
    const nameRe = (n) =>
      new RegExp(
        "\\b" + n.split(".")[0].replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\b",
      );
    let rules = [];
    D.table_rules.forEach((t) => {
      const hit = [...names].filter((n) => nameRe(n).test(t.text));
      if (hit.length)
        rules.push({
          doc: t.doc,
          line: t.line,
          text: t.text,
          files: hit.slice(0, 3),
          weight: t.weight,
        });
    });
    D.docs_index.forEach((d) => {
      if (d.weight < 2 || d.text.length >= 400) return;
      const hit = [...names].filter((n) => nameRe(n).test(d.text));
      if (hit.length && CONSTRAINT.test(d.text))
        rules.push({
          doc: d.doc,
          line: d.line,
          text: d.text,
          files: hit.slice(0, 3),
          weight: d.weight,
        });
    });
    const seedNames = new Set();
    seeds.forEach((p) => {
      seedNames.add(base(p).replace(/\.[^.]+$/, ""));
      const cn = (D.metrics[p] || {}).class_name;
      if (cn) seedNames.add(cn);
    });
    const ruleScore = (x) =>
      3 * [...seedNames].filter((n) => nameRe(n).test(x.text)).length +
      x.files.length +
      x.weight -
      0.004 * x.text.length;
    rules.sort((a, b) => ruleScore(b) - ruleScore(a));
    const ruleSeen = new Set();
    rules = rules
      .filter((x) => (ruleSeen.has(x.text) ? false : ruleSeen.add(x.text)))
      .slice(0, 8);
    const stop = [...area]
      .sort(
        (a, b) =>
          ((riskBy[b] || {}).score || 0) - ((riskBy[a] || {}).score || 0),
      )
      .filter((p) =>
        ["do-not-touch", "generated"].includes((riskBy[p] || {}).tier),
      )
      .slice(0, 6)
      .map((p) => ({ path: p, reasons: riskBy[p].reasons.slice(0, 3) }));
    const med = D.stats.median_commit_lines || 0;
    const budget = {
      lines:
        D.stats.commits >= 5
          ? Math.floor(Math.min(800, Math.max(200, 2 * med)))
          : 400,
      files:
        seeds.length + impacted.filter((i) => i.kind === "breaks").length + 2,
    };
    const res = {
      request,
      intent: q.intent,
      missing_terms: missing,
      novel,
      seeds: seeds.map((p) => ({
        path: p,
        matched: (ranking[p] || [0, []])[1].slice(0, 6),
        tier: (riskBy[p] || {}).tier,
      })),
      impacted,
      context: context.slice(0, 6),
      tests,
      constraints: cons,
      invariants: inv,
      rules,
      stop,
      budget,
    };
    res.summary = summary(res);
    res.pack = pack(res);
    return res;
  }
  function summary(b) {
    const parts = [],
      conflictsN = b.constraints.filter((c) => c.conflict).length;
    if (conflictsN)
      parts.push(
        `This request runs into ${conflictsN} documented constraint${conflictsN === 1 ? "" : "s"}; settle ${conflictsN === 1 ? "it" : "them"} before any code is written.`,
      );
    if (b.novel)
      parts.push(
        `Nothing in the code is called ${b.missing_terms
          .slice(0, 2)
          .map((m) => "“" + m + "”")
          .join(
            " or ",
          )} yet, so the radius is measured from the closest existing ideas.`,
      );
    const br = b.impacted.filter((i) => i.kind === "breaks").length,
      rp = b.impacted.filter((i) => i.kind === "ripples").length,
      other = b.impacted.length - br - rp;
    parts.push(
      `The change lands in ${b.seeds
        .slice(0, 3)
        .map((s) => base(s.path))
        .join(
          ", ",
        )} and reaches ${b.impacted.length} more file${b.impacted.length === 1 ? "" : "s"}: ${br} depend${br === 1 ? "s" : ""} on the seeds directly, ${rp} through another file, ${other} only by history or shared strings.`,
    );
    if (b.stop.length)
      parts.push(
        `${b.stop.length} of them ${b.stop.length === 1 ? "is" : "are"} on the do-not-touch list.`,
      );
    if (
      b.impacted.some((i) => (D.metrics[i.path] || {}).persists) ||
      b.seeds.some((s) => (D.metrics[s.path] || {}).persists)
    )
      parts.push("Saved data is inside the radius.");
    return parts.join(" ");
  }
  function pack(b) {
    const L = [];
    L.push(
      `# Task: ${b.request}`,
      "",
      `You are working in \`${D.repo.name}\` at commit \`${D.repo.short}\`. VibeCheck wrote this brief from a static and historical analysis of the repository. Every claim names a file; check a claim against the code before you rely on it.`,
      "",
    );
    const conf = b.constraints.filter((c) => c.conflict);
    if (conf.length) {
      L.push("## 0. Stop: the request contradicts the project's own rules", "");
      conf.slice(0, 4).forEach((c) => {
        L.push(
          `> ${c.text}  \n> — \`${c.line ? c.doc + ":" + c.line : c.doc}\``,
          "",
        );
      });
      L.push(
        "Do not write code until the person who asked confirms how this request squares with the lines above.",
        "",
      );
    }
    if (b.novel)
      L.push(
        `> Nothing in the codebase is named ${b.missing_terms
          .slice(0, 3)
          .map((m) => "“" + m + "”")
          .join(
            " or ",
          )} yet. The files below are where it would connect, found through related ideas.`,
        "",
      );
    L.push("## 1. Where the change goes", "");
    b.seeds.forEach((s) =>
      L.push(
        `- \`${s.path}\`${["do-not-touch", "generated"].includes(s.tier) ? " — **" + s.tier + "**" : ""}  (matched: ${s.matched.slice(0, 5).join(", ") || "given"})`,
      ),
    );
    L.push("", "## 2. What will probably break", "");
    const labels = {
      breaks: "depends on it directly",
      ripples: "reached through another file",
      "changes-with": "changes with it in history",
      "shares-strings": "shares string keys with it",
    };
    b.impacted.forEach((i) => {
      let why = "";
      if (i.kind === "breaks" && i.why.length)
        why = " — " + i.why[0].because.join("; ");
      else if (i.why.length)
        why = ` — via ${[i.why[0].from]
          .concat(i.why.map((w) => w.to))
          .map(base)
          .join(" → ")}: ${i.why[i.why.length - 1].because.join("; ")}`;
      L.push(
        `- \`${i.path}\` (${labels[i.kind]})${why}${i.tier === "do-not-touch" ? " **[do-not-touch]**" : ""}`,
      );
    });
    if (!b.impacted.length)
      L.push(
        "- Nothing beyond the files above: they have no dependents VibeCheck can see.",
      );
    L.push("");
    if (b.context.length) {
      L.push(
        "## 3. Read before editing (change only if the task requires it)",
        "",
      );
      b.context.forEach((c) =>
        L.push(`- \`${c.path}\` — ${c.because.join("; ")}`),
      );
      L.push("");
    }
    if (b.invariants.length) {
      L.push("## 4. Invariants to preserve", "");
      b.invariants.forEach((x) =>
        L.push(`- ${x.text} (\`${x.line ? x.file + ":" + x.line : x.file}\`)`),
      );
      L.push("");
    }
    const nonconf = b.constraints.filter((c) => !c.conflict);
    if (b.rules.length || nonconf.length) {
      L.push("## 5. Rules the docs already state", "");
      const seen = new Set();
      b.rules
        .concat(nonconf)
        .slice(0, 10)
        .forEach((x) => {
          if (seen.has(x.text)) return;
          seen.add(x.text);
          L.push(`> ${x.text}  \n> — \`${x.doc}:${x.line}\``, "");
        });
    }
    if (b.stop.length) {
      L.push("## 6. Do not modify without stopping to ask", "");
      b.stop.forEach((s) =>
        L.push(`- \`${s.path}\` — ${s.reasons.join("; ")}`),
      );
      L.push("");
    }
    L.push("## 7. Verify", "");
    (D.commands || [])
      .slice(0, 2)
      .forEach((c) =>
        L.push(
          `From \`${c.doc}\` (${c.heading}):`,
          "",
          "```bash",
          c.text,
          "```",
          "",
        ),
      );
    L.push(
      b.tests.length
        ? `Tests that exercise this area: ${b.tests.map((t) => "`" + t.path + "`").join(", ")}.`
        : "No test file references these files. Before changing behaviour, write a test that pins what the code does now.",
      "",
    );
    L.push("## 8. Stop or roll back when", "");
    const allowed = b.seeds
      .map((s) => s.path)
      .concat(b.impacted.filter((i) => i.kind === "breaks").map((i) => i.path));
    L.push(
      `- The change needs an edit outside these files, beyond updating a call site: ${allowed
        .slice(0, 10)
        .map((p) => "`" + base(p) + "`")
        .join(", ")}.`,
    );
    L.push(
      `- The diff grows past about ${b.budget.lines} changed lines or ${b.budget.files} files. That is the change getting away from the request.`,
    );
    if (b.stop.length)
      L.push("- A file from section 6 needs more than a call-site change.");
    if (b.invariants.some((x) => x.text.startsWith("Writes persistent data")))
      L.push(
        "- Saved data would change shape. Stop and ask; if approved, follow the project's migration rule.",
      );
    L.push(
      "- A string key, signal or payload field from section 4 is renamed in some of its sites but not all of them.",
      "- A check in section 7 fails and the only way to make it pass is to change what the test expects.",
      "",
    );
    L.push(
      "## 9. Report back with",
      "",
      "- The files you changed, and why each one needed to change.",
      "- Any invariant from section 4 you had to touch, and how its other sites were updated.",
      "- The commands you ran from section 7 and their results.",
      "- Anything listed in section 2 that you did not check.",
      "",
    );
    return L.join("\n");
  }
  return { run };
}

if (typeof module !== "undefined") module.exports = { createBlast };
