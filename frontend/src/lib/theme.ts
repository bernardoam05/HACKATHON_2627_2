// Owner: Team B. Light/dark theme: follows the OS until the user picks one, then remembers it.
import { useEffect, useState } from "react";

export type Theme = "light" | "dark";

const KEY = "theme";
const media = () => matchMedia("(prefers-color-scheme: dark)");

function saved(): Theme | null {
  try {
    const t = localStorage.getItem(KEY);
    return t === "light" || t === "dark" ? t : null;
  } catch {
    return null; // storage blocked (private mode, etc.): fall back to the OS setting
  }
}

function current(): Theme {
  const t = document.documentElement.dataset.theme;
  return t === "dark" || t === "light" ? t : media().matches ? "dark" : "light";
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(current);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // Without a saved choice, keep following the OS if it changes while the app is open.
  useEffect(() => {
    const mq = media();
    const onChange = () => { if (!saved()) setTheme(mq.matches ? "dark" : "light"); };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const toggle = () => setTheme((t) => {
    const next: Theme = t === "dark" ? "light" : "dark";
    try { localStorage.setItem(KEY, next); } catch { /* not persisted; still applied */ }
    return next;
  });

  return [theme, toggle];
}
