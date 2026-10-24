// 床・ベルトの模様をプログラムで描く（外部の画像素材は使わない）
import * as THREE from "three";
import type { Line } from "./model";

/** 同じ模様になるよう、乱数は種から作る */
function random(seed: number) {
  let s = seed;
  return () => {
    s = (s * 16807) % 2147483647;
    return (s - 1) / 2147483646;
  };
}

const FONT = '"Yu Gothic UI", "Meiryo", "Noto Sans JP", system-ui, sans-serif';

/**
 * 工場の床（コンクリート、目地、ラインの区画線と名前、安全通路、エリア名）。
 * キャンバスの上が北（3D の -z）、右が東（+x）。座標は 3D と同じ（エリアの中心が原点）。
 */
export function floorTexture(w: number, d: number, lines: Line[], areaLabel: string, anisotropy: number): THREE.CanvasTexture {
  const ppm = 2048 / w; // 1 メートルあたりの画素
  const canvas = document.createElement("canvas");
  canvas.width = 2048;
  canvas.height = Math.round(d * ppm);
  const g = canvas.getContext("2d")!;
  const px = (x: number) => (x + w / 2) * ppm;
  const pz = (z: number) => (z + d / 2) * ppm;
  const rand = random(7);

  // コンクリート: 地の色 + 大きなむら + 細かい粒
  g.fillStyle = "#80868d";
  g.fillRect(0, 0, canvas.width, canvas.height);
  for (let i = 0; i < 90; i++) {
    const x = rand() * canvas.width;
    const y = rand() * canvas.height;
    const r = 40 + rand() * 160;
    const grad = g.createRadialGradient(x, y, 0, x, y, r);
    const dark = rand() < 0.5;
    grad.addColorStop(0, dark ? "rgba(40,44,50,0.10)" : "rgba(200,205,210,0.08)");
    grad.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = grad;
    g.fillRect(x - r, y - r, r * 2, r * 2);
  }
  for (let i = 0; i < 26000; i++) {
    const shade = Math.floor(90 + rand() * 80);
    g.fillStyle = `rgba(${shade},${shade + 3},${shade + 6},0.35)`;
    g.fillRect(rand() * canvas.width, rand() * canvas.height, 1.5, 1.5);
  }
  // 目地（5 m ごと）
  g.strokeStyle = "rgba(30,33,38,0.35)";
  g.lineWidth = 2;
  for (let x = 5; x < w; x += 5) {
    g.beginPath();
    g.moveTo(x * ppm, 0);
    g.lineTo(x * ppm, canvas.height);
    g.stroke();
  }
  for (let y = 5; y < d; y += 5) {
    g.beginPath();
    g.moveTo(0, y * ppm);
    g.lineTo(canvas.width, y * ppm);
    g.stroke();
  }

  for (const line of lines) {
    const { x, z, w: lw, d: ld } = line.box;
    // ラインの区画（黄色の線）と名前
    g.strokeStyle = "rgba(234,179,8,0.95)";
    g.lineWidth = 0.1 * ppm;
    g.strokeRect(px(x - lw / 2), pz(z - ld / 2), lw * ppm, ld * ppm);
    g.fillStyle = "rgba(240,240,235,0.85)";
    g.font = `bold ${Math.round(0.42 * ppm)}px ${FONT}`;
    g.textBaseline = "top";
    g.fillText(`${line.id}  ${line.label}`, px(x - lw / 2) + 0.25 * ppm, pz(z - ld / 2) + 0.2 * ppm);

    // 安全通路（ラインの南側。緑の通路 + 黄黒の縁）
    const top = z + ld / 2 + 0.8;
    const width = 1.4;
    g.fillStyle = "rgba(52,110,80,0.85)";
    g.fillRect(px(-w / 2 + 0.5), pz(top), (w - 1) * ppm, width * ppm);
    for (const edge of [top - 0.15, top + width]) {
      const y0 = pz(edge);
      const hgt = 0.15 * ppm;
      g.fillStyle = "#e0b000";
      g.fillRect(px(-w / 2 + 0.5), y0, (w - 1) * ppm, hgt);
      g.fillStyle = "#1a1a1a";
      for (let s = px(-w / 2 + 0.5); s < px(w / 2 - 0.5); s += hgt * 2) {
        g.beginPath();
        g.moveTo(s, y0 + hgt);
        g.lineTo(s + hgt, y0);
        g.lineTo(s + hgt * 1.6, y0);
        g.lineTo(s + hgt * 0.6, y0 + hgt);
        g.fill();
      }
    }
    g.fillStyle = "rgba(255,255,255,0.75)";
    g.font = `bold ${Math.round(0.5 * ppm)}px ${FONT}`;
    g.textBaseline = "middle";
    for (let s = -w / 2 + 3; s < w / 2 - 3; s += 9) {
      g.fillText("安全通路  ▶", px(s), pz(top + width / 2));
    }
  }

  // エリア名（南西の角）
  g.fillStyle = "rgba(235,235,230,0.55)";
  g.font = `bold ${Math.round(0.9 * ppm)}px ${FONT}`;
  g.textBaseline = "bottom";
  g.fillText(areaLabel, 0.6 * ppm, canvas.height - 0.5 * ppm);

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = anisotropy;
  return texture;
}

/** コンベアのベルト（ゴムの横筋）。offset を動かすと流れて見える */
export function beltTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 64;
  const g = canvas.getContext("2d")!;
  g.fillStyle = "#202428";
  g.fillRect(0, 0, 256, 64);
  for (let x = 0; x < 256; x += 32) {
    g.fillStyle = "#33393f";
    g.fillRect(x, 0, 6, 64);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  return texture;
}
