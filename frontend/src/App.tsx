// Owner: Team B. App shell: tabs for Chat / Atividade / Aprendizagem / Gestor.
import { useState } from "react";
import ActivityFeed from "./features/activity/ActivityFeed";
import ChatPage from "./features/chat/ChatPage";
import BeliefPanel from "./features/learned/BeliefPanel";
import InsightsList from "./features/learned/InsightsList";
import ReflexList from "./features/learned/ReflexList";
import DeviceList from "./features/manager/DeviceList";
import PluginConfig from "./features/manager/PluginConfig";
import RoomEditor from "./features/manager/RoomEditor";

type Tab = "chat" | "activity" | "learned" | "manager";

export default function App() {
  const [tab, setTab] = useState<Tab>("chat");
  return (
    <main>
      <nav>
        <button onClick={() => setTab("chat")}>Chat</button>
        <button onClick={() => setTab("activity")}>Atividade</button>
        <button onClick={() => setTab("learned")}>Aprendizagem</button>
        <button onClick={() => setTab("manager")}>Gestor</button>
      </nav>
      {tab === "chat" && <ChatPage />}
      {tab === "activity" && <ActivityFeed />}
      {tab === "learned" && (<><BeliefPanel /><InsightsList /><ReflexList /></>)}
      {tab === "manager" && (<><DeviceList /><RoomEditor /><PluginConfig /></>)}
    </main>
  );
}
