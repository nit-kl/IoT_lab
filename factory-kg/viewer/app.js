// 工場ナレッジグラフ ビューア
// - グラフの構造は SPARQL（KG）から、値は TTL に書かれた replay の API から取る（どちらも kg_viewer.py が署名して中継）
// - 画面に出す文字列はすべて textContent で入れる（グラフの文字列を HTML として解釈しない。懸念点 V3）
// - 自動更新は画面を表示している間だけ（決定事項 6）。対象は replay の値だけで、グラフは「更新」ボタンで取り直す
"use strict";

const AUTO_REFRESH_MS = 60_000;
const MAX_READING_PAGES = 5;
const READINGS_LIMIT = 1000;
const KIND = {
  factory: { label: "工場", color: "#1e3a8a", size: 46 },
  site: { label: "サイト", color: "#1d4ed8", size: 40 },
  area: { label: "エリア", color: "#2563eb", size: 36 },
  line: { label: "ライン（ワークセンタ）", color: "#0891b2", size: 34 },
  equipment: { label: "設備", color: "#0f766e", size: 30 },
  property: { label: "計測項目", color: "#a16207", size: 20 },
};
const STATUS_RING = { 0: "#9ca3af", 1: "#16a34a", 2: "#dc2626" };
const GRAPH_NAMES = { ontology: "スキーマ", imports: "標準語彙の抜粋", instances: "インスタンス" };
const PREDICATE_LABELS = { "rdf:type": "種類" };

const state = {
  prefixes: {},
  prefixHeader: "",
  queries: {},
  nodes: new Map(),        // IRI → { iri, kind, label, code, parent, category }
  props: new Map(),        // IRI → 計測項目
  propsByEquipment: new Map(),
  services: new Map(),     // IRI → { base, latestTemplate, readingsTemplate, label }
  codes: new Map(),        // コード表 IRI → Map(notation → 意味)
  equipmentStatus: new Map(), // 設備 IRI → 稼働状態のコード（"0" / "1" / "2"）
  selected: null,
  expanded: new Set(),
  cy: null,
  chart: null,
  timer: null,
};

// ---------- 通信 ----------

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "X-Requested-By": "kg-viewer", ...(options.headers || {}) },
  });
  const text = await res.text();
  let body;
  try { body = JSON.parse(text); } catch { body = text; }
  if (!res.ok) {
    const message = (body && body.error) || (body && body.Message) || text || res.statusText;
    throw new Error(`HTTP ${res.status}: ${message}`);
  }
  return body;
}

async function sparql(query) {
  const body = await api("/api/sparql", {
    method: "POST",
    headers: { "Content-Type": "application/sparql-query", Accept: "application/sparql-results+json" },
    body: state.prefixHeader + query,
  });
  return body.results.bindings.map((row) => {
    const out = {};
    for (const [k, v] of Object.entries(row)) out[k] = v.value;
    return out;
  });
}

function replay(url) {
  return api("/api/replay?url=" + encodeURIComponent(url));
}

async function loadQuery(name) {
  if (!state.queries[name]) {
    const res = await fetch(`queries/${name}.rq`);
    state.queries[name] = await res.text();
  }
  return state.queries[name];
}

// ---------- 表示の部品 ----------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of children) {
    if (child == null) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function shorten(iri) {
  for (const [prefix, ns] of Object.entries(state.prefixes)) {
    if (iri.startsWith(ns)) return `${prefix}:${iri.slice(ns.length)}`;
  }
  return iri;
}

function showError(message) {
  const box = document.getElementById("error");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(showError.timer);
  showError.timer = setTimeout(() => { box.hidden = true; }, 8000);
}

function formatTime(iso) {
  return iso ? iso.replace("T", " ").replace("+09:00", "") : "";
}

// ---------- URL テンプレート（hydra:template の {?a,b,c} 形式） ----------

function expandTemplate(base, template, values) {
  return base + template.replace(/\{\?([^}]+)\}/, (_, names) => {
    const pairs = names.split(",")
      .filter((n) => values[n] !== undefined && values[n] !== null && values[n] !== "")
      .map((n) => `${encodeURIComponent(n)}=${encodeURIComponent(values[n])}`);
    return pairs.length ? "?" + pairs.join("&") : "";
  });
}

// ---------- グラフの読み込み ----------

async function loadGraph() {
  const [nodes, props, services, codes] = await Promise.all(
    ["nodes", "properties", "services", "codes"].map(async (q) => sparql(await loadQuery(q))));

  state.nodes.clear();
  for (const r of nodes) {
    const existing = state.nodes.get(r.node);
    state.nodes.set(r.node, { iri: r.node, kind: r.kind, label: r.label || r.code || shorten(r.node),
      code: r.code || "", parent: r.parent || (existing && existing.parent) || "", category: r.category || (existing && existing.category) || "" });
  }
  state.props.clear();
  state.propsByEquipment.clear();
  for (const r of props) {
    if (state.props.has(r.prop)) continue; // 単位記号が複数ある場合など
    const p = { iri: r.prop, kind: "property", equipment: r.equipment, equipmentId: r.equipmentId, metric: r.metric,
      label: r.label || r.metric, comment: r.comment || "", unit: r.unit, unitLabel: r.unitLabel || shorten(r.unit),
      symbol: r.symbol || "", latest: r.latest, readings: r.readings, service: r.service, codes: r.codes || "" };
    state.props.set(p.iri, p);
    if (!state.propsByEquipment.has(p.equipment)) state.propsByEquipment.set(p.equipment, []);
    state.propsByEquipment.get(p.equipment).push(p);
  }
  state.services.clear();
  for (const r of services) state.services.set(r.service, r);
  state.codes.clear();
  for (const r of codes) {
    if (!state.codes.has(r.scheme)) state.codes.set(r.scheme, new Map());
    state.codes.get(r.scheme).set(r.notation, r.label);
  }
  renderGraph();
  fillFilters();
  const status = await api("/api/kg-status");
  setStatus("kg-status", `KG: ${status.triples.toLocaleString()} トリプル`, true,
    `公開: ${status.loaded_at}（版 ${status.version_id || "-"}）`);
}

// ---------- グラフの描画（Cytoscape.js） ----------

function nodeElement(n) {
  const kind = KIND[n.kind];
  return { data: { id: n.iri, label: n.kind === "equipment" ? `${n.code}\n${n.label}` : n.label, kind: n.kind,
    color: kind.color, size: kind.size } };
}

function renderGraph() {
  const elements = [];
  for (const n of state.nodes.values()) {
    elements.push(nodeElement(n));
    if (n.parent && state.nodes.has(n.parent)) {
      elements.push({ data: { id: `${n.parent}->${n.iri}`, source: n.parent, target: n.iri } });
    }
  }
  for (const equipment of state.expanded) addPropertyNodes(elements, equipment);

  if (state.cy) state.cy.destroy();
  state.cy = cytoscape({
    container: document.getElementById("graph"),
    elements,
    wheelSensitivity: 0.3,
    style: [
      { selector: "node", style: {
        "background-color": "data(color)", width: "data(size)", height: "data(size)",
        label: "data(label)", "font-size": 11, "text-wrap": "wrap", "text-valign": "bottom", "text-margin-y": 4,
        color: "#1f2933", "text-background-color": "#fff", "text-background-opacity": 0.8, "text-background-padding": 1,
        "border-width": 0 } },
      { selector: "node[kind = 'equipment']", style: { "border-width": 5, "border-color": "#d9dde3" } },
      { selector: "node.st-0", style: { "border-color": STATUS_RING[0] } },
      { selector: "node.st-1", style: { "border-color": STATUS_RING[1] } },
      { selector: "node.st-2", style: { "border-color": STATUS_RING[2] } },
      { selector: "node[kind = 'property']", style: { shape: "round-rectangle", "font-size": 10 } },
      { selector: "node:selected", style: { "overlay-color": "#2563eb", "overlay-opacity": 0.25, "overlay-padding": 6 } },
      { selector: "node.dim", style: { opacity: 0.15 } },
      { selector: "edge", style: { width: 1.5, "line-color": "#c5ccd6", "target-arrow-color": "#c5ccd6",
        "target-arrow-shape": "triangle", "curve-style": "bezier" } },
      { selector: "edge.dim", style: { opacity: 0.1 } },
    ],
  });
  const roots = [...state.nodes.values()].filter((n) => n.kind === "factory").map((n) => n.iri);
  runLayout(roots);
  applyStatusRings();
  applyFilters();

  if (state.selected) state.cy.getElementById(state.selected).select();
  state.cy.on("tap", "node", (evt) => select(evt.target.id()));
  state.cy.on("dbltap", (evt) => { if (evt.target === state.cy) state.cy.fit(undefined, 30); });
  renderLegend();
}

function runLayout(roots) {
  // 木構造が見やすい dagre（上 → 下）。読み込めなかったときは標準の breadthfirst
  const options = typeof cytoscapeDagre !== "undefined" || cytoscape.prototype.hasOwnProperty("dagre")
    ? { name: "dagre", rankDir: "TB", nodeSep: 70, rankSep: 80, padding: 40, animate: false }
    : { name: "breadthfirst", directed: true, roots: roots.length ? roots : undefined, spacingFactor: 1.15, padding: 60, animate: false };
  try {
    state.cy.layout(options).run();
  } catch {
    state.cy.layout({ name: "breadthfirst", directed: true, padding: 60, animate: false }).run();
  }
}

function addPropertyNodes(elements, equipment) {
  for (const p of state.propsByEquipment.get(equipment) || []) {
    elements.push({ data: { id: p.iri, label: p.label.replace(`${p.equipmentId} `, ""), kind: "property",
      color: KIND.property.color, size: KIND.property.size } });
    elements.push({ data: { id: `${equipment}->${p.iri}`, source: equipment, target: p.iri } });
  }
}

function toggleProperties(equipment) {
  if (state.expanded.has(equipment)) {
    state.expanded.delete(equipment);
    state.cy.remove(state.cy.nodes().filter((n) => n.data("kind") === "property" &&
      state.props.get(n.id())?.equipment === equipment));
  } else {
    state.expanded.add(equipment);
    const elements = [];
    addPropertyNodes(elements, equipment);
    state.cy.add(elements);
  }
  const roots = [...state.nodes.values()].filter((n) => n.kind === "factory").map((n) => n.iri);
  runLayout(roots);
  applyFilters();
}

function renderLegend() {
  const legend = document.getElementById("legend");
  legend.replaceChildren();
  for (const k of Object.values(KIND)) {
    const item = el("span", { text: k.label });
    item.style.setProperty("--swatch", k.color);
    legend.append(item);
  }
  for (const [code, color] of Object.entries(STATUS_RING)) {
    const label = { 0: "停止", 1: "稼働", 2: "異常停止" }[code];
    const item = el("span", { text: `設備の縁: ${label}` });
    item.style.setProperty("--swatch", "#fff");
    item.style.setProperty("--ring", color);
    legend.append(item);
  }
}

function applyStatusRings() {
  if (!state.cy) return;
  state.cy.nodes("[kind = 'equipment']").forEach((n) => {
    n.removeClass("st-0 st-1 st-2");
    const code = state.equipmentStatus.get(n.id());
    if (code !== undefined) n.addClass(`st-${code}`);
  });
}

// ---------- 検索・絞り込み ----------

function fillFilters() {
  const lines = [...state.nodes.values()].filter((n) => n.kind === "line").sort((a, b) => a.code.localeCompare(b.code));
  const categories = [...new Set([...state.nodes.values()].filter((n) => n.category).map((n) => n.category))].sort();
  for (const [id, items, value, label] of [["line-filter", lines, (n) => n.iri, (n) => `${n.code}（${n.label}）`],
                                           ["category-filter", categories, (c) => c, (c) => c]]) {
    const select = document.getElementById(id);
    const current = select.value;
    select.replaceChildren(el("option", { value: "", text: "すべて" }));
    for (const item of items) select.append(el("option", { value: value(item), text: label(item) }));
    select.value = [...select.options].some((o) => o.value === current) ? current : "";
  }
}

function lineOf(iri) {
  let node = state.nodes.get(iri) || (state.props.has(iri) ? state.nodes.get(state.props.get(iri).equipment) : null);
  while (node && node.kind !== "line") node = state.nodes.get(node.parent);
  return node ? node.iri : "";
}

function applyFilters() {
  if (!state.cy) return;
  const line = document.getElementById("line-filter").value;
  const category = document.getElementById("category-filter").value;
  state.cy.elements().removeClass("dim");
  if (!line && !category) return;
  state.cy.nodes().forEach((n) => {
    const kind = n.data("kind");
    if (kind === "factory" || kind === "site" || kind === "area") return;
    const equipment = kind === "property" ? state.nodes.get(state.props.get(n.id()).equipment) : state.nodes.get(n.id());
    const lineOk = !line || lineOf(n.id()) === line;
    const categoryOk = !category || kind === "line" || (equipment && equipment.category === category);
    if (!(lineOk && categoryOk)) n.addClass("dim");
  });
  state.cy.edges().forEach((e) => { if (e.target().hasClass("dim")) e.addClass("dim"); });
}

function search(text) {
  const q = text.trim().toLowerCase();
  if (!q || !state.cy) return;
  const hit = [...state.nodes.values(), ...state.props.values()].find((n) =>
    (n.code || "").toLowerCase().includes(q) || (n.label || "").toLowerCase().includes(q) ||
    (n.category || "").toLowerCase().includes(q));
  if (!hit) { showError(`「${text}」に一致するノードはありません`); return; }
  if (hit.kind === "property" && !state.expanded.has(hit.equipment)) toggleProperties(hit.equipment);
  select(hit.iri);
  state.cy.animate({ center: { eles: state.cy.getElementById(hit.iri) }, zoom: 1.4 }, { duration: 300 });
}

// ---------- 詳細 ----------

async function select(iri) {
  const again = state.selected === iri;
  state.selected = iri;
  const node = state.nodes.get(iri) || state.props.get(iri);
  // 設備: 選ぶと計測項目を展開、選んだ設備をもう一度クリックすると畳む
  if (node && node.kind === "equipment" && (!state.expanded.has(iri) || again)) toggleProperties(iri);
  if (state.cy) {
    state.cy.nodes().unselect();
    state.cy.getElementById(iri).select();
  }
  await renderDetail(iri);
}

async function renderDetail(iri) {
  const pane = document.getElementById("detail");
  const node = state.nodes.get(iri) || state.props.get(iri);
  if (!node) { pane.replaceChildren(el("p", { class: "placeholder", text: "ノードが見つかりません" })); return; }
  disposeChart();
  const head = el("div", {},
    el("div", { class: "kind", text: `${KIND[node.kind].label}${node.code ? `　${node.code}` : ""}${node.category ? `　${node.category}` : ""}` }),
    el("h2", { text: node.label }));
  pane.replaceChildren(head);

  if (node.kind === "equipment") pane.append(...await equipmentSection(node));
  if (node.kind === "property") pane.append(...await propertySection(node));
  pane.append(await relationsSection(iri));
}

async function equipmentSection(node) {
  const props = state.propsByEquipment.get(node.iri) || [];
  const table = el("table", {}, el("tr", {}, el("th", { text: "計測項目" }), el("th", { text: "最新値" }),
    el("th", { text: "CSV 上の時刻" })));
  const statusBox = el("div");
  const values = await Promise.all(props.map((p) => replay(p.latest).then((r) => r.items[0]).catch((e) => ({ error: e }))));
  props.forEach((p, i) => {
    const v = values[i];
    const row = el("tr", { class: "clickable", onclick: () => select(p.iri) },
      el("td", { text: p.label.replace(`${node.code} `, "") }),
      el("td", { text: v && !v.error ? formatValue(p, v.value) : "取得できません" }),
      el("td", { text: v && !v.error ? formatTime(v.source_ts) : "" }));
    table.append(row);
    if (p.metric === "status" && v && !v.error) {
      const code = String(Math.round(v.value));
      state.equipmentStatus.set(node.iri, code);
      statusBox.append(el("span", { class: `badge st-${code}`, text: codeLabel(p, v.value) }), " ",
        el("span", { class: "hint", text: `（${formatTime(v.source_ts)} 時点）` }));
    }
  });
  applyStatusRings();
  const sections = [];
  if (statusBox.childNodes.length) sections.push(el("h3", { text: "稼働状態" }), statusBox);
  if (values.some((v) => v && v.error)) sections.push(unavailable(values.find((v) => v && v.error).error));
  sections.push(el("h3", { text: "計測項目（クリックで詳細）" }), table);
  return sections;
}

function codeLabel(prop, value) {
  const codes = prop.codes && state.codes.get(prop.codes);
  const key = String(Math.round(value));
  return codes && codes.has(key) ? `${codes.get(key)}（${key}）` : String(value);
}

function formatValue(prop, value) {
  if (prop.codes) return codeLabel(prop, value);
  const unit = prop.symbol && prop.unit !== `${state.prefixes.unit}UNITLESS` ? ` ${prop.symbol}` : "";
  return `${value}${unit}`;
}

function unavailable(error) {
  return el("p", { class: "unavailable",
    text: `replay から値を取得できません（replay の環境がない・DB の再開中など）: ${error.message}` });
}

async function propertySection(prop) {
  const service = state.services.get(prop.service);
  const sections = [];
  if (prop.comment) sections.push(el("p", { text: prop.comment }));
  sections.push(el("h3", { text: "最新値" }));
  try {
    const item = (await replay(prop.latest)).items[0];
    const big = el("div", {}, el("span", { class: "value-big", text: prop.codes ? codeLabel(prop, item.value) : String(item.value) }),
      prop.codes ? null : el("span", { class: "value-unit", text: `${prop.symbol}（${prop.unitLabel}）` }));
    sections.push(big, el("div", { class: "hint", text: `CSV 上の時刻 ${formatTime(item.source_ts)}／反映時刻 ${formatTime(item.ts)}` }));
  } catch (e) {
    sections.push(unavailable(e));
  }

  sections.push(el("h3", { text: "時系列" }));
  const chartBox = el("div", { class: "chart" });
  sections.push(chartBox);
  const minutes = document.getElementById("minutes").value;
  const url = service ? expandTemplate(service.base, service.readingsTemplate,
    { equipment: prop.equipmentId, metric: prop.metric, minutes, limit: READINGS_LIMIT }) : prop.readings;
  loadReadings(url).then((items) => drawChart(chartBox, prop, items)).catch((e) => chartBox.replaceWith(unavailable(e)));

  sections.push(el("h3", { text: "値の取得方法（グラフに書かれた API）" }),
    el("table", {},
      el("tr", {}, el("th", { text: "最新値" }), el("td", { class: "url", text: prop.latest })),
      el("tr", {}, el("th", { text: "時系列（今の表示）" }), el("td", { class: "url", text: url })),
      service ? el("tr", {}, el("th", { text: "テンプレート" }), el("td", { class: "url", text: service.base + service.readingsTemplate })) : null,
      el("tr", {}, el("th", { text: "単位" }), el("td", { text: `${prop.unitLabel}（${shorten(prop.unit)}）` }))));
  return sections;
}

async function loadReadings(url) {
  const items = [];
  let next = null;
  for (let page = 0; page < MAX_READING_PAGES; page++) {
    const body = await replay(next ? `${url}&next=${encodeURIComponent(next)}` : url);
    items.push(...body.items);
    next = body.next;
    if (!next) break;
  }
  return items;
}

function drawChart(box, prop, items) {
  if (!items.length) { box.replaceWith(el("p", { class: "hint", text: "この範囲のデータはありません（範囲を広げてください）" })); return; }
  disposeChart();
  state.chart = echarts.init(box);
  const codes = prop.codes && state.codes.get(prop.codes);
  state.chart.setOption({
    grid: { left: codes ? 76 : 56, right: 16, top: 16, bottom: 40 },
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        const p = params[0];
        const item = items[p.dataIndex];
        return `${escapeHtml(formatTime(item.source_ts))}<br>${escapeHtml(formatValue(prop, item.value))}`;
      },
    },
    xAxis: { type: "category", data: items.map((i) => formatTime(i.source_ts).slice(5, 16)), name: "CSV 上の時刻",
      nameLocation: "middle", nameGap: 26, axisLabel: { hideOverlap: true } },
    yAxis: codes
      ? { type: "value", min: 0, max: Math.max(...codes.keys()), interval: 1,
          axisLabel: { formatter: (v) => codes.get(String(v)) || "" } }
      : { type: "value", scale: true, name: prop.symbol },
    series: [{ type: "line", step: codes ? "end" : false, showSymbol: false, data: items.map((i) => i.value),
      lineStyle: { width: 1.5 } }],
  });
}

function disposeChart() {
  if (state.chart) { state.chart.dispose(); state.chart = null; }
}

async function relationsSection(iri) {
  // IRI として安全な文字だけのときに埋め込む（グラフ由来の IRI だが念のため）
  if (!/^https?:\/\/[^\s<>"{}|\\^`]+$/.test(iri)) throw new Error(`IRI として扱えません: ${iri}`);
  const query = (await loadQuery("describe")).replaceAll("$NODE", `<${iri}>`);
  const rows = await sparql(query);
  const table = el("table", {}, el("tr", {}, el("th", { text: "関係" }), el("th", { text: "値" }), el("th", { text: "記述のあるグラフ" })));
  const seen = new Set();
  for (const r of rows) {
    const key = `${r.p}|${r.o}|${r.graph}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const predicate = PREDICATE_LABELS[shorten(r.p)] || r.pLabel || shorten(r.p);
    const isNode = state.nodes.has(r.o) || state.props.has(r.o);
    const object = r.oLabel ? `${r.oLabel}（${shorten(r.o)}）` : shorten(r.o);
    const graph = GRAPH_NAMES[r.graph.split("/").pop()] || shorten(r.graph);
    table.append(el("tr", isNode ? { class: "clickable", onclick: () => select(r.o) } : {},
      el("td", { text: predicate, title: shorten(r.p) }), el("td", { text: object }), el("td", { class: "graph", text: graph })));
  }
  return el("div", {}, el("h3", { text: "ナレッジグラフ上の関係（標準語彙の IRI つき）" }), table);
}

// ---------- replay の状態・自動更新 ----------

function setStatus(id, text, ok, title) {
  const box = document.getElementById(id);
  box.textContent = text;
  box.className = `status ${ok ? "ok" : "ng"}`;
  if (title) box.title = title;
}

async function refreshReplay() {
  const service = [...state.services.values()][0];
  if (!service) { setStatus("replay-status", "replay: グラフに API がありません", false); return; }
  try {
    const s = await replay(service.base + "status");
    const text = s.status === "running"
      ? `replay: 再生中 ${s.percent}%（CSV ${formatTime(s.csv_position)}、${s.speed} 倍速）`
      : `replay: ${s.status}（${s.reflected_rows.toLocaleString()} 行）`;
    setStatus("replay-status", text, true, `CSV: ${s.source_key}`);
  } catch (e) {
    setStatus("replay-status", "replay: 取得できません", false, e.message);
  }
  // 設備の稼働状態（縁の色）
  const statusProps = [...state.props.values()].filter((p) => p.metric === "status");
  await Promise.all(statusProps.map((p) => replay(p.latest).then((r) => {
    if (r.items[0]) state.equipmentStatus.set(p.equipment, String(Math.round(r.items[0].value)));
  }).catch(() => {})));
  applyStatusRings();
  if (state.selected) {
    const node = state.nodes.get(state.selected) || state.props.get(state.selected);
    if (node && (node.kind === "equipment" || node.kind === "property")) await renderDetail(state.selected);
  }
}

function scheduleAutoRefresh() {
  clearInterval(state.timer);
  state.timer = null;
  const enabled = document.getElementById("auto-refresh").checked;
  if (enabled && document.visibilityState === "visible") {
    state.timer = setInterval(() => refreshReplay().catch((e) => showError(e.message)), AUTO_REFRESH_MS);
  }
}

async function refreshAll() {
  const button = document.getElementById("refresh");
  button.disabled = true;
  try {
    await loadGraph();
    await refreshReplay();
    if (state.selected && !(state.nodes.has(state.selected) || state.props.has(state.selected))) {
      state.selected = null;
      document.getElementById("detail").replaceChildren(el("p", { class: "placeholder", text: "左のグラフでノードを選んでください。" }));
    }
  } catch (e) {
    showError(e.message);
  } finally {
    button.disabled = false;
  }
}

// ---------- SPARQL コンソール ----------

async function runConsole() {
  const info = document.getElementById("sparql-info");
  const table = document.getElementById("sparql-result");
  table.replaceChildren();
  info.textContent = "実行中…";
  const started = performance.now();
  try {
    const rows = await sparql(document.getElementById("sparql-text").value);
    const vars = rows.length ? Object.keys(rows.reduce((a, r) => Object.assign(a, r), {})) : [];
    table.append(el("tr", {}, ...vars.map((v) => el("th", { text: v }))));
    for (const r of rows) table.append(el("tr", {}, ...vars.map((v) => el("td", { text: r[v] ? shorten(r[v]) : "" }))));
    info.textContent = `${rows.length} 行（${Math.round(performance.now() - started)} ms）`;
  } catch (e) {
    info.textContent = e.message;
  }
}

// ---------- 起動 ----------

async function main() {
  const config = await api("/api/config");
  state.prefixes = { ...config.prefixes, rdf: "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    rdfs: "http://www.w3.org/2000/01/rdf-schema#" };
  state.prefixHeader = Object.entries(state.prefixes).map(([p, ns]) => `PREFIX ${p}: <${ns}>\n`).join("");

  document.getElementById("refresh").addEventListener("click", refreshAll);
  document.getElementById("auto-refresh").addEventListener("change", scheduleAutoRefresh);
  document.addEventListener("visibilitychange", () => {
    scheduleAutoRefresh();
    if (document.visibilityState === "visible" && document.getElementById("auto-refresh").checked) {
      refreshReplay().catch((e) => showError(e.message)); // 戻ってきたらすぐ最新にする
    }
  });
  document.getElementById("search").addEventListener("keydown", (e) => { if (e.key === "Enter") search(e.target.value); });
  document.getElementById("line-filter").addEventListener("change", applyFilters);
  document.getElementById("category-filter").addEventListener("change", applyFilters);
  document.getElementById("minutes").addEventListener("change", () => {
    if (state.selected && state.props.has(state.selected)) renderDetail(state.selected);
  });
  document.getElementById("sparql-run").addEventListener("click", runConsole);

  await refreshAll();
  scheduleAutoRefresh();
  // http://127.0.0.1:8765/#q=CNC-01 のように開くと、その設備・計測項目を選んだ状態で始める（ブックマーク用）
  const openFromHash = () => {
    const q = new URLSearchParams(location.hash.slice(1)).get("q");
    if (q) { document.getElementById("search").value = q; search(q); }
  };
  window.addEventListener("hashchange", openFromHash);
  openFromHash();
}

main().catch((e) => showError(`起動に失敗しました: ${e.message}`));
