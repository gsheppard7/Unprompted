import { useEffect, useState } from "react";
import { brainSocket, getBrain } from "./api";
import { Explorer } from "./Explorer";
import { GraphPanel } from "./GraphPanel";
import { Overview } from "./Overview";
import { QuestionsPanel } from "./QuestionsPanel";
import { TeachPanel } from "./TeachPanel";
import { UploadPanel } from "./UploadPanel";
import type { BrainTab } from "./types";

const TABS: { id: BrainTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "teach", label: "Teach" },
  { id: "upload", label: "Upload" },
  { id: "memories", label: "Memories" },
  { id: "graph", label: "Graph" },
  { id: "questions", label: "Questions" },
];

type Props = {
  tab: BrainTab;
  memoryStatus: string;
  onTab: (tab: BrainTab, status?: string) => void;
};

export function BrainPage({ tab, memoryStatus, onTab }: Props) {
  const [revision, setRevision] = useState(0);
  const [modelReady, setModelReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getBrain()
      .then((overview) => {
        if (!cancelled) setModelReady(overview.extractor === "model");
      })
      .catch(() => {
        if (!cancelled) setModelReady(false);
      });
    return () => {
      cancelled = true;
    };
  }, [revision]);

  useEffect(() => {
    let stopped = false;
    let attempt = 0;
    let timer = 0;
    let socket: WebSocket | null = null;
    const connect = () => {
      socket = brainSocket();
      socket.onopen = () => {
        attempt = 0;
      };
      socket.onmessage = () => {
        if (!stopped) setRevision((current) => current + 1);
      };
      socket.onclose = () => {
        if (stopped) return;
        const delay = Math.min(8000, 400 * 2 ** attempt);
        attempt += 1;
        timer = window.setTimeout(connect, delay);
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      socket?.close();
    };
  }, []);

  function changed() {
    setRevision((current) => current + 1);
  }

  return (
    <section className="brain">
      <header className="chat-head brain-head">
        <div>
          <h1>Brain</h1>
          <p>Accepted notes about you. Proposed notes stay out of chat until you approve them.</p>
        </div>
        <nav className="brain-tabs" aria-label="Brain sections">
          {TABS.map((item) => (
            <button key={item.id} type="button" aria-pressed={tab === item.id} onClick={() => onTab(item.id)}>
              {item.label}
            </button>
          ))}
        </nav>
      </header>
      <div className="brain-scroll">
        {tab === "overview" && <Overview revision={revision} onChanged={changed} onOpen={onTab} />}
        {tab === "teach" && <TeachPanel modelReady={modelReady} onChanged={changed} />}
        {tab === "upload" && <UploadPanel modelReady={modelReady} onChanged={changed} />}
        {tab === "memories" && (
          <Explorer revision={revision} status={memoryStatus} onStatus={(status) => onTab("memories", status)} onChanged={changed} />
        )}
        {tab === "graph" && <GraphPanel revision={revision} />}
        {tab === "questions" && <QuestionsPanel revision={revision} onChanged={changed} />}
      </div>
    </section>
  );
}
