// TS mirror of backend/app/schemas. SHARED CONTRACT: change only together with the schemas,
// docs/CONTRACTS.md and contract tests. No `any` allowed in src/api.

export type JsonValue = string | number | boolean | null | JsonValue[] | { [k: string]: JsonValue };
export type ISODate = string;

// ---- device.py
export type DeviceSource = "altice" | "sim";

export interface Capability {
  type: string;
  name?: string;
  value?: JsonValue;
  availableValues?: JsonValue[] | null;
  min?: number | null;
  max?: number | null;
  [extra: string]: JsonValue | undefined;
}

export interface Device {
  id: string;
  name: string;
  type: string;
  capabilities: Capability[];
  categories: string[];
  roomName?: string | null;
  lastCommunicationTime?: number | string | null; // Altice: epoch millis
  source: DeviceSource;
  alias?: string | null;
  roomId?: string | null;
  enabled: boolean;
  [extra: string]: JsonValue | Capability[] | undefined;
}

export interface CapabilityUpdate {
  type: string;
  value?: JsonValue;
  min?: number | null;
  max?: number | null;
  redValue?: number | null;
  greenValue?: number | null;
  blueValue?: number | null;
}

export interface Room { id: string; name: string; isEntrance: boolean }
export interface RoomCreate { name: string; isEntrance?: boolean }
export interface DeviceAssignment { alias?: string | null; roomId?: string | null; enabled?: boolean | null }
export interface SimDeviceCreate {
  type: string;
  name: string;
  roomId?: string | null;
  template?: string | null;
  capabilities?: Capability[] | null;
}

// ---- plugin.py
export type AutonomyLevel = "auto" | "auto_notify" | "suggest";
export type DevicePurpose = "comfort" | "entertainment" | "energy" | "security" | "environment";
export type PluginStatus = "builtin" | "proposed" | "confirmed";
export type ConfigMode = "basic" | "advanced";

export interface CapabilitySpec {
  type: string;
  description: string;
  writable: boolean;
  autonomy: AutonomyLevel;
}

export interface DevicePlugin {
  type: string;
  displayName: string;
  purpose: DevicePurpose;
  capabilities: CapabilitySpec[];
  starterAutomations: string[];
  status: PluginStatus;
  configMode?: ConfigMode | null;
}

// ---- events.py
export interface StateSnapshot { id: string; takenAt: ISODate; devices: Device[] }

export interface StateChange {
  id: string;
  deviceId: string;
  capability: string;
  old?: JsonValue;
  new?: JsonValue;
  at: ISODate;
  causedByAgent: boolean;
  actionId?: string | null;
}

export type ActionStatus = "executed" | "pending_confirmation" | "rejected" | "undone" | "failed";

export interface ActionRecord {
  id: string;
  decisionId: string;
  deviceId: string;
  update: CapabilityUpdate;
  autonomy: AutonomyLevel;
  status: ActionStatus;
  expectedValue?: JsonValue;
  createdAt: ISODate;
  resolvedAt?: ISODate | null;
}

export interface UserFeedback {
  actionId: string;
  kind: "undo" | "confirm" | "reject" | "manual_override";
  at: ISODate;
  note?: string | null;
}

// ---- agent.py
export interface ProposedAction { deviceId: string; update: CapabilityUpdate; reason: string }

export interface Insight {
  id: string;
  statement: string;
  confidence: number;
  evidence: string[];
  createdAt: ISODate;
  lastConfirmedAt: ISODate;
  status: "active" | "dropped";
}

export interface InsightUpdate {
  op: "create" | "reinforce" | "weaken" | "delete";
  insightId?: string | null;
  statement?: string | null;
  delta?: number | null;
  evidence?: string[] | null;
}

export interface TimeWindow { from: string; to: string; weekdays?: number[] | null }

export interface ReflexTrigger {
  deviceId: string;
  capability: string;
  operator: "=" | "<" | ">" | "changed";
  value?: number | string | boolean | null;
  roomId?: string | null;
  timeWindow?: TimeWindow | null;
}

export type ReflexStatus = "proposed" | "active" | "paused" | "deleted";

export interface Reflex {
  id: string;
  name: string;
  triggers: ReflexTrigger[];
  triggerLogic: "all" | "any";
  actions: ProposedAction[];
  sourceInsightId?: string | null;
  status: ReflexStatus;
  createdAt: ISODate;
  executions: number;
  undos: number;
}

export interface ReflexProposal { reflex: Reflex; rationale: string }
export interface ReflexUpdate {
  reflexId: string;
  op: "pause" | "resume" | "delete" | "modify";
  reflex?: Reflex | null;
  reason: string;
}

export type DecisionTrigger = "state_change" | "chat" | "startup" | "reflex_report";

export interface Decision {
  id: string;
  createdAt: ISODate;
  trigger: DecisionTrigger;
  explanation: string;
  inferredRoom?: string | null;
  inferredActivity?: string | null;
  actions: ProposedAction[];
  confidence: number;
  beliefState: string;
  insightUpdates: InsightUpdate[];
  reflexProposals: ReflexProposal[];
  reflexUpdates: ReflexUpdate[];
}

export interface ChatRequest { message: string; inputMode: "text" | "voice" }
export interface ChatResponse { reply: string; decision?: Decision | null }
export interface PluginProposal { plugin: DevicePlugin; rationale: string }

export interface DoorEvent { deviceId: string; roomId?: string | null; openedAt: ISODate; closedAt?: ISODate | null }
export interface RoomMotion { roomId?: string | null; deviceId: string; at: ISODate }
export interface PresenceFacts {
  now: ISODate;
  lastDoorEvent?: DoorEvent | null;
  lastMotionByRoom: RoomMotion[];
  secondsSinceLastMotion?: number | null;
  secondsSinceLastDoorEvent?: number | null;
  motionAfterDoorEvent: boolean;
  emptyAfterSeconds: number;
}

export interface AgentStatus {
  busy: boolean;
  dirty: boolean;
  lastRunAt?: ISODate | null;
  model: string;
  beliefState: string;
  runsSinceFullSnapshot: number;
}

// ---- ws.py
export type WsMessage =
  | { type: "state_changed"; changes: StateChange[] }
  | { type: "decision"; decision: Decision }
  | { type: "action_updated"; action: ActionRecord }
  | { type: "agent_status"; busy: boolean; dirty: boolean }
  | { type: "device_added"; device: Device }
  | { type: "device_removed"; device: Device }
  | { type: "plugin_needed"; deviceType: string }
  | { type: "insight_updated"; insight: Insight }
  | { type: "reflex_proposed"; reflex: Reflex }
  | { type: "reflex_updated"; reflex: Reflex }
  | { type: "reflex_executed"; reflexId: string; actions: ActionRecord[] };
