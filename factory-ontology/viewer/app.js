// 工場ナレッジグラフ ビューア
// 画面に出す文字列はすべて textContent で入れる(グラフの文字列を HTML として解釈しない)
"use strict";

const COLORS = {
  Factory: "#1e3a8a", Area: "#2563eb", WorkCenter: "#0891b2", ProductionEquipment: "#0f766e",
  ProductionEquipmentCategory: "#65a30d", Part: "#7c3aed", MaterialBatch: "#a21caf", Supplier: "#be185d",
  PartInstallation: "#c084fc", WorkOrder: "#d97706", ProductionRun: "#f59e0b", Inspection: "#ca8a04",
  ItemCategory: "#b45309", Alarm: "#dc2626", Stoppage: "#ea580c", MaintenanceWork: "#059669",
  DutyShift: "#475569", Person: "#334155", Role: "#64748b", RoleAssignment: "#94a3b8", Other: "#9ca3af",
};
const QUERY_PARAM_LABELS = { EQUIPMENT: "設備(ID)", LOT: "ロット番号", AT: "日時", DATE: "日付", ID: "どのシステムの ID でも可" };

let cy;
const expanded = new Set();

async function api(path, options = {}) {
  const res = await fetch(path, { ...options, headers: { "X-Requested-By": "kg-viewer", ...(options.headers || {}) } });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function showError(error) {
  const box = document.getElementById("error");
  box.textContent = String(error.message || error);
  box.hidden = false;
  setTimeout(() => { box.hidden = true; }, 8000);
}

function addToGraph({ nodes, edges }) {
  const added = [];
  for (const n of nodes) {
    if (cy.getElementById(n.iri).empty()) {
      added.push(cy.add({ data: { id: n.iri, label: n.label, kind: n.kind, kindLabel: n.kindLabel, color: COLORS[n.kind] || COLORS.Other } }));
    }
  }
  for (const e of edges) {
    const id = `${e.source}|${e.label}|${e.target}`;
    if (cy.getElementById(id).empty()) cy.add({ data: { id, source: e.source, target: e.target, label: e.label } });
  }
  return added.length;
}

function layout() {
  // 全体は階層で見せ、ノードを広げたら関係が重ならない力学レイアウトにする
  const options = expanded.size
    ? { name: "cose", animate: false, padding: 40, nodeRepulsion: 40000, idealEdgeLength: 90, nodeDimensionsIncludeLabels: true, randomize: false }
    : { name: "breadthfirst", directed: true, spacingFactor: 1.05, padding: 40, animate: false };
  cy.layout(options).run();
}

function renderLegend() {
  const legend = document.getElementById("legend");
  legend.replaceChildren();
  const kinds = new Map();
  cy.nodes().forEach((n) => kinds.set(n.data("kind"), n.data("kindLabel") || n.data("kind")));
  for (const [kind, label] of kinds) {
    const item = el("span", label);
    item.style.setProperty("--dot", COLORS[kind] || COLORS.Other);
    legend.append(item);
  }
}

function renderDetail(detail, label) {
  const box = document.getElementById("detail");
  box.replaceChildren(el("h3", label));
  const tags = el("div");
  detail.types.forEach((t) => tags.append(el("span", t, "tag")));
  box.append(tags);
  const list = el("dl");
  for (const item of detail.literals) {
    list.append(el("dt", item.name), el("dd", item.value));
  }
  box.append(list);
  if (detail.truncated.length) {
    box.append(el("h2", "表示を省略した関係"));
    for (const t of detail.truncated) {
      box.append(el("p", `${t.name}: ${t.total} 件のうち ${t.shown} 件を表示`, "hint"));
    }
  }
  box.append(el("p", detail.iri, "hint"));
}

async function selectNode(iri, label) {
  try {
    const detail = await api(`/api/node?iri=${encodeURIComponent(iri)}`);
    renderDetail(detail, label);
    if (!expanded.has(iri)) {
      expanded.add(iri);
      if (addToGraph(detail)) layout();
      renderLegend();
    }
    cy.elements().unselect();
    cy.getElementById(iri).select();
  } catch (e) { showError(e); }
}

async function focusNode(node) {
  addToGraph({ nodes: [node], edges: [] });
  await selectNode(node.iri, node.label);
  layout();
}

async function loadOverview() {
  cy.elements().remove();
  expanded.clear();
  addToGraph(await api("/api/overview"));
  layout();
  renderLegend();
}

function renderResult(rows) {
  const box = document.getElementById("result");
  box.replaceChildren();
  box.hidden = false;
  if (!rows.length) {
    box.append(el("p", "該当なし", "hint"));
    return;
  }
  const table = el("table");
  const head = el("tr");
  Object.keys(rows[0]).forEach((k) => head.append(el("th", k)));
  table.append(head);
  for (const row of rows) {
    const tr = el("tr");
    Object.values(row).forEach((v) => tr.append(el("td", v ?? "")));
    table.append(tr);
  }
  box.append(table);
}

function renderParams(query) {
  const box = document.getElementById("query-params");
  box.replaceChildren();
  for (const [name, value] of Object.entries(query.params)) {
    const label = el("label", QUERY_PARAM_LABELS[name] || name);
    const input = el("input");
    input.value = value;
    input.dataset.param = name;
    label.append(input);
    box.append(label);
  }
}

async function setupQueries() {
  const queries = await api("/api/queries");
  const select = document.getElementById("query-select");
  queries.forEach((q) => {
    const option = el("option", q.title);
    option.value = q.name;
    select.append(option);
  });
  const current = () => queries.find((q) => q.name === select.value);
  select.addEventListener("change", () => renderParams(current()));
  renderParams(queries[0]);
  document.getElementById("query-run").addEventListener("click", async () => {
    const params = {};
    document.querySelectorAll("#query-params input").forEach((i) => { params[i.dataset.param] = i.value.trim(); });
    try {
      const started = performance.now();
      const rows = await api(`/api/query/${select.value}`, { method: "POST", body: JSON.stringify(params) });
      document.getElementById("query-info").textContent = `${rows.length} 行 / ${Math.round(performance.now() - started)} ms`;
      renderResult(rows);
    } catch (e) { showError(e); }
  });
}

async function setupSummary() {
  const box = document.getElementById("summary");
  for (const item of await api("/api/summary")) {
    box.append(el("span", item.name), el("span", item.count.toLocaleString(), "n"));
  }
}

function setupSearch() {
  const input = document.getElementById("search");
  const results = document.getElementById("search-results");
  let timer;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const text = input.value.trim();
      results.replaceChildren();
      results.hidden = !text;
      if (!text) return;
      try {
        const nodes = await api(`/api/search?q=${encodeURIComponent(text)}`);
        if (!nodes.length) results.append(el("div", "該当なし"));
        for (const node of nodes) {
          const row = el("div", `${node.label}(${node.kindLabel || node.kind})`);
          row.addEventListener("click", () => { results.hidden = true; input.value = ""; focusNode(node); });
          results.append(row);
        }
      } catch (e) { showError(e); }
    }, 200);
  });
}

async function main() {
  cy = cytoscape({
    container: document.getElementById("graph"),
    style: [
      { selector: "node", style: { "background-color": "data(color)", label: "data(label)", color: "#1f2933", "font-size": 11,
        "text-valign": "bottom", "text-margin-y": 3, width: 22, height: 22, "text-wrap": "ellipsis", "text-max-width": 110 } },
      { selector: "node:selected", style: { "border-width": 3, "border-color": "#111827" } },
      { selector: "edge", style: { width: 1, "line-color": "#c3cad3", "target-arrow-color": "#c3cad3", "target-arrow-shape": "triangle",
        "curve-style": "bezier", label: "data(label)", "font-size": 9, color: "#66727f", "text-rotation": "autorotate",
        "text-background-color": "#fff", "text-background-opacity": 0.8, "text-background-padding": 1 } },
    ],
  });
  cy.on("tap", "node", (event) => selectNode(event.target.id(), event.target.data("label")));
  document.getElementById("reset").addEventListener("click", loadOverview);
  setupSearch();
  await Promise.all([loadOverview(), setupSummary(), setupQueries()]);
}

main().catch(showError);
