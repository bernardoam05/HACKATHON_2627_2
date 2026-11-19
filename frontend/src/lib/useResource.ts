// Owner: Team B. Load data from the REST API with loading/error state and manual reload.
import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { ApiError } from "../api/client";

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

/** PT-PT message for the user, following the backend's status codes (docs/CONTRACTS.md 5.5). */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 501) return "Ainda não implementado no backend.";
    if (e.status === 404) return "Isto já não existe. Atualiza a página.";
    // 409: the backend explains the conflict (e.g. action no longer pending); its detail is in English.
    if (e.status === 409) return "Isto já mudou entretanto. Atualiza para ver o estado atual.";
    if (e.status >= 500) return "O servidor teve um problema. Tenta outra vez.";
    return e.detail ?? `Pedido recusado (${e.status}).`;
  }
  const text = e instanceof Error ? e.message : String(e);
  if (text.includes("Failed to fetch") || text.includes("NetworkError")) return "Sem ligação ao servidor.";
  return text;
}
