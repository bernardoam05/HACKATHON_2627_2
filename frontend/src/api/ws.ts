// Owner: Team B. Auto-reconnecting WebSocket subscription.
import type { WsMessage } from "./types";

export function connectWs(onMessage: (m: WsMessage) => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (e: MessageEvent<string>) => onMessage(JSON.parse(e.data) as WsMessage);
    ws.onclose = () => { if (!closed) setTimeout(open, 1500); };
  };
  open();
  return () => { closed = true; ws?.close(); };
}
