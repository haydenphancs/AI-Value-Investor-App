"""
schema_doc_template.py — the HTML/CSS/JS shell of the Database Atlas.

Kept apart from the data layer so the page can be restyled without touching the
parser. Placeholders are `{{NAME}}` and are substituted by str.replace, NOT by
f-strings or .format(), because the CSS and JS below are full of braces.
"""

CSS = r"""
:root{
  --bg:#ffffff; --bg2:#f7f9fc; --ink:#0f172a; --muted:#55637a; --faint:#8592a6;
  --panel:rgba(15,23,42,.025); --panel2:rgba(15,23,42,.05);
  --border:rgba(15,23,42,.10); --border2:rgba(15,23,42,.18);
  --line:#e6e9ef; --code:#f3f4f6;
  --shadow:0 10px 34px rgba(15,23,42,.08);
  --radius:16px;
  --font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Inter","Helvetica Neue",Arial,sans-serif;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  color-scheme: light;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  font-family:var(--font); color:var(--ink); background:var(--bg);
  background-image:
    radial-gradient(1100px 600px at 15% -8%, rgba(2,132,199,.06), transparent 60%),
    radial-gradient(1000px 620px at 100% 6%, rgba(124,58,237,.055), transparent 55%),
    radial-gradient(900px 700px at 50% 120%, rgba(217,119,6,.05), transparent 55%);
  -webkit-font-smoothing:antialiased; line-height:1.45; min-height:100vh;
}
a{color:inherit}
::selection{background:rgba(56,189,248,.35)}
code{font-family:var(--mono);background:var(--code);padding:1px 5px;border-radius:5px;font-size:.9em}

/* ---------- top bar ---------- */
header.top{
  position:sticky; top:0; z-index:30; backdrop-filter:blur(14px);
  background:linear-gradient(180deg, rgba(255,255,255,.95), rgba(255,255,255,.76));
  border-bottom:1px solid var(--border);
  padding:14px clamp(16px,3vw,34px);
  display:flex; align-items:center; gap:16px; flex-wrap:wrap;
}
.brand{display:flex; align-items:center; gap:13px; min-width:0}
.logo{
  width:42px;height:42px;border-radius:12px;flex:none;
  display:grid;place-items:center;font-weight:800;font-size:17px;color:#04121a;
  background:conic-gradient(from 210deg,#38bdf8,#a78bfa,#34d399,#fbbf24,#38bdf8);
  box-shadow:0 6px 22px rgba(56,189,248,.35);
}
.brand h1{font-size:18px;margin:0;letter-spacing:.2px;font-weight:700}
.brand p{margin:2px 0 0;font-size:12.5px;color:var(--muted)}
.top .spacer{flex:1}
.stats{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--muted)}
.stats b{color:var(--ink);font-weight:650;font-variant-numeric:tabular-nums}
.search{
  font-family:inherit;font-size:13px;color:var(--ink);
  border:1px solid var(--border2);border-radius:999px;padding:7px 13px;min-width:210px;
  background:#fff;outline:none;transition:.15s
}
.search:focus{border-color:#0284c7;box-shadow:0 0 0 3px rgba(2,132,199,.13)}

/* ---------- layout ---------- */
.wrap{display:grid;grid-template-columns:224px minmax(0,1fr) 402px;gap:0 26px;
  padding:22px clamp(16px,3vw,34px) 60px; align-items:start; max-width:1780px;margin:0 auto}
@media (max-width:1180px){
  .wrap{grid-template-columns:1fr}
  nav.side{position:static;border-right:none;border-bottom:1px solid var(--line);
    max-height:none;padding-bottom:16px;margin-bottom:14px}
  .detail-col{position:static !important;order:-1;margin-bottom:18px}
}

/* ---------- side nav ---------- */
nav.side{position:sticky;top:82px;align-self:start;padding:4px 10px 40px;
  max-height:calc(100vh - 96px);overflow:auto}
.navgroup{font-size:10.5px;letter-spacing:1.1px;text-transform:uppercase;color:var(--faint);
  font-weight:700;margin:16px 8px 6px}
.navitem{display:flex;align-items:center;gap:9px;width:100%;text-align:left;font-family:inherit;
  font-size:13px;color:var(--ink);background:transparent;border:1px solid transparent;
  border-radius:9px;padding:6px 9px;cursor:pointer;transition:.12s;margin:1px 0}
.navitem:hover{background:var(--panel);border-color:var(--border)}
.navitem.on{background:color-mix(in srgb,var(--c,#475569) 8%,#fff);
  border-color:color-mix(in srgb,var(--c,#475569) 38%,transparent);font-weight:600}
.navitem .dot{width:9px;height:9px;border-radius:50%;flex:none;background:var(--c,#94a3b8)}
.navitem .nl{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.navitem .ct{font-size:11px;color:var(--faint);font-variant-numeric:tabular-nums}
.seg{display:flex;gap:3px;background:var(--panel2);border-radius:9px;padding:3px;margin:2px 6px 0}
.seg button{flex:1;font-family:inherit;font-size:11.5px;border:0;background:transparent;
  border-radius:7px;padding:5px 4px;cursor:pointer;color:var(--muted);transition:.12s}
.seg button.on{background:#fff;color:var(--ink);font-weight:650;box-shadow:0 1px 3px rgba(15,23,42,.12)}

/* ---------- bands ---------- */
main.doc{min-width:0}
.band{position:relative;border:1px solid var(--border);border-radius:var(--radius);
  background:linear-gradient(180deg, var(--panel), rgba(255,255,255,.012));
  padding:16px 16px 18px;margin-bottom:14px;box-shadow:var(--shadow);
  border-left:3px solid var(--c)}
.band::before{content:"";position:absolute;inset:0;border-radius:var(--radius);pointer-events:none;
  background:radial-gradient(600px 120px at 0% 0%, color-mix(in srgb, var(--c) 13%, transparent), transparent 70%)}
.band-head{display:flex;align-items:baseline;gap:11px;flex-wrap:wrap;margin-bottom:4px;position:relative}
.band-badge{font-size:10.5px;font-weight:800;letter-spacing:1.4px;text-transform:uppercase;
  color:var(--c);padding:4px 10px;border:1px solid color-mix(in srgb,var(--c) 40%,transparent);
  border-radius:999px;background:color-mix(in srgb,var(--c) 10%,transparent)}
.band-head h2{font-size:16.5px;margin:0;font-weight:700;letter-spacing:.2px}
.band-head .n{font-size:12px;color:var(--faint);font-variant-numeric:tabular-nums}
.band-blurb{font-size:12.8px;color:var(--muted);margin:0 0 12px;max-width:96ch;position:relative}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(202px,1fr));gap:9px;position:relative}
.card{text-align:left;font-family:inherit;cursor:pointer;transition:.13s;
  border:1.3px solid color-mix(in srgb,var(--c) 34%,transparent);border-radius:11px;
  background:color-mix(in srgb,var(--c) 5%,#fff);padding:9px 11px;min-width:0}
.card:hover{box-shadow:0 6px 16px rgba(17,24,39,.10);transform:translateY(-1px);
  border-color:color-mix(in srgb,var(--c) 62%,transparent)}
.card.on{border-color:var(--c);box-shadow:0 0 0 2.5px color-mix(in srgb,var(--c) 22%,transparent)}
.card .cn{font-family:var(--mono);font-size:12.4px;font-weight:600;color:var(--ink);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card .cn .sch{color:var(--faint);font-weight:400}
.card .cm{font-size:10.8px;color:var(--muted);margin-top:3px;display:flex;flex-wrap:wrap;gap:3px 6px}
.b{font-size:9.5px;font-weight:700;letter-spacing:.3px;text-transform:uppercase;
  border-radius:4px;padding:1px 4px;background:var(--panel2);color:var(--muted)}
.b.pk{text-transform:none;font-family:var(--mono);letter-spacing:0;background:#e0f2fe;color:#075985}
.b.j{background:#fef3c7;color:#92400e}
.b.v{background:#cffafe;color:#155e75}
.b.r{background:#dcfce7;color:#166534}
.b.x{background:#fee2e2;color:#991b1b}
.b.s{background:#ede9fe;color:#5b21b6}

/* ---------- inspector ---------- */
.detail-col{position:sticky;top:82px;align-self:start}
.detail{border:1px solid var(--border);border-radius:var(--radius);background:#fff;
  box-shadow:var(--shadow);overflow:hidden;max-height:calc(100vh - 100px);display:flex;flex-direction:column}
.detail .bar{height:4px;background:var(--acc,#5a6b82);flex:none}
.detail .body{padding:16px 17px 22px;overflow:auto}
.detail .eyebrow{font-size:10.5px;letter-spacing:1.3px;text-transform:uppercase;font-weight:800;
  color:var(--acc,#5a6b82)}
.detail h2{font-family:var(--mono);font-size:16px;margin:6px 0 2px;font-weight:650;word-break:break-word}
.detail h2 .sch{color:var(--faint);font-weight:400}
.detail .tag{font-size:11.5px;color:var(--faint);margin-bottom:10px}
.detail p.purpose{font-size:13.2px;color:#334155;margin:0 0 8px}
.detail .src{font-size:10px;letter-spacing:.4px;text-transform:uppercase;color:var(--faint);
  border:1px solid var(--border);border-radius:4px;padding:1px 5px;margin-left:6px;white-space:nowrap}
.note{border-left:3px solid #d97706;background:#fffbeb;padding:9px 12px;border-radius:0 8px 8px 0;
  margin:10px 0;font-size:12.4px;color:#78350f}
.sec{margin-top:15px}
.sec h4{font-size:10.5px;letter-spacing:1.2px;text-transform:uppercase;color:var(--faint);
  font-weight:800;margin:0 0 7px;display:flex;align-items:center;gap:7px}
.sec h4::after{content:"";flex:1;height:1px;background:var(--line)}
table.cols{width:100%;border-collapse:collapse;font-size:11.9px}
table.cols td{padding:3.5px 6px 3.5px 0;vertical-align:top;border-bottom:1px solid var(--line)}
table.cols td.cn{font-family:var(--mono);font-weight:600;white-space:nowrap}
table.cols td.ct{font-family:var(--mono);color:var(--muted);font-size:11px;white-space:nowrap}
table.cols td.cf{text-align:right;white-space:nowrap;color:var(--faint);font-size:10px}
table.cols tr.dim td{opacity:.62}
.k{font-size:9px;font-weight:800;border-radius:3px;padding:0 3px;margin-left:3px;letter-spacing:.3px}
.k.pk{background:#e0f2fe;color:#075985}
.k.fk{background:#dcfce7;color:#166534}
.k.sf{background:#fef3c7;color:#92400e}
.k.en{background:#ede9fe;color:#5b21b6}
.colnote{font-size:10.8px;color:var(--muted);padding:0 0 5px 0;border-bottom:1px solid var(--line)}
.chips{display:flex;flex-wrap:wrap;gap:5px}
.chip{font-family:inherit;font-size:11.4px;border:1px solid var(--border);background:#fff;
  border-radius:999px;padding:3px 9px;cursor:pointer;display:flex;align-items:center;gap:6px;
  transition:.12s;max-width:100%}
.chip:hover{border-color:var(--border2);background:var(--panel)}
.chip .cdot{width:8px;height:8px;border-radius:50%;flex:none}
.chip .ct2{font-family:var(--mono);font-size:10.6px;color:var(--muted)}
.chip.dashed{border-style:dashed}
.rel{font-size:11.9px;margin:0 0 6px;padding-left:1px}
.rel .arrow{font-family:var(--mono);color:var(--faint)}
.rel .why{display:block;color:var(--muted);font-size:10.9px;margin-top:1px}
ul.tight{margin:0;padding-left:16px;font-size:12.1px}
ul.tight li{margin:3px 0}
.files{display:flex;flex-direction:column;gap:3px}
.files code{font-size:10.9px;background:var(--code);padding:2px 6px;border-radius:5px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block}
.muted{color:var(--muted);font-size:12px}
details.ddl{margin-top:14px}
details.ddl summary{font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--faint);
  font-weight:800;cursor:pointer;outline:none}
details.ddl pre{font-family:var(--mono);font-size:10.6px;background:var(--code);padding:10px;
  border-radius:8px;overflow:auto;max-height:340px;margin:8px 0 0;line-height:1.45}

/* ---------- extra sections ---------- */
.sheet{border:1px solid var(--border);border-radius:var(--radius);background:#fff;
  box-shadow:var(--shadow);padding:18px 18px 20px;margin-bottom:14px}
.sheet > h2{font-size:16.5px;margin:0 0 4px;font-weight:700}
.sheet > p.lede{font-size:12.8px;color:var(--muted);margin:0 0 14px;max-width:92ch}
.svgwrap{border:1px solid var(--line);border-radius:12px;background:#fff;padding:12px;
  overflow-x:auto}
.fnlist{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:9px}
.fn{border:1px solid var(--border);border-radius:10px;padding:9px 11px;background:var(--panel);min-width:0}
.fn .fnn{font-family:var(--mono);font-size:12.1px;font-weight:600;word-break:break-word}
.fn .fnm{font-size:10.9px;color:var(--muted);margin-top:3px}
.enumgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:9px}
.en{border:1px solid var(--border);border-radius:10px;padding:9px 11px;background:var(--panel)}
.en .enn{font-family:var(--mono);font-size:12px;font-weight:600}
.en .env{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.en .env span{font-family:var(--mono);font-size:10.6px;background:#fff;border:1px solid var(--border);
  border-radius:5px;padding:1px 5px}

footer.foot{max-width:1780px;margin:0 auto;padding:18px clamp(16px,3vw,34px) 40px;
  color:var(--faint);font-size:11.8px;border-top:1px solid var(--line);
  display:flex;gap:16px;flex-wrap:wrap;justify-content:space-between}
.hidden{display:none !important}
"""

BODY = r"""
<header class="top">
  <div class="brand">
    <div class="logo">DB</div>
    <div>
      <h1>Caydex · Database Atlas</h1>
      <p>{{SUBTITLE}}</p>
    </div>
  </div>
  <div class="spacer"></div>
  <div class="stats" id="stats"></div>
  <input class="search" id="q" type="search" placeholder="Search tables & columns…"
         autocomplete="off" spellcheck="false">
</header>

<div class="wrap">
  <nav class="side">
    <div class="navgroup">Group by</div>
    <div class="seg" id="seg">
      <button data-mode="domain" class="on">Domain</button>
      <button data-mode="kind">Kind</button>
      <button data-mode="schema">Schema</button>
    </div>
    <div id="nav"></div>
  </nav>

  <main class="doc" id="map"></main>

  <aside class="detail-col">
    <div class="detail" id="detail">
      <div class="bar" id="dbar"></div>
      <div class="body" id="dbody"></div>
    </div>
  </aside>
</div>

<footer class="foot">
  <span>Generated from <code>{{SOURCE}}</code> · PostgreSQL {{PGV}} / pg_dump {{DUMPV}} · {{DATE}}</span>
  <span>Regenerate with <code>backend/scripts/generate_schema_doc.py</code> — do not hand-edit</span>
</footer>
"""

JS = r"""
const $  = (s) => document.querySelector(s);
const el = (t, c) => { const e = document.createElement(t); if (c) e.className = c; return e; };
const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const T = SCHEMA.tables;
const DOMAIN = {};
SCHEMA.domains.forEach(d => DOMAIN[d.key] = d);
const COLOR_OF = (q) => (DOMAIN[T[q].domain] || {}).color || "#475569";
const qnames = Object.keys(T);

let mode = "domain";
let current = null;

/* ---------------------------------------------------------------- stats */
(function stats() {
  const c = SCHEMA.meta.counts;
  const rows = [
    [c.tables, "tables"], [c.public, "in public"], [c.columns, "columns"],
    [c.fk, "foreign keys"], [c.soft, "unenforced joins"], [c.indexes, "indexes"],
    [c.policies, "RLS policies"], [c.functions, "functions"], [c.enums, "enums"],
  ];
  $("#stats").innerHTML = rows.map(([n, l]) => `<span><b>${n}</b> ${l}</span>`).join("");
})();

/* ---------------------------------------------------------------- groups */
function groups() {
  if (mode === "domain") {
    return SCHEMA.domains
      .filter(d => d.tables.length)
      .map(d => ({ id: d.key, label: d.label, color: d.color, blurb: d.blurb, tables: d.tables }));
  }
  if (mode === "kind") {
    const by = {};
    qnames.forEach(q => (by[T[q].kind] = by[T[q].kind] || []).push(q));
    return Object.keys(SCHEMA.kinds).filter(k => by[k]).map(k => ({
      id: k, label: SCHEMA.kinds[k], color: COLOR_OF(by[k][0]),
      blurb: "", tables: by[k].sort((a, b) => T[a].name.localeCompare(T[b].name)),
    }));
  }
  const by = {};
  qnames.forEach(q => (by[T[q].schema] = by[T[q].schema] || []).push(q));
  return Object.keys(by).sort().map(s => ({
    id: s, label: s, color: s === "public" ? "#0284c7" : "#94a3b8",
    blurb: SCHEMA.meta.managedSchemas[s] || "",
    tables: by[s].sort((a, b) => T[a].name.localeCompare(T[b].name)),
  }));
}

/* ---------------------------------------------------------------- badges */
function badges(t) {
  const out = [`${t.cols.length} col`];
  if (t.pk.length) out.push(`<span class="b pk">pk ${esc(t.pk.join("+"))}</span>`);
  if (t.policies.length) out.push(`<span class="b r">${t.policies.length}p</span>`);
  else if (t.rls) out.push(`<span class="b x">rls, 0p</span>`);
  if (!t.rls && t.schema === "public") out.push(`<span class="b x">no rls</span>`);
  if (t.cols.some(c => c.t.indexOf("jsonb") >= 0)) out.push(`<span class="b j">jsonb</span>`);
  if (t.cols.some(c => c.t.indexOf("vector") >= 0)) out.push(`<span class="b v">vector</span>`);
  if (t.out.length) out.push(`<span class="b s">&rarr;${t.out.length}</span>`);
  if (t.in.length) out.push(`<span class="b s">&larr;${t.in.length}</span>`);
  return out.join(" ");
}

/* ---------------------------------------------------------------- render */
function render() {
  const map = $("#map"), nav = $("#nav");
  map.innerHTML = ""; nav.innerHTML = "";
  const gs = groups();

  const head = el("div", "navgroup"); head.textContent = mode; nav.appendChild(head);
  gs.forEach(g => {
    const b = el("button", "navitem");
    b.style.setProperty("--c", g.color);
    b.innerHTML = `<span class="dot"></span><span class="nl">${esc(g.label)}</span>` +
                  `<span class="ct">${g.tables.length}</span>`;
    b.onclick = () => document.getElementById("band-" + g.id)
      .scrollIntoView({ behavior: "smooth", block: "start" });
    nav.appendChild(b);
  });

  gs.forEach(g => {
    const band = el("section", "band");
    band.id = "band-" + g.id;
    band.style.setProperty("--c", g.color);
    const h = el("div", "band-head");
    h.innerHTML = `<span class="band-badge">${esc(g.id)}</span>` +
                  `<h2>${esc(g.label)}</h2><span class="n">${g.tables.length} tables</span>`;
    band.appendChild(h);
    if (g.blurb) {
      const p = el("p", "band-blurb"); p.textContent = g.blurb; band.appendChild(p);
    }
    const cards = el("div", "cards");
    g.tables.forEach(q => {
      const t = T[q];
      const c = el("button", "card");
      c.id = "card-" + g.id + "-" + q;
      c.dataset.q = q;
      c.style.setProperty("--c", mode === "domain" ? g.color : COLOR_OF(q));
      const sch = t.schema === "public" ? "" : `<span class="sch">${esc(t.schema)}.</span>`;
      c.innerHTML = `<div class="cn">${sch}${esc(t.name)}</div><div class="cm">${badges(t)}</div>`;
      c.onclick = () => select(q);
      cards.appendChild(c);
    });
    band.appendChild(cards);
    map.appendChild(band);
  });

  map.appendChild(relationshipSheet());
  map.appendChild(functionSheet());
  map.appendChild(enumSheet());

  applyFilter($("#q").value);
  if (current) markActive(current);
}

function relationshipSheet() {
  const s = el("section", "sheet");
  s.id = "sheet-rel";
  s.innerHTML =
    `<h2>Relationship map</h2>` +
    `<p class="lede">Every table other tables point at, with its children. ` +
    `<b>Solid blue</b> is an enforced <code>FOREIGN KEY</code> — Postgres cascades it. ` +
    `<b>Dashed amber</b> is a join that exists only by column value: the guest-partitioned ` +
    `tables had their <code>user_id</code> constraint dropped on purpose so a signed-out ` +
    `caller can be partitioned per install, which means integrity and deletion for those ` +
    `rows are the application's job, not the database's.</p>` +
    `<div class="svgwrap">` + SCHEMA.svg + `</div>`;
  return s;
}

function functionSheet() {
  const s = el("section", "sheet");
  s.id = "sheet-fn";
  const items = SCHEMA.functions.map(f => {
    const sd = f.secdef ? `<span class="b x">security definer</span>` : "";
    const vol = f.vol ? `<span class="b">${esc(f.vol.toLowerCase())}</span>` : "";
    const calls = f.callsites.length
      ? `<div class="fnm">called from ${esc(f.callsites[0])}` +
        (f.callsites.length > 1 ? ` +${f.callsites.length - 1}` : "") + `</div>`
      : "";
    return `<div class="fn"><div class="fnn">${esc(f.name)}(${esc(f.args)})</div>` +
           `<div class="fnm">&rarr; ${esc(f.returns)} ${sd} ${vol}</div>` +
           (f.comment ? `<div class="fnm">${esc(f.comment)}</div>` : "") + calls + `</div>`;
  }).join("");
  s.innerHTML = `<h2>Functions &amp; RPCs</h2>` +
    `<p class="lede">${SCHEMA.functions.length} functions in <code>public</code>, ` +
    `${SCHEMA.meta.counts.secdef} of them <code>SECURITY DEFINER</code> — those run with the ` +
    `owner's rights and bypass RLS, which is exactly why the credit and claim operations live ` +
    `in here rather than in application code.</p><div class="fnlist">${items}</div>`;
  return s;
}

function enumSheet() {
  const s = el("section", "sheet");
  s.id = "sheet-enum";
  const items = Object.keys(SCHEMA.enums)
    .filter(k => k.indexOf("public.") === 0)
    .map(k => `<div class="en"><div class="enn">${esc(k)}</div><div class="env">` +
      SCHEMA.enums[k].values.map(v => `<span>${esc(v)}</span>`).join("") + `</div></div>`)
    .join("");
  s.innerHTML = `<h2>Enum types</h2><p class="lede">A value outside these lists is rejected by ` +
    `Postgres, so they are the tightest contract the backend and the iOS decoder share.</p>` +
    `<div class="enumgrid">${items}</div>`;
  return s;
}

/* ------------------------------------------------------------- inspector */
function relLine(e, dir) {
  const other = dir === "out" ? e.to : e.from;
  const t = T[other];
  if (!t) return "";
  const dash = e.kind === "soft" ? " dashed" : "";
  const arrow = dir === "out" ? "&rarr;" : "&larr;";
  const od = e.onDelete && e.onDelete !== "NO ACTION"
    ? ` <span class="ct2">on delete ${esc(e.onDelete.toLowerCase())}</span>` : "";
  const why = e.kind === "soft"
    ? `<span class="why">no constraint${e.why ? " — " + esc(e.why) : ""}</span>` : "";
  return `<div class="rel"><button class="chip${dash}" onclick="select('${other}')">` +
    `<span class="cdot" style="background:${COLOR_OF(other)}"></span>` +
    `<span class="ct2">${esc(e.col)}</span> <span class="arrow">${arrow}</span> ` +
    `${esc(t.schema === "public" ? t.name : other)}</button>${od}${why}</div>`;
}

function colRows(t, all) {
  const keys = new Set(t.keyCols);
  const list = all ? t.cols : t.cols.filter(c => keys.has(c.n) || c.pk || c.fk);
  return list.map(c => {
    const marks =
      (c.pk ? `<span class="k pk">PK</span>` : "") +
      (c.fk ? `<span class="k fk">FK</span>` : "") +
      (c.soft ? `<span class="k sf">JOIN</span>` : "") +
      (c.enum ? `<span class="k en">ENUM</span>` : "");
    const row = `<tr${keys.has(c.n) || c.pk ? "" : ' class="dim"'}>` +
      `<td class="cn">${esc(c.n)}${marks}</td>` +
      `<td class="ct">${esc(c.t)}</td>` +
      `<td class="cf">${c.null ? "null" : "not null"}</td></tr>`;
    const note = c.c ? `<tr><td colspan="3" class="colnote">${esc(c.c)}</td></tr>` : "";
    return row + note;
  }).join("");
}

function select(q) {
  const t = T[q];
  if (!t) return;
  current = q;
  markActive(q);
  const color = COLOR_OF(q);
  const d = DOMAIN[t.domain] || { label: t.domain };
  $("#dbar").style.background = color;
  const body = $("#dbody");
  body.style.setProperty("--acc", color);

  const src = t.purposeSrc === "db"
    ? `<span class="src" title="COMMENT ON TABLE, authored in a migration">from schema</span>`
    : t.purposeSrc === "curated" ? `<span class="src">curated</span>` : "";
  const purpose = t.purpose
    ? `<p class="purpose">${esc(t.purpose)}${src}</p>`
    : `<p class="purpose muted">No description yet — add one to <code>schema_curation.py</code> ` +
      `or as a <code>COMMENT ON TABLE</code> in a migration.</p>`;
  const extra = t.extraPurpose ? `<p class="purpose muted">${esc(t.extraPurpose)}</p>` : "";
  const note = t.note ? `<div class="note">${esc(t.note)}</div>` : "";

  const outs = t.out.map(e => relLine(e, "out")).join("");
  const ins = t.in.map(e => relLine(e, "in")).join("");

  const uniq = t.uniques.map(u => `<li><code>${esc(u.join(", "))}</code></li>`).join("");
  const idx = t.indexes.map(i =>
    `<li><code>${esc(i.e)}</code>${i.u ? " <b>unique</b>" : ""}` +
    (i.m !== "btree" ? ` <span class="b v">${esc(i.m)}</span>` : "") +
    (i.w ? `<span class="why">where ${esc(i.w)}</span>` : "") + `</li>`).join("");
  const pol = t.policies.map(p =>
    `<li><code>${esc(p.c)}</code> ${esc(p.n)}` +
    (p.r.length ? ` <span class="ct2">to ${esc(p.r.join(", "))}</span>` : "") + `</li>`).join("");
  const trg = t.triggers.map(x =>
    `<li><code>${esc(x.t)}</code> ${esc(x.n)} &rarr; <code>${esc(x.f)}</code></li>`).join("");
  const chk = t.checks.map(c => `<li><code>${esc(c.e)}</code></li>`).join("");
  const fns = t.fns.map(f => `<li><code>${esc(f)}</code></li>`).join("");
  const enums = t.enums.map(e =>
    `<li><code>${esc(e)}</code> — ${esc((SCHEMA.enums[e] || { values: [] }).values.join(" · "))}</li>`
  ).join("");

  let code;
  if (t.direct.length) {
    code = `<div class="files">` +
      t.direct.slice(0, 8).map(s => `<code>${esc(s)}</code>`).join("") +
      (t.direct.length > 8 ? `<span class="muted">+${t.direct.length - 8} more</span>` : "") +
      `</div>`;
  } else if (t.named.length) {
    code = `<p class="muted">No <code>.table("${esc(t.name)}")</code> call site. Named in ` +
      `${t.named.length} place${t.named.length === 1 ? "" : "s"} — reached through a table-name ` +
      `constant or an RPC:</p><div class="files">` +
      t.named.slice(0, 6).map(s => `<code>${esc(s)}</code>`).join("") +
      (t.named.length > 6 ? `<span class="muted">+${t.named.length - 6} more</span>` : "") +
      `</div>`;
  } else if (t.rpcVia.length) {
    code = `<p class="muted">No direct table access — reached only through a database ` +
      `function:</p><div class="files">` +
      t.rpcVia.map(r => `<code>${esc(r.fn)}</code>` +
        r.sites.slice(0, 2).map(s => `<code>&nbsp;&nbsp;${esc(s)}</code>`).join("")).join("") +
      `</div>`;
  } else if (t.schema === "public") {
    code = `<p class="muted">No reference found anywhere in <code>backend/app</code> or ` +
      `<code>backend/scripts</code>.</p>`;
  } else {
    code = `<p class="muted">Supabase-managed — reached through the client library, not by ` +
      `table name.</p>`;
  }

  const sec = (title, inner, wrap) => inner
    ? `<div class="sec"><h4>${title}</h4>${wrap ? `<ul class="tight">${inner}</ul>` : inner}</div>`
    : "";

  const schPrefix = `<span class="sch">${esc(t.schema)}.</span>`;
  body.innerHTML =
    `<div class="eyebrow">${esc(d.label)} · ${esc(SCHEMA.kinds[t.kind] || t.kind)}</div>` +
    `<h2>${schPrefix}${esc(t.name)}</h2>` +
    `<div class="tag">${t.cols.length} columns · ${t.indexes.length} indexes · ` +
    `${t.rls ? t.policies.length + " RLS policies" : "RLS off"}` +
    (t.partitionBy ? ` · ${esc(t.partitionBy)}` : "") + `</div>` +
    purpose + extra + note +
    sec("Key columns", `<table class="cols">${colRows(t, false)}</table>` +
      (t.cols.length > t.keyCols.length
        ? `<button class="chip" style="margin-top:7px" onclick="expandCols('${q}')">` +
          `show all ${t.cols.length} columns</button>` : "")) +
    sec("References out", outs) +
    sec("Referenced by", ins) +
    sec("Primary key", t.pk.length ? `<li><code>${esc(t.pk.join(", "))}</code></li>` : "", true) +
    sec("Unique", uniq, true) +
    sec("Enums used", enums, true) +
    sec("Check constraints", chk, true) +
    sec("Indexes", idx, true) +
    sec("RLS policies", pol, true) +
    sec("Triggers", trg, true) +
    sec("Functions naming this table", fns, true) +
    sec("Touched by", code) +
    `<details class="ddl"><summary>CREATE TABLE</summary><pre>${esc(t.ddl)}</pre></details>`;

  if (window.innerWidth <= 1180) $("#detail").scrollIntoView({ behavior: "smooth", block: "start" });
}
window.select = select;

window.expandCols = function (q) {
  const t = T[q];
  const tbl = $("#dbody").querySelector("table.cols");
  if (tbl) tbl.innerHTML = colRows(t, true);
  const btn = $("#dbody").querySelector(".sec .chip");
  if (btn) btn.remove();
};

function markActive(q) {
  document.querySelectorAll(".card.on").forEach(e => e.classList.remove("on"));
  document.querySelectorAll('.card[data-q="' + q + '"]').forEach(e => e.classList.add("on"));
}

/* ---------------------------------------------------------------- filter */
function applyFilter(term) {
  term = (term || "").trim().toLowerCase();
  document.querySelectorAll(".band").forEach(band => {
    let shown = 0;
    band.querySelectorAll(".card").forEach(c => {
      const t = T[c.dataset.q];
      const hit = !term ||
        c.dataset.q.toLowerCase().indexOf(term) >= 0 ||
        (t.purpose || "").toLowerCase().indexOf(term) >= 0 ||
        t.cols.some(col => col.n.toLowerCase().indexOf(term) >= 0);
      c.classList.toggle("hidden", !hit);
      if (hit) shown++;
    });
    band.classList.toggle("hidden", shown === 0);
    const n = band.querySelector(".band-head .n");
    if (n) n.textContent = term
      ? shown + " of " + band.querySelectorAll(".card").length + " tables"
      : band.querySelectorAll(".card").length + " tables";
  });
  ["sheet-rel", "sheet-fn", "sheet-enum"].forEach(id => {
    const s = document.getElementById(id);
    if (s) s.classList.toggle("hidden", !!term);
  });
}

$("#q").addEventListener("input", e => applyFilter(e.target.value));
document.querySelectorAll("#seg button").forEach(b => {
  b.onclick = () => {
    document.querySelectorAll("#seg button").forEach(x => x.classList.remove("on"));
    b.classList.add("on");
    mode = b.dataset.mode;
    render();
  };
});

/* ------------------------------------------------------------- overview */
function overview() {
  const c = SCHEMA.meta.counts;
  current = null;
  document.querySelectorAll(".card.on").forEach(e => e.classList.remove("on"));
  $("#dbar").style.background = "#5a6b82";
  $("#dbody").style.setProperty("--acc", "#5a6b82");
  $("#dbody").innerHTML = `
    <div class="eyebrow">Overview</div>
    <h2 style="font-family:var(--font)">How to read this</h2>
    <div class="tag">Click any table. Esc returns here.</div>
    <p class="purpose">Every fact on this page is parsed out of
      <code>backend/database/schema_snapshot.sql</code> — the live
      <code>pg_dump</code>. The grouping and the one-line descriptions are the only
      hand-written parts.</p>
    <div class="note">Only <b>${c.fk}</b> of the joins in <code>public</code> are enforced
      foreign keys. Another <b>${c.soft}</b> are joins by column value with nothing behind
      them — mostly <code>user_id</code>, whose constraint was deliberately dropped
      (migrations 108 / 110 / 111 / 131) so a signed-out caller can be partitioned per
      install. For those tables Postgres will not cascade a delete, so account deletion
      has to name each one by hand.</div>
    <div class="sec"><h4>What the badges mean</h4><ul class="tight">
      <li><span class="b pk">pk …</span> the primary key. A natural key like
        <code>ticker</code> means a cache table keyed on the thing itself.</li>
      <li><span class="b r">Np</span> RLS policy count.
        <span class="b x">no rls</span> would mean an unprotected table — there are
        currently ${c.rls} of ${c.public} with RLS on.</li>
      <li><span class="b j">jsonb</span> carries a schemaless payload —
        the shape lives in Python/Swift, not in Postgres.</li>
      <li><span class="b v">vector</span> pgvector embedding column (RAG).</li>
      <li><span class="b s">&rarr;N</span> / <span class="b s">&larr;N</span> outbound and
        inbound relationships, enforced or not.</li>
    </ul></div>
    <div class="sec"><h4>Try it</h4><ul class="tight">
      <li>Search matches table names, descriptions <i>and column names</i>.</li>
      <li>Switch <b>Group by</b> to <b>Kind</b> to see the cache / audit / user-scoped split
        cut across domains.</li>
      <li>In a table, <b>References out</b> and <b>Referenced by</b> are clickable — dashed
        chips are the unenforced ones.</li>
      <li><b>Touched by</b> is scanned from <code>backend/app</code>, so it shows which service
        actually owns each table.</li>
    </ul></div>`;
}

document.addEventListener("keydown", e => { if (e.key === "Escape") overview(); });

render();
overview();
"""


def page(css: str, body: str, js: str, payload_json: str, title: str, maintenance: str) -> str:
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"UTF-8\" />\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />\n"
        f"<title>{title}</title>\n"
        f"<!--\n{maintenance}\n-->\n"
        f"<style>{css}</style>\n</head>\n<body>\n"
        f"{body}\n"
        "<script>\nconst SCHEMA = " + payload_json + ";\n"
        + js +
        "</script>\n</body>\n</html>\n"
    )
