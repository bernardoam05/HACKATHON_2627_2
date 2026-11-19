// Owner: Team B. Sample insights/reflexes for building the UI while the backend fakes return none.
// Only active with "?exemplo" in the URL, so they can never be mistaken for real data.
import type { Insight, Reflex, WsMessage } from "../api/types";

export const SAMPLE_MODE = new URLSearchParams(location.search).has("exemplo");

const now = Date.now();
const ago = (minutes: number) => new Date(now - minutes * 60_000).toISOString();

export const sampleInsights: Insight[] = [
  {
    id: "ins-1", statement: "Nos dias úteis, a máquina de café é ligada por volta das 07:30.",
    confidence: 0.86, evidence: ["chg-101", "chg-118", "chg-133", "chg-150"],
    createdAt: ago(60 * 24 * 9), lastConfirmedAt: ago(60 * 20), status: "active",
  },
  {
    id: "ins-2", statement: "Quando a televisão liga ao serão, preferes a luz da sala a 30%.",
    confidence: 0.64, evidence: ["act-12", "fb-3"],
    createdAt: ago(60 * 24 * 3), lastConfirmedAt: ago(60 * 26), status: "active",
  },
  {
    id: "ins-3", statement: "Quando sais da cozinha para o corredor, a luz da cozinha deixa de ser precisa.",
    confidence: 0.31, evidence: ["chg-201"],
    createdAt: ago(90), lastConfirmedAt: ago(90), status: "active",
  },
];

export const sampleReflexes: Reflex[] = [
  {
    id: "rfx-1", name: "Café antes das 07:30", status: "proposed", sourceInsightId: "ins-1",
    triggerLogic: "all", createdAt: ago(30), executions: 0, undos: 0,
    triggers: [{
      deviceId: "sim-movimento-cozinha", capability: "motionDetected", operator: "=", value: true,
      timeWindow: { from: "07:00", to: "07:45", weekdays: [0, 1, 2, 3, 4] },
    }],
    actions: [{ deviceId: "sim-cafe-cozinha", update: { type: "power", value: "on" }, reason: "Rotina do café" }],
  },
  {
    id: "rfx-2", name: "Luz segue-te para o corredor", status: "active", sourceInsightId: "ins-3",
    triggerLogic: "all", createdAt: ago(60 * 24 * 2), executions: 14, undos: 1,
    triggers: [{ deviceId: "sim-movimento-corredor", capability: "motionDetected", operator: "=", value: true }],
    actions: [
      { deviceId: "sim-luz-corredor", update: { type: "power", value: "on" }, reason: "Entraste no corredor" },
      { deviceId: "sim-luz-cozinha", update: { type: "power", value: "off" }, reason: "Saíste da cozinha" },
    ],
  },
];

/**
 * The fakes never push WebSocket events, so with ?exemplo the app replays this one at startup to show
 * the override notice (D9). Same device as the fakes' sample block: the fixture bulb "Light".
 */
export const sampleCooldownStarted: Extract<WsMessage, { type: "cooldown_started" }> = {
  type: "cooldown_started",
  cooldown: {
    deviceId: "dc9814bc-81bb-4546-b22d-80705b65fd22",
    until: new Date(now + 20 * 60_000).toISOString(),
    reason: "manual_override",
    actionId: "act-1",
    capability: "brightness",
    note: "Mudei brightness para 30, mas alteraste para 90. Não mexo neste dispositivo durante 20 min.",
    startedAt: new Date(now).toISOString(),
    defaultMinutes: 20,
  },
};
