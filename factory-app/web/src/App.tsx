import { Box, Network, X } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { AgentDock } from "./agent/AgentDock";
import { getConfig, setPrefixes } from "./api";
import { DetailPanel } from "./components/DetailPanel";
import { Header } from "./components/Header";
import { SparqlConsole } from "./components/SparqlConsole";
import { useVisibleInterval } from "./hooks";
import { KnowledgeGraph } from "./kg/KnowledgeGraph";
import { useApp, type Tab } from "./store";

// 3D のライブラリは大きいので、② を開いたときに読み込む
const Factory3D = lazy(() => import("./factory3d/Factory3D"));

const TABS: { key: Tab; label: string; icon: React.ReactNode }[] = [
  { key: "graph", label: "① ナレッジグラフ", icon: <Network size={15} /> },
  { key: "factory3d", label: "② 3D 工場", icon: <Box size={15} /> },
];

export function App() {
  const { tab, setTab, setConfig, loadStructure, refreshValues, autoRefresh, error, setError, select } = useApp();
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(
    async (structure: boolean) => {
      setRefreshing(true);
      try {
        if (structure) await loadStructure();
        await refreshValues();
        setError(null);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setRefreshing(false);
      }
    },
    [loadStructure, refreshValues, setError],
  );

  useEffect(() => {
    getConfig()
      .then((config) => {
        setConfig(config);
        setPrefixes(config.prefixes);
        return refresh(true);
      })
      .then(() => {
        // #q=CNC-01（設備）や #q=CNC-01/spindle_load（計測項目）のように開くと、それを選んだ状態で始める
        // #tab=3d で ② 3D 工場から始める（#tab=3d&q=CNC-01 のように組み合わせられる）
        const hash = new URLSearchParams(location.hash.slice(1));
        if (hash.get("tab") === "3d") setTab("factory3d");
        const q = hash.get("q") ?? "";
        const m = /^([A-Za-z0-9_-]+)(?:\/([A-Za-z0-9_-]+))?$/.exec(q);
        if (m?.[1]) select(m[2] ? { kind: "property", equipment: m[1], metric: m[2] } : { kind: "equipment", id: m[1] });
      })
      .catch((e: Error) => setError(`起動に失敗しました: ${e.message}`));
  }, [setConfig, refresh, setError, select, setTab]);

  // 値は画面を表示している間だけ 1 分ごとに取り直す（replay の DB を起こし続けないため）
  useVisibleInterval(() => void refresh(false), 60_000, autoRefresh);

  return (
    <div className="flex h-full flex-col">
      <Header onRefresh={() => void refresh(true)} refreshing={refreshing} />
      <div className="flex min-h-0 flex-1 flex-col gap-3 p-3">
        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_400px] grid-rows-[minmax(0,1fr)] gap-3">
          <section className="panel flex min-h-0 flex-col overflow-hidden">
            <nav className="flex gap-1 border-b border-slate-200 px-2 pt-2 dark:border-ink-800">
              {TABS.map((t) => (
                <button
                  key={t.key}
                  onClick={() => setTab(t.key)}
                  className={`-mb-px flex items-center gap-1.5 rounded-t-lg border-b-2 px-4 py-2 text-sm font-medium transition ${
                    tab === t.key
                      ? "border-cyan-500 text-cyan-700 dark:border-cyan-400 dark:text-cyan-300"
                      : "border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
                  }`}
                >
                  {t.icon}
                  {t.label}
                </button>
              ))}
            </nav>
            <div className="flex min-h-0 flex-1 flex-col">
              {tab === "graph" ? (
                <div className="flex min-h-0 flex-1 flex-col">
                  <div className="flex min-h-0 flex-1 flex-col">
                    <KnowledgeGraph />
                  </div>
                  <SparqlConsole />
                </div>
              ) : (
                <Suspense fallback={<div className="grid flex-1 place-items-center text-sm text-slate-500">3D を準備中…</div>}>
                  <Factory3D />
                </Suspense>
              )}
            </div>
          </section>
          <DetailPanel />
        </div>
        <AgentDock />
      </div>
      {error && (
        <div className="fixed right-4 bottom-4 flex max-w-lg items-start gap-2 rounded-xl border border-red-500/50 bg-red-50 p-3 text-sm text-red-700 shadow-xl dark:bg-red-950/80 dark:text-red-200">
          <span className="flex-1">{error}</span>
          <button onClick={() => setError(null)}><X size={14} /></button>
        </div>
      )}
    </div>
  );
}
