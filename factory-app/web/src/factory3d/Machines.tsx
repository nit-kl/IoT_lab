// 設備の 3D モデル（分類ごとに部品を組み合わせて作る）と、データに連動した動き・積層信号灯・ラベル
import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { STATE_COLORS, STATE_LABELS, type Machine, type MachineState } from "./model";
import { beltTexture } from "./textures";

// 塗装・金属の材質（PBR）
const PAINT = { blue: "#2f5f98", green: "#3f6f55", ivory: "#d9dcd6", gray: "#3a3f47", red: "#a8322a" };
const metal = { color: "#9aa3ad", metalness: 0.85, roughness: 0.32 } as const;
const darkMetal = { color: "#41474f", metalness: 0.7, roughness: 0.45 } as const;

interface MachineProps {
  m: Machine;
  selected: boolean;
  highlighted: boolean;
  showLabel: boolean;
  onSelect(id: string): void;
}

/** 設備 1 台（本体 + 床の状態表示 + 信号灯 + 選択枠 + ラベル） */
export function MachineObject({ m, selected, highlighted, showLabel, onSelect }: MachineProps) {
  const group = useRef<THREE.Group>(null);
  const [hovered, setHovered] = useState(false);
  const { x, z, w, d, h, rot } = m.box;

  // 本体の部品はすべて影を落とし・受ける
  useLayoutEffect(() => {
    group.current?.traverse((o) => {
      if ((o as THREE.Mesh).isMesh && !o.userData.noShadow) {
        o.castShadow = true;
        o.receiveShadow = true;
      }
    });
  });
  useEffect(() => () => void (document.body.style.cursor = ""), []);

  const Body = BODIES[m.category] ?? GenericBody;
  return (
    <group position={[x, 0, z]} rotation={[0, rot, 0]}>
      <group
        ref={group}
        onClick={(e) => {
          e.stopPropagation();
          onSelect(m.id);
        }}
        onPointerOver={(e) => {
          e.stopPropagation();
          setHovered(true);
          document.body.style.cursor = "pointer";
        }}
        onPointerOut={() => {
          setHovered(false);
          document.body.style.cursor = "";
        }}
      >
        <Body m={m} />
      </group>
      <StatusPad w={w} d={d} state={m.state} />
      {(selected || highlighted || hovered) && <Frame w={w} d={d} h={h} kind={selected ? "selected" : highlighted ? "highlighted" : "hover"} />}
      {showLabel && (
        <Html position={[0, h + 0.9, 0]} center distanceFactor={16} zIndexRange={[20, 0]}>
          <button
            type="button"
            onClick={() => onSelect(m.id)}
            className={`flex min-w-36 flex-col items-start rounded-lg border px-2.5 py-1.5 text-left whitespace-nowrap shadow-lg backdrop-blur ${
              selected ? "border-cyan-400 bg-slate-900/90" : highlighted ? "border-amber-400 bg-slate-900/90" : "border-white/15 bg-slate-900/75"
            }`}
          >
            <span className="flex items-center gap-1.5 text-[13px] font-bold text-white">
              <span className={`h-2 w-2 rounded-full ${m.state === "alarm" ? "alarm-pulse" : ""}`} style={{ background: STATE_COLORS[m.state] }} />
              {m.id}
              <span className="font-normal text-slate-300">{m.label.replace(m.id, "").trim() || m.categoryLabel}</span>
            </span>
            {m.readouts.map((r) => (
              <span key={r.label} className="flex w-full justify-between gap-3 text-[12px] text-slate-300">
                <span>{r.label}</span>
                <span className="font-mono font-semibold text-cyan-200">{r.text}</span>
              </span>
            ))}
            {m.hasTower && <span className="text-[11px]" style={{ color: STATE_COLORS[m.state] }}>{STATE_LABELS[m.state]}</span>}
            {!m.placed && <span className="text-[11px] text-amber-300">配置未登録（仮の位置）</span>}
          </button>
        </Html>
      )}
    </group>
  );
}

/** 床に設備の状態の色を薄く表示（異常停止は点滅） */
function StatusPad({ w, d, state }: { w: number; d: number; state: MachineState }) {
  const mat = useRef<THREE.MeshBasicMaterial>(null);
  useFrame(({ clock }) => {
    if (!mat.current) return;
    mat.current.opacity = state === "alarm" ? 0.18 + 0.17 * Math.max(0, Math.sin(clock.elapsedTime * 6)) : state === "running" ? 0.16 : 0.08;
  });
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.015, 0]} userData={{ noShadow: true }}>
      <planeGeometry args={[w + 0.8, d + 0.8]} />
      <meshBasicMaterial ref={mat} color={STATE_COLORS[state]} transparent depthWrite={false} toneMapped={false} />
    </mesh>
  );
}

/** 選択枠（水色）・エージェントの強調（琥珀色・点滅）・カーソルを乗せたとき（白） */
function Frame({ w, d, h, kind }: { w: number; d: number; h: number; kind: "selected" | "highlighted" | "hover" }) {
  const geometry = useMemo(() => new THREE.EdgesGeometry(new THREE.BoxGeometry(w + 0.4, h + 0.3, d + 0.4)), [w, d, h]);
  const mat = useRef<THREE.LineBasicMaterial>(null);
  useFrame(({ clock }) => {
    if (mat.current && kind === "highlighted") mat.current.opacity = 0.45 + 0.55 * Math.abs(Math.sin(clock.elapsedTime * 3));
  });
  const color = kind === "selected" ? "#22d3ee" : kind === "highlighted" ? "#fbbf24" : "#e2e8f0";
  return (
    <lineSegments geometry={geometry} position={[0, (h + 0.3) / 2, 0]}>
      <lineBasicMaterial ref={mat} color={color} transparent opacity={kind === "hover" ? 0.5 : 1} toneMapped={false} />
    </lineSegments>
  );
}

/** 積層信号灯（上から赤・黄・緑）。稼働 = 緑、異常停止 = 赤の点滅、停止 = 消灯 */
export function SignalTower({ state, position }: { state: MachineState; position: [number, number, number] }) {
  const red = useRef<THREE.MeshStandardMaterial>(null);
  useFrame(({ clock }) => {
    if (red.current) red.current.emissiveIntensity = state === "alarm" ? (Math.sin(clock.elapsedTime * 9) > 0 ? 7 : 0.15) : 0;
  });
  const lamps: { color: string; on: boolean; ref?: React.RefObject<THREE.MeshStandardMaterial | null> }[] = [
    { color: "#ff3b30", on: false, ref: red },
    { color: "#ffb020", on: state === "unknown" },
    { color: "#22e36a", on: state === "running" },
  ];
  return (
    <group position={position}>
      <mesh position={[0, 0.12, 0]}>
        <cylinderGeometry args={[0.025, 0.025, 0.24, 12]} />
        <meshStandardMaterial {...metal} />
      </mesh>
      {lamps.map((l, i) => (
        <mesh key={l.color} position={[0, 0.24 + 0.05 + (2 - i) * 0.1, 0]}>
          <cylinderGeometry args={[0.065, 0.065, 0.09, 24]} />
          <meshStandardMaterial
            ref={l.ref}
            color={l.on ? l.color : new THREE.Color(l.color).multiplyScalar(0.35)}
            emissive={l.color}
            emissiveIntensity={l.on ? 5 : 0}
            roughness={0.25}
            transparent
            opacity={0.92}
            toneMapped={false}
          />
        </mesh>
      ))}
      <mesh position={[0, 0.24 + 0.36, 0]}>
        <cylinderGeometry args={[0.07, 0.07, 0.03, 24]} />
        <meshStandardMaterial {...darkMetal} />
      </mesh>
    </group>
  );
}

// ---------- 分類ごとの本体 ----------

type BodyProps = { m: Machine };

/** プレス機: 左右のフレーム・クラウン・ボルスタ、ショット数に合わせて上下するラム */
function PressBody({ m }: BodyProps) {
  const { w, d, h } = m.box;
  const ram = useRef<THREE.Group>(null);
  const phase = useRef(0);
  const base = 0.9;
  const ramH = 0.55;
  const top = h - 0.7 - ramH / 2 - 0.05;
  const bottom = base + 0.25 + ramH / 2 + 0.05;
  useFrame((_, dt) => {
    if (!ram.current) return;
    const shots = m.values.shot_count ?? 0;
    if (m.state === "running") phase.current += dt * Math.PI * 2 * Math.max(shots / 60, 0.2);
    const y =
      m.state === "running" ? top - (top - bottom) * (0.5 - 0.5 * Math.cos(phase.current)) : m.state === "alarm" ? top - (top - bottom) * 0.55 : top;
    ram.current.position.y = y;
  });
  return (
    <group>
      <mesh position={[0, base / 2, 0]}>
        <boxGeometry args={[w * 0.92, base, d * 0.88]} />
        <meshStandardMaterial color={PAINT.gray} roughness={0.6} metalness={0.3} />
      </mesh>
      <mesh position={[0, base + 0.1, 0]}>
        <boxGeometry args={[w * 0.6, 0.2, d * 0.6]} />
        <meshStandardMaterial {...metal} />
      </mesh>
      {[-1, 1].map((s) => (
        <mesh key={s} position={[s * (w / 2 - 0.28), h / 2, 0]}>
          <boxGeometry args={[0.5, h, d * 0.8]} />
          <meshStandardMaterial color={PAINT.blue} roughness={0.45} metalness={0.25} />
        </mesh>
      ))}
      <mesh position={[0, h - 0.35, 0]}>
        <boxGeometry args={[w, 0.7, d * 0.86]} />
        <meshStandardMaterial color={PAINT.blue} roughness={0.45} metalness={0.25} />
      </mesh>
      {/* 油圧シリンダ */}
      <mesh position={[0, h - 0.7 - 0.1, 0]}>
        <cylinderGeometry args={[0.16, 0.16, 0.4, 24]} />
        <meshStandardMaterial {...metal} />
      </mesh>
      <group ref={ram} position={[0, top, 0]}>
        <mesh>
          <boxGeometry args={[w * 0.6, ramH, d * 0.58]} />
          <meshStandardMaterial color={PAINT.blue} roughness={0.4} metalness={0.3} />
        </mesh>
        <mesh position={[0, -ramH / 2 - 0.06, 0]}>
          <boxGeometry args={[w * 0.5, 0.12, d * 0.5]} />
          <meshStandardMaterial {...metal} />
        </mesh>
      </group>
      {/* 光線式安全装置の柱（黄） */}
      {[-1, 1].map((s) => (
        <mesh key={`lc${s}`} position={[s * (w * 0.33), base + 0.6, d * 0.42]}>
          <boxGeometry args={[0.06, 1.2, 0.06]} />
          <meshStandardMaterial color="#e0b000" roughness={0.5} />
        </mesh>
      ))}
      <ControlPanel position={[w / 2 + 0.35, 0, d * 0.25]} />
      <SignalTower state={m.state} position={[w / 2 - 0.28, h, -d * 0.25]} />
    </group>
  );
}

/** CNC 加工機: 窓付きの筐体（稼働中は中が光り、主軸が回る）と操作盤 */
function CncBody({ m }: BodyProps) {
  const { w, d, h } = m.box;
  const glow = useRef<THREE.MeshStandardMaterial>(null);
  const spindle = useRef<THREE.Mesh>(null);
  useFrame(({ clock }, dt) => {
    const load = m.values.spindle_load ?? 0;
    const rpm = m.values.spindle_speed ?? 0;
    if (glow.current) {
      glow.current.emissive.set(m.state === "alarm" ? "#ff4030" : "#7dd3fc");
      glow.current.emissiveIntensity =
        m.state === "running" ? 0.5 + (load / 100) * 2.2 + 0.15 * Math.sin(clock.elapsedTime * 17) : m.state === "alarm" ? 1.2 : 0.05;
    }
    if (spindle.current && m.state === "running" && rpm > 0) spindle.current.rotation.y += dt * (6 + rpm / 600);
  });
  const winW = w * 0.46;
  const winH = h * 0.38;
  const front = d / 2;
  return (
    <group>
      <mesh position={[0, 0.15, 0]}>
        <boxGeometry args={[w, 0.3, d]} />
        <meshStandardMaterial color={PAINT.gray} roughness={0.6} metalness={0.3} />
      </mesh>
      <mesh position={[0, 0.3 + (h * 0.88 - 0.3) / 2, 0]}>
        <boxGeometry args={[w, h * 0.88 - 0.3, d]} />
        <meshStandardMaterial color={PAINT.ivory} roughness={0.55} metalness={0.1} />
      </mesh>
      {/* 上部の帯（機械の名前の入る部分） */}
      <mesh position={[0, h * 0.94, -0.1]}>
        <boxGeometry args={[w * 0.96, h * 0.12, d - 0.2]} />
        <meshStandardMaterial color={PAINT.blue} roughness={0.45} metalness={0.25} />
      </mesh>
      {/* 窓: 中の光 → 主軸 → ガラス → 枠 */}
      <mesh position={[-w * 0.12, h * 0.52, front + 0.005]}>
        <planeGeometry args={[winW, winH]} />
        <meshStandardMaterial ref={glow} color="#0b1220" emissive="#7dd3fc" emissiveIntensity={0} toneMapped={false} />
      </mesh>
      <mesh ref={spindle} position={[-w * 0.12, h * 0.6, front + 0.06]}>
        <cylinderGeometry args={[0.07, 0.04, 0.3, 6]} />
        <meshStandardMaterial {...metal} />
      </mesh>
      <mesh position={[-w * 0.12, h * 0.52, front + 0.12]} userData={{ noShadow: true }}>
        <planeGeometry args={[winW, winH]} />
        <meshStandardMaterial color="#a5d8ff" transparent opacity={0.22} roughness={0.05} metalness={0.2} />
      </mesh>
      {[
        [-w * 0.12, h * 0.52 + winH / 2 + 0.04, winW + 0.16, 0.08],
        [-w * 0.12, h * 0.52 - winH / 2 - 0.04, winW + 0.16, 0.08],
      ].map(([px, py, fw, fh]) => (
        <mesh key={py} position={[px!, py!, front + 0.07]}>
          <boxGeometry args={[fw!, fh!, 0.1]} />
          <meshStandardMaterial color={PAINT.gray} roughness={0.5} metalness={0.4} />
        </mesh>
      ))}
      {/* 扉の取っ手 */}
      <mesh position={[-w * 0.12 + winW / 2 + 0.12, h * 0.5, front + 0.06]}>
        <boxGeometry args={[0.05, 0.5, 0.06]} />
        <meshStandardMaterial {...metal} />
      </mesh>
      {/* 操作盤（画面つき） */}
      <group position={[w * 0.36, h * 0.55, front + 0.12]} rotation={[0, -0.25, 0]}>
        <mesh>
          <boxGeometry args={[0.55, 0.75, 0.12]} />
          <meshStandardMaterial color={PAINT.gray} roughness={0.5} metalness={0.4} />
        </mesh>
        <mesh position={[0, 0.12, 0.065]}>
          <planeGeometry args={[0.44, 0.3]} />
          <meshStandardMaterial
            color="#0b1220"
            emissive={m.state === "alarm" ? "#ff4030" : "#38bdf8"}
            emissiveIntensity={m.state === "stopped" ? 0.25 : 1.2}
            toneMapped={false}
          />
        </mesh>
      </group>
      {/* 切りくずコンベア */}
      <mesh position={[w / 2 + 0.3, 0.45, -d * 0.1]}>
        <boxGeometry args={[0.6, 0.9, d * 0.5]} />
        <meshStandardMaterial color={PAINT.gray} roughness={0.6} metalness={0.3} />
      </mesh>
      <SignalTower state={m.state} position={[w / 2 - 0.2, h, front - 0.25]} />
    </group>
  );
}

/** 搬送コンベア: 架台・ローラー・ベルト。速度に合わせてベルトとワークが流れる */
function ConveyorBody({ m }: BodyProps) {
  const { w, d, h } = m.box;
  const texture = useMemo(() => {
    const t = beltTexture();
    t.repeat.set(w / 0.5, 1);
    return t;
  }, [w]);
  useEffect(() => () => texture.dispose(), [texture]);
  const items = useRef<THREE.Group>(null);
  const travelled = useRef(0);
  useFrame((_, dt) => {
    const speed = m.state === "running" ? (m.values.speed ?? 0) / 60 : 0; // m/min → m/s
    travelled.current += speed * dt;
    texture.offset.x = -travelled.current / 0.5;
    items.current?.children.forEach((c, i, all) => {
      const span = w - 0.6;
      c.position.x = (((travelled.current + (i * span) / all.length) % span) + span) % span - span / 2;
    });
  });
  const legs: number[] = [];
  for (let x = -w / 2 + 0.3; x <= w / 2 - 0.2; x += 1.5) legs.push(x);
  const beltY = h - 0.06;
  return (
    <group>
      {legs.flatMap((x) =>
        [-1, 1].map((s) => (
          <mesh key={`${x}${s}`} position={[x, (h - 0.12) / 2, s * (d / 2 - 0.1)]}>
            <boxGeometry args={[0.08, h - 0.12, 0.08]} />
            <meshStandardMaterial {...darkMetal} />
          </mesh>
        )),
      )}
      {[-1, 1].map((s) => (
        <mesh key={`rail${s}`} position={[0, h - 0.06, s * (d / 2 - 0.03)]}>
          <boxGeometry args={[w, 0.16, 0.06]} />
          <meshStandardMaterial color="#d9a400" roughness={0.45} metalness={0.3} />
        </mesh>
      ))}
      <mesh position={[0, beltY, 0]}>
        <boxGeometry args={[w - 0.1, 0.04, d - 0.16]} />
        <meshStandardMaterial map={texture} roughness={0.85} />
      </mesh>
      {[-1, 1].map((s) => (
        <mesh key={`roller${s}`} position={[s * (w / 2 - 0.08), beltY - 0.02, 0]} rotation={[Math.PI / 2, 0, 0]}>
          <cylinderGeometry args={[0.07, 0.07, d - 0.12, 20]} />
          <meshStandardMaterial {...metal} />
        </mesh>
      ))}
      {/* 駆動モータ */}
      <mesh position={[w / 2 - 0.4, h * 0.45, -(d / 2 + 0.15)]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.14, 0.14, 0.4, 20]} />
        <meshStandardMaterial color={PAINT.blue} roughness={0.45} metalness={0.3} />
      </mesh>
      {/* 流れるワーク（打ち抜いた板の束） */}
      <group ref={items} position={[0, beltY + 0.02, 0]}>
        {[0, 1, 2, 3, 4].map((i) => (
          <mesh key={i} position={[0, 0.09, 0]}>
            <boxGeometry args={[0.45, 0.16, d * 0.5]} />
            <meshStandardMaterial {...metal} color="#b8c0c8" />
          </mesh>
        ))}
      </group>
      <SignalTower state={m.state} position={[w / 2 - 0.15, h + 0.02, d / 2 - 0.03]} />
    </group>
  );
}

/** エアコンプレッサ: 空気タンク・モータ・圧縮機・圧力計（吐出圧力を指す）・壁への配管 */
function CompressorBody({ m }: BodyProps) {
  const { w, d, h } = m.box;
  const needle = useRef<THREE.Group>(null);
  const fan = useRef<THREE.Group>(null);
  useFrame((_, dt) => {
    const p = m.values.discharge_pressure ?? 0;
    const target = THREE.MathUtils.degToRad(135 - Math.min(Math.max(p / 1.0, 0), 1) * 270); // 0〜1 MPa
    if (needle.current) needle.current.rotation.z = THREE.MathUtils.lerp(needle.current.rotation.z, target, Math.min(dt * 3, 1));
    if (fan.current && m.state === "running") fan.current.rotation.x += dt * 25;
  });
  const tankR = Math.min(d * 0.32, 0.5);
  return (
    <group>
      <mesh position={[0, 0.08, 0]}>
        <boxGeometry args={[w, 0.16, d]} />
        <meshStandardMaterial {...darkMetal} />
      </mesh>
      {/* 空気タンク（横置き） */}
      <mesh position={[0, 0.16 + tankR + 0.1, -d * 0.15]} rotation={[0, 0, Math.PI / 2]}>
        <capsuleGeometry args={[tankR, w * 0.75, 8, 24]} />
        <meshStandardMaterial color={PAINT.red} roughness={0.4} metalness={0.3} />
      </mesh>
      {/* 圧縮機（フィン付き）とモータ */}
      <group position={[-w * 0.15, 0.16 + tankR * 2 + 0.25, -d * 0.1]}>
        <mesh>
          <boxGeometry args={[0.55, 0.45, 0.5]} />
          <meshStandardMaterial color={PAINT.green} roughness={0.5} metalness={0.3} />
        </mesh>
        {[0, 1, 2, 3].map((i) => (
          <mesh key={i} position={[0, 0.25 + i * 0.05, 0]}>
            <boxGeometry args={[0.5, 0.02, 0.45]} />
            <meshStandardMaterial {...metal} />
          </mesh>
        ))}
      </group>
      <mesh position={[w * 0.25, 0.16 + tankR * 2 + 0.2, -d * 0.1]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.2, 0.2, 0.55, 24]} />
        <meshStandardMaterial color={PAINT.green} roughness={0.5} metalness={0.3} />
      </mesh>
      <group ref={fan} position={[w * 0.25 + 0.33, 0.16 + tankR * 2 + 0.2, -d * 0.1]}>
        {[0, 1, 2].map((i) => (
          <mesh key={i} rotation={[(i * Math.PI * 2) / 3, 0, 0]} position={[0, 0, 0]}>
            <boxGeometry args={[0.02, 0.36, 0.06]} />
            <meshStandardMaterial {...metal} />
          </mesh>
        ))}
      </group>
      {/* 圧力計（手前を向く） */}
      <group position={[-w * 0.38, h * 0.62, d / 2 - 0.05]}>
        <mesh position={[0, -h * 0.31, -0.02]}>
          <cylinderGeometry args={[0.02, 0.02, h * 0.62, 8]} />
          <meshStandardMaterial {...metal} />
        </mesh>
        <mesh rotation={[Math.PI / 2, 0, 0]}>
          <cylinderGeometry args={[0.16, 0.16, 0.05, 32]} />
          <meshStandardMaterial {...darkMetal} />
        </mesh>
        <mesh position={[0, 0, 0.027]}>
          <circleGeometry args={[0.14, 32]} />
          <meshStandardMaterial color="#f5f5f0" roughness={0.6} />
        </mesh>
        {/* 目盛りの赤い範囲（0.8 MPa 以上） */}
        <mesh position={[0, 0, 0.029]} rotation={[0, 0, THREE.MathUtils.degToRad(-135)]}>
          <ringGeometry args={[0.11, 0.135, 24, 1, 0, THREE.MathUtils.degToRad(54)]} />
          <meshBasicMaterial color="#dc2626" />
        </mesh>
        <group ref={needle} position={[0, 0, 0.033]}>
          <mesh position={[0, 0.055, 0]}>
            <boxGeometry args={[0.012, 0.11, 0.004]} />
            <meshBasicMaterial color="#111" />
          </mesh>
        </group>
      </group>
      {/* 吐出配管（壁へ立ち上がる） */}
      <mesh position={[w * 0.4, (h + 3.2) / 2, -d / 2 - 0.15]}>
        <cylinderGeometry args={[0.06, 0.06, 3.2 + h, 16]} />
        <meshStandardMaterial color="#2563eb" roughness={0.4} metalness={0.4} />
      </mesh>
      <mesh position={[w * 0.4, 0.16 + tankR + 0.1, -d * 0.15 - tankR - 0.05]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.06, 0.06, d / 2 - d * 0.15 - tankR + 0.2, 16]} />
        <meshStandardMaterial color="#2563eb" roughness={0.4} metalness={0.4} />
      </mesh>
      <SignalTower state={m.state} position={[w / 2 - 0.15, 0.16 + tankR * 2 + 0.47, d / 2 - 0.2]} />
    </group>
  );
}

/** 環境センサ: 柱に取り付けた計器箱。表示の色が温度で変わる */
function EnvironmentSensorBody({ m }: BodyProps) {
  const { h } = m.box;
  const t = m.values.temperature;
  const color = t === undefined ? "#64748b" : t < 18 ? "#38bdf8" : t <= 28 ? "#22c55e" : "#f97316";
  return (
    <group>
      {/* 取り付け柱（建屋の高さまで） */}
      <mesh position={[0, 3.5, -0.3]}>
        <boxGeometry args={[0.36, 7, 0.36]} />
        <meshStandardMaterial color="#56616f" roughness={0.6} metalness={0.5} />
      </mesh>
      <mesh position={[0, 0.5, -0.3]}>
        <boxGeometry args={[0.38, 1, 0.38]} />
        <meshStandardMaterial color="#e0b000" roughness={0.6} />
      </mesh>
      <mesh position={[0, h, -0.05]}>
        <boxGeometry args={[0.32, 0.42, 0.14]} />
        <meshStandardMaterial color="#e5e7eb" roughness={0.5} />
      </mesh>
      <mesh position={[0, h + 0.06, 0.025]}>
        <planeGeometry args={[0.22, 0.14]} />
        <meshStandardMaterial color="#0b1220" emissive={color} emissiveIntensity={1.6} toneMapped={false} />
      </mesh>
      {/* 通気口 */}
      {[0, 1, 2].map((i) => (
        <mesh key={i} position={[0, h - 0.1 - i * 0.04, 0.025]}>
          <boxGeometry args={[0.2, 0.012, 0.01]} />
          <meshStandardMaterial color="#9ca3af" />
        </mesh>
      ))}
    </group>
  );
}

/** 分類が分からない設備: 塗装した箱 + 信号灯 */
function GenericBody({ m }: BodyProps) {
  const { w, d, h } = m.box;
  return (
    <group>
      <mesh position={[0, h / 2, 0]}>
        <boxGeometry args={[w, h, d]} />
        <meshStandardMaterial color={PAINT.ivory} roughness={0.55} metalness={0.1} />
      </mesh>
      {m.hasTower && <SignalTower state={m.state} position={[w / 2 - 0.15, h, d / 2 - 0.15]} />}
    </group>
  );
}

function ControlPanel({ position }: { position: [number, number, number] }) {
  return (
    <group position={position}>
      <mesh position={[0, 0.55, 0]}>
        <boxGeometry args={[0.08, 1.1, 0.08]} />
        <meshStandardMaterial {...darkMetal} />
      </mesh>
      <mesh position={[0, 1.25, 0]} rotation={[0, -Math.PI / 2, 0]}>
        <boxGeometry args={[0.45, 0.4, 0.14]} />
        <meshStandardMaterial color={PAINT.gray} roughness={0.5} metalness={0.4} />
      </mesh>
      {/* 非常停止ボタン */}
      <mesh position={[0.08, 1.25, 0.08]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.04, 0.04, 0.04, 16]} />
        <meshStandardMaterial color="#dc2626" roughness={0.4} />
      </mesh>
    </group>
  );
}

const BODIES: Record<string, (p: BodyProps) => React.ReactNode> = {
  press: PressBody,
  cnc: CncBody,
  conveyor: ConveyorBody,
  compressor: CompressorBody,
  "environment-sensor": EnvironmentSensorBody,
};
