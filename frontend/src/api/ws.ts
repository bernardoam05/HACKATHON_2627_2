// Owner: Team B. Auto-reconnecting WebSocket subscription.
import type { WsMessage } from "./types";

export type WsStatus = "connecting" | "open" | "closed";

const RETRY_MS = 1500;

export function connectWs(
  onMessage: (m: WsMessage) => void,
  onStatus: (s: WsStatus) => void = () => {},
): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let retry: ReturnType<typeof setTimeout> | undefined;

  const open = () => {
    onStatus("connecting");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => onStatus("open");
    ws.onmessage = (e: MessageEvent<string>) => {
      try {
        onMessage(JSON.parse(e.data) as WsMessage);
      } catch (err) {
        console.warn("ws: ignored malformed message", err);
      }
    };
    ws.onclose = () => {
      if (closed) return;
      onStatus("closed");
      retry = setTimeout(open, RETRY_MS);
    };
  };

  open();
  return () => {
    closed = true;
    clearTimeout(retry);
    ws?.close();
  };
}
