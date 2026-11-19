// Owner: Team B. Devices the agent leaves alone after the user overrode it (D9), plus the open notices.
// Loads GET /api/cooldowns, then follows cooldown_started (upsert by deviceId) and cooldown_ended
// (remove), and drops blocks whose `until` has passed even if no event arrives.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { DeviceCooldown } from "../api/types";
import { SAMPLE_MODE, sampleCooldownStarted } from "../dev/samples";
import { useResource } from "../lib/useResource";
import { useLiveMessages } from "./LiveProvider";

/** A PUT re-sends cooldown_started for the same device; don't reopen a notice the user just handled. */
const HANDLED_GRACE_MS = 5_000;
const MAX_TIMEOUT_MS = 2_147_000_000; // setTimeout limit (~24.8 days)

interface CooldownsValue {
  cooldowns: DeviceCooldown[];
  byDevice: Map<string, DeviceCooldown>;
  /** Block that was caused by overriding this agent action (to label it in the activity feed). */
  byAction: Map<string, DeviceCooldown>;
  loading: boolean;
  error: string | undefined;
  reload: () => void;
  /** Local update with the backend's response to a PUT/DELETE. */
  upsert: (c: DeviceCooldown) => void;
  remove: (deviceId: string) => void;
  /** Device ids with an open override notice, newest first; only devices still blocked. */
  notices: string[];
  dismissNotice: (deviceId: string) => void;
}

const CooldownsContext = createContext<CooldownsValue | null>(null);

const active = (c: DeviceCooldown, now = Date.now()) => !c.until || new Date(c.until).getTime() > now;
const noticeKey = (c: DeviceCooldown) => `${c.deviceId}|${c.startedAt}`;

function replace(list: DeviceCooldown[] | undefined, c: DeviceCooldown): DeviceCooldown[] {
  return [...(list ?? []).filter((x) => x.deviceId !== c.deviceId), c];
}

export function CooldownsProvider({ children }: { children: ReactNode }) {
  const res = useResource(api.cooldowns);
  const { setData } = res;
  const [notices, setNotices] = useState<string[]>([]);
  const handledKeys = useRef(new Set<string>());
  const handledAt = useRef(new Map<string, number>());

  const onStarted = useCallback((c: DeviceCooldown) => {
    setData((list) => replace(list, c));
    if (c.reason !== "manual_override" && c.reason !== "undo") return; // reason "user" = a block set from the UI
    const recently = Date.now() - (handledAt.current.get(c.deviceId) ?? 0) < HANDLED_GRACE_MS;
    if (handledKeys.current.has(noticeKey(c)) || recently) return;
    setNotices((ns) => [c.deviceId, ...ns.filter((id) => id !== c.deviceId)]);
  }, [setData]);

  useLiveMessages((m) => {
    if (m.type === "cooldown_started") onStarted(m.cooldown);
    else if (m.type === "cooldown_ended") setData((list) => (list ?? []).filter((c) => c.deviceId !== m.deviceId));
  });

  // ?exemplo: the fakes send no WebSocket events, so replay one override to show the notice.
  useEffect(() => {
    if (!SAMPLE_MODE) return;
    const t = setTimeout(() => onStarted(sampleCooldownStarted.cooldown), 800);
    return () => clearTimeout(t);
  }, [onStarted]);

  // Drop blocks once their `until` passes (the backend's cooldown_ended may come later, or never offline).
  const list = res.data;
  useEffect(() => {
    const next = Math.min(...(list ?? []).filter((c) => c.until).map((c) => new Date(c.until as string).getTime()));
    if (!Number.isFinite(next)) return;
    const t = setTimeout(() => setData((l) => (l ?? []).filter((c) => active(c))),
      Math.min(Math.max(next - Date.now() + 250, 0), MAX_TIMEOUT_MS));
    return () => clearTimeout(t);
  }, [list, setData]);

  const value = useMemo(() => {
    const cooldowns = (res.data ?? []).filter((c) => active(c));
    const byDevice = new Map(cooldowns.map((c) => [c.deviceId, c]));
    return {
      cooldowns,
      byDevice,
      byAction: new Map(cooldowns.filter((c) => c.actionId).map((c) => [c.actionId as string, c])),
      loading: res.loading,
      error: res.error,
      reload: res.reload,
      upsert: (c: DeviceCooldown) => setData((l) => replace(l, c)),
      remove: (deviceId: string) => setData((l) => (l ?? []).filter((c) => c.deviceId !== deviceId)),
      notices: notices.filter((id) => byDevice.has(id)), // a notice closes when its block ends
      dismissNotice: (deviceId: string) => {
        const c = byDevice.get(deviceId);
        if (c) handledKeys.current.add(noticeKey(c));
        handledAt.current.set(deviceId, Date.now());
        setNotices((ns) => ns.filter((id) => id !== deviceId));
      },
    };
  }, [res.data, res.loading, res.error, res.reload, setData, notices]);

  return <CooldownsContext.Provider value={value}>{children}</CooldownsContext.Provider>;
}

export function useCooldowns(): CooldownsValue {
  const ctx = useContext(CooldownsContext);
  if (!ctx) throw new Error("useCooldowns must be used inside <CooldownsProvider>");
  return ctx;
}

export function useCooldown(deviceId: string): DeviceCooldown | undefined {
  return useCooldowns().byDevice.get(deviceId);
}
