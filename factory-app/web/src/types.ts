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

export interface TreeNode {
  kind: "factory" | "site" | "area" | "line" | "equipment";
  id: string | null;
  "@id": string;
  label: string;
  category?: string;
  metrics?: string[];
  children?: TreeNode[];
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
