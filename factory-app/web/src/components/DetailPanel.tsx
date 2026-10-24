import { ChevronRight, ExternalLink, Gauge, Layers, Link2, Move3d } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { rest, sparql } from "../api";
import { formatTime, formatValue, shorten } from "../format";
import describeQuery from "../queries/describe.rq?raw";
import { useApp } from "../store";
import type { EquipmentDetail, Property, Readings, Selection, TreeNode } from "../types";
import { StatusBadge } from "./StatusBadge";
import { TimeSeriesChart } from "./TimeSeriesChart";

const GRAPH_NAMES: Record<string, string> = { ontology: "スキーマ", imports: "標準語彙の抜粋", instances: "インスタンス", layout: "配置" };
const KIND_LABELS: Record<string, string> = { factory: "工場", site: "サイト", area: "エリア", line: "ライン（ワークセンタ）", equipment: "設備" };
const RANGES = [
  { key: "day", label: "CSV の 1 日" },
  { key: "60", label: "直近 60 分" },
  { key: "180", label: "直近 3 時間" },
  { key: "1440", label: "直近 24 時間" },
] as const;

function findNode(nodes: TreeNode[], iri: string): TreeNode | undefined {
  for (const n of nodes) {
    if (n["@id"] === iri) return n;
    const hit = findNode(n.children ?? [], iri);
    if (hit) return hit;
  }
  return undefined;
}

/** 右の詳細欄（①・②・③ で共有）。選択中のものに応じて表示を切り替える */
export function DetailPanel() {
  const selection = useApp((s) => s.selection);
  return (
    <aside className="panel thin-scroll flex h-full min-h-0 flex-col overflow-y-auto p-4">
      {!selection && <Overview />}
      {selection?.kind === "equipment" && <EquipmentView id={selection.id} />}
      {selection?.kind === "property" && <PropertyView equipment={selection.equipment} metric={selection.metric} />}
      {selection?.kind === "node" && <NodeView iri={selection.iri} />}
    </aside>
  );
}

function Overview() {
  const { equipment, status, select } = useApp();
  return (
    <div className="space-y-4">
      <div>
        <div className="panel-title">工場の概要</div>
        <div className="mt-3 grid grid-cols-3 gap-2">
          {[
            ["ライン", status?.counts.lines],
            ["設備", status?.counts.equipment],
            ["計測項目", status?.counts.properties],
          ].map(([label, n]) => (
            <div key={String(label)} className="rounded-lg border border-slate-200 p-3 text-center dark:border-ink-700">
              <div className="text-2xl font-bold">{n ?? "—"}</div>
              <div className="text-xs text-slate-500 dark:text-slate-400">{label}</div>
            </div>
          ))}
        </div>
      </div>
      <div>
        <div className="panel-title mb-2">設備</div>
        <ul className="space-y-1">
          {equipment.map((e) => (
            <li key={e.id}>
              <button
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-slate-100 dark:hover:bg-ink-800"
                onClick={() => select({ kind: "equipment", id: e.id })}
              >
                <span className="font-mono text-xs text-slate-500 dark:text-slate-400">{e.id}</span>
                <span className="flex-1">{e.label}</span>
                <StatusBadge status={e.status} compact />
                <ChevronRight size={14} className="text-slate-400" />
              </button>
            </li>
          ))}
        </ul>
      </div>
      <p className="text-xs text-slate-500 dark:text-slate-400">グラフ（①）や 3D（②）で設備・計測項目を選ぶと、ここに詳細が出ます。</p>
    </div>
  );
}

function Header({ kind, title, sub, badge }: { kind: string; title: string; sub?: string; badge?: React.ReactNode }) {
  return (
    <div className="mb-3">
      <div className="text-xs text-slate-500 dark:text-slate-400">{kind}</div>
      <div className="mt-0.5 flex items-center gap-2">
        <h2 className="text-lg font-bold">{title}</h2>
        {badge}
      </div>
      {sub && <div className="mt-0.5 font-mono text-xs text-slate-500 dark:text-slate-400">{sub}</div>}
    </div>
  );
}

function EquipmentView({ id }: { id: string }) {
  const { details, loadDetail, select, equipment, refreshedAt, tree } = useApp();
  const [error, setError] = useState<string | null>(null);
  const detail: EquipmentDetail | undefined = details[id];
  useEffect(() => {
    setError(null);
    loadDetail(id).catch((e: Error) => setError(e.message));
  }, [id, loadDetail, refreshedAt]);
  const summary = equipment.find((e) => e.id === id);
  if (error) return <p className="text-sm text-red-500">{error}</p>;
  if (!detail) return <p className="text-sm text-slate-500">読み込み中…</p>;
  return (
    <div className="space-y-4">
      <Header kind={`設備 · ${detail.category?.label ?? ""} · ${detail.line ?? ""}`} title={detail.label} sub={detail.id} badge={<StatusBadge status={summary?.status ?? detail.status} />} />
      <div className="flex flex-wrap gap-1">
        {detail.types.map((t) => (
          <span key={t} className="chip font-mono">{t}</span>
        ))}
      </div>
      <section>
        <div className="panel-title mb-1 flex items-center gap-1"><Gauge size={12} /> 計測項目（クリックで時系列）</div>
        <table className="data-table">
          <tbody>
            {detail.properties.map((p) => (
              <tr key={p.metric} className="cursor-pointer hover:bg-slate-50 dark:hover:bg-ink-850" onClick={() => select({ kind: "property", equipment: id, metric: p.metric })}>
                <td>{p.label.replace(`${id} `, "")}</td>
                <td className="text-right font-mono font-semibold">{p.latest?.unavailable ? "取得できません" : formatValue(p.latest?.value, p.unit)}</td>
                <td className="text-right text-xs text-slate-500 dark:text-slate-400">{formatTime(p.latest?.source_ts)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {detail.properties.some((p) => p.latest?.unavailable) && (
          <p className="mt-2 rounded-md bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-300">
            replay から値を取得できません（replay の環境がない・DB の再開中など）。構造は表示できます。
          </p>
        )}
      </section>
      <Placement layout={findNode(tree, detail["@id"])?.layout} />
      <Relations iri={detail["@id"]} />
    </div>
  );
}

function PropertyView({ equipment, metric }: { equipment: string; metric: string }) {
  const { details, loadDetail, refreshedAt, status } = useApp();
  const [range, setRange] = useState<(typeof RANGES)[number]["key"]>("day");
  const [readings, setReadings] = useState<Readings | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    loadDetail(equipment).catch((e: Error) => setError(e.message));
  }, [equipment, loadDetail, refreshedAt]);
  const prop: Property | undefined = details[equipment]?.properties.find((p) => p.metric === metric);
  const csvDate = (status?.replay?.csv_position ?? prop?.latest?.source_ts ?? "").slice(0, 10);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const params: Record<string, string> =
        range === "day" && csvDate ? { source_from: `${csvDate}T00:00`, source_to: `${csvDate}T23:59` } : { minutes: range === "day" ? "1440" : range };
      const items = [];
      let next: string | null | undefined = undefined;
      let first: Readings | null = null;
      for (let page = 0; page < 5; page++) {
        const r: Readings = await rest<Readings>(`/v1/equipment/${equipment}/properties/${metric}/readings`, { ...params, limit: 1000, next });
        first ??= r;
        if (r.unavailable) break;
        items.push(...(r.items ?? []));
        next = r.next;
        if (!next) break;
      }
      if (!cancelled && first) setReadings({ ...first, items });
    }
    setReadings(null);
    load().catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [equipment, metric, range, csvDate, refreshedAt]);

  if (error) return <p className="text-sm text-red-500">{error}</p>;
  if (!prop) return <p className="text-sm text-slate-500">読み込み中…</p>;
  const latest = prop.latest;
  return (
    <div className="space-y-4">
      <Header kind={`計測項目 · ${equipment}`} title={prop.label} sub={`${prop.metric} · ${prop.unit.label ?? ""}（${prop.unit.id}）`} />
      {prop.description && <p className="text-sm text-slate-600 dark:text-slate-300">{prop.description}</p>}
      <div className="rounded-xl border border-slate-200 p-4 dark:border-ink-700">
        <div className="panel-title">最新値</div>
        {latest?.unavailable ? (
          <p className="mt-2 text-sm text-amber-600 dark:text-amber-300">取得できません: {latest.unavailable}</p>
        ) : (
          <>
            <div className="mt-1 text-3xl font-bold tracking-tight">{formatValue(latest?.value, prop.unit)}</div>
            <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">CSV 上の時刻 {formatTime(latest?.source_ts)} ／ 反映 {formatTime(latest?.ts)}</div>
          </>
        )}
        {prop.codes && (
          <div className="mt-3 flex flex-wrap gap-1">
            {prop.codes.values.map((c) => (
              <span key={c.value} className="chip">{c.value} = {c.label}</span>
            ))}
          </div>
        )}
      </div>
      <section>
        <div className="mb-1 flex items-center justify-between">
          <div className="panel-title">時系列（CSV 上の時刻）</div>
          <select className="input !py-0.5 text-xs" value={range} onChange={(e) => setRange(e.target.value as typeof range)}>
            {RANGES.map((r) => (
              <option key={r.key} value={r.key}>{r.label}</option>
            ))}
          </select>
        </div>
        {!readings && <div className="h-56 animate-pulse rounded-lg bg-slate-200/50 dark:bg-ink-850" />}
        {readings?.unavailable && <p className="text-sm text-amber-600 dark:text-amber-300">取得できません: {readings.unavailable}</p>}
        {readings && !readings.unavailable && (readings.items?.length ? (
          <TimeSeriesChart items={readings.items} unit={prop.unit} codes={prop.codes?.values} />
        ) : (
          <p className="text-sm text-slate-500">この範囲のデータはありません。</p>
        ))}
      </section>
      <section>
        <div className="panel-title mb-1 flex items-center gap-1"><Link2 size={12} /> 値の取得方法（グラフに書かれた API）</div>
        <div className="space-y-1 font-mono text-[11px] break-all text-slate-500 dark:text-slate-400">
          <div><span className="text-slate-400">latest </span>{prop.endpoints.latest}</div>
          <div><span className="text-slate-400">readings </span>{prop.endpoints.readings}</div>
        </div>
      </section>
      <Relations iri={prop["@id"]} />
    </div>
  );
}

function NodeView({ iri }: { iri: string }) {
  const { tree, select } = useApp();
  const node = useMemo(() => findNode(tree, iri), [tree, iri]);
  if (!node) return <Relations iri={iri} />;
  return (
    <div className="space-y-4">
      <Header kind={KIND_LABELS[node.kind] ?? node.kind} title={node.label} sub={node.id ?? undefined} />
      {node.children && node.children.length > 0 && (
        <section>
          <div className="panel-title mb-1 flex items-center gap-1"><Layers size={12} /> 構成要素</div>
          <ul className="space-y-1">
            {node.children.map((c) => (
              <li key={c["@id"]}>
                <button
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left text-sm hover:bg-slate-100 dark:hover:bg-ink-800"
                  onClick={() => select(c.kind === "equipment" && c.id ? { kind: "equipment", id: c.id } : { kind: "node", iri: c["@id"] })}
                >
                  <span className="text-xs text-slate-500">{KIND_LABELS[c.kind]}</span>
                  <span className="flex-1">{c.label}</span>
                  <ChevronRight size={14} className="text-slate-400" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      <Relations iri={iri} />
    </div>
  );
}

/** ナレッジグラフ上の関係（標準語彙の IRI つき、どの名前付きグラフの記述か） */
function Relations({ iri }: { iri: string }) {
  const { config, select } = useApp();
  const [rows, setRows] = useState<{ p: string; pLabel?: string; o: string; oLabel?: string; graph: string }[] | null>(null);
  const prefixes = config?.prefixes ?? {};
  useEffect(() => {
    if (!/^https?:\/\/[^\s<>"{}|\\^`]+$/.test(iri)) return;
    setRows(null);
    sparql(describeQuery.replaceAll("$NODE", `<${iri}>`))
      .then(({ rows }) => {
        const seen = new Set<string>();
        setRows(
          rows
            // 空白ノード（配置の中身など）は IRI で辿れないので出さない（配置は設備の詳細に別に出す）
            .filter((r) => r.o?.type !== "bnode")
            .map((r) => ({ p: r.p?.value ?? "", pLabel: r.pLabel?.value, o: r.o?.value ?? "", oLabel: r.oLabel?.value, graph: r.graph?.value ?? "" }))
            .filter((r) => !seen.has(`${r.p}|${r.o}`) && seen.add(`${r.p}|${r.o}`)),
        );
      })
      .catch(() => setRows([]));
  }, [iri]);
  const target = (o: string): Selection | null => {
    const m = /\/id\/equipment\/([^/]+)$/.exec(o);
    if (m?.[1]) return { kind: "equipment", id: m[1] };
    const p = /\/id\/property\/([^/]+)\/([^/]+)$/.exec(o);
    if (p?.[1] && p[2]) return { kind: "property", equipment: p[1], metric: p[2] };
    return o.startsWith("https://xcp-factory.example/id/") ? { kind: "node", iri: o } : null;
  };
  return (
    <section>
      <div className="panel-title mb-1 flex items-center gap-1"><ExternalLink size={12} /> ナレッジグラフ上の関係</div>
      {!rows && <div className="h-16 animate-pulse rounded-lg bg-slate-200/50 dark:bg-ink-850" />}
      {rows && (
        <table className="data-table table-fixed">
          <tbody>
            {rows.map((r) => {
              const to = target(r.o);
              return (
                <tr key={`${r.p}|${r.o}`} className={to ? "cursor-pointer hover:bg-slate-50 dark:hover:bg-ink-850" : ""} onClick={() => to && select(to)}>
                  <td className="w-[34%] break-all text-xs text-slate-500 dark:text-slate-400" title={shorten(r.p, prefixes)}>
                    {shorten(r.p, prefixes) === "rdf:type" ? "種類" : (r.pLabel ?? shorten(r.p, prefixes))}
                  </td>
                  <td className="break-all">
                    {r.oLabel ? <>{r.oLabel} <span className="font-mono text-[11px] text-slate-400">{shorten(r.o, prefixes)}</span></> : <span className="font-mono text-xs">{shorten(r.o, prefixes)}</span>}
                  </td>
                  <td className="w-16 text-[10px] text-slate-400">{GRAPH_NAMES[r.graph.split("/").pop() ?? ""] ?? ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** 配置（グラフの xf:placement。② 3D 工場の位置・大きさ） */
function Placement({ layout }: { layout?: TreeNode["layout"] }) {
  const n = (v: number) => (Number.isInteger(v) ? v.toFixed(1) : String(Math.round(v * 100) / 100));
  return (
    <section>
      <div className="panel-title mb-1 flex items-center gap-1"><Move3d size={12} /> 配置（② 3D 工場）</div>
      {layout ? (
        <p className="font-mono text-xs text-slate-600 dark:text-slate-300">
          位置 x {n(layout.x)} m・y {n(layout.y)} m ／ 大きさ {n(layout.width)} × {n(layout.depth)} × 高さ {n(layout.height)} m
          {layout.rotation ? ` ／ 向き ${n(layout.rotation)}°` : ""}
        </p>
      ) : (
        <p className="text-xs text-amber-600 dark:text-amber-300">グラフに配置（layout.ttl の xf:placement）がありません。3D では仮の位置に並びます。</p>
      )}
    </section>
  );
}
