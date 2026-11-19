// Owner: Team B. PT-PT relative times ("há 2 minutos") that refresh while on screen.
import { useEffect, useState } from "react";

const rtf = new Intl.RelativeTimeFormat("pt-PT", { numeric: "auto" });
const STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["second", 60], ["minute", 60], ["hour", 24], ["day", 7], ["week", 4.35], ["month", 12], ["year", Infinity],
];

export function timeAgo(iso: string, now = Date.now()): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  let delta = (then - now) / 1000;
  if (Math.abs(delta) < 10) return "agora mesmo";
  for (const [unit, size] of STEPS) {
    if (Math.abs(delta) < size) return rtf.format(Math.round(delta), unit);
    delta /= size;
  }
  return "";
}

export function formatClock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("pt-PT", { dateStyle: "short", timeStyle: "short" });
}

/** Re-render every `ms` so relative times stay correct. */
export function useNow(ms = 30_000): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}
