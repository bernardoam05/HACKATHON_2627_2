// Owner: Team B. Thin typed REST client for backend/app/api.
import type {
  ActionRecord, AgentStatus, CapabilityUpdate, ChatRequest, ChatResponse, Decision, Device,
  DeviceAssignment, DevicePlugin, Insight, PluginProposal, Reflex, Room, RoomCreate,
  SimDeviceCreate, StateChange,
} from "./types";

/** A non-2xx response. `detail` is the backend's reason ({"detail": str}, see docs/CONTRACTS.md 5.5). */
export class ApiError extends Error {
  constructor(readonly status: number, readonly detail: string | undefined, method: string, path: string) {
    super(`${method} ${path} -> ${status}${detail ? `: ${detail}` : ""}`);
    this.name = "ApiError";
  }
}

async function http<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail: string | undefined;
    try {
      const data = (await res.json()) as { detail?: unknown };
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      // no JSON body
    }
    throw new ApiError(res.status, detail, method, path);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

/** Query string without empty values: the backend reads `status=` as the status "", not "any". */
function qs(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const api = {
  devices: () => http<Device[]>("GET", "/api/devices"),
  device: (id: string) => http<Device>("GET", `/api/devices/${id}`),
  assign: (id: string, b: DeviceAssignment) => http<Device>("PATCH", `/api/devices/${id}`, b),
  setCapability: (id: string, b: CapabilityUpdate) => http<Device>("POST", `/api/devices/${id}/capabilities`, b),
  addSimDevice: (b: SimDeviceCreate) => http<Device>("POST", "/api/sim/devices", b),
  removeSimDevice: (id: string) => http<void>("DELETE", `/api/sim/devices/${id}`),
  templates: () => http<{ id: string; type: string; name: string }[]>("GET", "/api/sim/templates"),
  rooms: () => http<Room[]>("GET", "/api/rooms"),
  createRoom: (b: RoomCreate) => http<Room>("POST", "/api/rooms", b),
  deleteRoom: (id: string) => http<void>("DELETE", `/api/rooms/${id}`),
  plugins: () => http<DevicePlugin[]>("GET", "/api/plugins"),
  unconfigured: () => http<string[]>("GET", "/api/plugins/unconfigured"),
  proposePlugin: (type: string) => http<PluginProposal>("POST", `/api/plugins/${type}/propose`),
  savePlugin: (type: string, p: DevicePlugin) => http<DevicePlugin>("PUT", `/api/plugins/${type}`, p),
  chat: (b: ChatRequest) => http<ChatResponse>("POST", "/api/chat", b),
  decisions: (limit = 50) => http<Decision[]>("GET", `/api/decisions${qs({ limit })}`),
  actions: (status = "", limit = 50) => http<ActionRecord[]>("GET", `/api/actions${qs({ status, limit })}`),
  confirmAction: (id: string) => http<ActionRecord>("POST", `/api/actions/${id}/confirm`),
  rejectAction: (id: string) => http<ActionRecord>("POST", `/api/actions/${id}/reject`),
  undoAction: (id: string) => http<ActionRecord>("POST", `/api/actions/${id}/undo`),
  changes: (deviceId = "", since = "", limit = 100) =>
    http<StateChange[]>("GET", `/api/history/changes${qs({ deviceId, since, limit })}`),
  agentStatus: () => http<AgentStatus>("GET", "/api/agent/status"),
  insights: () => http<Insight[]>("GET", "/api/insights"),
  deleteInsight: (id: string) => http<void>("DELETE", `/api/insights/${id}`),
  reflexes: (status = "") => http<Reflex[]>("GET", `/api/reflexes${qs({ status })}`),
  confirmReflex: (id: string) => http<Reflex>("POST", `/api/reflexes/${id}/confirm`),
  pauseReflex: (id: string) => http<Reflex>("POST", `/api/reflexes/${id}/pause`),
  resumeReflex: (id: string) => http<Reflex>("POST", `/api/reflexes/${id}/resume`),
  deleteReflex: (id: string) => http<void>("DELETE", `/api/reflexes/${id}`),
};
