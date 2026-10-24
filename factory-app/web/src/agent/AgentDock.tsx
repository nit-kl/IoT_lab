import { Bot, Send } from "lucide-react";

/** ③ AI エージェント（ステップ 3 で実装。費用の上限つきで Bedrock + MCP を使う） */
export function AgentDock() {
  return (
    <section className="panel flex flex-col p-3">
      <div className="mb-2 flex items-center gap-2">
        <Bot size={16} className="text-cyan-500" />
        <div className="panel-title">③ AI エージェント</div>
        <span className="chip ml-auto">ステップ 3 で実装</span>
      </div>
      <div className="flex-1 rounded-lg border border-dashed border-slate-300 p-3 text-sm text-slate-500 dark:border-ink-700 dark:text-slate-400">
        工場について日本語で質問すると、MCP のツールで調べて答えます（例:「14 時台に異常停止した設備は？」）。答えに出た設備は ①・② で強調されます。
      </div>
      <div className="mt-2 flex gap-2">
        <input className="input flex-1" placeholder="質問を入力…（準備中）" disabled />
        <button className="btn" disabled><Send size={14} />送信</button>
      </div>
    </section>
  );
}
