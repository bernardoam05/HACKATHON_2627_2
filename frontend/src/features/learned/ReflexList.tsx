// Owner: Team B. Confirm (proposed), Pause/Resume, Delete.
// Proposed reflexes wait for the user and stay open; the rest show one line and expand on demand.
import { useState } from "react";
import { api } from "../../api/client";
import type { Reflex, ReflexStatus, ReflexTrigger } from "../../api/types";
import { SAMPLE_MODE, sampleReflexes } from "../../dev/samples";
import { describeUpdate, formatValue, useDeviceLabel } from "../../live/DevicesProvider";
import { useLiveMessages } from "../../live/LiveProvider";
import { timeAgo } from "../../lib/time";
import { errorMessage, useResource } from "../../lib/useResource";
import { Badge, Button, Card, Empty, ErrorNote, Expandable, Loading } from "../../ui";
import ScheduleForm from "./ScheduleForm";

const STATUS: Record<ReflexStatus, { label: string; tone: "good" | "warn" | "neutral" }> = {
  proposed: { label: "Proposto", tone: "warn" },
  active: { label: "Ativo", tone: "good" },
  paused: { label: "Em pausa", tone: "neutral" },
  deleted: { label: "Apagado", tone: "neutral" },
};

// Assumes Python's weekday(): 0 = Monday. Check against agent/reflexes.py if days look shifted.
const WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"];

const CAPABILITY: Record<string, string> = {
  motionDetected: "deteta movimento",
  power: "energia",
  sensorState: "estado",
  brightness: "brilho",
  instantPower: "consumo",
};

const OPERATOR: Record<ReflexTrigger["operator"], string> = { "=": "=", "<": "<", ">": ">", changed: "muda" };

function upsert(list: Reflex[] | undefined, r: Reflex): Reflex[] {
  const rest = (list ?? []).filter((x) => x.id !== r.id);
  return r.status === "deleted" ? rest : [r, ...rest];
}

export default function ReflexList() {
  const reflexes = useResource(SAMPLE_MODE ? async () => sampleReflexes : () => api.reflexes());

  useLiveMessages((m) => {
    if (m.type === "reflex_proposed" || m.type === "reflex_updated") reflexes.setData((l) => upsert(l, m.reflex));
  });

  const update = (r: Reflex) => reflexes.setData((l) => upsert(l, r));
  const all = (reflexes.data ?? []).filter((r) => r.status !== "deleted");
  const proposed = all.filter((r) => r.status === "proposed");
  const others = all.filter((r) => r.status !== "proposed");

  return (
    <Card title="Reflexos">
      <p className="m-0 mb-3 text-sm text-muted">
        Ações que a casa faz sozinha, de imediato, depois de as aprovares. Os agendamentos correm à hora
        marcada (ou depois de acordares), todos os dias.
      </p>
      <ScheduleForm onCreated={update} />
      {reflexes.error ? <ErrorNote message={reflexes.error} onRetry={reflexes.reload} />
        : !reflexes.data ? <Loading />
        : all.length === 0 ? <Empty>Ainda não há reflexos. Cria um agendamento ou espera que a casa proponha um quando tiver confiança num padrão.</Empty>
        : (
          <div className="grid gap-2">
            {proposed.map((r) => <ProposedReflex key={r.id} reflex={r} onUpdate={update} />)}
            {others.map((r) => <ReflexItem key={r.id} reflex={r} onUpdate={update} />)}
          </div>
        )}
    </Card>
  );
}

type Op = "confirm" | "pause" | "resume" | "delete";

function useReflexOps(reflex: Reflex, onUpdate: (r: Reflex) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const run = async (op: Op) => {
    if (op === "delete" && !window.confirm(`Apagar o reflexo "${reflex.name}"?`)) return;
    setBusy(true);
    setError(undefined);
    try {
      if (SAMPLE_MODE) {
        const status: ReflexStatus = op === "delete" ? "deleted" : op === "pause" ? "paused" : "active";
        onUpdate({ ...reflex, status });
      } else if (op === "delete") {
        await api.deleteReflex(reflex.id);
        onUpdate({ ...reflex, status: "deleted" });
      } else {
        const call = { confirm: api.confirmReflex, pause: api.pauseReflex, resume: api.resumeReflex }[op];
        onUpdate(await call(reflex.id));
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

function ProposedReflex({ reflex, onUpdate }: { reflex: Reflex; onUpdate: (r: Reflex) => void }) {
  const { busy, error, run } = useReflexOps(reflex, onUpdate);
  return (
    <div className="grid gap-2 rounded-lg border border-warn/40 bg-warn-soft/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{reflex.name}</span>
        <Badge tone="warn">Proposto</Badge>
      </div>
      <ReflexRule reflex={reflex} />
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" disabled={busy} onClick={() => run("confirm")}>Aprovar</Button>
        <Button disabled={busy} onClick={() => run("delete")}>Recusar</Button>
      </div>
      {error && <ErrorNote message={error} />}
    </div>
  );
}

function ReflexItem({ reflex, onUpdate }: { reflex: Reflex; onUpdate: (r: Reflex) => void }) {
  const { busy, error, run } = useReflexOps(reflex, onUpdate);
  const status = STATUS[reflex.status];
  return (
    <Expandable summary={
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">{reflex.name}</span>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted">{reflex.executions}× usado</span>
          {reflex.createdBy === "user" && <Badge tone="accent">Criado por ti</Badge>}
          <Badge tone={status.tone}>{status.label}</Badge>
        </div>
      </div>
    }>
      <ReflexRule reflex={reflex} />
      {reflex.askAgent && (
        <p className="m-0 text-sm text-muted">À hora marcada, a casa pergunta-se primeiro se faz sentido (por exemplo, se há alguém em casa).</p>
      )}
      {reflex.lastFiredAt && (
        <p className="m-0 text-xs text-muted">Última vez: {timeAgo(reflex.lastFiredAt)}.</p>
      )}
      {reflex.undos > 0 && (
        <p className="m-0 text-sm text-muted">
          Desfeito {reflex.undos} {reflex.undos === 1 ? "vez" : "vezes"}. Se continuares a desfazer, a casa põe-no em pausa.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {reflex.status === "active" && <Button disabled={busy} onClick={() => run("pause")}>Pausar</Button>}
        {reflex.status === "paused" && <Button disabled={busy} onClick={() => run("resume")}>Retomar</Button>}
        <Button variant="danger" disabled={busy} onClick={() => run("delete")}>Apagar</Button>
      </div>
      {error && <ErrorNote message={error} />}
    </Expandable>
  );
}

/** "Quando … → faz …" in plain PT-PT. */
function ReflexRule({ reflex }: { reflex: Reflex }) {
  const label = useDeviceLabel();
  const joiner = reflex.triggerLogic === "all" ? " e " : " ou ";
  return (
    <div className="grid gap-1 text-sm">
      <p className="m-0">
        <span className="text-muted">Quando </span>
        {reflex.triggers.map((t, i) => (
          <span key={i}>{i > 0 && <span className="text-muted">{joiner}</span>}{describeTrigger(t, label)}</span>
        ))}
      </p>
      <p className="m-0">
        <span className="text-muted">faz </span>
        {reflex.actions.map((a, i) => (
          <span key={i}>
            {i > 0 && <span className="text-muted">, </span>}
            <span className="font-medium">{label(a.deviceId)}</span> {describeUpdate(a.update).toLowerCase()}
          </span>
        ))}
      </p>
    </div>
  );
}

function describeDays(weekdays: number[] | null | undefined): string {
  if (!weekdays?.length) return "";
  const days = [...weekdays].sort((a, b) => a - b);
  if (days.join() === "0,1,2,3,4") return " (dias úteis)";
  if (days.join() === "5,6") return " (fim de semana)";
  return ` (${days.map((d) => WEEKDAYS[d] ?? d).join(", ")})`;
}

function describeTrigger(t: ReflexTrigger, label: (id: string) => string): string {
  if (t.kind === "time") return `são ${t.at ?? "—"}${describeDays(t.weekdays)}`;
  if (t.kind === "first_motion") {
    const delay = t.delayMinutes ?? 0;
    const when = delay > 0 ? `passam ${delay} min desde que acordaste` : "acordas";
    return `${when} (primeiro movimento do dia)${describeDays(t.weekdays)}`;
  }
  const cap = CAPABILITY[t.capability] ?? t.capability;
  let text = t.operator === "changed"
    ? `${label(t.deviceId)}: ${cap} muda`
    : t.capability === "motionDetected" && t.value === true
      ? `${label(t.deviceId)} deteta movimento`
      : `${label(t.deviceId)}: ${cap} ${OPERATOR[t.operator]} ${formatValue(t.value)}`;
  const w = t.timeWindow;
  if (w) {
    text += `, entre as ${w.from} e as ${w.to}`;
    if (w.weekdays?.length) {
      const days = [...w.weekdays].sort((a, b) => a - b);
      const weekdaysOnly = days.join() === "0,1,2,3,4";
      text += weekdaysOnly ? " (dias úteis)" : ` (${days.map((d) => WEEKDAYS[d] ?? d).join(", ")})`;
    }
  }
  return text;
}
