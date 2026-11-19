// Owner: Team B. Insights with confidence bars, evidence, delete.
import { useState } from "react";
import { api } from "../../api/client";
import type { Insight } from "../../api/types";
import { SAMPLE_MODE, sampleInsights } from "../../dev/samples";
import { useLiveMessages } from "../../live/LiveProvider";
import { formatClock, timeAgo, useNow } from "../../lib/time";
import { errorMessage, useResource } from "../../lib/useResource";
import { Button, Card, Empty, ErrorNote, Expandable, Loading } from "../../ui";

const byConfidence = (a: Insight, b: Insight) => b.confidence - a.confidence;

export default function InsightsList() {
  const insights = useResource(SAMPLE_MODE ? async () => sampleInsights : api.insights);

  useLiveMessages((m) => {
    if (m.type !== "insight_updated") return;
    insights.setData((list) => {
      const rest = (list ?? []).filter((i) => i.id !== m.insight.id);
      return m.insight.status === "active" ? [...rest, m.insight] : rest;
    });
  });

  const remove = (id: string) => insights.setData((list) => (list ?? []).filter((i) => i.id !== id));
  const list = [...(insights.data ?? [])].sort(byConfidence);

  return (
    <Card title="O que a casa aprendeu">
      {insights.error ? <ErrorNote message={insights.error} onRetry={insights.reload} />
        : !insights.data ? <Loading />
        : list.length === 0 ? <Empty>Ainda nada. À medida que a casa repara em padrões, aparecem aqui.</Empty>
        : (
          <ul className="m-0 grid list-none gap-2 p-0">
            {list.map((i) => <li key={i.id}><InsightItem insight={i} onDeleted={remove} /></li>)}
          </ul>
        )}
    </Card>
  );
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  const tone = pct >= 75 ? "bg-good" : pct >= 45 ? "bg-accent" : "bg-warn";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-surface-2" role="meter" aria-valuenow={pct}
           aria-valuemin={0} aria-valuemax={100} aria-label="Confiança">
        <div className={`h-full rounded-full ${tone}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs tabular-nums text-muted">{pct}%</span>
    </div>
  );
}

function InsightItem({ insight, onDeleted }: { insight: Insight; onDeleted: (id: string) => void }) {
  const now = useNow();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const remove = async () => {
    if (!window.confirm("Esquecer isto? A casa deixa de usar este padrão.")) return;
    setBusy(true);
    setError(undefined);
    try {
      if (!SAMPLE_MODE) await api.deleteInsight(insight.id);
      onDeleted(insight.id);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };

  const n = insight.evidence.length;
  return (
    <Expandable summary={
      <div className="grid gap-1.5">
        <p className="m-0">{insight.statement}</p>
        <ConfidenceBar value={insight.confidence} />
      </div>
    }>
      <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted">Visto pela primeira vez</dt>
        <dd className="m-0" title={formatClock(insight.createdAt)}>{timeAgo(insight.createdAt, now)}</dd>
        <dt className="text-muted">Confirmado</dt>
        <dd className="m-0" title={formatClock(insight.lastConfirmedAt)}>{timeAgo(insight.lastConfirmedAt, now)}</dd>
        <dt className="text-muted">Baseado em</dt>
        <dd className="m-0">{n} {n === 1 ? "observação" : "observações"}</dd>
      </dl>
      <div>
        <Button variant="danger" disabled={busy} onClick={remove}>Esquecer</Button>
      </div>
      {error && <ErrorNote message={error} />}
    </Expandable>
  );
}
