// Owner: Team B. Text + voice-input chat (assistant-ui); text-only replies, decisions as cards.
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  ErrorPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
  useLocalRuntime,
} from "@assistant-ui/react";
import { useId, useMemo, useState } from "react";
import type { Decision } from "../../api/types";
import { ChevronIcon, MicIcon, SendIcon, StopIcon } from "../../ui";
import { DecisionCard } from "../decisions/DecisionCard";
import { RunInfoChip } from "../decisions/RunInfoChip";
import { chatAdapter, decisionOf } from "./chatAdapter";
import { createDictation, dictationSupported } from "./useVoice";

const SUGGESTIONS = [
  "O que está a acontecer em casa?",
  "Liga a luz da sala",
  "Desliga tudo, vou sair",
];

export default function ChatPage() {
  const dictation = useMemo(createDictation, []);
  const runtime = useLocalRuntime(chatAdapter, { adapters: { dictation } });
  const clear = () => {
    if (runtime.thread.getState().isRunning) runtime.thread.cancelRun();
    runtime.thread.reset();
  };
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread onClear={clear} />
    </AssistantRuntimeProvider>
  );
}

function Thread({ onClear }: { onClear: () => void }) {
  return (
    <ThreadPrimitive.Root
      className="flex h-[calc(100dvh-7.5rem)] min-h-[320px] flex-col overflow-hidden rounded-xl border border-line
                 bg-surface shadow-sm sm:h-[calc(100dvh-10rem)] sm:min-h-[420px]">
      <ThreadPrimitive.If empty={false}>
        <div className="flex justify-end border-b border-line px-2.5 py-1.5">
          <button type="button" onClick={onClear}
            className="cursor-pointer rounded-md px-2 py-1 text-sm text-muted hover:bg-surface-2 hover:text-ink
                       focus-visible:outline-2 focus-visible:outline-accent">
            Limpar conversa
          </button>
        </div>
      </ThreadPrimitive.If>
      <ThreadPrimitive.Viewport className="flex flex-1 flex-col gap-3 overflow-y-auto p-3.5 sm:p-4">
        <ThreadPrimitive.Empty>
          <Welcome />
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
      </ThreadPrimitive.Viewport>
      <Composer />
    </ThreadPrimitive.Root>
  );
}

function Welcome() {
  return (
    <div className="m-auto grid max-w-md gap-3 px-1 text-center sm:gap-4">
      <div>
        <h2 className="text-base font-semibold">Olá! Sou a tua casa.</h2>
        <p className="m-0 mt-1 text-[15px] text-muted">
          Pede-me alguma coisa ou pergunta o que se passa.
          {dictationSupported && " Também podes carregar no microfone e falar."}
        </p>
      </div>
      <div className="flex flex-wrap justify-center gap-2">
        {SUGGESTIONS.map((s) => (
          <ThreadPrimitive.Suggestion key={s} prompt={s} send
            className="cursor-pointer rounded-full border border-line bg-canvas px-3 py-1.5 text-sm hover:bg-surface-2">
            {s}
          </ThreadPrimitive.Suggestion>
        ))}
      </div>
    </div>
  );
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="flex justify-end">
      <div className="max-w-[88%] whitespace-pre-wrap break-words rounded-2xl rounded-br-sm bg-accent px-3.5 py-2
                      text-[15px] text-surface sm:max-w-[80%]">
        <MessagePrimitive.Parts />
      </div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  const custom = useAuiState(({ message }) => message.metadata.custom);
  const running = useAuiState(({ message }) => message.status?.type === "running");
  const decision = decisionOf(custom);
  // The agent's explanation is the answer; the raw reply is only a fallback when there is no decision.
  const answer = decision?.explanation.trim();
  return (
    <MessagePrimitive.Root className="flex max-w-[92%] flex-col gap-2 sm:max-w-[85%]">
      <div className="whitespace-pre-wrap break-words rounded-2xl rounded-bl-sm bg-surface-2 px-3.5 py-2
                      text-[15px]">
        {running
          ? <span className="text-muted motion-safe:animate-pulse">A pensar…</span>
          : answer || <MessagePrimitive.Parts />}
        <MessagePrimitive.Error>
          <ErrorPrimitive.Root className="text-bad">
            Não consegui falar com a casa. <ErrorPrimitive.Message />
          </ErrorPrimitive.Root>
        </MessagePrimitive.Error>
      </div>
      {decision && <DecisionDetails decision={decision} />}
    </MessagePrimitive.Root>
  );
}

/** Collapsed by default: an arrow opens what the agent inferred (room, activity, confidence) and its actions. */
function DecisionDetails({ decision }: { decision: Decision }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const n = decision.actions.length;
  return (
    <div>
      {/* The response time stays visible next to the toggle; the rest is collapsed. */}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls={id}
          className="flex cursor-pointer items-center gap-1 rounded-md px-1 py-0.5 text-sm text-muted hover:text-ink
                     focus-visible:outline-2 focus-visible:outline-accent">
          <ChevronIcon open={open} />
          Detalhes{n > 0 && ` · ${n} ${n === 1 ? "ação" : "ações"}`}
        </button>
        <RunInfoChip runInfo={decision.runInfo} />
      </div>
      <div id={id} hidden={!open} className="mt-1.5">
        <DecisionCard decision={decision} showTrigger={false} showExplanation={false} showRunInfo={false} />
      </div>
    </div>
  );
}

function Composer() {
  const dictating = useAuiState(({ composer }) => composer.dictation != null);
  return (
    <ComposerPrimitive.Root className="flex items-end gap-2 border-t border-line bg-surface p-2.5 sm:p-3">
      {/* text-base on phones: iOS zooms into inputs under 16px */}
      <ComposerPrimitive.Input
        rows={1}
        maxRows={5}
        autoFocus
        placeholder={dictating ? "A ouvir…" : "Escreve à casa…"}
        aria-label="Mensagem"
        className="min-h-10 min-w-0 flex-1 resize-none rounded-lg border border-line bg-canvas px-3 py-2 text-base
                   outline-none placeholder:text-muted focus:border-accent sm:text-[15px]"
      />
      {dictationSupported && (dictating ? (
        <ComposerPrimitive.StopDictation aria-label="Parar de ouvir"
          className="grid size-10 shrink-0 cursor-pointer place-items-center rounded-lg bg-bad-soft text-bad
                     motion-safe:animate-pulse">
          <StopIcon className="size-4" />
        </ComposerPrimitive.StopDictation>
      ) : (
        <ComposerPrimitive.Dictate aria-label="Falar"
          className="grid size-10 shrink-0 cursor-pointer place-items-center rounded-lg border border-line bg-surface
                     text-muted hover:bg-surface-2 hover:text-ink">
          <MicIcon />
        </ComposerPrimitive.Dictate>
      ))}
      <ComposerPrimitive.Send aria-label="Enviar"
        className="grid h-10 w-10 shrink-0 cursor-pointer place-items-center rounded-lg bg-accent text-surface
                   hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50 sm:flex sm:w-auto sm:px-4">
        <SendIcon className="size-5 sm:hidden" />
        <span className="hidden sm:inline">Enviar</span>
      </ComposerPrimitive.Send>
    </ComposerPrimitive.Root>
  );
}
