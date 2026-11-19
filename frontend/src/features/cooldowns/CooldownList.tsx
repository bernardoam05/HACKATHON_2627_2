// Owner: Team B. Active blocks (D9) in the Gestor tab, with the same options as the notice.
import { useCooldowns } from "../../live/CooldownsProvider";
import { useDeviceLabel } from "../../live/DevicesProvider";
import { useNow } from "../../lib/time";
import { Badge, Card, Empty, ErrorNote, Expandable, Loading } from "../../ui";
import { blockedLabel, CooldownOptions, remaining } from "./CooldownOptions";

const REASON = {
  manual_override: "Contrariaste uma ação",
  undo: "Desfizeste uma ação",
  user: "Bloqueado por ti",
} as const;

/** DOM id of a block's entry, so other screens can link to it. */
export const cooldownAnchor = (deviceId: string) => `bloqueio-${deviceId}`;

export default function CooldownList() {
  const { cooldowns, loading, error, reload } = useCooldowns();
  const label = useDeviceLabel();
  const now = useNow(10_000);
  const list = [...cooldowns].sort((a, b) => b.startedAt.localeCompare(a.startedAt));

  return (
    <Card title="Dispositivos bloqueados">
      <p className="m-0 mb-3 text-sm text-muted">
        Quando contrarias a casa, ela deixa esse dispositivo em paz durante algum tempo.
      </p>
      {error ? <ErrorNote message={error} onRetry={reload} />
        : loading && list.length === 0 ? <Loading />
        : list.length === 0 ? <Empty>Nenhum dispositivo bloqueado.</Empty>
        : (
          <ul className="m-0 grid list-none gap-2 p-0">
            {list.map((c) => (
              <li key={c.deviceId} id={cooldownAnchor(c.deviceId)} className="scroll-mt-4">
                <Expandable summary={
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="min-w-0">
                      <p className="m-0 font-medium">{label(c.deviceId)}</p>
                      <p className="m-0 text-sm text-muted">{REASON[c.reason]} · {remaining(c, now)}</p>
                    </div>
                    <Badge tone="warn">{blockedLabel(c)}</Badge>
                  </div>
                }>
                  {c.note && <p className="m-0 text-sm">{c.note}</p>}
                  <CooldownOptions cooldown={c} />
                </Expandable>
              </li>
            ))}
          </ul>
        )}
    </Card>
  );
}
