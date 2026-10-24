import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import dagre from "cytoscape-dagre";
import { Maximize2, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { STATUS_COLOR } from "../format";
import { statusCode, useApp } from "../store";
import type { TreeNode } from "../types";

cytoscape.use(dagre);

const KIND_STYLE: Record<string, { label: string; color: string; size: number }> = {
  factory: { label: "工場", color: "#6366f1", size: 46 },
  site: { label: "サイト", color: "#3b82f6", size: 40 },
  area: { label: "エリア", color: "#0ea5e9", size: 36 },
  line: { label: "ライン", color: "#06b6d4", size: 34 },
  equipment: { label: "設備", color: "#14b8a6", size: 30 },
  property: { label: "計測項目", color: "#f59e0b", size: 18 },
};

interface Flat {
  node: TreeNode;
  parent?: string;
  line?: string;
}

function flatten(nodes: TreeNode[], parent?: string, line?: string, out: Flat[] = []): Flat[] {
  for (const n of nodes) {
    const thisLine = n.kind === "line" ? (n.id ?? undefined) : line;
    out.push({ node: n, parent, line: thisLine });
    flatten(n.children ?? [], n["@id"], thisLine, out);
  }
  return out;
}

const propId = (equipment: string, metric: string) => `prop:${equipment}/${metric}`;

/** ① ナレッジグラフ（工場 → サイト → エリア → ライン → 設備 → 計測項目） */
export function KnowledgeGraph() {
  const container = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const { tree, equipment, details, selection, highlighted, theme, select, loadDetail } = useApp();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [line, setLine] = useState("");
  const [category, setCategory] = useState("");
  const [query, setQuery] = useState("");

  const flat = useMemo(() => flatten(tree), [tree]);
  const lines = flat.filter((f) => f.node.kind === "line");
  const categories = [...new Set(flat.map((f) => f.node.category).filter(Boolean))] as string[];

  // 要素（グラフの構造 + 展開した設備の計測項目）
  const elements = useMemo<ElementDefinition[]>(() => {
    const out: ElementDefinition[] = [];
    for (const { node, parent, line: l } of flat) {
      const k = KIND_STYLE[node.kind]!;
      const id = node.kind === "equipment" && node.id ? node.id : node["@id"];
      const label = node.kind === "equipment" ? `${node.id}\n${node.label}` : node.label;
      out.push({ data: { id, label, kind: node.kind, color: k.color, size: k.size, line: l ?? "", category: node.category ?? "" } });
      if (parent) {
        const parentNode = flat.find((f) => f.node["@id"] === parent)?.node;
        const parentId = parentNode?.kind === "equipment" && parentNode.id ? parentNode.id : parent;
        out.push({ data: { id: `${parentId}->${id}`, source: parentId, target: id } });
      }
      if (node.kind === "equipment" && node.id && expanded.has(node.id)) {
        for (const metric of node.metrics ?? []) {
          const p = details[node.id]?.properties.find((x) => x.metric === metric);
          const pid = propId(node.id, metric);
          out.push({ data: { id: pid, label: p ? p.label.replace(`${node.id} `, "") : metric, kind: "property", color: KIND_STYLE.property!.color, size: KIND_STYLE.property!.size, line: l ?? "", category: node.category ?? "" } });
          out.push({ data: { id: `${node.id}->${pid}`, source: node.id, target: pid } });
        }
      }
    }
    return out;
  }, [flat, expanded, details]);

  // Cytoscape の作成（要素・テーマが変わったら作り直す）
  useEffect(() => {
    if (!container.current) return;
    const dark = theme === "dark";
    const instance = cytoscape({
      container: container.current,
      elements,
      minZoom: 0.2,
      maxZoom: 3,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            width: "data(size)",
            height: "data(size)",
            label: "data(label)",
            "font-size": 11,
            "text-wrap": "wrap",
            "text-valign": "bottom",
            "text-margin-y": 5,
            color: dark ? "#cbd5e1" : "#1e293b",
            "text-outline-color": dark ? "#05080f" : "#f1f5f9",
            "text-outline-width": 2,
            "border-width": 0,
          },
        },
        { selector: "node[kind = 'equipment']", style: { "border-width": 4, "border-color": dark ? "#1e2a42" : "#cbd5e1" } },
        { selector: "node[kind = 'property']", style: { shape: "round-rectangle", "font-size": 10 } },
        { selector: "node.st-0", style: { "border-color": STATUS_COLOR[0] } },
        { selector: "node.st-1", style: { "border-color": STATUS_COLOR[1] } },
        { selector: "node.st-2", style: { "border-color": STATUS_COLOR[2] } },
        { selector: "node:selected", style: { "overlay-color": "#22d3ee", "overlay-opacity": 0.25, "overlay-padding": 8 } },
        { selector: "node.hl", style: { "underlay-color": "#fbbf24", "underlay-opacity": 0.45, "underlay-padding": 12, "underlay-shape": "ellipse" } },
        { selector: "node.dim", style: { opacity: 0.15 } },
        {
          selector: "edge",
          style: {
            width: 1.5,
            "line-color": dark ? "#1e2a42" : "#cbd5e1",
            "target-arrow-color": dark ? "#1e2a42" : "#cbd5e1",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
          },
        },
        { selector: "edge.dim", style: { opacity: 0.08 } },
      ],
      layout: { name: "dagre", rankDir: "LR", nodeSep: 36, rankSep: 120, padding: 40 } as cytoscape.LayoutOptions,
    });
    instance.on("tap", "node", (evt) => {
      const n = evt.target;
      const kind = n.data("kind") as string;
      if (kind === "equipment") select({ kind: "equipment", id: n.id() });
      else if (kind === "property") {
        const [eq, metric] = n.id().slice(5).split("/");
        if (eq && metric) select({ kind: "property", equipment: eq, metric });
      } else select({ kind: "node", iri: n.id() });
    });
    instance.on("dbltap", (evt) => evt.target === instance && instance.fit(undefined, 40));
    cy.current = instance;
    // 画面の大きさが変わったら描き直す（タブ切り替え・ウィンドウの変更）
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      instance.destroy();
    };
  }, [elements, theme, select]);

  // 設備を選んだら計測項目を展開（もう一度選ぶと畳む）
  useEffect(() => {
    if (selection?.kind === "equipment") {
      loadDetail(selection.id).catch(() => undefined);
      setExpanded((prev) => (prev.has(selection.id) ? prev : new Set(prev).add(selection.id)));
    } else if (selection?.kind === "property") {
      loadDetail(selection.equipment).catch(() => undefined);
      setExpanded((prev) => (prev.has(selection.equipment) ? prev : new Set(prev).add(selection.equipment)));
    }
  }, [selection, loadDetail]);

  // 稼働状態（縁の色）・選択・強調・絞り込みを反映
  useEffect(() => {
    const c = cy.current;
    if (!c) return;
    c.nodes("[kind = 'equipment']").forEach((n) => {
      n.removeClass("st-0 st-1 st-2");
      const code = statusCode(equipment.find((e) => e.id === n.id()));
      if (code !== undefined) n.addClass(`st-${code}`);
    });
    c.nodes().unselect();
    const selId =
      selection?.kind === "equipment" ? selection.id : selection?.kind === "property" ? propId(selection.equipment, selection.metric) : selection?.iri;
    if (selId) c.getElementById(selId).select();
    c.nodes().removeClass("hl");
    highlighted.forEach((id) => c.getElementById(id).addClass("hl"));
    c.elements().removeClass("dim");
    if (line || category) {
      c.nodes().forEach((n) => {
        const kind = n.data("kind") as string;
        if (kind === "factory" || kind === "site" || kind === "area") return;
        const ok = (!line || n.data("line") === line) && (!category || kind === "line" || n.data("category") === category);
        if (!ok) n.addClass("dim");
      });
      c.edges().forEach((e) => {
        if (e.target().hasClass("dim")) e.addClass("dim");
      });
    }
  }, [equipment, selection, highlighted, line, category, elements]);

  const onSearch = () => {
    const q = query.trim().toLowerCase();
    if (!q || !cy.current) return;
    const hit = cy.current.nodes().filter((n) => String(n.data("label")).toLowerCase().includes(q) || n.id().toLowerCase().includes(q))[0];
    if (!hit) return;
    hit.emit("tap");
    cy.current.animate({ center: { eles: hit }, zoom: 1.3 }, { duration: 300 });
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-3 py-2 dark:border-ink-800">
        <div className="relative">
          <Search size={14} className="absolute top-2.5 left-2.5 text-slate-400" />
          <input className="input w-64 !pl-8" placeholder="設備 ID・名前で検索（Enter）" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && onSearch()} />
        </div>
        <select className="input" value={line} onChange={(e) => setLine(e.target.value)}>
          <option value="">すべてのライン</option>
          {lines.map((l) => (
            <option key={l.node.id} value={l.node.id ?? ""}>{l.node.id}（{l.node.label}）</option>
          ))}
        </select>
        <select className="input" value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">すべての分類</option>
          {categories.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <button className="btn ml-auto" onClick={() => cy.current?.fit(undefined, 40)}>
          <Maximize2 size={14} />全体
        </button>
      </div>
      <div className="relative min-h-0 flex-1">
        {/* Cytoscape は容器を position: relative に書き換えるので、外側の箱で大きさを決める */}
        <div className="absolute inset-0">
          <div ref={container} className="h-full w-full" />
        </div>
        <div className="pointer-events-none absolute bottom-2 left-3 flex flex-wrap gap-3 text-[11px] text-slate-500 dark:text-slate-400">
          {Object.values(KIND_STYLE).map((k) => (
            <span key={k.label} className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full" style={{ background: k.color }} />{k.label}</span>
          ))}
          {(["停止", "稼働", "異常停止"] as const).map((label, i) => (
            <span key={label} className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full border-2" style={{ borderColor: STATUS_COLOR[i] }} />縁: {label}</span>
          ))}
        </div>
      </div>
    </div>
  );
}
