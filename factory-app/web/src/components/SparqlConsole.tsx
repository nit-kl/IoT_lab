import { Play, TerminalSquare } from "lucide-react";
import { useState } from "react";
import { sparql, type Binding } from "../api";
import { shorten } from "../format";
import { useApp } from "../store";

const EXAMPLE = `SELECT ?equipment ?label ?category WHERE {
  ?e xf:equipmentId ?equipment ; rdfs:label ?label ;
     s4inma:belongsToCategory/skos:prefLabel ?category .
  FILTER (LANG(?label) = "ja" && LANG(?category) = "ja")
} ORDER BY ?equipment`;

/** SPARQL を直接実行する欄（上級者向け。PREFIX は自動で付く。更新は KG 側で拒否される） */
export function SparqlConsole() {
  // セレクタで毎回新しいオブジェクト（?? {}）を返すと無限に再描画されるので、外で補う
  const config = useApp((s) => s.config);
  const prefixes = config?.prefixes ?? {};
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(EXAMPLE);
  const [result, setResult] = useState<{ vars: string[]; rows: Binding[] } | null>(null);
  const [info, setInfo] = useState("");

  const run = async () => {
    setInfo("実行中…");
    const started = performance.now();
    try {
      const r = await sparql(text);
      setResult(r);
      setInfo(`${r.rows.length} 行（${Math.round(performance.now() - started)} ms）`);
    } catch (e) {
      setResult(null);
      setInfo((e as Error).message);
    }
  };

  return (
    <div className="border-t border-slate-200 dark:border-ink-800">
      <button className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-slate-500 hover:text-cyan-600 dark:text-slate-400 dark:hover:text-cyan-300" onClick={() => setOpen(!open)}>
        <TerminalSquare size={13} /> SPARQL を直接実行する（上級者向け） {open ? "▾" : "▸"}
      </button>
      {open && (
        <div className="space-y-2 px-3 pb-3">
          <textarea className="input h-28 w-full font-mono text-xs" spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} />
          <div className="flex items-center gap-3">
            <button className="btn" onClick={run}><Play size={13} />実行</button>
            <span className="text-xs text-slate-500 dark:text-slate-400">{info}</span>
          </div>
          {result && (
            <div className="thin-scroll max-h-48 overflow-auto">
              <table className="data-table">
                <thead><tr>{result.vars.map((v) => <th key={v}>{v}</th>)}</tr></thead>
                <tbody>
                  {result.rows.map((r, i) => (
                    <tr key={i}>{result.vars.map((v) => <td key={v} className="font-mono text-xs">{r[v] ? shorten(r[v].value, prefixes) : ""}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
