// ② 3D 工場。配置はナレッジグラフ（layout.ttl → /v1/factory の layout）、状態と値は replay の最新値から作る
import { CameraControls, Environment, Lightformer } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { Bloom, EffectComposer, N8AO, ToneMapping } from "@react-three/postprocessing";
import { Eye, Focus, Moon, Route, Sparkles, Sun, Tag } from "lucide-react";
import { ToneMappingMode } from "postprocessing";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../store";
import { Building } from "./Building";
import { MachineObject } from "./Machines";
import { buildScene, STATE_COLORS, STATE_LABELS, type Machine, type Scene } from "./model";

type Quality = "standard" | "high";

/** 画面の設定（画質・昼夜・ラベル）は、この PC のブラウザにだけ覚えておく */
function usePreference<T extends string>(key: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      return (localStorage.getItem(`factory-app.${key}`) as T | null) ?? initial;
    } catch {
      return initial;
    }
  });
  const set = (v: T) => {
    setValue(v);
    try {
      localStorage.setItem(`factory-app.${key}`, v);
    } catch {
      /* 保存できなくても動く */
    }
  };
  return [value, set];
}

export default function Factory3D() {
  const { tree, equipment, details, selection, highlighted, select, loadAllDetails, refreshedAt } = useApp();
  const [quality, setQuality] = usePreference<Quality>("quality", "high");
  const [time, setTime] = usePreference<"day" | "night">("time", "day");
  const [labels, setLabels] = usePreference<"on" | "off">("labels", "on");
  const controls = useRef<CameraControls>(null);

  // ② を開いたら全設備の最新値を読む（以降は 1 分ごとの自動更新で入れ替わる）
  useEffect(() => {
    loadAllDetails().catch(() => undefined);
  }, [loadAllDetails, refreshedAt, equipment.length]);

  const scene = useMemo(() => buildScene(tree, equipment, details), [tree, equipment, details]);
  const selectedId = selection?.kind === "equipment" ? selection.id : selection?.kind === "property" ? selection.equipment : null;
  const selected = scene.machines.find((m) => m.id === selectedId);
  const night = time === "night";

  const view = {
    overview: () => controls.current?.setLookAt(0, scene.area.h * 2.1, scene.area.d * 1.1, 0, 0, -1.5, true),
    line: () => {
      const line = scene.lines[0];
      if (!line) return;
      const { x, z, w } = line.box;
      controls.current?.setLookAt(x - w / 2 - 3, 2.4, z + 4.5, x + w / 4, 1, z, true);
    },
    machine: (m: Machine) => {
      const { x, z, h } = m.box;
      controls.current?.setLookAt(x + 3.5, h + 2.6, z + 6, x, h * 0.5, z, true);
    },
  };

  const counts = scene.machines
    .filter((m) => m.hasTower)
    .reduce<Record<string, number>>((acc, m) => ({ ...acc, [m.state]: (acc[m.state] ?? 0) + 1 }), {});
  const unplaced = scene.machines.filter((m) => !m.placed).length;

  return (
    <div className="relative min-h-0 flex-1 bg-[#05080f]">
      <div className="absolute inset-0">
        <Canvas
          shadows="percentage"
          dpr={quality === "high" ? [1, 2] : 1}
          camera={{ position: [0, scene.area.h * 2.1, scene.area.d * 1.1], fov: 45, near: 0.1, far: 300 }}
          gl={{ antialias: quality !== "high", powerPreference: "high-performance" }}
          onCreated={({ camera }) => camera.lookAt(0, 0, -1.5)}
        >
          <color attach="background" args={[night ? "#04070d" : "#111a29"]} />
          <fog attach="fog" args={[night ? "#04070d" : "#111a29", 40, 110]} />
          <Suspense fallback={null}>
            <World scene={scene} night={night} quality={quality} />
            {scene.machines.map((m) => (
              <MachineObject
                key={m.id}
                m={m}
                selected={m.id === selectedId}
                highlighted={highlighted.includes(m.id)}
                showLabel={labels === "on"}
                onSelect={(id) => select({ kind: "equipment", id })}
              />
            ))}
            {quality === "high" && (
              <EffectComposer multisampling={4}>
                <N8AO aoRadius={1.2} intensity={2.2} distanceFalloff={1} />
                <Bloom mipmapBlur luminanceThreshold={1} intensity={night ? 1.2 : 0.7} />
                <ToneMapping mode={ToneMappingMode.ACES_FILMIC} />
              </EffectComposer>
            )}
          </Suspense>
          <CameraControls ref={controls} makeDefault minDistance={2} maxDistance={70} maxPolarAngle={Math.PI / 2.08} />
          <FollowSelection machine={selected} fly={view.machine} />
        </Canvas>
      </div>

      {/* 視点 */}
      <div className="absolute top-3 left-3 flex gap-1.5">
        <button className="btn" onClick={view.overview}><Eye size={14} />全体</button>
        <button className="btn" onClick={view.line} disabled={!scene.lines.length}><Route size={14} />ラインに沿って</button>
        <button className="btn" onClick={() => selected && view.machine(selected)} disabled={!selected}><Focus size={14} />選択中の設備</button>
      </div>
      {/* 表示の設定 */}
      <div className="absolute top-3 right-3 flex gap-1.5">
        <button className="btn" onClick={() => setTime(night ? "day" : "night")} title="夜間・休日は照明を落とす">
          {night ? <Moon size={14} /> : <Sun size={14} />}
          {night ? "夜間" : "昼間"}
        </button>
        <button className="btn" onClick={() => setQuality(quality === "high" ? "standard" : "high")} title="高画質: 影・光のにじみ・環境光の遮蔽（PC が重いときは標準に）">
          <Sparkles size={14} />
          {quality === "high" ? "高画質" : "標準"}
        </button>
        <button className="btn" onClick={() => setLabels(labels === "on" ? "off" : "on")}>
          <Tag size={14} />
          ラベル{labels === "on" ? "あり" : "なし"}
        </button>
      </div>
      {/* 凡例と集計 */}
      <div className="pointer-events-none absolute bottom-3 left-3 flex flex-wrap items-center gap-3 rounded-lg bg-slate-950/70 px-3 py-2 text-[11px] text-slate-300 backdrop-blur">
        <span className="font-semibold text-slate-200">信号灯</span>
        {(["running", "alarm", "stopped"] as const).map((s) => (
          <span key={s} className="flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: STATE_COLORS[s] }} />
            {STATE_LABELS[s]} {counts[s] ?? 0}
          </span>
        ))}
        <span className="text-slate-400">緑 = 稼働、赤の点滅 = 異常停止、消灯 = 停止 ／ ドラッグで回転・ホイールで拡大・右ドラッグで移動</span>
        {unplaced > 0 && <span className="text-amber-300">配置が未登録の設備 {unplaced} 台は仮の位置</span>}
      </div>
    </div>
  );
}

/** 選んだ設備（① や ③ で選んだものも）に視点を寄せる。
 *  Canvas の中に置くのは、CameraControls ができてから動かすため（Canvas の外の effect の時点ではまだ無い） */
function FollowSelection({ machine, fly }: { machine?: Machine; fly(m: Machine): void }) {
  const ready = useThree((s) => s.controls) !== null;
  const id = machine?.id;
  useEffect(() => {
    if (ready && machine) fly(machine);
    // 値の更新（machine の中身が変わる）では動かさず、選び直したときだけ動かす
  }, [ready, id]);
  return null;
}

/** 光・環境（映り込み）・建屋 */
function World({ scene, night, quality }: { scene: Scene; night: boolean; quality: Quality }) {
  const { w, d, h } = scene.area;
  const shadowSize = quality === "high" ? 4096 : 1024;
  return (
    <>
      <hemisphereLight args={[night ? "#3b4a66" : "#dfe8f5", night ? "#0b0e14" : "#4a4f57", night ? 0.25 : 0.9]} />
      {/* 天井からの主光（影を落とす） */}
      <directionalLight
        position={[-6, h * 3, 8]}
        intensity={night ? 0.5 : 2.4}
        color={night ? "#b9c6e0" : "#fff4e6"}
        castShadow
        shadow-mapSize={[shadowSize, shadowSize]}
        shadow-bias={-0.0004}
        shadow-normalBias={0.03}
        shadow-camera-left={-w / 2 - 2}
        shadow-camera-right={w / 2 + 2}
        shadow-camera-top={d / 2 + 2}
        shadow-camera-bottom={-d / 2 - 2}
        shadow-camera-near={1}
        shadow-camera-far={h * 6}
      />
      {/* 北の高窓からの光 */}
      {!night && <directionalLight position={[4, h, -d]} intensity={0.6} color="#dbeafe" />}
      {/* 映り込み用の環境（外部の画像は使わず、光の板で作る） */}
      <Environment resolution={256} frames={1}>
        <Lightformer form="rect" intensity={night ? 0.6 : 2.2} position={[0, 6, 0]} rotation-x={Math.PI / 2} scale={[20, 6, 1]} />
        <Lightformer form="rect" intensity={night ? 0.1 : 1.2} color="#cfe3ff" position={[0, 3, -9]} scale={[20, 2, 1]} />
        <Lightformer form="rect" intensity={0.4} position={[-10, 2, 0]} rotation-y={Math.PI / 2} scale={[10, 2, 1]} />
      </Environment>
      <Building w={w} d={d} h={h} label={scene.area.label} lines={scene.lines} night={night} />
    </>
  );
}
