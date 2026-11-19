// Owner: Team B. What the user can do with a block (D9): shorter/longer, until unblocked, unblock now,
// remember the duration for this device, or never block it. Shared by the notice and the Gestor list.
import { useId, useState } from "react";
import { api } from "../../api/client";
import type { CooldownUpdate, DeviceCooldown } from "../../api/types";
import { useCooldowns } from "../../live/CooldownsProvider";
import { errorMessage } from "../../lib/useResource";
import { Button, ErrorNote } from "../../ui";

/** "até às 21:40", "durante mais 18 min" or "até desbloqueares". */
export function remaining(c: DeviceCooldown, now: number): string {
  if (!c.until) return "até desbloqueares";
  const ms = new Date(c.until).getTime() - now;
  const minutes = Math.max(1, Math.ceil(ms / 60_000));
  return minutes <= 60 ? `durante mais ${minutes} min` : `até às ${clock(c.until)}`;
}

/** Short badge text: "Bloqueado até 21:40" or "Bloqueado". */
export function blockedLabel(c: DeviceCooldown): string {
  return c.until ? `Bloqueado até ${clock(c.until)}` : "Bloqueado";
}

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" });
}

type Choice = "5" | "60" | "until" | "unblock" | "never";

export function CooldownOptions({ cooldown, onKeep, onDone, compact = false }: {
  cooldown: DeviceCooldown;
  /** When set, a "Manter" button closes without changing anything (the notice). */
  onKeep?: () => void;
  /** Called after any successful change. */
  onDone?: () => void;
  compact?: boolean;
}) {
  const { upsert, remove } = useCooldowns();
  const rememberId = useId();
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState<Choice | null>(null);
  const [error, setError] = useState<string>();
  const { deviceId } = cooldown;

  const run = async (choice: Choice) => {
    setBusy(choice);
    setError(undefined);
    try {
      if (choice === "unblock") {
        await api.clearCooldown(deviceId);
        remove(deviceId);
      } else {
        const body: CooldownUpdate =
          choice === "until" ? { untilUnblocked: true }
            : choice === "never" ? { minutes: 0, rememberForDevice: true }
              : { minutes: Number(choice), rememberForDevice: remember };
        const updated = await api.updateCooldown(deviceId, body);
        if (choice === "never") remove(deviceId); // minutes 0 = unblocked now
        else upsert(updated);
      }
      onDone?.();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const disabled = busy !== null;
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap gap-1.5">
        {onKeep && <Button variant="primary" disabled={disabled} onClick={onKeep}>Manter</Button>}
        <Button disabled={disabled} onClick={() => run("5")}>5 min</Button>
        <Button disabled={disabled} onClick={() => run("60")}>1 h</Button>
        <Button disabled={disabled || remember} onClick={() => run("until")}
                title={remember ? "Só 5 min ou 1 h podem ficar lembrados" : undefined}>
          Até eu desbloquear
        </Button>
        <Button disabled={disabled} onClick={() => run("unblock")}>Desbloquear agora</Button>
      </div>
      <div className={`flex flex-wrap items-center gap-x-3 gap-y-1 ${compact ? "" : "pt-0.5"}`}>
        <label htmlFor={rememberId} className="flex items-center gap-2 text-sm">
          <input id={rememberId} type="checkbox" className="size-4 accent-accent" checked={remember}
                 disabled={disabled} onChange={(e) => setRemember(e.target.checked)} />
          Lembrar para este dispositivo
        </label>
        <Button variant="ghost" disabled={disabled} onClick={() => run("never")}>
          Nunca bloquear este dispositivo
        </Button>
      </div>
      {remember && <p className="m-0 text-xs text-muted">Da próxima vez que me contrariares aqui, bloqueio pelo tempo que escolheres.</p>}
      {error && <ErrorNote message={error} />}
    </div>
  );
}
