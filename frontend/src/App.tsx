// Owner: Team B. App shell: tabs for Chat / Atividade / Aprendizagem / Gestor.
import { useEffect, useState } from "react";
import ActivityFeed from "./features/activity/ActivityFeed";
import ChatPage from "./features/chat/ChatPage";
import CooldownList from "./features/cooldowns/CooldownList";
import CooldownNotices from "./features/cooldowns/CooldownNotices";
import BeliefPanel from "./features/learned/BeliefPanel";
import InsightsList from "./features/learned/InsightsList";
import ReflexList from "./features/learned/ReflexList";
import DeviceList from "./features/manager/DeviceList";
import PluginConfig from "./features/manager/PluginConfig";
import RoomEditor from "./features/manager/RoomEditor";
import { useLive } from "./live/LiveProvider";
import { useTheme } from "./lib/theme";
import { Badge, Dot, MoonIcon, SunIcon } from "./ui";

const TABS = [
  { id: "chat", label: "Chat" },
  { id: "activity", label: "Atividade" },
  { id: "learned", label: "Aprendizagem" },
  { id: "manager", label: "Gestor" },
] as const;
type Tab = (typeof TABS)[number]["id"];

function tabFromHash(): Tab | null {
  const hash = location.hash.slice(1);
  return TABS.some((t) => t.id === hash) ? (hash as Tab) : null;
}

export default function App() {
  const [tab, setTab] = useState<Tab>(() => tabFromHash() ?? "chat");
  useEffect(() => { history.replaceState(null, "", `#${tab}`); }, [tab]);
  // Links like <a href="#manager"> (e.g. "Ver bloqueio" in the activity feed) switch tabs.
  useEffect(() => {
    const onHash = () => { const t = tabFromHash(); if (t) setTab(t); };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <div className="mx-auto max-w-5xl px-4 pb-4 sm:pb-12">
      <header className="flex items-center justify-between gap-3 pb-2 pt-3 sm:pb-3 sm:pt-4">
        <h1 className="text-[17px] font-semibold sm:text-lg">Casa Inteligente</h1>
        <div className="flex items-center gap-2">
          <StatusBar />
          <ThemeToggle />
        </div>
      </header>
      <nav role="tablist"
           className="mb-4 flex gap-1 overflow-x-auto overflow-y-hidden border-b border-line sm:mb-5
                      [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
                  className="cursor-pointer whitespace-nowrap border-b-2 border-transparent px-3 py-2.5 text-[15px]
                             text-muted hover:text-ink focus-visible:outline-2 focus-visible:-outline-offset-2
                             focus-visible:outline-accent aria-selected:border-accent aria-selected:font-semibold
                             aria-selected:text-ink sm:px-3.5 sm:py-2.5 sm:text-base">
            {t.label}
          </button>
        ))}
      </nav>
      <main className="grid gap-4">
        {/* Tabs stay mounted so chat history and live feeds survive switching tabs. */}
        <div hidden={tab !== "chat"}><ChatPage /></div>
        <div hidden={tab !== "activity"}><ActivityFeed /></div>
        <div hidden={tab !== "learned"} className="grid gap-4"><BeliefPanel /><InsightsList /><ReflexList /></div>
        <div hidden={tab !== "manager"} className="grid gap-4">
          <DeviceList /><CooldownList /><RoomEditor /><PluginConfig />
        </div>
      </main>
      <CooldownNotices />
    </div>
  );
}

function ThemeToggle() {
  const [theme, toggle] = useTheme();
  const dark = theme === "dark";
  const label = dark ? "Mudar para modo claro" : "Mudar para modo escuro";
  return (
    <button type="button" onClick={toggle} aria-label={label} title={label}
      className="grid size-8 shrink-0 cursor-pointer place-items-center rounded-lg text-muted hover:bg-surface-2
                 hover:text-ink focus-visible:outline-2 focus-visible:outline-accent">
      {dark ? <SunIcon className="size-[18px]" /> : <MoonIcon className="size-[18px]" />}
    </button>
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
