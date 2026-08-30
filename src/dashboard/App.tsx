import { useState } from "react";
import { AlertsView } from "./components/AlertsView";
import { CasesView } from "./components/CasesView";
import { LiveView } from "./components/LiveView";
import { RulesView } from "./components/RulesView";
import { TelegramPanel } from "./components/TelegramPanel";
import { useOps } from "./store";
import type { ViewId } from "./types";

const tabs: { id: ViewId; label: string }[] = [
  { id: "live", label: "Live" },
  { id: "rules", label: "Rules" },
  { id: "cases", label: "Cases" },
  { id: "alerts", label: "Alerts" },
  { id: "bot", label: "Telegram" },
];

function PipelineStrip() {
  const { state } = useOps();
  const recentDetect = state.detections.some((item) => Date.now() - item.ts < 60_000);
  const recentDispatch = state.alerts.some((item) => !item.dismissed && item.telegramState === "sent");

  const steps = [
    { n: "01", title: "Ingest", note: `${state.cameras.length} RTSP streams`, live: true, scan: false },
    {
      n: "02",
      title: "Infer",
      note: state.scanning ? "Sampling frames…" : recentDetect ? "YOLO detections" : "Idle",
      live: recentDetect || state.scanning,
      scan: state.scanning,
    },
    { n: "03", title: "Rules", note: `Cooldown ${state.rules.cooldownSec}s`, live: true, scan: false },
    {
      n: "04",
      title: "Dispatch",
      note: recentDispatch ? "Telegram sendMessage" : "No outbound",
      live: recentDispatch,
      scan: false,
    },
  ];

  return (
    <div className="pipeline" aria-label="CCTV AI Telegram pipeline">
      {steps.map((step) => (
        <div
          key={step.n}
          className={`pipeline__step${step.live ? " is-live" : ""}${step.scan ? " is-scan" : ""}`}
        >
          <span className="pipeline__n">{step.n}</span>
          <strong>{step.title}</strong>
          <em>{step.note}</em>
        </div>
      ))}
    </div>
  );
}

function Shell() {
  const { state } = useOps();
  const [view, setView] = useState<ViewId>("live");
  const mainView = view === "bot" ? "live" : view;

  return (
    <div className={`ops${view === "bot" ? " is-bot" : ""}`}>
      <header className="ops__top">
        <a className="brand" href="/">
          <span className="brand__mark" aria-hidden="true">
            <svg viewBox="0 0 32 32" fill="none">
              <circle cx="16" cy="16" r="12.5" stroke="currentColor" strokeWidth="1.6" />
              <circle cx="16" cy="16" r="5" stroke="currentColor" strokeWidth="1.6" />
              <circle cx="16" cy="16" r="1.8" fill="currentColor" />
            </svg>
          </span>
          <span className="brand__copy">
            <strong>Inbound Surveillance</strong>
            <span className="brand__sub">Operator console</span>
          </span>
        </a>
        <p className="venue">{state.venue}</p>
      </header>
      <PipelineStrip />
      <div className="ops__body">
        <div className="ops__main">
          <nav className="tabs" aria-label="Console sections">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                className={`${view === tab.id ? "is-on" : ""}${tab.id === "bot" ? " tab-bot" : ""}`}
                onClick={() => setView(tab.id)}
              >
                {tab.label}
              </button>
            ))}
          </nav>
          {mainView === "live" && <LiveView />}
          {mainView === "rules" && <RulesView />}
          {mainView === "cases" && <CasesView />}
          {mainView === "alerts" && <AlertsView />}
        </div>
        <TelegramPanel />
      </div>
      {state.toast && (
        <div className="toast" role="status">
          {state.toast}
        </div>
      )}
    </div>
  );
}

export function App() {
  return <Shell />;
}
