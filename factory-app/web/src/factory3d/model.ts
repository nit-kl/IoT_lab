// ② 3D 工場の表示用モデル。グラフの配置（/v1/factory の layout）と最新値（/v1/equipment/{id}）から作る
import { formatValue, isCode } from "../format";
import type { EquipmentDetail, EquipmentSummary, Layout, TreeNode } from "../types";

/** 3D 空間での床の上の箱。x は東、z は南（画面の手前）、高さは上（メートル）。rot は y 軸まわり（ラジアン） */
export interface Footprint {
  x: number;
  z: number;
  w: number;
  d: number;
  h: number;
  rot: number;
}

export type MachineState = "stopped" | "running" | "alarm" | "unknown";

export interface Machine {
  id: string;
  label: string;
  category: string; // 分類のキー（press・cnc・conveyor・compressor・environment-sensor）
  categoryLabel: string;
  box: Footprint;
  placed: boolean; // false: グラフに配置がなく、仮の場所に並べた
  state: MachineState;
  hasTower: boolean; // 積層信号灯を付けるか（環境センサには付けない）
  values: Record<string, number | undefined>; // メトリクス名 → 数値（コード値は value）
  readouts: { label: string; text: string }[]; // ラベルに出す主な値
}

export interface Line {
  id: string;
  label: string;
  box: Footprint;
}

export interface Scene {
  area: { label: string; w: number; d: number; h: number };
  lines: Line[];
  machines: Machine[];
}

const DEFAULT_AREA: Layout = { x: 15, y: 8, width: 30, depth: 16, height: 7, rotation: 0 };
const DEFAULT_SIZE = { width: 1.6, depth: 1.6, height: 1.8 };

// 分類ごとにラベルへ出す計測項目（先頭から順に。無いものは飛ばす）
const READOUTS: Record<string, string[]> = {
  press: ["shot_count", "hydraulic_pressure"],
  cnc: ["spindle_load", "spindle_speed"],
  conveyor: ["speed", "motor_temp"],
  compressor: ["discharge_pressure", "power"],
  "environment-sensor": ["temperature", "humidity", "co2"],
};

// 稼働状態（status）を持たない設備の「動いている」の判定（値から推定）
const RUNNING_RULES: Record<string, (v: Record<string, number | undefined>) => boolean | undefined> = {
  conveyor: (v) => (v.speed === undefined ? undefined : v.speed > 0),
  compressor: (v) => (v.power === undefined ? undefined : v.power >= 10), // 10 kW 未満は無負荷運転
};

function flatten(nodes: TreeNode[], out: TreeNode[] = []): TreeNode[] {
  for (const n of nodes) {
    out.push(n);
    flatten(n.children ?? [], out);
  }
  return out;
}

export function buildScene(tree: TreeNode[], equipment: EquipmentSummary[], details: Record<string, EquipmentDetail>): Scene {
  const nodes = flatten(tree);
  const areaNode = nodes.find((n) => n.kind === "area" && n.layout) ?? nodes.find((n) => n.kind === "area");
  const area = areaNode?.layout ?? DEFAULT_AREA;
  // グラフの座標（南西の角が原点、y は北）→ 3D（エリアの中心が原点、z は南）
  const toWorld = (l: Layout): Footprint => ({
    x: l.x - area.x,
    z: area.y - l.y,
    w: l.width,
    d: l.depth,
    h: l.height,
    rot: (l.rotation * Math.PI) / 180,
  });

  const lines: Line[] = nodes
    .filter((n) => n.kind === "line" && n.layout)
    .map((n) => ({ id: n.id ?? "", label: n.label, box: toWorld(n.layout!) }));

  // 配置のない設備は、エリアの南側に左から仮に並べる
  let spare = 0;
  const machines: Machine[] = nodes
    .filter((n) => n.kind === "equipment" && n.id)
    .map((n) => {
      const id = n.id!;
      const summary = equipment.find((e) => e.id === id);
      const detail = details[id];
      const placed = !!n.layout;
      const layout = n.layout ?? {
        ...DEFAULT_SIZE,
        x: 2 + (spare++ % 12) * 2.4,
        y: area.y - area.depth / 2 + 1.2,
        rotation: 0,
      };
      const values: Record<string, number | undefined> = {};
      const readouts: Machine["readouts"] = [];
      const category = summary?.category?.key ?? "";
      for (const p of detail?.properties ?? []) {
        const v = p.latest?.value;
        values[p.metric] = v === undefined ? undefined : isCode(v) ? v.value : v;
      }
      for (const metric of READOUTS[category] ?? detail?.properties.map((p) => p.metric).slice(0, 2) ?? []) {
        const p = detail?.properties.find((x) => x.metric === metric);
        if (p && p.latest && !p.latest.unavailable) {
          readouts.push({ label: p.label.replace(`${id} `, ""), text: formatValue(p.latest.value, p.unit) });
        }
      }
      return {
        id,
        label: n.label,
        category,
        categoryLabel: n.category ?? summary?.category?.label ?? "",
        box: toWorld(layout),
        placed,
        state: machineState(category, summary, values, detail),
        hasTower: category !== "environment-sensor",
        values,
        readouts,
      };
    });

  return { area: { label: areaNode?.label ?? "エリア", w: area.width, d: area.depth, h: area.height }, lines, machines };
}

function machineState(
  category: string,
  summary: EquipmentSummary | undefined,
  values: Record<string, number | undefined>,
  detail: EquipmentDetail | undefined,
): MachineState {
  const status = summary?.status?.value;
  if (status !== undefined) {
    const code = isCode(status) ? status.value : status;
    return code === 2 ? "alarm" : code === 1 ? "running" : "stopped";
  }
  if (!detail) return "unknown";
  const running = RUNNING_RULES[category]?.(values);
  if (running === undefined) return detail.properties.some((p) => p.latest?.value !== undefined) ? "running" : "unknown";
  return running ? "running" : "stopped";
}

export const STATE_COLORS: Record<MachineState, string> = {
  stopped: "#94a3b8",
  running: "#22c55e",
  alarm: "#ef4444",
  unknown: "#64748b",
};

export const STATE_LABELS: Record<MachineState, string> = {
  stopped: "停止",
  running: "稼働",
  alarm: "異常停止",
  unknown: "不明",
};
