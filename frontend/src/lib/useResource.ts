// Owner: Team B. Load data from the REST API with loading/error state and manual reload.
import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";

export interface Resource<T> {
  data: T | undefined;
  error: string | undefined;
  loading: boolean;
  reload: () => void;
  /** Local update, e.g. to merge a WebSocket event without refetching. */
  setData: Dispatch<SetStateAction<T | undefined>>;
}

export function useResource<T>(load: () => Promise<T>, deps: readonly unknown[] = []): Resource<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadRef.current()
      .then((d) => { if (!cancelled) { setData(d); setError(undefined); } })
      .catch((e: unknown) => { if (!cancelled) setError(errorMessage(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [tick, ...deps]); // `load` is read through a ref; pass what it depends on in `deps`

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload, setData };
}

export function errorMessage(e: unknown): string {
  const text = e instanceof Error ? e.message : String(e);
  if (text.endsWith("-> 501")) return "Ainda não implementado no backend.";
  if (/-> 5\d\d$/.test(text)) return "O servidor teve um problema. Tenta outra vez.";
  if (text.includes("Failed to fetch")) return "Sem ligação ao servidor.";
  return text;
}
