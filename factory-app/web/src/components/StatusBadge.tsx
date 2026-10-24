import { isCode } from "../format";
import type { Latest } from "../types";

const STYLE: Record<number, string> = {
  0: "bg-slate-500/15 text-slate-600 ring-slate-500/30 dark:text-slate-300",
  1: "bg-emerald-500/15 text-emerald-700 ring-emerald-500/40 dark:text-emerald-300",
  2: "bg-red-500/15 text-red-700 ring-red-500/50 dark:text-red-300",
};
const DOT: Record<number, string> = { 0: "bg-slate-400", 1: "bg-emerald-400", 2: "bg-red-500 alarm-pulse" };

/** 稼働状態のバッジ（停止・稼働・異常停止） */
export function StatusBadge({ status, compact = false }: { status?: Latest; compact?: boolean }) {
  if (!status) return null;
  if (status.unavailable) {
    return <span className="chip text-amber-600 dark:text-amber-300" title={status.unavailable}>取得できません</span>;
  }
  const v = status.value;
  if (!isCode(v)) return null;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ${STYLE[v.value] ?? STYLE[0]}`}>
      <span className={`h-2 w-2 rounded-full ${DOT[v.value] ?? DOT[0]}`} />
      {compact ? v.label : `${v.label}（${v.value}）`}
    </span>
  );
}
