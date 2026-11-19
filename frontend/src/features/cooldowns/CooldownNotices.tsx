// Owner: Team B. Override notices (D9): shown on every tab when the user contradicts the agent.
// Several stack; each closes on "Manter", after an option, or when its block ends (cooldown_ended).
import { useCooldowns } from "../../live/CooldownsProvider";
import { useDeviceLabel } from "../../live/DevicesProvider";
import { useNow } from "../../lib/time";
import { CooldownOptions, remaining } from "./CooldownOptions";

export default function CooldownNotices() {
  const { notices, byDevice, dismissNotice } = useCooldowns();
  const label = useDeviceLabel();
  const now = useNow(10_000);
  if (notices.length === 0) return null;

  return (
    <div aria-live="polite"
         className="fixed inset-x-3 bottom-3 z-30 grid max-h-[70dvh] gap-2 overflow-y-auto sm:inset-x-auto sm:right-4
                    sm:bottom-4 sm:w-[420px]">
      {notices.map((deviceId) => {
        const c = byDevice.get(deviceId);
        if (!c) return null;
        return (
          <section key={deviceId} role="alertdialog" aria-labelledby={`cooldown-title-${deviceId}`}
                   className="grid gap-2.5 rounded-xl border border-line bg-surface p-3.5 shadow-xl">
            <div className="grid gap-1">
              <h2 id={`cooldown-title-${deviceId}`} className="text-[15px] font-semibold">
                Deixo de mexer em {label(deviceId)} {remaining(c, now)}
              </h2>
              {c.note && <p className="m-0 text-sm text-muted">{c.note}</p>}
            </div>
            <CooldownOptions cooldown={c} onKeep={() => dismissNotice(deviceId)}
                             onDone={() => dismissNotice(deviceId)} compact />
          </section>
        );
      })}
    </div>
  );
}
