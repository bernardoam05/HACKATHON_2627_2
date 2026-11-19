// Owner: Team B. Devices grouped by room, source badge, alias/room/enabled editing, add/remove sim devices.
import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { Capability, CapabilityUpdate, Device, JsonValue } from "../../api/types";
import { useCooldown } from "../../live/CooldownsProvider";
import { formatValue, useDevices } from "../../live/DevicesProvider";
import { blockedLabel } from "../cooldowns/CooldownOptions";
import { useRooms } from "../../live/RoomsProvider";
import { errorMessage, useResource } from "../../lib/useResource";
import {
  Badge, Button, Card, Combobox, Empty, ErrorNote, Expandable, Field, inputClass, Loading, normalize,
  type ComboOption,
} from "../../ui";

const NO_ROOM = "";

export default function DeviceList() {
  const { devices, loading, error, reload } = useDevices();
  const { rooms } = useRooms();
  const [adding, setAdding] = useState(false);

  const roomName = new Map(rooms.map((r) => [r.id, r.name]));
  const groups = new Map<string, Device[]>();
  for (const d of [...devices].sort((a, b) => displayName(a).localeCompare(displayName(b), "pt"))) {
    const key = d.roomId && roomName.has(d.roomId) ? d.roomId : NO_ROOM;
    groups.set(key, [...(groups.get(key) ?? []), d]);
  }
  const ordered = [...groups.entries()].sort(([a], [b]) =>
    a === NO_ROOM ? 1 : b === NO_ROOM ? -1 : (roomName.get(a) ?? "").localeCompare(roomName.get(b) ?? "", "pt"));

  return (
    <Card title="Dispositivos" actions={
      !adding && <Button variant="primary" onClick={() => setAdding(true)}>Adicionar simulado</Button>
    }>
      {adding && <AddSimDevice onDone={() => setAdding(false)} />}
      {error ? <ErrorNote message={error} onRetry={reload} />
        : loading && devices.length === 0 ? <Loading />
        : devices.length === 0 ? <Empty>Não há dispositivos.</Empty>
        : (
          <div className="grid gap-4">
            {ordered.map(([roomId, list]) => (
              <section key={roomId || "none"} className="grid gap-2">
                <h3 className="m-0 text-xs font-semibold uppercase tracking-wide text-muted">
                  {roomId === NO_ROOM ? "Sem divisão" : roomName.get(roomId)}
                  <span className="ml-1.5 font-normal normal-case tracking-normal">({list.length})</span>
                </h3>
                <ul className="m-0 grid list-none gap-2 p-0">
                  {list.map((d) => <li key={d.id}><DeviceItem device={d} /></li>)}
                </ul>
              </section>
            ))}
          </div>
        )}
    </Card>
  );
}

export function displayName(d: Device): string {
  return d.alias || d.name || d.id;
}

/** Names already used by other devices (original name and alias), compared without case/accents. */
function namesInUse(devices: Device[], exceptId?: string): Set<string> {
  const used = new Set<string>();
  for (const d of devices) {
    if (d.id === exceptId) continue;
    if (d.name) used.add(normalize(d.name));
    if (d.alias) used.add(normalize(d.alias));
  }
  return used;
}

/** "Lâmpada" -> "Lâmpada 2" when taken, so the suggested default is always valid. */
function uniqueName(base: string, used: Set<string>): string {
  if (!base || !used.has(normalize(base))) return base;
  let n = 2;
  while (used.has(normalize(`${base} ${n}`))) n++;
  return `${base} ${n}`;
}

const DUPLICATE_NAME = "Já existe um dispositivo com este nome.";

function cap(d: Device, type: string): Capability | undefined {
  return d.capabilities.find((c) => c.type === type);
}

const isOn = (v: JsonValue | undefined) => v === "on" || v === true;

/** One short PT-PT line with what matters for this device right now. */
function stateSummary(d: Device): string {
  const parts: string[] = [];
  const power = cap(d, "power");
  if (power) {
    parts.push(isOn(power.value) ? "Ligado" : "Desligado");
    const b = cap(d, "brightness");
    if (b && isOn(power.value) && typeof b.value === "number") parts.push(`${b.value}%`);
  }
  const contact = d.type === "contactSensor" ? cap(d, "sensorState") ?? cap(d, "contact") : undefined;
  if (contact) parts.push(contact.value === "alarm" || contact.value === true ? "Aberta" : "Fechada");
  const motion = cap(d, "motionDetected");
  if (motion && typeof motion.value === "boolean") parts.push(motion.value ? "Movimento" : "Sem movimento");
  const temp = cap(d, "temperatureValue");
  if (temp && typeof temp.value === "number") parts.push(`${temp.value} °C`);
  const watts = cap(d, "instantPower");
  if (watts && typeof watts.value === "number" && watts.value > 0) parts.push(`${watts.value} W`);
  return parts.join(" · ");
}

function DeviceItem({ device }: { device: Device }) {
  const summary = stateSummary(device);
  const cooldown = useCooldown(device.id);
  return (
    <Expandable className={device.enabled ? "" : "opacity-70"} summary={
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="m-0 font-medium">{displayName(device)}</p>
          {summary && <p className="m-0 text-sm text-muted">{summary}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {cooldown && <Badge tone="warn">{blockedLabel(cooldown)}</Badge>}
          {!device.enabled && <Badge tone="warn">Desativado</Badge>}
          <Badge tone={device.source === "sim" ? "accent" : "neutral"}>
            {device.source === "sim" ? "Simulado" : "Real"}
          </Badge>
        </div>
      </div>
    }>
      <QuickControls device={device} />
      <EditDevice device={device} />
      <AllValues device={device} />
      {device.source === "sim" && <RemoveSimDevice device={device} />}
    </Expandable>
  );
}

/** Manual control for the common writable capabilities (logged by the backend as a user action). */
function QuickControls({ device }: { device: Device }) {
  const { upsert } = useDevices();
  const power = cap(device, "power");
  const brightness = cap(device, "brightness");
  const [level, setLevel] = useState<number>(typeof brightness?.value === "number" ? brightness.value : 0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (typeof brightness?.value === "number") setLevel(brightness.value);
  }, [brightness?.value]);

  if (!power && !brightness) return null;

  const send = async (update: CapabilityUpdate) => {
    setBusy(true);
    setError(undefined);
    try {
      upsert(await api.setCapability(device.id, update));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const usesStrings = power?.availableValues?.includes("on") ?? true;
  const on = isOn(power?.value);
  const commitLevel = () => { if (level !== brightness?.value) void send({ type: "brightness", value: level }); };

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-3">
        {power && (
          <Button variant={on ? "secondary" : "primary"} disabled={busy}
                  onClick={() => send({ type: "power", value: usesStrings ? (on ? "off" : "on") : !on })}>
            {on ? "Desligar" : "Ligar"}
          </Button>
        )}
        {brightness && (
          <label className="flex min-w-48 flex-1 items-center gap-2 text-sm">
            <span className="text-muted">Brilho</span>
            <input type="range" className="flex-1 accent-accent" disabled={busy}
                   min={brightness.min ?? 0} max={brightness.max ?? 100} value={level}
                   onChange={(e) => setLevel(Number(e.target.value))}
                   onPointerUp={commitLevel} onKeyUp={commitLevel} />
            <span className="w-10 text-right tabular-nums">{level}%</span>
          </label>
        )}
      </div>
      {error && <ErrorNote message={error} />}
    </div>
  );
}

function EditDevice({ device }: { device: Device }) {
  const { upsert, devices } = useDevices();
  const { rooms } = useRooms();
  const [alias, setAlias] = useState(device.alias ?? "");
  const [roomId, setRoomId] = useState(device.roomId ?? "");
  const [enabled, setEnabled] = useState(device.enabled);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [saved, setSaved] = useState(false);

  const dirty = alias !== (device.alias ?? "") || roomId !== (device.roomId ?? "") || enabled !== device.enabled;
  const duplicate = Boolean(alias.trim()) && namesInUse(devices, device.id).has(normalize(alias));

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (duplicate) return;
    setBusy(true);
    setError(undefined);
    try {
      upsert(await api.assign(device.id, { alias: alias.trim() || null, roomId: roomId || null, enabled }));
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={save} className="grid gap-3 rounded-lg border border-line bg-surface p-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Nome a mostrar" hint={`Nome original: ${device.name || device.id}`}
               error={duplicate && DUPLICATE_NAME}>
          <input className={inputClass} value={alias} placeholder={device.name}
                 onChange={(e) => { setAlias(e.target.value); setSaved(false); }} />
        </Field>
        <Field label="Divisão" hint={device.roomName ? `Na app Altice: ${device.roomName}` : undefined}>
          <select className={inputClass} value={roomId}
                  onChange={(e) => { setRoomId(e.target.value); setSaved(false); }}>
            <option value="">Sem divisão</option>
            {rooms.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </Field>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" className="size-4 accent-accent" checked={enabled}
               onChange={(e) => { setEnabled(e.target.checked); setSaved(false); }} />
        Ativo <span className="text-muted">(a casa só observa e controla dispositivos ativos)</span>
      </label>
      <div className="flex items-center gap-3">
        <Button type="submit" variant="primary" disabled={busy || !dirty || duplicate}>Guardar</Button>
        {saved && !dirty && <span className="text-sm text-good">Guardado</span>}
      </div>
      {error && <ErrorNote message={error} />}
    </form>
  );
}

function AllValues({ device }: { device: Device }) {
  const [open, setOpen] = useState(false);
  const n = device.capabilities.length;
  if (n === 0) return null;
  return (
    <div className="text-sm">
      <Button variant="ghost" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? "Esconder valores" : `Ver todos os valores (${n})`}
      </Button>
      {open && (
        <dl className="m-0 mt-1 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
          {device.capabilities.map((c) => (
            <div key={c.type} className="contents">
              <dt className="text-muted">{c.type}</dt>
              <dd className="m-0 break-all">{formatValue(c.value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function RemoveSimDevice({ device }: { device: Device }) {
  const { remove } = useDevices();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const run = async () => {
    if (!window.confirm(`Remover "${displayName(device)}" da casa simulada?`)) return;
    setBusy(true);
    setError(undefined);
    try {
      await api.removeSimDevice(device.id);
      remove(device.id);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };
  return (
    <div className="grid gap-2">
      <div><Button variant="danger" disabled={busy} onClick={run}>Remover dispositivo</Button></div>
      {error && <ErrorNote message={error} />}
    </div>
  );
}

// ---- add a simulated device (existing template, or a brand-new type) --------------------------------

type CapKind = "onoff" | "number" | "bool" | "text";

const CAP_KINDS: Record<CapKind, string> = {
  onoff: "Ligar / desligar",
  number: "Número (0–100)",
  bool: "Sim / não",
  text: "Texto",
};

interface CapDraft { key: number; name: string; kind: CapKind }

/** "Fechadura inteligente" -> "fechaduraInteligente" (device types are identifiers, like "motionSensor"). */
function toTypeId(label: string): string {
  const words = normalize(label).replace(/[^a-z0-9]+/g, " ").trim().split(" ").filter(Boolean);
  return words.map((w, i) => (i === 0 ? w : w[0].toUpperCase() + w.slice(1))).join("");
}

function toCapability(c: CapDraft): Capability {
  const type = toTypeId(c.name);
  switch (c.kind) {
    case "onoff": return { type, name: "", value: "off", availableValues: ["on", "off"] };
    case "number": return { type, name: "", value: 0, min: 0, max: 100 };
    case "bool": return { type, name: "", value: false };
    case "text": return { type, name: "", value: "" };
  }
}

type Choice = { kind: "template"; id: string } | { kind: "new"; label: string };

function AddSimDevice({ onDone }: { onDone: () => void }) {
  const { upsert, devices } = useDevices();
  const { rooms } = useRooms();
  const templates = useResource(api.templates);
  const [choice, setChoice] = useState<Choice | null>(null);
  const [caps, setCaps] = useState<CapDraft[]>([{ key: 0, name: "", kind: "onoff" }]);
  const [name, setName] = useState("");
  const [roomId, setRoomId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const list = templates.data ?? [];
  const options: ComboOption[] = list.map((t) => ({ id: t.id, label: t.name, hint: t.name === t.type ? undefined : t.type }));
  const tpl = choice?.kind === "template" ? list.find((t) => t.id === choice.id) : undefined;
  const newType = choice?.kind === "new" ? toTypeId(choice.label) : "";

  const capIds = caps.map((c) => toTypeId(c.name)).filter(Boolean);
  const capsValid = capIds.length > 0 && capIds.length === caps.length && new Set(capIds).size === capIds.length;

  // A "new" type must really be new: not a template's type nor a type already in the house.
  const knownTypes = new Set([...list.map((t) => t.type), ...devices.map((d) => d.type)].map(normalize));
  const typeTaken = Boolean(newType) && knownTypes.has(normalize(newType));

  const used = namesInUse(devices);
  const defaultName = uniqueName(tpl?.name ?? (choice?.kind === "new" ? choice.label : ""), used);
  const finalName = name.trim() || defaultName;
  const nameTaken = Boolean(name.trim()) && used.has(normalize(name));

  const valid = !nameTaken && Boolean(finalName) && (choice?.kind === "template"
    ? Boolean(tpl)
    : choice?.kind === "new" && Boolean(newType) && !typeTaken && capsValid);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!valid || !choice) return;
    setBusy(true);
    setError(undefined);
    try {
      const common = { name: finalName, roomId: roomId || null };
      upsert(await api.addSimDevice(tpl
        ? { ...common, type: tpl.type, template: tpl.id }
        : { ...common, type: newType, template: null, capabilities: caps.map(toCapability) }));
      onDone();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="mb-4 grid gap-3 rounded-lg border border-line bg-canvas p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="grid gap-0.5">
          <h3 className="m-0 text-sm font-semibold">Novo dispositivo simulado</h3>
          <p className="m-0 text-sm text-muted">
            Um tipo que a casa não conhece pede configuração a seguir.
          </p>
        </div>
        <Button type="button" onClick={onDone}>Fechar</Button>
      </div>
      {templates.error ? <ErrorNote message={templates.error} onRetry={templates.reload} /> : (
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Tipo" hint={choice?.kind === "new" ? `Tipo novo: ${newType || "—"}` : undefined}
                 error={typeTaken && `O tipo "${newType}" já existe. Escolhe-o na lista.`}>
            <Combobox ariaLabel="Tipo" options={options} placeholder="Procurar tipo…"
                      value={choice?.kind === "template" ? choice.id : null}
                      onSelect={(id) => setChoice({ kind: "template", id })}
                      onCreate={(label) => setChoice({ kind: "new", label })}
                      createLabel={(q) => `Adicionar tipo novo "${q}"`} />
          </Field>
          <Field label="Nome" error={nameTaken && DUPLICATE_NAME}>
            <input className={inputClass} value={name} placeholder={defaultName}
                   onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Divisão">
            <select className={inputClass} value={roomId} onChange={(e) => setRoomId(e.target.value)}>
              <option value="">Sem divisão</option>
              {rooms.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </select>
          </Field>
        </div>
      )}
      {choice?.kind === "new" && <NewTypeCapabilities caps={caps} onChange={setCaps} />}
      <div>
        <Button type="submit" variant="primary" disabled={busy || !valid}>Adicionar</Button>
      </div>
      {error && <ErrorNote message={error} />}
    </form>
  );
}

/** What a brand-new type can do, in plain terms; each row becomes one Altice-shaped capability. */
function NewTypeCapabilities({ caps, onChange }: { caps: CapDraft[]; onChange: (c: CapDraft[]) => void }) {
  const set = (key: number, patch: Partial<CapDraft>) =>
    onChange(caps.map((c) => (c.key === key ? { ...c, ...patch } : c)));
  const ids = caps.map((c) => toTypeId(c.name));
  return (
    <fieldset className="m-0 grid gap-2 rounded-lg border border-line bg-surface p-3">
      <legend className="px-1 text-sm font-medium">O que este tipo tem</legend>
      {caps.map((c, i) => {
        const dup = ids[i] && ids.indexOf(ids[i]) !== i;
        return (
          <div key={c.key} className="grid gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <input className={`${inputClass} min-w-40 flex-1`} value={c.name} placeholder="ex. trancada, bateria"
                     aria-label="Nome da capacidade" onChange={(e) => set(c.key, { name: e.target.value })} />
              <select className={`${inputClass} w-auto`} value={c.kind} aria-label="Formato"
                      onChange={(e) => set(c.key, { kind: e.target.value as CapKind })}>
                {Object.entries(CAP_KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              {caps.length > 1 && (
                <Button variant="ghost" onClick={() => onChange(caps.filter((x) => x.key !== c.key))}>Remover</Button>
              )}
            </div>
            {dup && <span className="text-xs text-bad">Nome repetido.</span>}
          </div>
        );
      })}
      <div>
        <Button variant="ghost"
                onClick={() => onChange([...caps, { key: Math.max(...caps.map((c) => c.key)) + 1, name: "", kind: "number" }])}>
          + Adicionar capacidade
        </Button>
      </div>
    </fieldset>
  );
}
