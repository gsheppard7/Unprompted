import { useState } from "react";
import type { Memory, Thought } from "../types";

type Props = {
  tab: "memory" | "activity";
  memories: Memory[];
  thoughts: Thought[];
  onClose: () => void;
  onAddMemory: (content: string, source: string, verified: boolean) => void;
  onDeleteMemory: (id: string) => void;
};

export function Inspector({ tab, memories, thoughts, onClose, onAddMemory, onDeleteMemory }: Props) {
  return (
    <aside className="panel" aria-label={tab === "memory" ? "Memory" : "Activity"}>
      <button className="panel-close" type="button" onClick={onClose}>
        Close
      </button>
      {tab === "memory" ? (
        <MemoryTab memories={memories} onAddMemory={onAddMemory} onDeleteMemory={onDeleteMemory} />
      ) : (
        <ActivityTab thoughts={thoughts} />
      )}
    </aside>
  );
}

function MemoryTab({
  memories,
  onAddMemory,
  onDeleteMemory,
}: {
  memories: Memory[];
  onAddMemory: (content: string, source: string, verified: boolean) => void;
  onDeleteMemory: (id: string) => void;
}) {
  const [content, setContent] = useState("");
  const [source, setSource] = useState("");
  const [verified, setVerified] = useState(true);
  const facts = memories.filter((memory) => memory.kind === "fact");
  const threads = memories.filter((memory) => memory.kind !== "fact");

  return (
    <div className="panel-body">
      <header>
        <h2>Memory</h2>
        <p>Only saved notes count as evidence. Unverified disagreements stay in the log.</p>
      </header>
      <form
        className="memory-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!content.trim()) return;
          onAddMemory(content.trim(), source.trim() || "saved by you", verified);
          setContent("");
          setSource("");
        }}
      >
        <label>
          Fact
          <textarea value={content} onChange={(event) => setContent(event.target.value)} rows={3} />
        </label>
        <label>
          Source label
          <input value={source} onChange={(event) => setSource(event.target.value)} placeholder="demo ledger" />
        </label>
        <label className="check">
          <input type="checkbox" checked={verified} onChange={(event) => setVerified(event.target.checked)} />
          Verified enough to cite
        </label>
        <button type="submit" disabled={!content.trim()}>
          Add memory
        </button>
      </form>
      <MemoryList title="Facts" memories={facts} onDelete={onDeleteMemory} />
      <MemoryList title="Noticed, not verified" memories={threads} onDelete={onDeleteMemory} />
    </div>
  );
}

function MemoryList({
  title,
  memories,
  onDelete,
}: {
  title: string;
  memories: Memory[];
  onDelete: (id: string) => void;
}) {
  return (
    <section className="memory-list">
      <h3>{title}</h3>
      {memories.length === 0 && <p className="muted">None yet.</p>}
      {memories.map((memory) => (
        <article key={memory.id}>
          <p>{memory.content}</p>
          <div className="memory-meta">
            <span>{memory.source_label || "untitled"}</span>
            <span>{memory.verified ? "Verified" : "Unverified"}</span>
            <button type="button" onClick={() => onDelete(memory.id)}>
              Delete
            </button>
          </div>
        </article>
      ))}
    </section>
  );
}

function ActivityTab({ thoughts }: { thoughts: Thought[] }) {
  return (
    <div className="panel-body">
      <header>
        <h2>Activity</h2>
        <p>Utility is 0.30 relevance + 0.25 evidence + 0.20 novelty + 0.15 urgency − 0.10 interruption.</p>
      </header>
      {thoughts.length === 0 && <p className="muted">No decisions yet.</p>}
      <ol className="thoughts">
        {thoughts.map((thought) => (
          <li key={thought.id}>
            <div className="thought-top">
              <span className={`pill ${thought.status}`}>{pretty(thought.status)}</span>
              <span className="thought-kind">{pretty(thought.kind)}</span>
              <time dateTime={thought.created_at}>{formatTime(thought.created_at)}</time>
            </div>
            <p>{thought.reason}</p>
            {thought.draft && thought.status !== "delivered" && <p className="draft">Withheld: {thought.draft}</p>}
            <div className="utility">
              <span style={{ width: `${Math.max(0, Math.min(100, thought.score.utility * 100))}%` }} />
            </div>
            <p className="score-line">
              {thought.score.utility.toFixed(2)}
              {thought.score.threshold == null ? "" : ` / ${thought.score.threshold.toFixed(2)} to speak`}
            </p>
          </li>
        ))}
      </ol>
    </div>
  );
}

function pretty(value: string): string {
  return value.replaceAll("_", " ");
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
