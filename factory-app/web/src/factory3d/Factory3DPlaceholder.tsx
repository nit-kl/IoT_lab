import { Box } from "lucide-react";

/** ② 3D 工場（ステップ 2 で実装） */
export function Factory3DPlaceholder() {
  return (
    <div className="grid h-full place-items-center">
      <div className="max-w-md text-center">
        <div className="mx-auto mb-3 grid h-14 w-14 place-items-center rounded-2xl bg-cyan-500/10 text-cyan-500 ring-1 ring-cyan-500/30">
          <Box size={28} />
        </div>
        <div className="text-lg font-bold">3D 工場ビューア</div>
        <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
          ステップ 2 で実装します。設備の配置をナレッジグラフから読み、積層信号灯・設備の動き・計測値の表示で工場の今を 3D で表します。
        </p>
      </div>
    </div>
  );
}
