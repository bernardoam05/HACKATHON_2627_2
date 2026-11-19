// Owner: Team B. One agent Decision: explanation, what it inferred, and the actions as chips.
import type { Decision, DecisionTrigger } from "../../api/types";
import { describeUpdate, useDeviceLabel } from "../../live/DevicesProvider";
import { Badge } from "../../ui";

const TRIGGER_LABEL: Record<DecisionTrigger, string> = {
  state_change: "Mudança em casa",
  chat: "Pedido",
  startup: "Arranque",
  reflex_report: "Reflexo",
};

export function DecisionCard({ decision, showTrigger = true }: { decision: Decision; showTrigger?: boolean }) {
  const label = useDeviceLabel();
  const { explanation, inferredRoom, inferredActivity, actions, confidence } = decision;
  return (
    <article className="grid gap-2 rounded-lg border border-line bg-canvas p-3">
      <p className="m-0">{explanation}</p>
      <div className="flex flex-wrap items-center gap-1.5">
        {showTrigger && <Badge>{TRIGGER_LABEL[decision.trigger]}</Badge>}
        {inferredRoom && <Badge tone="accent">📍 {inferredRoom}</Badge>}
        {inferredActivity && <Badge tone="accent">{inferredActivity}</Badge>}
        <span className="text-xs text-muted">Confiança {Math.round(confidence * 100)}%</span>
      </div>
      {actions.length > 0 && (
        <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
          {actions.map((a, i) => (
            <li key={i} title={a.reason}
                className="rounded-md border border-line bg-surface px-2 py-1 text-sm">
              <span className="font-medium">{label(a.deviceId)}</span>
              <span className="text-muted"> · {describeUpdate(a.update)}</span>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
