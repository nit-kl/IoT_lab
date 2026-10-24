// REST API（factory-kg の /v1/…。仕様は factory-kg/aws/openapi.yaml）の型

export interface CodeValue {
  value: number;
  label: string;
}

/** 数値、またはコード値の意味つき（例 {"value": 2, "label": "異常停止"}） */
export type Value = number | CodeValue;

export interface Latest {
  value?: Value;
  ts?: string;
  source_ts?: string;
  unavailable?: string;
}

export interface Unit {
  "@id": string;
  id: string;
  label: string | null;
  symbol: string | null;
}

export interface Category {
  key: string;
  label: string;
  notation: string | null;
}

export interface EquipmentSummary {
  id: string;
  "@id": string;
  label: string;
  label_en?: string;
  line: string | null;
  category: Category | null;
  types: string[];
  metrics: string[];
  status?: Latest;
}

export interface Property {
  metric: string;
  "@id": string;
  label: string;
  label_en?: string;
  description: string | null;
  equipment: string;
  unit: Unit;
  endpoints: { latest: string; readings: string };
  codes?: { key: string; label: string; values: CodeValue[] };
  latest?: Latest;
}

export interface EquipmentDetail extends EquipmentSummary {
  properties: Property[];
}

/** 配置（グラフの xf:placement）。床の南西の角が原点、x は東・y は北・高さは上（メートル）、向きは反時計回り（度） */
export interface Layout {
  x: number;
  y: number;
  width: number;
  depth: number;
  height: number;
  rotation: number;
}

export interface TreeNode {
  kind: "factory" | "site" | "area" | "line" | "equipment";
  id: string | null;
  "@id": string;
  label: string;
  category?: string;
  metrics?: string[];
  children?: TreeNode[];
  layout?: Layout;
}

export interface Reading {
  ts: string;
  source_ts: string;
  value: Value;
}

export interface Readings {
  equipment: string;
  metric: string;
  label: string;
  unit: Unit;
  items?: Reading[];
  count?: number;
  next?: string | null;
  unavailable?: string;
}

export interface ReplayStatus {
  status?: string;
  percent?: number;
  speed?: number;
  csv_position?: string;
  reflected_rows?: number;
  total_rows?: number;
  source_key?: string;
  unavailable?: string;
}

export interface AppStatus {
  graph: { triples: number; etag: string | null; version_id: string | null; loaded_at: string | null };
  counts: { lines: number; equipment: number; properties: number };
  replay?: ReplayStatus;
}

export interface AppConfig {
  mode: "aws" | "demo";
  owner?: string;
  kg?: string;
  rest?: string;
  replay?: string;
  prefixes: Record<string, string>;
}

/** 画面で選んでいるもの（①・②・③ で共有） */
export type Selection =
  | { kind: "node"; iri: string }
  | { kind: "equipment"; id: string }
  | { kind: "property"; equipment: string; metric: string };

// ---------- ③ AI エージェント（app_server.py の /api/agent） ----------

export interface AgentModel {
  key: string;
  label: string;
  model_id: string;
  allowed: boolean;
  price: { input: number; output: number }; // 100 万トークンあたりのドル
}

export interface AgentStatus {
  enabled: boolean;
  reason: string | null;
  busy?: boolean;
  default?: string;
  models: AgentModel[];
  limits?: {
    max_tool_calls: number;
    max_output_tokens: number;
    max_input_tokens: number;
    per_question_yen: number;
    daily_yen: number;
    monthly_yen: number;
  };
  usd_jpy?: number;
  spent: { today?: number; month?: number };
}

export interface AgentUsage {
  calls: number;
  tool_calls: number;
  yen: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadInputTokens: number;
  cacheWriteInputTokens: number;
}

/** 答えの途中経過（1 行 1 JSON で届く） */
export type AgentEvent =
  | { type: "text"; delta: string }
  | { type: "tool_start"; id: string; name: string; input: unknown }
  | { type: "tool_end"; id: string; name: string; error: boolean; preview: string }
  | { type: "highlight"; ids: string[]; reason: string }
  | { type: "usage"; call: number; yen: number }
  | { type: "done"; stop: string; notice: string | null; model: string; usage: AgentUsage; spent: { today: number; month: number } }
  | { type: "error"; message: string }
  | { type: "cancelled" };
