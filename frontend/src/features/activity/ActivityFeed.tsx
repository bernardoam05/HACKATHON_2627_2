// Owner: Team B. Live from /ws: Undo for auto_notify, Confirm/Reject for suggest, reflex badge.
// Each entry shows the essential line; details expand on demand so the feed stays light.
import { useMemo, useState } from "react";
import { api } from "../../api/client";
import type { ActionRecord, ActionStatus, AutonomyLevel, Decision } from "../../api/types";
import { describeUpdate, useDeviceLabel } from "../../live/DevicesProvider";
import { useLiveMessages } from "../../live/LiveProvider";
import { formatClock, timeAgo, useNow } from "../../lib/time";
import { errorMessage, useResource } from "../../lib/useResource";
import { Badge, Button, Card, Empty, ErrorNote, Expandable, Loading } from "../../ui";
import { DecisionCard } from "../decisions/DecisionCard";

const LIMIT = 50;

type Tone = "good" | "warn" | "neutral" | "bad";

const STATUS: Record<ActionStatus, { label: string; tone: Tone }> = {
  executed: { label: "Feito", tone: "good" },
  pending_confirmation: { label: "À espera", tone: "warn" },
  rejected: { label: "Rejeitado", tone: "neutral" },
  undone: { label: "Desfeito", tone: "neutral" },
  failed: { label: "Falhou", tone: "bad" },
};

const AUTONOMY: Record<AutonomyLevel, string> = {
  auto: "Automático",
  auto_notify: "Automático com aviso",
  suggest: "Sugestão",
};

type Item =
  | { kind: "decision"; at: string; decision: Decision; actions: ActionRecord[] }
  | { kind: "action"; at: string; action: ActionRecord };

function upsert<T extends { id: string }>(list: T[] | undefined, item: T): T[] {
  const rest = (list ?? []).filter((x) => x.id !== item.id);
  return [item, ...rest].slice(0, LIMIT * 2);
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export default function ActivityFeed() {
  const decisions = useResource(() => api.decisions(LIMIT));
  const actions = useResource(() => api.actions("", LIMIT));
  const [reflexIds, setReflexIds] = useState<ReadonlySet<string>>(new Set());

  useLiveMessages((m) => {
    if (m.type === "decision") decisions.setData((ds) => upsert(ds, m.decision));
    else if (m.type === "action_updated") actions.setData((as) => upsert(as, m.action));
    else if (m.type === "reflex_executed") {
      actions.setData((as) => m.actions.reduce((acc, a) => upsert(acc, a), as ?? []));
      setReflexIds((ids) => new Set([...ids, ...m.actions.map((a) => a.id)]));
    }
  });

  const onResolved = (a: ActionRecord) => actions.setData((as) => upsert(as, a));

  const { pending, items } = useMemo(() => {
    const all = actions.data ?? [];
    const byDecision = new Map<string, ActionRecord[]>();
    for (const a of all) byDecision.set(a.decisionId, [...(byDecision.get(a.decisionId) ?? []), a]);
    const known = new Set((decisions.data ?? []).map((d) => d.id));
    const list: Item[] = [
      ...(decisions.data ?? []).map((d): Item =>
        ({ kind: "decision", at: d.createdAt, decision: d, actions: byDecision.get(d.id) ?? [] })),
      ...all.filter((a) => !known.has(a.decisionId))
        .map((a): Item => ({ kind: "action", at: a.createdAt, action: a })),
    ];
    return {
      pending: all.filter((a) => a.status === "pending_confirmation")
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt)),
      items: list.sort((a, b) => b.at.localeCompare(a.at)).slice(0, LIMIT),
    };
  }, [actions.data, decisions.data]);

  const error = decisions.error ?? actions.error;
  const loading = (decisions.loading && !decisions.data) || (actions.loading && !actions.data);
  const reload = () => { decisions.reload(); actions.reload(); };

  return (
    <div className="grid gap-4">
      {pending.length > 0 && (
        <Card title={<span className="flex items-center gap-2">À tua espera <Badge tone="warn">{pending.length}</Badge></span>}>
          <ul className="m-0 grid list-none gap-2 p-0">
            {pending.map((a) => (
              <li key={a.id}>
                <PendingAction action={a} reason={reasonFor(a, decisions.data)} onResolved={onResolved} />
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card title="Atividade" actions={<Button variant="ghost" onClick={reload}>Atualizar</Button>}>
        {error && <div className="mb-3"><ErrorNote message={error} onRetry={reload} /></div>}
        {loading ? <Loading /> : items.length === 0 ? (
          <Empty>Ainda não aconteceu nada. Quando a casa reagir a alguma coisa, aparece aqui.</Empty>
        ) : (
          <ol className="m-0 grid list-none gap-2 p-0">
            {items.map((it) => (
              <li key={it.kind === "decision" ? `d-${it.decision.id}` : `a-${it.action.id}`}>
                {it.kind === "decision"
                  ? <DecisionEntry decision={it.decision} actions={it.actions} reflexIds={reflexIds}
                                   onResolved={onResolved} />
                  : <ActionEntry action={it.action} reflex={reflexIds.has(it.action.id)} onResolved={onResolved} />}
              </li>
            ))}
          </ol>
        )}
      </Card>
    </div>
  );
}

function reasonFor(a: ActionRecord, decisions: Decision[] | undefined): string | undefined {
  const d = decisions?.find((x) => x.id === a.decisionId);
  return d?.actions.find((p) => p.deviceId === a.deviceId && p.update.type === a.update.type)?.reason;
}

function When({ iso }: { iso: string }) {
  const now = useNow();
  return <time dateTime={iso} title={formatClock(iso)} className="text-xs text-muted">{timeAgo(iso, now)}</time>;
}

/** One badge per distinct action status, e.g. "Feito" or "À espera". */
function StatusBadges({ actions }: { actions: ActionRecord[] }) {
  const statuses = [...new Set(actions.map((a) => a.status))];
  return <>{statuses.map((s) => <Badge key={s} tone={STATUS[s].tone}>{STATUS[s].label}</Badge>)}</>;
}

function DecisionEntry({ decision, actions, reflexIds, onResolved }: {
  decision: Decision;
  actions: ActionRecord[];
  reflexIds: ReadonlySet<string>;
  onResolved: (a: ActionRecord) => void;
}) {
  const n = decision.actions.length;
  return (
    <Expandable summary={
      <div className="grid gap-1">
        <p className="m-0">{decision.explanation}</p>
        <div className="flex flex-wrap items-center gap-1.5">
          <When iso={decision.createdAt} />
          {n > 0 && <span className="text-xs text-muted">· {plural(n, "ação", "ações")}</span>}
          {actions.some((a) => reflexIds.has(a.id)) && <Badge tone="accent">Reflexo</Badge>}
          <StatusBadges actions={actions} />
        </div>
      </div>
    }>
      <DecisionCard decision={decision} showExplanation={false} />
      {actions.length > 0 && (
        <ul className="m-0 grid list-none gap-1.5 p-0">
          {actions.map((a) => (
            <li key={a.id}>
              <ActionDetails action={a} reason={reasonFor(a, [decision])} reflex={reflexIds.has(a.id)}
                             onResolved={onResolved} />
            </li>
          ))}
        </ul>
      )}
    </Expandable>
  );
}

/** An action without a known decision (e.g. a reflex execution). */
function ActionEntry({ action, reflex, onResolved }: {
  action: ActionRecord;
  reflex: boolean;
  onResolved: (a: ActionRecord) => void;
}) {
  return (
    <Expandable summary={
      <div className="grid gap-1">
        <ActionLine action={action} />
        <div className="flex flex-wrap items-center gap-1.5">
          <When iso={action.createdAt} />
          {reflex && <Badge tone="accent">Reflexo</Badge>}
          <StatusBadges actions={[action]} />
        </div>
      </div>
    }>
      <p className="m-0 text-sm text-muted">{AUTONOMY[action.autonomy]}</p>
      <ActionButtons action={action} onResolved={onResolved} />
    </Expandable>
  );
}

function ActionLine({ action }: { action: ActionRecord }) {
  const label = useDeviceLabel();
  return (
    <p className="m-0">
      <span className="font-medium">{label(action.deviceId)}</span>
      <span className="text-muted"> · {describeUpdate(action.update)}</span>
    </p>
  );
}

/** Inside an expanded decision: what was done, why, status, and Undo when allowed. */
function ActionDetails({ action, reason, reflex, onResolved }: {
  action: ActionRecord;
  reason?: string;
  reflex: boolean;
  onResolved: (a: ActionRecord) => void;
}) {
  const status = STATUS[action.status];
  return (
    <div className="grid gap-1.5 rounded-lg border border-line bg-surface px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <ActionLine action={action} />
        <div className="flex items-center gap-1.5">
          {reflex && <Badge tone="accent">Reflexo</Badge>}
          <Badge tone={status.tone}>{status.label}</Badge>
        </div>
      </div>
      <p className="m-0 text-sm text-muted">
        {reason ? `${reason} · ` : ""}{AUTONOMY[action.autonomy]}
      </p>
      <ActionButtons action={action} onResolved={onResolved} />
    </div>
  );
}

/** A suggestion waiting for the user: always open, with Confirmar / Rejeitar. */
function PendingAction({ action, reason, onResolved }: {
  action: ActionRecord;
  reason?: string;
  onResolved: (a: ActionRecord) => void;
}) {
  return (
    <div className="grid gap-2 rounded-lg border border-line bg-canvas p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <ActionLine action={action} />
        <When iso={action.createdAt} />
      </div>
      {reason && <p className="m-0 text-sm text-muted">{reason}</p>}
      <ActionButtons action={action} onResolved={onResolved} />
    </div>
  );
}

function ActionButtons({ action, onResolved }: { action: ActionRecord; onResolved: (a: ActionRecord) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const pending = action.status === "pending_confirmation";
  const canUndo = action.status === "executed" && action.autonomy === "auto_notify";
  if (!pending && !canUndo && !error) return null;

  const run = async (call: (id: string) => Promise<ActionRecord>) => {
    setBusy(true);
    setError(undefined);
    try {
      onResolved(await call(action.id));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap gap-2">
        {pending && <>
          <Button variant="primary" disabled={busy} onClick={() => run(api.confirmAction)}>Confirmar</Button>
          <Button disabled={busy} onClick={() => run(api.rejectAction)}>Rejeitar</Button>
        </>}
        {canUndo && <Button disabled={busy} onClick={() => run(api.undoAction)}>Desfazer</Button>}
      </div>
      {error && <ErrorNote message={error} />}
    </div>
  );
}
