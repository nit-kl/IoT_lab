// 建屋: コンクリートの床（区画線・安全通路）、壁・柱・梁、天井照明、高窓
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import type * as THREE from "three";
import type { Line } from "./model";
import { floorTexture } from "./textures";

interface Props {
  w: number;
  d: number;
  h: number;
  label: string;
  lines: Line[];
  night: boolean;
}

const SPAN = 6; // 柱の間隔（m）

export function Building({ w, d, h, label, lines, night }: Props) {
  const gl = useThree((s) => s.gl);
  const texture = useMemo(
    () => floorTexture(w, d, lines, label, gl.capabilities.getMaxAnisotropy()),
    [w, d, lines, label, gl],
  );
  useEffect(() => () => texture.dispose(), [texture]);
  // 屋根の梁と照明は、視点が屋根より上にあるときは隠す（上から見下ろすと中が見えなくなるため）
  const roof = useRef<THREE.Group>(null);
  useFrame(({ camera }) => {
    if (roof.current) roof.current.visible = camera.position.y < h + 0.3;
  });

  const columnsX: number[] = [];
  for (let x = -w / 2; x <= w / 2 + 0.01; x += SPAN) columnsX.push(x);
  const lightsX: number[] = [];
  for (let x = -w / 2 + SPAN / 2; x < w / 2; x += SPAN) lightsX.push(x);
  const lightsZ = [-d / 2 + 3, 0, d / 2 - 3];

  return (
    <group>
      {/* 床（建屋の外も少し広げて、端が切れて見えないように） */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.02, 0]} receiveShadow>
        <planeGeometry args={[w + 40, d + 40]} />
        <meshStandardMaterial color="#3b4048" roughness={0.95} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[w, d]} />
        <meshStandardMaterial map={texture} roughness={0.82} metalness={0.02} />
      </mesh>

      {/* 壁（北・西・東。南は手前から見えるよう腰壁だけ） */}
      <Wall position={[0, h / 2, -d / 2 - 0.1]} size={[w + 0.4, h, 0.2]} />
      <Wall position={[-w / 2 - 0.1, h / 2, 0]} size={[0.2, h, d]} />
      <Wall position={[w / 2 + 0.1, h / 2, 0]} size={[0.2, h, d]} />
      <mesh position={[0, 0.5, d / 2 + 0.1]} castShadow receiveShadow>
        <boxGeometry args={[w + 0.4, 1, 0.2]} />
        <meshStandardMaterial color="#8a9098" roughness={0.9} />
      </mesh>

      {/* 北の高窓（昼は空の明るさ、夜は暗い） */}
      {columnsX.slice(0, -1).map((x) => (
        <mesh key={`win-${x}`} position={[x + SPAN / 2, h - 1.6, -d / 2 + 0.01]}>
          <planeGeometry args={[SPAN - 1.2, 1.4]} />
          <meshStandardMaterial
            color={night ? "#0b1424" : "#dbeafe"}
            emissive={night ? "#0b1830" : "#cfe3ff"}
            emissiveIntensity={night ? 0.4 : 1.4}
            toneMapped={night}
          />
        </mesh>
      ))}

      {/* 柱（北と南の列）。足元に黄黒の注意表示 */}
      {columnsX.flatMap((x) =>
        [-d / 2 + 0.2, d / 2 - 0.2].map((z) => (
          <group key={`col-${x}-${z}`} position={[Math.min(Math.max(x, -w / 2 + 0.2), w / 2 - 0.2), 0, z]}>
            <mesh position={[0, h / 2, 0]} castShadow receiveShadow>
              <boxGeometry args={[0.36, h, 0.36]} />
              <meshStandardMaterial color="#56616f" roughness={0.6} metalness={0.5} />
            </mesh>
            <mesh position={[0, 0.5, 0]}>
              <boxGeometry args={[0.38, 1, 0.38]} />
              <meshStandardMaterial color="#e0b000" roughness={0.6} />
            </mesh>
          </group>
        )),
      )}

      <group ref={roof}>
        {/* 屋根の梁（柱の上を南北に）と、東西のつなぎ梁 */}
        {columnsX.map((x) => (
          <mesh key={`beam-${x}`} position={[Math.min(Math.max(x, -w / 2 + 0.2), w / 2 - 0.2), h + 0.25, 0]} castShadow>
            <boxGeometry args={[0.22, 0.5, d]} />
            <meshStandardMaterial color="#4b5563" roughness={0.6} metalness={0.5} />
          </mesh>
        ))}
        {[-d / 2 + 0.2, -d / 6, d / 6, d / 2 - 0.2].map((z) => (
          <mesh key={`purlin-${z}`} position={[0, h + 0.4, z]}>
            <boxGeometry args={[w, 0.18, 0.12]} />
            <meshStandardMaterial color="#4b5563" roughness={0.6} metalness={0.5} />
          </mesh>
        ))}

        {/* 天井照明（つり下げ型）。夜は 1 列だけ点ける */}
        {lightsX.flatMap((x) =>
          lightsZ.map((z, i) => {
            const on = !night || i === 1;
            return (
              <group key={`lamp-${x}-${z}`} position={[x, h - 0.9, z]}>
                <mesh>
                  <boxGeometry args={[1.5, 0.1, 0.4]} />
                  <meshStandardMaterial color="#2b3038" roughness={0.5} metalness={0.6} />
                </mesh>
                <mesh position={[0, -0.06, 0]} rotation={[Math.PI / 2, 0, 0]}>
                  <planeGeometry args={[1.35, 0.28]} />
                  <meshStandardMaterial
                    color="#ffffff"
                    emissive="#fff6e0"
                    emissiveIntensity={on ? (night ? 3 : 2) : 0.02}
                    toneMapped={false}
                  />
                </mesh>
              </group>
            );
          }),
        )}
      </group>
    </group>
  );
}

function Wall({ position, size }: { position: [number, number, number]; size: [number, number, number] }) {
  return (
    <group position={position}>
      <mesh receiveShadow>
        <boxGeometry args={size} />
        <meshStandardMaterial color="#b9c0c8" roughness={0.75} metalness={0.15} />
      </mesh>
      {/* 腰壁（コンクリート） */}
      <mesh position={[0, -size[1] / 2 + 0.6, 0]} receiveShadow>
        <boxGeometry args={[size[0] + 0.02, 1.2, size[2] + 0.02]} />
        <meshStandardMaterial color="#868c94" roughness={0.9} />
      </mesh>
    </group>
  );
}
