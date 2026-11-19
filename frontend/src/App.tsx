// Owner: Team B. App shell: tabs for Chat / Atividade / Aprendizagem / Gestor.
import { useEffect, useState } from "react";
import ActivityFeed from "./features/activity/ActivityFeed";
import ChatPage from "./features/chat/ChatPage";
import BeliefPanel from "./features/learned/BeliefPanel";
import InsightsList from "./features/learned/InsightsList";
import ReflexList from "./features/learned/ReflexList";
import DeviceList from "./features/manager/DeviceList";
import PluginConfig from "./features/manager/PluginConfig";
import RoomEditor from "./features/manager/RoomEditor";
import { useLive } from "./live/LiveProvider";
import { Badge, Dot } from "./ui";

const TABS = [
  { id: "chat", label: "Chat" },
  { id: "activity", label: "Atividade" },
  { id: "learned", label: "Aprendizagem" },
  { id: "manager", label: "Gestor" },
] as const;
type Tab = (typeof TABS)[number]["id"];

function initialTab(): Tab {
  const hash = location.hash.slice(1);
  return TABS.some((t) => t.id === hash) ? (hash as Tab) : "chat";
}

export default function App() {
  const [tab, setTab] = useState<Tab>(initialTab);
  useEffect(() => { history.replaceState(null, "", `#${tab}`); }, [tab]);

  return (
    <div className="mx-auto max-w-5xl px-4 pb-12">
      <header className="flex items-center justify-between gap-3 pb-3 pt-4">
        <h1 className="text-lg font-semibold">Casa Inteligente</h1>
        <StatusBar />
      </header>
      <nav className="mb-5 flex gap-1 overflow-x-auto border-b border-line" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
                  className="-mb-px cursor-pointer whitespace-nowrap border-b-2 border-transparent px-3.5 py-2.5
                             text-muted hover:text-ink focus-visible:outline-2 focus-visible:outline-accent
                             aria-selected:border-accent aria-selected:font-semibold aria-selected:text-ink">
            {t.label}
          </button>
        ))}
      </nav>
      <main className="grid gap-4">
        {tab === "chat" && <ChatPage />}
        {tab === "activity" && <ActivityFeed />}
        {tab === "learned" && (<><BeliefPanel /><InsightsList /><ReflexList /></>)}
        {tab === "manager" && (<><DeviceList /><RoomEditor /><PluginConfig /></>)}
      </main>
    </div>
  );
}

function StatusBar() {
  const { status, agent } = useLive();
  return (
    <div className="flex flex-wrap justify-end gap-2" aria-live="polite">
      {agent.busy
        ? <Badge tone="accent"><Dot pulse />A pensar…</Badge>
        : <Badge><Dot />Em espera</Badge>}
      {status === "open"
        ? <Badge tone="good"><Dot />Ligado</Badge>
        : <Badge tone="warn"><Dot pulse />{status === "connecting" ? "A ligar…" : "Sem ligação"}</Badge>}
    </div>
  );
}
