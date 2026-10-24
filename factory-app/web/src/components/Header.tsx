import { Activity, Bot, Database, Factory, Moon, RefreshCw, Sun } from "lucide-react";
import { formatTime } from "../format";
import { useApp } from "../store";

function Pill({ icon, label, value, tone, title }: { icon: React.ReactNode; label: string; value: string; tone: "ok" | "warn" | "idle"; title?: string }) {
  const toneClass =
    tone === "ok"
      ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-300"
      : tone === "warn"
        ? "border-amber-500/50 text-amber-700 dark:text-amber-300"
        : "border-slate-300 text-slate-500 dark:border-ink-700 dark:text-slate-400";
  return (
    <div className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs ${toneClass}`} title={title}>
      {icon}
      <span className="font-semibold">{label}</span>
      <span className="font-mono">{value}</span>
    </div>
  );
}

export function Header({ onRefresh, refreshing }: { onRefresh: () => void; refreshing: boolean }) {
  const { config, status, theme, toggleTheme, autoRefresh, setAutoRefresh } = useApp();
  const replay = status?.replay;
  const replayText = !replay
    ? "—"
    : replay.unavailable
      ? "取得できません"
      : replay.status === "running"
        ? `再生中 ${replay.percent ?? 0}%（CSV ${formatTime(replay.csv_position)}）`
        : `${replay.status ?? "—"}`;
  return (
    <header className="flex items-center gap-3 border-b border-slate-200 px-5 py-2.5 dark:border-ink-800">
      <div className="flex items-center gap-2">
        <div className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-cyan-400 to-indigo-500 text-white shadow-lg shadow-cyan-500/20">
          <Factory size={18} />
        </div>
        <div className="leading-tight">
          <div className="text-[15px] font-bold tracking-wide">工場アプリ</div>
          <div className="text-[10px] tracking-[0.2em] text-slate-500 uppercase dark:text-slate-400">Factory Knowledge Ops</div>
        </div>
        {config?.mode === "demo" && (
          <span className="ml-1 rounded-md bg-amber-400/15 px-2 py-0.5 text-[10px] font-bold tracking-widest text-amber-600 ring-1 ring-amber-400/40 dark:text-amber-300">
            DEMO
          </span>
        )}
      </div>
      <div className="ml-4 flex flex-wrap items-center gap-2">
        <Pill icon={<Activity size={13} />} label="replay" value={replayText} tone={replay?.unavailable ? "warn" : replay ? "ok" : "idle"} title={replay?.unavailable ?? replay?.source_key} />
        <Pill icon={<Database size={13} />} label="KG" value={status ? `${status.graph.triples.toLocaleString()} トリプル` : "—"} tone={status ? "ok" : "idle"} />
        <Pill icon={<Bot size={13} />} label="AI" value="ステップ 3 で実装" tone="idle" />
      </div>
      <div className="ml-auto flex items-center gap-3">
        <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-500 dark:text-slate-400" title="画面を表示している間だけ、1 分ごとに値を取り直す">
          <input type="checkbox" className="accent-cyan-500" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
          自動更新（表示中のみ・1 分）
        </label>
        <button className="btn" onClick={onRefresh} disabled={refreshing}>
          <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
          更新
        </button>
        <button className="btn !px-2" onClick={toggleTheme} title="テーマの切り替え">
          {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
        </button>
      </div>
    </header>
  );
}
