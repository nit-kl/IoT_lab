// app_server.py の API を呼ぶ（REST API と SPARQL。認証の署名はサーバー側で付ける）

import type { AgentEvent, AgentStatus, AppConfig } from "./types";

const HEADERS = { "X-Requested-By": "factory-app" };

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function parse(res: Response): Promise<unknown> {
  const text = await res.text();
  let body: unknown = text;
  try {
    body = JSON.parse(text);
  } catch {
    /* JSON でなければ文字列のまま */
  }
  if (!res.ok) {
    const message =
      (typeof body === "object" && body !== null && "error" in body && String((body as { error: unknown }).error)) ||
      (typeof body === "string" && body) ||
      res.statusText;
    throw new ApiError(res.status, `HTTP ${res.status}: ${message}`);
  }
  return body;
}

export async function getConfig(): Promise<AppConfig> {
  return (await parse(await fetch("/api/config", { headers: HEADERS }))) as AppConfig;
}

/** REST API（/v1/…）を GET する */
export async function rest<T>(path: string, params: Record<string, string | number | undefined | null> = {}): Promise<T> {
  const query = new URLSearchParams({ path });
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") query.set(k, String(v));
  }
  return (await parse(await fetch(`/api/rest?${query}`, { headers: HEADERS }))) as T;
}

export type Binding = Record<string, { type: string; value: string; "xml:lang"?: string }>;

let prefixHeader = "";

export function setPrefixes(prefixes: Record<string, string>) {
  const all = {
    ...prefixes,
    rdf: "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    rdfs: "http://www.w3.org/2000/01/rdf-schema#",
  };
  prefixHeader = Object.entries(all)
    .map(([p, ns]) => `PREFIX ${p}: <${ns}>\n`)
    .join("");
}

/** SPARQL（SELECT）を実行して行を返す。PREFIX は自動で付く */
export async function sparql(query: string): Promise<{ vars: string[]; rows: Binding[] }> {
  const res = await fetch("/api/sparql", {
    method: "POST",
    headers: { ...HEADERS, "Content-Type": "application/sparql-query", Accept: "application/sparql-results+json" },
    body: prefixHeader + query,
  });
  const body = (await parse(res)) as { head: { vars: string[] }; results?: { bindings: Binding[] }; boolean?: boolean };
  if (body.results === undefined) return { vars: ["result"], rows: [{ result: { type: "literal", value: String(body.boolean) } }] };
  return { vars: body.head.vars, rows: body.results.bindings };
}

// ---------- ③ AI エージェント ----------

export async function getAgentStatus(): Promise<AgentStatus> {
  return (await parse(await fetch("/api/agent", { headers: HEADERS }))) as AgentStatus;
}

/** 質問を送り、答えの途中経過（1 行 1 JSON）を届いた順に onEvent に渡す。signal で中止できる */
export async function askAgent(
  body: { question: string; history: { role: "user" | "assistant"; text: string }[]; model: string },
  onEvent: (event: AgentEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/agent/ask", {
    method: "POST",
    headers: { ...HEADERS, "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    await parse(res);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline: number;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) onEvent(JSON.parse(line) as AgentEvent);
    }
  }
}

export async function cancelAgent(): Promise<void> {
  await fetch("/api/agent/cancel", { method: "POST", headers: HEADERS }).catch(() => undefined);
}
