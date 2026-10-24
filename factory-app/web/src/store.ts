// 3 つの画面で共有する状態（選択中の設備・強調中の設備・データ）。各画面はこれを見て描き直す
import { create } from "zustand";
import { rest } from "./api";
import type { AppConfig, AppStatus, EquipmentDetail, EquipmentSummary, Selection, TreeNode } from "./types";

export type Tab = "graph" | "factory3d";

interface AppState {
  config: AppConfig | null;
  tab: Tab;
  theme: "dark" | "light";
  autoRefresh: boolean;
  selection: Selection | null;
  highlighted: string[]; // ③ のエージェントが挙げた設備 ID（①・② で強調）
  tree: TreeNode[];
  equipment: EquipmentSummary[];
  details: Record<string, EquipmentDetail>; // 設備 ID → 詳細（最新値つき）
  status: AppStatus | null;
  refreshedAt: number;
  error: string | null;

  setConfig(config: AppConfig): void;
  setTab(tab: Tab): void;
  toggleTheme(): void;
  setAutoRefresh(on: boolean): void;
  select(selection: Selection | null): void;
  highlight(ids: string[]): void;
  setError(error: string | null): void;
  loadStructure(): Promise<void>;
  refreshValues(): Promise<void>;
  loadDetail(id: string, force?: boolean): Promise<EquipmentDetail | undefined>;
}

export const useApp = create<AppState>((set, get) => ({
  config: null,
  tab: "graph",
  theme: "dark",
  autoRefresh: true,
  selection: null,
  highlighted: [],
  tree: [],
  equipment: [],
  details: {},
  status: null,
  refreshedAt: 0,
  error: null,

  setConfig: (config) => set({ config }),
  setTab: (tab) => set({ tab }),
  toggleTheme: () => {
    const theme = get().theme === "dark" ? "light" : "dark";
    document.documentElement.classList.toggle("dark", theme === "dark");
    set({ theme });
  },
  setAutoRefresh: (autoRefresh) => set({ autoRefresh }),
  select: (selection) => set({ selection }),
  highlight: (highlighted) => set({ highlighted }),
  setError: (error) => set({ error }),

  /** 構造（工場の木）を取り直す（グラフを更新したとき） */
  loadStructure: async () => {
    const tree = await rest<{ factories: TreeNode[] }>("/v1/factory");
    set({ tree: tree.factories });
  },

  /** 値（稼働状態・最新値・replay の状態）を取り直す（1 分ごと・更新ボタン） */
  refreshValues: async () => {
    const [equipment, status] = await Promise.all([
      rest<{ items: EquipmentSummary[] }>("/v1/equipment"),
      rest<AppStatus>("/v1/status"),
    ]);
    set({ equipment: equipment.items, status, refreshedAt: Date.now(), details: {} });
    const sel = get().selection;
    const id = sel?.kind === "equipment" ? sel.id : sel?.kind === "property" ? sel.equipment : null;
    if (id) await get().loadDetail(id, true);
  },

  loadDetail: async (id, force = false) => {
    const cached = get().details[id];
    if (cached && !force) return cached;
    const detail = await rest<EquipmentDetail>(`/v1/equipment/${encodeURIComponent(id)}`);
    set((s) => ({ details: { ...s.details, [id]: detail } }));
    return detail;
  },
}));

/** 設備 ID → 稼働状態のコード（0/1/2）。稼働状態を持たない設備は undefined */
export function statusCode(equipment: EquipmentSummary | undefined): number | undefined {
  const v = equipment?.status?.value;
  return typeof v === "object" && v !== null ? v.value : undefined;
}
