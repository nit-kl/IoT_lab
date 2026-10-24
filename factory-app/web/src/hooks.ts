import { useEffect, useRef } from "react";

/** 画面を表示している間だけ、interval ミリ秒ごとに fn を呼ぶ。表示に戻ったときはすぐ 1 回呼ぶ */
export function useVisibleInterval(fn: () => void, interval: number, enabled: boolean) {
  const saved = useRef(fn);
  saved.current = fn;
  useEffect(() => {
    if (!enabled) return;
    let timer: number | undefined;
    const start = () => {
      stop();
      if (document.visibilityState === "visible") timer = window.setInterval(() => saved.current(), interval);
    };
    const stop = () => {
      if (timer !== undefined) window.clearInterval(timer);
      timer = undefined;
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") saved.current();
      start();
    };
    start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [interval, enabled]);
}
