// Owner: Team B. Basic | Advanced tabs; opened automatically on plugin_needed (D12).
// Basic = accept the agent's proposal in one click; Advanced = edit every field, starting from it.
// Until confirmed, every capability of the type stays "suggest" on the backend.
import { useEffect, useId, useRef, useState } from "react";
import { api } from "../../api/client";
import type {
  AutonomyLevel, CapabilitySpec, ConfigMode, Device, DevicePlugin, DevicePurpose, PluginProposal,
} from "../../api/types";
import { SAMPLE_MODE } from "../../dev/samples";
import { useDevices } from "../../live/DevicesProvider";
import { useLiveMessages } from "../../live/LiveProvider";
import { errorMessage, useResource } from "../../lib/useResource";
import { Badge, Button, Card, Empty, ErrorNote, Field, inputClass, Loading } from "../../ui";

const PURPOSE: Record<DevicePurpose, string> = {
  comfort: "Conforto",
  entertainment: "Entretenimento",
  energy: "Energia",
  security: "Segurança",
  environment: "Ambiente",
};

const AUTONOMY: Record<AutonomyLevel, { label: string; hint: string }> = {
  auto: { label: "Automático", hint: "a casa faz e explica" },
  auto_notify: { label: "Automático com aviso", hint: "a casa faz e avisa, com Desfazer" },
  suggest: { label: "Só sugerir", hint: "a casa propõe e espera pela tua confirmação" },
};

export default function PluginConfig() {
  const { devices } = useDevices();
  const unconfigured = useResource(SAMPLE_MODE ? async () => ["motionSensor"] : api.unconfigured);
  const [open, setOpen] = useState<string | null>(null);
  const [done, setDone] = useState<string>();

  useLiveMessages((m) => {
    if (m.type !== "plugin_needed") return;
    unconfigured.setData((ts) => (ts?.includes(m.deviceType) ? ts : [...(ts ?? []), m.deviceType]));
    setOpen((current) => current ?? m.deviceType); // don't interrupt a dialog already open
  });

  const types = unconfigured.data ?? [];
  const count = (type: string) => devices.filter((d) => d.type === type).length;

  const onSaved = (p: DevicePlugin) => {
    unconfigured.setData((ts) => (ts ?? []).filter((t) => t !== p.type));
    setDone(`"${p.displayName}" configurado.`);
    setOpen(null);
  };

  return (
    <Card title="Tipos de dispositivo por configurar">
      {done && <p className="m-0 mb-3 text-sm text-good">{done}</p>}
      {unconfigured.error ? <ErrorNote message={unconfigured.error} onRetry={unconfigured.reload} />
        : !unconfigured.data ? <Loading />
        : types.length === 0 ? <Empty>Todos os tipos de dispositivo estão configurados.</Empty>
        : (
          <ul className="m-0 grid list-none gap-2 p-0">
            {types.map((t) => (
              <li key={t} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line
                                     bg-canvas px-3 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{t}</span>
                  <Badge tone="warn">Só sugere até configurares</Badge>
                  <span className="text-xs text-muted">{count(t)} {count(t) === 1 ? "dispositivo" : "dispositivos"}</span>
                </div>
                <Button variant="primary" onClick={() => { setDone(undefined); setOpen(t); }}>Configurar</Button>
              </li>
            ))}
          </ul>
        )}
      {open && (
        <ConfigDialog type={open} sample={devices.find((d) => d.type === open)}
                      onClose={() => setOpen(null)} onSaved={onSaved} />
      )}
    </Card>
  );
}

/** Starting point when there is no proposal: the device's own capabilities, all "suggest". */
function draftFromDevice(type: string, device: Device | undefined): DevicePlugin {
  return {
    type, displayName: type, purpose: "comfort", status: "proposed", starterAutomations: [],
    capabilities: (device?.capabilities ?? []).map((c) => ({
      type: c.type, description: "", writable: false, autonomy: "suggest",
    })),
  };
}

type Tab = "basic" | "advanced";

function ConfigDialog({ type, sample, onClose, onSaved }: {
  type: string;
  sample: Device | undefined;
  onClose: () => void;
  onSaved: (p: DevicePlugin) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [tab, setTab] = useState<Tab>("basic");
  const proposal = useResource<PluginProposal>(() => api.proposePlugin(type), [type]);
  const [draft, setDraft] = useState<DevicePlugin>(() => draftFromDevice(type, sample));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);

  // Advanced starts from the agent's proposal once it arrives.
  useEffect(() => {
    if (proposal.data) setDraft(structuredClone(proposal.data.plugin));
  }, [proposal.data]);

  // Without a proposal (e.g. 501) Basic has nothing to accept: go straight to Advanced.
  useEffect(() => {
    if (proposal.error) setTab("advanced");
  }, [proposal.error]);

  const save = async (plugin: DevicePlugin, configMode: ConfigMode) => {
    setBusy(true);
    setError(undefined);
    try {
      onSaved(await api.savePlugin(type, { ...plugin, configMode }));
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };

  return (
    <dialog ref={ref} aria-labelledby={titleId} onClose={onClose}
      className="m-auto max-h-[90dvh] w-[min(680px,calc(100vw-2rem))] overflow-hidden rounded-xl border border-line
                 bg-surface p-0 text-ink shadow-xl backdrop:bg-black/50">
      <div className="flex max-h-[90dvh] flex-col">
        <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
          <h2 id={titleId} className="text-base font-semibold">Configurar "{type}"</h2>
          <Button variant="ghost" onClick={() => ref.current?.close()}>Fechar</Button>
        </header>
        <div role="tablist" className="flex gap-1 border-b border-line px-4">
          {(["basic", "advanced"] as const).map((t) => (
            <button key={t} role="tab" type="button" aria-selected={tab === t} onClick={() => setTab(t)}
              className="-mb-px cursor-pointer border-b-2 border-transparent px-3 py-2 text-sm text-muted
                         hover:text-ink aria-selected:border-accent aria-selected:font-semibold aria-selected:text-ink">
              {t === "basic" ? "Básico" : "Avançado"}
            </button>
          ))}
        </div>
        <div className="grid gap-4 overflow-y-auto p-4">
          {tab === "basic" ? (
            proposal.loading && !proposal.data ? <Loading />
              : proposal.error ? (
                <ErrorNote message={`A casa não conseguiu propor uma configuração. ${proposal.error}`}
                           onRetry={proposal.reload} />
              ) : proposal.data && (
                <BasicView proposal={proposal.data} busy={busy}
                           onAccept={() => proposal.data && save(proposal.data.plugin, "basic")}
                           onEdit={() => setTab("advanced")} />
              )
          ) : (
            <AdvancedForm draft={draft} onChange={setDraft} busy={busy} onSave={() => save(draft, "advanced")} />
          )}
          {error && <ErrorNote message={error} />}
        </div>
      </div>
    </dialog>
  );
}

function BasicView({ proposal, busy, onAccept, onEdit }: {
  proposal: PluginProposal;
  busy: boolean;
  onAccept: () => void;
  onEdit: () => void;
}) {
  const p = proposal.plugin;
  return (
    <>
      <div className="grid gap-1">
        <p className="m-0 text-sm text-muted">A casa propõe:</p>
        <p className="m-0 text-base font-semibold">{p.displayName}</p>
        <div><Badge tone="accent">{PURPOSE[p.purpose]}</Badge></div>
        {proposal.rationale && <p className="m-0 text-sm">{proposal.rationale}</p>}
      </div>
      {p.capabilities.length > 0 && (
        <ul className="m-0 grid list-none gap-1.5 p-0">
          {p.capabilities.map((c) => (
            <li key={c.type} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line
                                        bg-canvas px-3 py-2 text-sm">
              <span>
                <span className="font-medium">{c.description || c.type}</span>
                {c.writable && <span className="text-muted"> · a casa pode controlar</span>}
              </span>
              <Badge tone={c.autonomy === "suggest" ? "warn" : "good"}>{AUTONOMY[c.autonomy].label}</Badge>
            </li>
          ))}
        </ul>
      )}
      {p.starterAutomations.length > 0 && (
        <div className="grid gap-1 text-sm">
          <p className="m-0 font-medium">Primeiras automações sugeridas</p>
          <ul className="m-0 grid gap-0.5 pl-5">
            {p.starterAutomations.map((a, i) => <li key={i}>{a}</li>)}
          </ul>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" disabled={busy} onClick={onAccept}>Aceitar proposta</Button>
        <Button disabled={busy} onClick={onEdit}>Ajustar no modo avançado</Button>
      </div>
    </>
  );
}

function AdvancedForm({ draft, onChange, busy, onSave }: {
  draft: DevicePlugin;
  onChange: (p: DevicePlugin) => void;
  busy: boolean;
  onSave: () => void;
}) {
  const setCap = (i: number, patch: Partial<CapabilitySpec>) =>
    onChange({ ...draft, capabilities: draft.capabilities.map((c, j) => (j === i ? { ...c, ...patch } : c)) });
  const autoOnSecurity = draft.purpose === "security" && draft.capabilities.some((c) => c.autonomy !== "suggest");

  return (
    <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); onSave(); }}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Nome do tipo">
          <input className={inputClass} value={draft.displayName} required
                 onChange={(e) => onChange({ ...draft, displayName: e.target.value })} />
        </Field>
        <Field label="Para que serve">
          <select className={inputClass} value={draft.purpose}
                  onChange={(e) => onChange({ ...draft, purpose: e.target.value as DevicePurpose })}>
            {Object.entries(PURPOSE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
      </div>

      <div className="grid gap-2">
        <p className="m-0 text-sm font-medium">Capacidades</p>
        {draft.capabilities.length === 0 && <Empty>Este tipo não tem capacidades conhecidas.</Empty>}
        {draft.capabilities.map((c, i) => (
          <fieldset key={c.type} className="m-0 grid gap-2 rounded-lg border border-line bg-canvas p-3">
            <legend className="px-1 font-mono text-xs text-muted">{c.type}</legend>
            <input className={inputClass} value={c.description} placeholder="O que é (ex. Movimento detetado)"
                   aria-label={`Descrição de ${c.type}`} onChange={(e) => setCap(i, { description: e.target.value })} />
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="size-4 accent-accent" checked={c.writable}
                       onChange={(e) => setCap(i, { writable: e.target.checked })} />
                A casa pode controlar
              </label>
              <select className={`${inputClass} w-auto flex-1`} value={c.autonomy} disabled={!c.writable}
                      aria-label={`Autonomia de ${c.type}`}
                      onChange={(e) => setCap(i, { autonomy: e.target.value as AutonomyLevel })}>
                {Object.entries(AUTONOMY).map(([k, v]) => <option key={k} value={k}>{v.label}: {v.hint}</option>)}
              </select>
            </div>
          </fieldset>
        ))}
        {autoOnSecurity && (
          <p className="m-0 rounded-lg bg-warn-soft px-3 py-2 text-sm text-warn">
            Em dispositivos de segurança, alarmes, sirenes e gravação ficam sempre em "Só sugerir".
          </p>
        )}
      </div>

      <Field label="Primeiras automações" hint="Uma por linha.">
        <textarea className={`${inputClass} min-h-20`} value={draft.starterAutomations.join("\n")}
                  onChange={(e) => onChange({
                    ...draft, starterAutomations: e.target.value.split("\n").filter((l) => l.trim()),
                  })} />
      </Field>

      <div><Button type="submit" variant="primary" disabled={busy || !draft.displayName.trim()}>Guardar configuração</Button></div>
    </form>
  );
}
