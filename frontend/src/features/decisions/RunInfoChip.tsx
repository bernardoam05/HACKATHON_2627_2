// Owner: Team B. How long the agent took to produce a Decision (RunInfo), as a small badge.
import type { FallbackInfo, RunInfo } from "../../api/types";
import { DEBUG } from "../../lib/devMode";
import { Badge } from "../../ui";

const SLOW_MS = 5_000;
const VERY_SLOW_MS = 15_000;

/** "850 ms", "2,4 s", "47,5 s", "1 min 12 s" (pt-PT decimal comma). */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toLocaleString("pt-PT", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} s`;
  return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

/** "gemini-2.5-flash,gpt-oss" -> "gemini-2.5-flash +1"; provider prefixes ("openai/") are dropped. */
function shortModel(model: string | null | undefined): string | null {
  const names = (model ?? "").split(",").map((m) => m.trim().split("/").pop() ?? "").filter(Boolean);
  if (names.length === 0) return null;
  return names.length > 1 ? `${names[0]} +${names.length - 1}` : names[0];
}

const failedLabel = (n: number) => `${n} ${n === 1 ? "modelo falhou" : "modelos falharam"}`;

export function RunInfoChip({ runInfo }: { runInfo?: RunInfo | null }) {
  if (!DEBUG || !runInfo) return null; // ?debug only; none on old decisions or ones made without an LLM run
  const tone = runInfo.durationMs >= VERY_SLOW_MS ? "bad" : runInfo.durationMs >= SLOW_MS ? "warn" : "neutral";
  const model = shortModel(runInfo.model);
  const failed = runInfo.fallbacks ?? [];
  const title = [
    `Tempo de resposta do agente: ${formatDuration(runInfo.durationMs)}`,
    `Modelo: ${runInfo.model || "desconhecido"}`,
    `Camada: ${runInfo.tier === "reasoning" ? "raciocínio" : "rápida"}`,
    `Contexto: ${runInfo.mode === "full" ? "completo" : "só alterações"}`,
    `Pedidos ao modelo: ${runInfo.modelRequests}`,
    ...(failed.length > 0 ? ["", "Falharam antes:", ...failed.map(failedLine)] : []),
  ].join("\n");
  return (
    <>
      <span title={title}>
        <Badge tone={tone}>
          <span aria-hidden>⏱</span>
          <span className="sr-only">Tempo de resposta: </span>
          {formatDuration(runInfo.durationMs)}
          {model && <span className="font-normal opacity-80">· {model}</span>}
        </Badge>
      </span>
      {failed.length > 0 && (
        <span title={title}>
          <Badge tone="warn">{failedLabel(failed.length)}</Badge>
        </span>
      )}
    </>
  );
}

function failedLine(f: FallbackInfo): string {
  return `${shortModel(f.model) ?? f.model} — ${f.error} (${formatDuration(f.ms)})`;
}

/** The models that failed before the answer, in order, with the time each one cost. */
export function FallbackList({ runInfo }: { runInfo?: RunInfo | null }) {
  const failed = runInfo?.fallbacks ?? [];
  if (!DEBUG || failed.length === 0) return null;
  const lost = failed.reduce((sum, f) => sum + f.ms, 0);
  return (
    <div className="grid gap-1 rounded-md border border-line bg-surface px-2 py-1.5 text-sm">
      <p className="m-0 text-xs text-muted">
        {failedLabel(failed.length)} antes da resposta · {formatDuration(lost)} perdidos
      </p>
      <ol className="m-0 grid list-none gap-0.5 p-0">
        {failed.map((f, i) => (
          <li key={i} className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-medium">{shortModel(f.model) ?? f.model}</span>
            <span className="text-muted">{f.error}</span>
            <span className="text-xs text-muted">{formatDuration(f.ms)}</span>
          </li>
        ))}
      </ol>
      {runInfo?.model && (
        <p className="m-0 text-xs text-muted">Respondeu: {runInfo.model.split(",").join(", ")}</p>
      )}
    </div>
  );
}
