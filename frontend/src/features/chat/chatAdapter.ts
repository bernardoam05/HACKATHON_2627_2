// Owner: Team B. assistant-ui ChatModelAdapter backed by POST /api/chat.
import type { ChatModelAdapter, ThreadMessage } from "@assistant-ui/react";
import { api } from "../../api/client";
import type { Decision } from "../../api/types";
import { errorMessage } from "../../lib/useResource";
import { voiceInput } from "./useVoice";

function lastUserText(messages: readonly ThreadMessage[]): string {
  const last = [...messages].reverse().find((m) => m.role === "user");
  return (last?.content ?? [])
    .map((p) => (p.type === "text" ? p.text : ""))
    .join("\n")
    .trim();
}

/** The agent's Decision travels with the assistant message, read back by decisionOf(). */
export const chatAdapter: ChatModelAdapter = {
  async run({ messages }) {
    const message = lastUserText(messages);
    const inputMode = voiceInput.used ? "voice" : "text";
    voiceInput.used = false;
    try {
      const res = await api.chat({ message, inputMode });
      return {
        content: [{ type: "text", text: res.reply }],
        metadata: { custom: { decision: res.decision ?? null } },
      };
    } catch (e) {
      throw new Error(errorMessage(e));
    }
  },
};

export function decisionOf(custom: Record<string, unknown> | undefined): Decision | null {
  const d = custom?.decision;
  return d && typeof d === "object" ? (d as Decision) : null;
}
