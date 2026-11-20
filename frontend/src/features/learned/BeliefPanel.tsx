// Owner: Team B. Shows current beliefState ("what the house thinks is happening").
import { api } from "../../api/client";
import { useLiveMessages } from "../../live/LiveProvider";
import { formatClock, timeAgo, useNow } from "../../lib/time";
import { useResource } from "../../lib/useResource";
import { Button, Card, Empty, ErrorNote, Loading, RefreshIcon } from "../../ui";

export default function BeliefPanel() {
  const status = useResource(api.agentStatus);
  const now = useNow();

  // Every decision carries the agent's updated belief: show it without refetching.
  useLiveMessages((m) => {
    if (m.type === "decision") {
      status.setData((s) => s && { ...s, beliefState: m.decision.beliefState, lastRunAt: m.decision.createdAt });
    }
  });

  const s = status.data;
  return (
    <Card title="O que a casa pensa que está a acontecer"
          actions={<Button variant="ghost" onClick={status.reload} aria-label="Atualizar" title="Atualizar"><RefreshIcon className="size-4" /></Button>}>
      {status.error ? <ErrorNote message={status.error} onRetry={status.reload} />
        : !s ? <Loading />
        : !s.beliefState ? <Empty>A casa ainda não formou uma ideia do que se passa.</Empty>
        : (
          <div className="grid gap-1.5">
            <p className="m-0 whitespace-pre-wrap">{s.beliefState}</p>
            {s.lastRunAt && (
              <p className="m-0 text-xs text-muted">
                Atualizado <time dateTime={s.lastRunAt} title={formatClock(s.lastRunAt)}>{timeAgo(s.lastRunAt, now)}</time>
              </p>
            )}
          </div>
        )}
    </Card>
  );
}
