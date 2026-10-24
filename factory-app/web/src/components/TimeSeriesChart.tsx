import { LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, MarkAreaComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import { formatTime, formatValue, isCode } from "../format";
import { useApp } from "../store";
import type { CodeValue, Reading, Unit } from "../types";

echarts.use([LineChart, GridComponent, TooltipComponent, DataZoomComponent, MarkAreaComponent, CanvasRenderer]);

const escape = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

/** 時系列のグラフ（横軸は CSV 上の時刻）。コード値（稼働状態）は階段状にし、軸にコードの意味を出す */
export function TimeSeriesChart({ items, unit, codes }: { items: Reading[]; unit: Unit; codes?: CodeValue[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useApp((s) => s.theme);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    const dark = theme === "dark";
    const axisColor = dark ? "#64748b" : "#94a3b8";
    const values = items.map((i) => (isCode(i.value) ? i.value.value : i.value));
    const codeLabel = new Map((codes ?? []).map((c) => [c.value, c.label]));
    chart.setOption({
      animation: false,
      grid: { left: codes ? 70 : 52, right: 14, top: 14, bottom: 46 },
      tooltip: {
        trigger: "axis",
        backgroundColor: dark ? "#0e1626" : "#fff",
        borderColor: dark ? "#1e2a42" : "#e2e8f0",
        textStyle: { color: dark ? "#e2e8f0" : "#1e293b" },
        formatter: (params: { dataIndex: number }[]) => {
          const item = items[params[0]?.dataIndex ?? 0];
          return item ? `${escape(formatTime(item.source_ts))}<br/><b>${escape(formatValue(item.value, unit))}</b>` : "";
        },
      },
      xAxis: {
        type: "category",
        data: items.map((i) => formatTime(i.source_ts)),
        axisLine: { lineStyle: { color: axisColor } },
        axisLabel: { color: axisColor, hideOverlap: true },
      },
      yAxis: codes
        ? {
            type: "value",
            min: 0,
            max: Math.max(...codes.map((c) => c.value)),
            interval: 1,
            axisLabel: { color: axisColor, formatter: (v: number) => codeLabel.get(v) ?? "" },
            splitLine: { lineStyle: { color: dark ? "#141e31" : "#eef2f7" } },
          }
        : {
            type: "value",
            scale: true,
            name: unit.symbol ?? "",
            nameTextStyle: { color: axisColor },
            axisLabel: { color: axisColor },
            splitLine: { lineStyle: { color: dark ? "#141e31" : "#eef2f7" } },
          },
      dataZoom: [{ type: "inside" }],
      series: [
        {
          type: "line",
          step: codes ? "end" : false,
          showSymbol: false,
          data: values,
          lineStyle: { width: 1.6, color: "#22d3ee" },
          areaStyle: codes ? undefined : { color: dark ? "rgba(34,211,238,0.10)" : "rgba(8,145,178,0.08)" },
        },
      ],
    });
    const resize = new ResizeObserver(() => chart.resize());
    resize.observe(ref.current);
    return () => {
      resize.disconnect();
      chart.dispose();
    };
  }, [items, unit, codes, theme]);

  return <div ref={ref} className="h-56 w-full" />;
}
