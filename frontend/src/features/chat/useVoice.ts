// Owner: Team B. Voice input for the chat (Web Speech API dictation, pt-PT), as an assistant-ui adapter.
// Replies are text only: the assistant does not speak.
import { WebSpeechDictationAdapter, type DictationAdapter } from "@assistant-ui/react";

const LANG = "pt-PT";

/** Set when the next message was dictated, so the backend gets inputMode "voice". */
export const voiceInput = { used: false };

export const dictationSupported = WebSpeechDictationAdapter.isSupported();

/** assistant-ui dictation in pt-PT that marks the next message as spoken. */
export function createDictation(): DictationAdapter | undefined {
  if (!dictationSupported) return undefined;
  const inner = new WebSpeechDictationAdapter({ language: LANG, continuous: true, interimResults: true });
  return {
    listen: () => {
      voiceInput.used = true;
      return inner.listen();
    },
  };
}
