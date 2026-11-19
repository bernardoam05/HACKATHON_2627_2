// Owner: Team B. One WebSocket for the whole app; features subscribe with useLiveMessages.
import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode,
} from "react";
import { api } from "../api/client";
import type { WsMessage } from "../api/types";
import { connectWs, type WsStatus } from "../api/ws";

type Listener = (m: WsMessage) => void;

export interface AgentLive {
  busy: boolean;
  dirty: boolean;
  model?: string;
}

interface LiveContextValue {
  status: WsStatus;
  agent: AgentLive;
  subscribe: (fn: Listener) => () => void;
}

const LiveContext = createContext<LiveContextValue | null>(null);

export function LiveProvider({ children }: { children: ReactNode }) {
  const listeners = useRef(new Set<Listener>());
  const [status, setStatus] = useState<WsStatus>("connecting");
  const [agent, setAgent] = useState<AgentLive>({ busy: false, dirty: false });

  useEffect(() => {
    api.agentStatus()
      .then((s) => setAgent({ busy: s.busy, dirty: s.dirty, model: s.model }))
      .catch(() => {}); // 501 until Team A implements it; the WS keeps it fresh later
    return connectWs((m) => {
      if (m.type === "agent_status") setAgent((a) => ({ ...a, busy: m.busy, dirty: m.dirty }));
      listeners.current.forEach((fn) => fn(m));
    }, setStatus);
  }, []);

  const subscribe = useCallback((fn: Listener) => {
    listeners.current.add(fn);
    return () => { listeners.current.delete(fn); };
  }, []);
  const value = useMemo(() => ({ status, agent, subscribe }), [status, agent, subscribe]);
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveContextValue {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error("useLive must be used inside <LiveProvider>");
  return ctx;
}

/** Calls `handler` for every WS message; always uses the latest handler without resubscribing. */
export function useLiveMessages(handler: Listener): void {
  const { subscribe } = useLive();
  const latest = useRef(handler);
  latest.current = handler;
  useEffect(() => subscribe((m) => latest.current(m)), [subscribe]);
}
