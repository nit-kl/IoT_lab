import type { Unit, Value } from "./types";

export const STATUS_COLOR: Record<number, string> = { 0: "#94a3b8", 1: "#22c55e", 2: "#ef4444" };

export function isCode(value: Value | undefined): value is { value: number; label: string } {
  return typeof value === "object" && value !== null && "label" in value;
}

/** 値を表示用の文字列にする（コード値は「異常停止（2）」、数値は単位つき） */
export function formatValue(value: Value | undefined, unit?: Unit | null): string {
  if (value === undefined) return "—";
  if (isCode(value)) return `${value.label}（${value.value}）`;
  const n = Number.isInteger(value) ? String(value) : value.toFixed(Math.abs(value) < 10 ? 2 : 1);
  return unit?.symbol ? `${n} ${unit.symbol}` : n;
}

/** ISO 8601 の時刻を「10/01 14:30」の形に（日本時間のまま表示） */
export function formatTime(iso: string | undefined | null, withDate = true): string {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  if (!m) return iso;
  return withDate ? `${m[2]}/${m[3]} ${m[4]}:${m[5]}` : `${m[4]}:${m[5]}`;
}

// サーバーの接頭辞に無い標準の語彙（rdf:type などを短く見せる）
const STANDARD_PREFIXES: Record<string, string> = {
  rdf: "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
  rdfs: "http://www.w3.org/2000/01/rdf-schema#",
  owl: "http://www.w3.org/2002/07/owl#",
  xsd: "http://www.w3.org/2001/XMLSchema#",
};

export function shorten(iri: string, prefixes: Record<string, string>): string {
  for (const [p, ns] of Object.entries({ ...STANDARD_PREFIXES, ...prefixes })) {
    if (iri.startsWith(ns)) return `${p}:${iri.slice(ns.length)}`;
  }
  return iri;
}
