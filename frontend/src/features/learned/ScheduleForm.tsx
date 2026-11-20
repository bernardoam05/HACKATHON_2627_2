// Owner: Team B. "Novo agendamento": a time-based reflex created by the user (POST /api/reflexes).
// Runs every day (or on the chosen weekdays) without the LLM, unless "perguntar à casa antes" is on.
import { useMemo, useState, type FormEvent } from "react";
import { ApiError, api } from "../../api/client";
import type { CapabilityUpdate, Device, Reflex, ReflexTrigger } from "../../api/types";
import { SAMPLE_MODE } from "../../dev/samples";
import { useDevices } from "../../live/DevicesProvider";
import { errorMessage } from "../../lib/useResource";
import { Button, ErrorNote, Field, inputClass } from "../../ui";

// Python's weekday(): 0 = Monday (agent/reflexes.py).
const WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"];
const WORKDAYS = [0, 1, 2, 3, 4];

type When = "time" | "first_motion";
type Act = "on" | "off" | "brightness";

/** Devices a schedule can drive: those that can be switched (power) or dimmed (brightness). */
function controllable(devices: Device[]): Device[] {
  return devices.filter((d) => d.enabled && d.capabilities.some((c) => c.type === "power" || c.type === "brightness"));
}

function toUpdate(act: Act, brightness: number): CapabilityUpdate {
  if (act === "brightness") return { type: "brightness", value: brightness };
  return { type: "power", value: act };
}

export default function ScheduleForm({ onCreated }: { onCreated: (r: Reflex) => void }) {
  const { devices } = useDevices();
  const options = useMemo(() => controllable(devices), [devices]);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [when, setWhen] = useState<When>("time");
  const [at, setAt] = useState("07:00");
  const [delay, setDelay] = useState(30);
  const [days, setDays] = useState<number[]>([]); // empty = every day
  const [deviceId, setDeviceId] = useState("");
  const [act, setAct] = useState<Act>("on");
  const [brightness, setBrightness] = useState(50);
  const [askAgent, setAskAgent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const device = options.find((d) => d.id === deviceId);
  const canDim = Boolean(device?.capabilities.some((c) => c.type === "brightness"));
  const ready = Boolean(name.trim() && deviceId && (when === "first_motion" || at));

  const toggleDay = (d: number) =>
    setDays((ds) => (ds.includes(d) ? ds.filter((x) => x !== d) : [...ds, d].sort((a, b) => a - b)));

  const reset = () => {
    setName(""); setWhen("time"); setAt("07:00"); setDelay(30); setDays([]);
    setDeviceId(""); setAct("on"); setBrightness(50); setAskAgent(false);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!ready || !device) return;
    const weekdays = days.length > 0 ? days : null;
    const trigger: ReflexTrigger = when === "time"
      ? { kind: "time", deviceId: "", capability: "", operator: "=", at, weekdays }
      : { kind: "first_motion", deviceId: "", capability: "", operator: "=", delayMinutes: delay, weekdays };
    const body = {
      name: name.trim(),
      triggers: [trigger],
      actions: [{ deviceId, update: toUpdate(act, brightness), reason: "Agendamento criado pelo utilizador" }],
      askAgent,
    };
    setBusy(true);
    setError(undefined);
    try {
      const created: Reflex = SAMPLE_MODE
        ? { ...body, id: `rfx-${Date.now()}`, triggerLogic: "all", status: "active", createdAt: new Date().toISOString(),
            executions: 0, undos: 0, createdBy: "user" }
        : await api.createReflex(body);
      onCreated(created);
      reset();
      setOpen(false);
    } catch (err) {
      // 409 here means the action cannot be automated (it needs confirmation every time, e.g. TV, alarm).
      setError(err instanceof ApiError && err.status === 409
        ? "Essa ação precisa da tua confirmação de cada vez (por exemplo TV ou alarme), por isso não pode ser agendada."
        : errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <div className="mb-3">
        <Button onClick={() => setOpen(true)}>Novo agendamento</Button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="mb-4 grid gap-3 rounded-lg border border-line bg-canvas p-3">
      <Field label="Nome">
        <input className={inputClass} value={name} placeholder="ex. Acordar com luz"
               onChange={(e) => setName(e.target.value)} />
      </Field>

      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-44 flex-1">
          <Field label="Quando">
            <select className={inputClass} value={when} onChange={(e) => setWhen(e.target.value as When)}>
              <option value="time">A uma hora certa</option>
              <option value="first_motion">Depois de acordar (primeiro movimento do dia)</option>
            </select>
          </Field>
        </div>
        {when === "time" ? (
          <Field label="Hora">
            <input type="time" className={inputClass} value={at} required onChange={(e) => setAt(e.target.value)} />
          </Field>
        ) : (
          <Field label="Minutos depois">
            <input type="number" min={0} max={720} className={inputClass} value={delay}
                   onChange={(e) => setDelay(Math.max(0, Math.min(720, Number(e.target.value) || 0)))} />
          </Field>
        )}
      </div>

      <div className="grid gap-1 text-sm">
        <span className="font-medium">Dias</span>
        <div className="flex flex-wrap items-center gap-1.5">
          {WEEKDAYS.map((label, d) => (
            <button key={d} type="button" onClick={() => toggleDay(d)} aria-pressed={days.includes(d)}
              className={`cursor-pointer rounded-full border px-2.5 py-0.5 text-xs ${days.includes(d)
                ? "border-accent bg-accent-soft text-accent" : "border-line bg-surface text-muted"}`}>
              {label}
            </button>
          ))}
          <Button variant="ghost" type="button" onClick={() => setDays(WORKDAYS)}>Dias úteis</Button>
          <Button variant="ghost" type="button" onClick={() => setDays([])}>Todos</Button>
        </div>
        <span className="text-xs text-muted">{days.length === 0 ? "Todos os dias." : "Só nos dias marcados."}</span>
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-48 flex-1">
          <Field label="Dispositivo">
            <select className={inputClass} value={deviceId} required
                    onChange={(e) => { setDeviceId(e.target.value); setAct("on"); }}>
              <option value="">Escolhe…</option>
              {options.map((d) => <option key={d.id} value={d.id}>{d.alias || d.name || d.id}</option>)}
            </select>
          </Field>
        </div>
        <Field label="Ação">
          <select className={inputClass} value={act} onChange={(e) => setAct(e.target.value as Act)}>
            <option value="on">Ligar</option>
            <option value="off">Desligar</option>
            {canDim && <option value="brightness">Brilho…</option>}
          </select>
        </Field>
        {act === "brightness" && (
          <Field label="Brilho (%)">
            <input type="number" min={1} max={100} className={inputClass} value={brightness}
                   onChange={(e) => setBrightness(Math.max(1, Math.min(100, Number(e.target.value) || 1)))} />
          </Field>
        )}
      </div>

      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-0.5 size-4 accent-accent" checked={askAgent}
               onChange={(e) => setAskAgent(e.target.checked)} />
        <span>
          Perguntar à casa antes
          <span className="block text-muted">
            À hora marcada a casa decide se faz sentido (por exemplo, só se houver alguém em casa). Sem isto, faz sempre.
          </span>
        </span>
      </label>

      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="primary" disabled={busy || !ready}>Criar agendamento</Button>
        <Button type="button" disabled={busy} onClick={() => { reset(); setOpen(false); setError(undefined); }}>
          Cancelar
        </Button>
      </div>
      {error && <ErrorNote message={error} />}
    </form>
  );
}
