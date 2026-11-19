// Owner: Team B. Rooms shared by the device list and the room editor (rooms are ours, D11).
import { createContext, useContext, useMemo, type ReactNode } from "react";
import { api } from "../api/client";
import type { Room } from "../api/types";
import { useResource } from "../lib/useResource";

interface RoomsValue {
  rooms: Room[];
  loading: boolean;
  error: string | undefined;
  reload: () => void;
  add: (r: Room) => void;
  remove: (id: string) => void;
}

const RoomsContext = createContext<RoomsValue | null>(null);

export function RoomsProvider({ children }: { children: ReactNode }) {
  const res = useResource(api.rooms);
  const { setData } = res;
  const value = useMemo(() => ({
    rooms: [...(res.data ?? [])].sort((a, b) => a.name.localeCompare(b.name, "pt")),
    loading: res.loading,
    error: res.error,
    reload: res.reload,
    add: (r: Room) => setData((rs) => [...(rs ?? []).filter((x) => x.id !== r.id), r]),
    remove: (id: string) => setData((rs) => (rs ?? []).filter((r) => r.id !== id)),
  }), [res.data, res.loading, res.error, res.reload, setData]);
  return <RoomsContext.Provider value={value}>{children}</RoomsContext.Provider>;
}

export function useRooms(): RoomsValue {
  const ctx = useContext(RoomsContext);
  if (!ctx) throw new Error("useRooms must be used inside <RoomsProvider>");
  return ctx;
}
