// ③ AI エージェント。質問を送ると、サーバーが Bedrock の Claude と MCP のツールで調べて答える（答えは届いた順に表示）
import { AlertTriangle, Bot, ChevronDown, ChevronRight, Coins, Maximize2, Minimize2, Plus, Send, Sparkles, Square, Wrench } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { askAgent, cancelAgent } from "../api";
import { useApp } from "../store";
import type { AgentEvent, AgentUsage } from "../types";

interface ToolCall {
  id: string;
  name: string;
  input: unknown;
  preview?: string;
  error?: boolean;
}

interface Turn {
  question: string;
  answer: string;
  tools: ToolCall[];
  highlighted: string[];
  usage?: AgentUsage;
  notice?: string | null;
  error?: string;
  state: "running" | "done" | "error" | "cancelled";
}

const EXAMPLES = ["CNC-01 の今の状態は？", "14 時台に異常停止した設備は？", "ライン A の設備と主な計測項目を一覧にして", "コンプレッサの吐出圧力は正常？"];

const yen = (v: number | undefined, digits = 2) => (v === undefined ? "—" : `${v.toFixed(digits)} 円`);

export function AgentDock() {
  const { agent, loadAgent, highlight, select } = useApp();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [model, setModel] = useState<string>("");
  const [expanded, setExpanded] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const running = turns.at(-1)?.state === "running";

  useEffect(() => {
    loadAgent().catch(() => undefined);
  }, [loadAgent]);
  useEffect(() => {
    if (!model && agent?.default) setModel(agent.default);
  }, [agent, model]);
  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [turns]);

  const update = (fn: (t: Turn) => Turn) => setTurns((prev) => [...prev.slice(0, -1), fn(prev.at(-1)!)]);

  const onEvent = (e: AgentEvent) => {
    switch (e.type) {
      case "text":
        update((t) => ({ ...t, answer: t.answer + e.delta }));
        break;
      case "tool_start":
        update((t) => ({ ...t, tools: [...t.tools, { id: e.id, name: e.name, input: e.input }] }));
        break;
      case "tool_end":
        update((t) => ({ ...t, tools: t.tools.map((c) => (c.id === e.id ? { ...c, preview: e.preview, error: e.error } : c)) }));
        break;
      case "highlight":
        highlight(e.ids);
        // 1 台だけなら選んで、② の視点もその設備へ
        if (e.ids.length === 1) select({ kind: "equipment", id: e.ids[0]! });
        update((t) => ({ ...t, highlighted: e.ids }));
        break;
      case "done":
        update((t) => ({ ...t, usage: e.usage, notice: e.notice, state: "done" }));
        break;
      case "error":
        update((t) => ({ ...t, error: e.message, state: "error" }));
        break;
      case "cancelled":
        update((t) => ({ ...t, state: "cancelled" }));
        break;
    }
  };

  const send = async (text?: string) => {
    const question = (text ?? input).trim();
    if (!question || running || !agent?.enabled) return;
    // これまでの会話（答えが出たものだけ）を一緒に送る。会話は画面を開いている間だけ覚える
    const history = turns
      .filter((t) => t.state === "done" && t.answer)
      .flatMap((t) => [
        { role: "user" as const, text: t.question },
        { role: "assistant" as const, text: t.answer },
      ]);
    setTurns((prev) => [...prev, { question, answer: "", tools: [], highlighted: [], state: "running" }]);
    setInput("");
    setExpanded(true);
    const controller = new AbortController();
    abort.current = controller;
    try {
      await askAgent({ question, history, model }, onEvent, controller.signal);
      update((t) => (t.state === "running" ? { ...t, state: "done" } : t));
    } catch (err) {
      const aborted = (err as Error).name === "AbortError";
      update((t) => ({ ...t, state: aborted ? "cancelled" : "error", error: aborted ? undefined : (err as Error).message }));
    } finally {
      abort.current = null;
      loadAgent().catch(() => undefined);
    }
  };

  const stop = () => {
    void cancelAgent();
    abort.current?.abort();
  };

  const reset = () => {
    setTurns([]);
    highlight([]);
  };

  const limits = agent?.limits;
  const models = agent?.models.filter((m) => m.allowed) ?? [];
  const nearBudget = !!limits && (agent!.spent.today ?? 0) >= limits.daily_yen * 0.8;

  return (
    <section className={`panel flex flex-col p-3 transition-[height] ${expanded ? "h-[42vh]" : "h-56"}`}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Bot size={16} className="text-cyan-500" />
        <div className="panel-title">③ AI エージェント</div>
        {models.length > 0 && (
          <select className="input !py-1 text-xs" value={model} onChange={(e) => setModel(e.target.value)} disabled={running} title="使えるモデルは config/agent.toml の allowed で決まる">
            {models.map((m) => (
              <option key={m.key} value={m.key}>
                {m.label}（入力 ${m.price.input}・出力 ${m.price.output} / 100 万トークン）
              </option>
            ))}
          </select>
        )}
        {limits && (
          <span className={`chip gap-1 ${nearBudget ? "!text-amber-500" : ""}`} title="この PC で使った分の目安（Bedrock が返したトークン数 × 単価）。正確な額は AWS の請求で確認">
            <Coins size={12} />
            今日 {yen(agent!.spent.today)} / {limits.daily_yen} 円 ・ 今月 {yen(agent!.spent.month)} / {limits.monthly_yen.toLocaleString()} 円
          </span>
        )}
        {limits && <span className="chip">1 回の上限 {limits.per_question_yen} 円・ツール {limits.max_tool_calls} 回</span>}
        <div className="ml-auto flex gap-1.5">
          <button className="btn !py-1 text-xs" onClick={reset} disabled={running || turns.length === 0} title="会話と強調を消す">
            <Plus size={13} />新しい会話
          </button>
          <button className="btn !px-2 !py-1" onClick={() => setExpanded(!expanded)} title={expanded ? "小さくする" : "大きくする"}>
            {expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
          </button>
        </div>
      </div>

      <div ref={scroller} className="thin-scroll min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
        {agent && !agent.enabled && (
          <div className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-sm text-amber-700 dark:text-amber-300">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            {agent.reason}
          </div>
        )}
        {turns.length === 0 && agent?.enabled && (
          <div className="text-sm text-slate-500 dark:text-slate-400">
            工場について日本語で質問すると、ナレッジグラフと計測値を MCP のツールで調べて答えます。答えに出た設備は ①・② で強調されます。
            <div className="mt-2 flex flex-wrap gap-1.5">
              {EXAMPLES.map((q) => (
                <button key={q} className="chip cursor-pointer hover:!border-cyan-400 hover:!text-cyan-500" onClick={() => void send(q)}>
                  <Sparkles size={11} />
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t, i) => (
          <TurnView key={i} turn={t} onSelect={(id) => select({ kind: "equipment", id })} />
        ))}
      </div>

      <form
        className="mt-2 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <input
          className="input flex-1"
          placeholder={agent?.enabled ? "質問を入力…（例: PRESS-01 の油圧の最大はいつ？）" : "使えません"}
          value={input}
          maxLength={2000}
          onChange={(e) => setInput(e.target.value)}
          disabled={!agent?.enabled}
        />
        {running ? (
          <button type="button" className="btn !border-red-500/50 !text-red-500" onClick={stop}>
            <Square size={13} />中止
          </button>
        ) : (
          <button className="btn" disabled={!agent?.enabled || !input.trim()}>
            <Send size={14} />送信
          </button>
        )}
      </form>
    </section>
  );
}

function TurnView({ turn, onSelect }: { turn: Turn; onSelect: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const u = turn.usage;
  return (
    <div className="space-y-1.5">
      <div className="ml-auto w-fit max-w-[80%] rounded-2xl rounded-br-sm bg-cyan-500/15 px-3 py-1.5 text-sm text-cyan-900 dark:text-cyan-100">{turn.question}</div>
      <div className="max-w-[92%] rounded-2xl rounded-bl-sm border border-slate-200 bg-white/60 px-3 py-2 text-sm dark:border-ink-700 dark:bg-ink-850/60">
        {turn.answer ? (
          <div className="whitespace-pre-wrap leading-relaxed">{turn.answer}</div>
        ) : turn.state === "running" ? (
          <div className="text-slate-500">{turn.tools.length ? `調べています…（${turn.tools.at(-1)!.name}）` : "考えています…"}</div>
        ) : null}
        {turn.state === "running" && turn.answer && <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-cyan-400 align-middle" />}
        {turn.error && <div className="mt-1 text-red-500">{turn.error}</div>}
        {turn.state === "cancelled" && <div className="mt-1 text-xs text-slate-500">中止しました（それまでの分の費用は記録されています）</div>}
        {turn.notice && <div className="mt-1 text-xs text-amber-600 dark:text-amber-300">{turn.notice}</div>}

        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-slate-500 dark:text-slate-400">
          {turn.tools.length > 0 && (
            <button className="flex items-center gap-1 hover:text-cyan-500" onClick={() => setOpen(!open)}>
              {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              <Wrench size={11} />使ったツール（{turn.tools.length}）
            </button>
          )}
          {turn.highlighted.length > 0 && (
            <span className="flex items-center gap-1">
              強調:
              {turn.highlighted.map((id) => (
                <button key={id} className="rounded bg-amber-400/20 px-1.5 font-mono text-amber-700 hover:bg-amber-400/30 dark:text-amber-300" onClick={() => onSelect(id)}>
                  {id}
                </button>
              ))}
            </span>
          )}
          {u && (
            <span className="ml-auto font-mono" title={`Bedrock の呼び出し ${u.calls} 回・ツール ${u.tool_calls} 回`}>
              {u.yen.toFixed(3)} 円 ・ 入力 {u.inputTokens.toLocaleString()}（キャッシュ読込 {u.cacheReadInputTokens.toLocaleString()}・書込 {u.cacheWriteInputTokens.toLocaleString()}）・ 出力 {u.outputTokens.toLocaleString()} トークン
            </span>
          )}
        </div>
        {open && (
          <div className="mt-1.5 space-y-1.5">
            {turn.tools.map((c) => (
              <details key={c.id} className="rounded-md border border-slate-200 px-2 py-1 text-xs dark:border-ink-700">
                <summary className="cursor-pointer font-mono">
                  <span className={c.error ? "text-red-500" : "text-cyan-600 dark:text-cyan-300"}>{c.name}</span>{" "}
                  <span className="text-slate-500">{JSON.stringify(c.input)}</span>
                  {c.preview === undefined && " …"}
                </summary>
                {c.preview !== undefined && <pre className="thin-scroll mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-[11px] text-slate-600 dark:text-slate-300">{c.preview}</pre>}
              </details>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
