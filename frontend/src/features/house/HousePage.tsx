// Owner: Team B. "Casa" tab: floor plan of our rooms, lit by their lights, with devices as live chips
// and a short log of what happens. Everything comes from the device list + /ws.
import { useState } from "react";
import type { Device, JsonValue, Room, StateChange } from "../../api/types";
import { useDeviceLabel, useDevices } from "../../live/DevicesProvider";
import { useLiveMessages } from "../../live/LiveProvider";
import { useRooms } from "../../live/RoomsProvider";
import { useNow } from "../../lib/time";
import { BoltIcon, Card, CheckIcon, Empty, ErrorNote, Loading, PersonIcon } from "../../ui";
import { displayName, isOffline } from "../manager/DeviceList";

const NO_ROOM = "";
const LOG_LINES = 6;

interface LogLine { id: string; who: "agent" | "person" | "reflex"; text: string }

const isOn = (v: JsonValue | undefined) => v === "on" || v === true;
const isTrue = (v: JsonValue | undefined) => v === true || v === "true";

function value(d: Device, type: string): JsonValue | undefined {
  return d.capabilities.find((c) => c.type === type)?.value ?? undefined;
}

type Kind = "light" | "tv" | "motion" | "door" | "plug" | "other";

/** How a device looks on the plan: kind (colour), whether it is "active", and a PT-PT label. */
function chip(d: Device): { kind: Kind; on: boolean; text: string } {
  const name = displayName(d);
  const power = value(d, "power");
  if (d.type === "bulb") {
    const b = value(d, "brightness");
    return { kind: "light", on: isOn(power), text: isOn(power) ? `${name} · ${typeof b === "number" ? b : 100}%` : `${name} · apagada` };
  }
  if (d.type === "tv") return { kind: "tv", on: isOn(power), text: `${name} · ${isOn(power) ? "ligada" : "desligada"}` };
  const motion = value(d, "motionDetected");
  if (d.type === "motionSensor" || (d.type === "camera" && motion !== undefined)) {
    return { kind: "motion", on: isTrue(motion), text: `${name} · ${isTrue(motion) ? "movimento" : "sem movimento"}` };
  }
  if (d.type === "contactSensor") {
    const open = value(d, "sensorState") === "alarm";
    return { kind: "door", on: open, text: `${name} · ${open ? "aberta" : "fechada"}` };
  }
  if (power !== undefined) {
    const w = value(d, "instantPower");
    const watts = isOn(power) && typeof w === "number" && w > 0 ? ` · ${Math.round(w)} W` : "";
    return { kind: "plug", on: isOn(power), text: `${name} · ${isOn(power) ? "ligada" : "desligada"}${watts}` };
  }
  const t = value(d, "temperatureValue");
  return { kind: "other", on: false, text: typeof t === "number" ? `${name} · ${t} °C` : name };
}

const CHIP_ON: Record<Kind, string> = {
  light: "border-amber-400/80 [--dot:var(--color-amber-400)]",
  tv: "border-sky-400/80 [--dot:var(--color-sky-400)]",
  motion: "border-blue-400/80 [--dot:var(--color-blue-400)]",
  door: "border-orange-400/80 [--dot:var(--color-orange-400)]",
  plug: "border-emerald-400/80 [--dot:var(--color-emerald-400)]",
  other: "",
};

/** Glow of a room: the brightest light that is on (0..1). */
function roomLight(devices: Device[]): number {
  let level = 0;
  for (const d of devices) {
    if (d.type !== "bulb" || !isOn(value(d, "power"))) continue;
    const b = value(d, "brightness");
    level = Math.max(level, typeof b === "number" ? b / 100 : 1);
  }
  return level;
}

/** One log line for a change made outside the agent (the person, a scenario, the user's app). */
function describeChange(c: StateChange, name: string, room: string | undefined): string | null {
  const where = room ? ` (${room})` : "";
  if (c.capability === "power") return `${name}${where} ${isOn(c.new) ? "ligou" : "desligou"}`;
  if (c.capability === "brightness" && typeof c.new === "number") return `${name}${where} a ${c.new}%`;
  if (c.capability === "sensorState") return `${name}${where} ${c.new === "alarm" ? "abriu" : "fechou"}`;
  if (c.capability === "motionDetected" && isTrue(c.new)) return `Movimento${room ? ` na ${room}` : ""}`;
  return null; // power readings, temperatures, etc. are noise on a plan
}

export default function HousePage() {
  const { devices, byId, loading, error, reload } = useDevices();
  const { rooms } = useRooms();
  const label = useDeviceLabel();
  const now = useNow(60_000); // only for the offline check
  const [log, setLog] = useState<LogLine[]>([]);

  const roomById = new Map(rooms.map((r) => [r.id, r]));
  const roomOf = (deviceId: string) => {
    const id = byId.get(deviceId)?.roomId;
    return id ? roomById.get(id)?.name : undefined;
  };
  const push = (lines: LogLine[]) => {
    if (lines.length) setLog((l) => [...lines.reverse(), ...l].slice(0, LOG_LINES));
  };

  useLiveMessages((m) => {
    if (m.type === "state_changed") {
      const lines: LogLine[] = [];
      for (const c of m.changes) {
        // The agent's own changes are told by its decision; "_device" marks additions/removals.
        if (c.causedByAgent || c.capability.startsWith("_")) continue;
        const text = describeChange(c, label(c.deviceId), roomOf(c.deviceId));
        if (text) lines.push({ id: c.id, who: "person", text });
      }
      push(lines);
    } else if (m.type === "decision" && m.decision.actions.length > 0) {
      push([{ id: m.decision.id, who: "agent", text: m.decision.explanation }]);
    } else if (m.type === "reflex_executed" && m.actions.length > 0) {
      const names = [...new Set(m.actions.map((a) => label(a.deviceId)))].join(", ");
      push([{ id: `${m.reflexId}-${Date.now()}`, who: "reflex", text: `Reflexo aprendido, sem IA: ${names}` }]);
    }
  });

  if (error) return <Card title="Casa"><ErrorNote message={error} onRetry={reload} /></Card>;
  if (loading && devices.length === 0) return <Card title="Casa"><Loading /></Card>;

  const groups = new Map<string, Device[]>();
  for (const d of devices) {
    if (!d.enabled) continue;
    const key = d.roomId && roomById.has(d.roomId) ? d.roomId : NO_ROOM;
    groups.set(key, [...(groups.get(key) ?? []), d]);
  }
  // Rooms in name order, the entrance last (the way in and out of the house), unassigned at the end.
  const tiles: { room: Room | null; list: Device[] }[] = [
    ...rooms.filter((r) => !r.isEntrance).map((r) => ({ room: r, list: groups.get(r.id) ?? [] })),
    ...rooms.filter((r) => r.isEntrance).map((r) => ({ room: r, list: groups.get(r.id) ?? [] })),
  ].filter((t) => t.list.length > 0 || t.room?.isEntrance);
  const unassigned = groups.get(NO_ROOM) ?? [];
  if (unassigned.length) tiles.push({ room: null, list: unassigned });
  // Two columns; a tile left alone on the last row spans both so the plan has no hole.
  const wide = (i: number) => tiles.length % 2 === 1 && i === tiles.length - 1;

  return (
    <div className="grid gap-4">
      <Card title="Casa">
        {tiles.length === 0
          ? <Empty>Cria divisões e atribui-lhes dispositivos no Gestor.</Empty>
          : (
            <div className="grid gap-2.5 sm:grid-cols-2">
              {tiles.map(({ room, list }, i) => (
                <RoomTile key={room?.id ?? "none"} room={room} devices={list} wide={wide(i)} now={now} />
              ))}
            </div>
          )}
      </Card>
      <Card title="O que acontece em casa">
        {log.length === 0
          ? <Empty>À espera de movimento na casa…</Empty>
          : (
            <ul className="m-0 grid list-none gap-1.5 p-0" aria-live="polite">
              {log.map((l) => (
                <li key={l.id} className={l.who === "person" ? "text-muted" : "font-medium text-good"}>
                  <span className="flex items-center gap-1.5">
                    {l.who === "person"
                      ? <PersonIcon className="size-4 shrink-0" />
                      : l.who === "reflex"
                        ? <BoltIcon className="size-4 shrink-0" />
                        : <CheckIcon className="size-4 shrink-0" />}
                    {l.text}
                  </span>
                </li>
              ))}
            </ul>
          )}
      </Card>
    </div>
  );
}

function RoomTile({ room, devices, wide, now }: {
  room: Room | null; devices: Device[]; wide: boolean; now: number;
}) {
  const light = roomLight(devices);
  const sorted = [...devices].sort((a, b) => a.type.localeCompare(b.type) || displayName(a).localeCompare(displayName(b), "pt"));
  return (
    <section className={`relative overflow-hidden rounded-xl border border-line bg-surface-2 p-3.5 ${
      wide ? "sm:col-span-2" : ""}`}>
      {/* Warm light, faded with the room's brightest bulb. */}
      <div aria-hidden style={{ opacity: light }}
        className="pointer-events-none absolute inset-0 transition-opacity duration-[1600ms] ease-out
                   bg-[radial-gradient(circle_at_50%_30%,rgba(255,196,92,.7),rgba(255,196,92,.14)_60%,transparent_85%)]" />
      <h3 className="relative m-0 text-xs font-semibold uppercase tracking-wide text-muted">
        {room?.name ?? "Sem divisão"}
        {light > 0 && <span className="sr-only"> · luz acesa a {Math.round(light * 100)}%</span>}
      </h3>
      <ul className="relative m-0 mt-2.5 flex list-none flex-col items-start gap-2 p-0">
        {sorted.map((d) => {
          const c = chip(d);
          const offline = isOffline(d, now);
          return (
            <li key={d.id} title={d.source === "sim" ? "Simulado" : offline ? "Real · offline" : "Real"}
              className={`flex items-center gap-1.5 rounded-full border bg-surface/85 px-2.5 py-1 text-[13.5px]
                          transition-colors duration-500 ${c.on ? CHIP_ON[c.kind] : "border-line"} ${offline ? "opacity-50" : ""}`}>
              <span aria-hidden className={`size-2.5 rounded-full transition-all duration-500 ${
                c.on ? "bg-(--dot) shadow-[0_0_10px_var(--dot)]" : "bg-line"} ${
                c.on && c.kind === "motion" ? "motion-safe:animate-pulse" : ""}`} />
              {c.text}
              {d.source !== "sim" && <span className="text-[11px] font-semibold text-muted">real</span>}
            </li>
          );
        })}
        {sorted.length === 0 && <li className="text-sm text-muted">Sem dispositivos</li>}
      </ul>
    </section>
  );
}
