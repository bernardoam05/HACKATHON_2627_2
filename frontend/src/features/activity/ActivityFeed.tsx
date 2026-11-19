// Owner: Team B. Live from /ws: Undo for auto_notify, Confirm/Reject for suggest, reflex badge.
import { useMemo, useState } from "react";
import { api } from "../../api/client";
import type { ActionRecord, ActionStatus, AutonomyLevel, Decision } from "../../api/types";
import { describeUpdate, useDeviceLabel } from "../../live/DevicesProvider";
import { useLiveMessages } from "../../live/LiveProvider";
import { formatClock, timeAgo, useNow } from "../../lib/time";
import { errorMessage, useResource } from "../../lib/useResource";
import { Badge, Button, Card, Empty, ErrorNote, Loading } from "../../ui";
import { DecisionCard } from "../decisions/DecisionCard";

const LIMIT = 50;

const STATUS: Record<ActionStatus, { label: string; tone: "good" | "warn" | "neutral" | "bad" }> = {
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

const newestFirst = (a: { at: string }, b: { at: string }) => b.at.localeCompare(a.at);

export default function ActivityFeed() {
  const decisions = useResource(() => api.decisions(LIMIT));
  const actions = useResource(() => api.actions("", LIMIT));
  const [reflexActionIds, setReflexActionIds] = useState<ReadonlySet<string>>(new Set());

  useLiveMessages((m) => {
    if (m.type === "decision") decisions.setData((ds) => upsert(ds, m.decision));
    else if (m.type === "action_updated") actions.setData((as) => upsert(as, m.action));
    else if (m.type === "reflex_executed") {
      actions.setData((as) => m.actions.reduce((acc, a) => upsert(acc, a), as ?? []));
      setReflexActionIds((ids) => new Set([...ids, ...m.actions.map((a) => a.id)]));
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
      ...all.filter((a) => !known.has(a.decisionId)).map((a): Item => ({ kind: "action", at: a.createdAt, action: a })),
    ];
    return {
      pending: all.filter((a) => a.status === "pending_confirmation")
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt)),
      items: list.sort(newestFirst).slice(0, LIMIT),
    };
  }, [actions.data, decisions.data]);

  const error = decisions.error ?? actions.error;
  const loading = (decisions.loading && !decisions.data) || (actions.loading && !actions.data);
  const reload = () => { decisions.reload(); actions.reload(); };

  return (
    <div className="grid gap-4">
      {pending.length > 0 && (
        <Card title={<>À tua espera <Badge tone="warn">{pending.length}</Badge></>}>
          <ul className="m-0 grid list-none gap-2 p-0">
            {pending.map((a) => (
              <li key={a.id}>
                <ActionRow action={a} reason={reasonFor(a, decisions.data)} reflex={reflexActionIds.has(a.id)}
                           onResolved={onResolved} />
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
          <ol className="m-0 grid list-none gap-3 p-0">
            {items.map((it) => (
              <li key={it.kind === "decision" ? `d-${it.decision.id}` : `a-${it.action.id}`}>
                {it.kind === "decision"
                  ? <DecisionEntry decision={it.decision} actions={it.actions} reflexIds={reflexActionIds}
                                   onResolved={onResolved} />
                  : <ActionRow action={it.action} reflex={reflexActionIds.has(it.action.id)}
                               onResolved={onResolved} showTime />}
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

function DecisionEntry({ decision, actions, reflexIds, onResolved }: {
  decision: Decision;
  actions: ActionRecord[];
  reflexIds: ReadonlySet<string>;
  onResolved: (a: ActionRecord) => void;
}) {
  return (
    <div className="grid gap-2">
      <When iso={decision.createdAt} />
      <DecisionCard decision={decision} />
      {actions.length > 0 && (
        <ul className="m-0 grid list-none gap-1.5 p-0 pl-3 sm:pl-4">
          {actions.map((a) => (
            <li key={a.id}>
              <ActionRow action={a} reason={reasonFor(a, [decision])} reflex={reflexIds.has(a.id)}
                         onResolved={onResolved} compact />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ActionRow({ action, reason, reflex, onResolved, compact = false, showTime = false }: {
  action: ActionRecord;
  reason?: string;
  reflex: boolean;
  onResolved: (a: ActionRecord) => void;
  compact?: boolean;
  showTime?: boolean;
}) {
  const label = useDeviceLabel();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const status = STATUS[action.status];

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

  // Inside a decision (compact) pending actions only show their badge: the buttons live in "À tua espera".
  const pending = action.status === "pending_confirmation" && !compact;
  const canUndo = action.status === "executed" && action.autonomy === "auto_notify";

  return (
    <div className={`grid gap-1.5 rounded-lg border border-line ${compact ? "bg-surface px-3 py-2" : "bg-canvas p-3"}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          {reflex && <Badge tone="accent">⚡ Reflexo</Badge>}
          <span className="font-medium">{label(action.deviceId)}</span>
          <span className="text-muted">· {describeUpdate(action.update)}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Badge tone={status.tone}>{status.label}</Badge>
          {showTime && <When iso={action.createdAt} />}
        </div>
      </div>
      {(reason || !compact) && (
        <p className="m-0 text-sm text-muted">
          {reason}
          {reason && !compact && " · "}
          {!compact && AUTONOMY[action.autonomy]}
        </p>
      )}
      {(pending || canUndo) && (
        <div className="flex flex-wrap gap-2">
          {pending && <>
            <Button variant="primary" disabled={busy} onClick={() => run(api.confirmAction)}>Confirmar</Button>
            <Button disabled={busy} onClick={() => run(api.rejectAction)}>Rejeitar</Button>
          </>}
          {canUndo && <Button disabled={busy} onClick={() => run(api.undoAction)}>↩ Desfazer</Button>}
        </div>
      )}
      {error && <ErrorNote message={error} />}
    </div>
  );
}
