// Owner: Team B. Devices loaded once for the whole app, kept fresh from device_added/removed.
import { createContext, useContext, useMemo, type ReactNode } from "react";
import { api } from "../api/client";
import type { CapabilityUpdate, Device, JsonValue } from "../api/types";
import { useResource } from "../lib/useResource";
import { useLiveMessages } from "./LiveProvider";

interface DevicesValue {
  devices: Device[];
  byId: Map<string, Device>;
  loading: boolean;
  error: string | undefined;
  reload: () => void;
  /** Replace (or add) one device locally, e.g. with the backend's response to an edit. */
  upsert: (d: Device) => void;
  remove: (id: string) => void;
}

const DevicesContext = createContext<DevicesValue | null>(null);

function replace(list: Device[] | undefined, d: Device): Device[] {
  const ds = list ?? [];
  return ds.some((x) => x.id === d.id) ? ds.map((x) => (x.id === d.id ? d : x)) : [...ds, d];
}

export function DevicesProvider({ children }: { children: ReactNode }) {
  const res = useResource(api.devices);
  const { setData } = res;
  useLiveMessages((m) => {
    if (m.type === "device_added") setData((ds) => replace(ds, m.device));
    else if (m.type === "device_removed") setData((ds) => (ds ?? []).filter((d) => d.id !== m.device.id));
    else if (m.type === "state_changed") {
      // Keep capability values live without refetching the whole list.
      setData((ds) => ds?.map((d) => {
        const changes = m.changes.filter((c) => c.deviceId === d.id);
        if (changes.length === 0) return d;
        const capabilities = d.capabilities.map((cap) => {
          const c = [...changes].reverse().find((x) => x.capability === cap.type); // latest wins
          return c ? { ...cap, value: c.new ?? null } : cap;
        });
        return { ...d, capabilities };
      }));
    }
  });
  const value = useMemo(() => {
    const devices = res.data ?? [];
    return {
      devices,
      byId: new Map(devices.map((d) => [d.id, d])),
      loading: res.loading,
      error: res.error,
      reload: res.reload,
      upsert: (d: Device) => setData((ds) => replace(ds, d)),
      remove: (id: string) => setData((ds) => (ds ?? []).filter((d) => d.id !== id)),
    };
  }, [res.data, res.loading, res.error, res.reload, setData]);
  return <DevicesContext.Provider value={value}>{children}</DevicesContext.Provider>;
}

export function useDevices(): DevicesValue {
  const ctx = useContext(DevicesContext);
  if (!ctx) throw new Error("useDevices must be used inside <DevicesProvider>");
  return ctx;
}

/** User-facing device name: alias, then name, then the raw id. */
export function useDeviceLabel(): (id: string) => string {
  const { byId } = useDevices();
  return (id) => {
    const d = byId.get(id);
    return d?.alias || d?.name || id;
  };
}

const CAPABILITY_LABELS: Record<string, string> = {
  power: "Energia",
  brightness: "Brilho",
  temperature: "Temperatura",
  volume: "Volume",
  color_rgb: "Cor",
};

/** PT-PT description of a capability update, e.g. "Ligar", "Brilho 30%". */
export function describeUpdate(u: CapabilityUpdate): string {
  if (u.type === "power") return u.value === "on" || u.value === true ? "Ligar" : "Desligar";
  if (u.type === "brightness") return `Brilho ${formatValue(u.value)}%`;
  if (u.type === "color_rgb") return `Cor rgb(${u.redValue ?? 0}, ${u.greenValue ?? 0}, ${u.blueValue ?? 0})`;
  return `${CAPABILITY_LABELS[u.type] ?? u.type} ${formatValue(u.value)}`;
}

export function formatValue(v: JsonValue | undefined): string {
  if (v === undefined || v === null) return "—";
  if (typeof v === "boolean") return v ? "sim" : "não";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
