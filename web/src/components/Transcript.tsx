import { useEffect, useRef } from "react";
import { looksLikeSelfKnowledge } from "../brain/types";
import type { FeedbackLabel, Memory, Message } from "../types";

type Props = {
  messages: Message[];
  memories: Memory[];
  isExample: boolean;
  connected: boolean;
  exampleRunning: boolean;
  onRunExample: () => void;
  onFeedback: (id: string, label: FeedbackLabel) => void;
  saveState: Record<string, "saving" | "saved" | "error">;
  onSaveToBrain: (message: Message) => void;
};

const FEEDBACK: { label: FeedbackLabel; text: string }[] = [
  { label: "useful", text: "Useful" },
  { label: "not_useful", text: "Not useful" },
  { label: "too_intrusive", text: "Too intrusive" },
];

export function Transcript({
  messages,
  memories,
  isExample,
  connected,
  exampleRunning,
  onRunExample,
  onFeedback,
  saveState,
  onSaveToBrain,
}: Props) {
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const box = scroller.current;
    if (!box || messages.length === 0) return;
    box.scrollTop = box.scrollHeight;
  }, [messages]);

  return (
    <div className="transcript" ref={scroller}>
      {messages.length === 0 ? (
        <Empty
          isExample={isExample}
          memories={memories}
          connected={connected}
          exampleRunning={exampleRunning}
          onRunExample={onRunExample}
        />
      ) : (
        <ol className="turns">
          {messages.map((message) => (
            <li key={message.id} className={`turn ${message.initiator}`}>
              <div className="turn-meta">
                <span>{message.role === "user" ? "You" : "Unprompted"}</span>
                {message.initiator === "proactive" && <span className="initiated">Initiated</span>}
                {message.initiator === "reply" && <span className="reply-tag">Reply</span>}
                <time dateTime={message.created_at}>{formatTime(message.created_at)}</time>
              </div>
              <p>{message.text}</p>
              {message.role === "user" && looksLikeSelfKnowledge(message.text) && (
                <div className="feedback">
                  <button
                    type="button"
                    disabled={saveState[message.id] === "saving"}
                    onClick={() => onSaveToBrain(message)}
                  >
                    {saveState[message.id] === "saving"
                      ? "Saving…"
                      : saveState[message.id] === "saved"
                        ? "Review in the dialog"
                        : "Suggest saving to Brain"}
                  </button>
                </div>
              )}
              {message.initiator === "proactive" && (
                <div className="proactive-foot">
                  {message.reason && <p className="because">{message.reason}</p>}
                  <div className="feedback" role="group" aria-label="Rate this interruption">
                    {FEEDBACK.map((item) => (
                      <button
                        key={item.label}
                        type="button"
                        aria-pressed={message.feedback_label === item.label}
                        onClick={() => onFeedback(message.id, item.label)}
                      >
                        {item.text}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function Empty({
  isExample,
  memories,
  connected,
  exampleRunning,
  onRunExample,
}: {
  isExample: boolean;
  memories: Memory[];
  connected: boolean;
  exampleRunning: boolean;
  onRunExample: () => void;
}) {
  const fact = memories.find((memory) => memory.kind === "fact" && memory.verified);
  if (!isExample) {
    return (
      <div className="empty">
        <h2>A quiet conversation.</h2>
        <p>
          Ask a question and it answers. Leave a claim, and it may stay silent. That silence is a decision, and you can
          read it under Activity.
        </p>
        {fact && (
          <p className="sample">
            Saved reference ({fact.source_label || "untitled"}): {fact.content}
          </p>
        )}
      </div>
    );
  }
  return (
    <div className="empty">
      <h2>Passer debate</h2>
      <button type="button" onClick={onRunExample} disabled={!connected || exampleRunning}>
        {exampleRunning ? "Sending…" : "Run example"}
      </button>
      <p>
        {fact
          ? `Fictional note from the ${fact.source_label || "demo ledger"}: ${fact.content}`
          : "A fictional demo reference is saved in Memory."}
      </p>
      <ol>
        <li>I think Jordan Hale was the best passer in the league last year.</li>
        <li>No way, he barely averaged five assists.</li>
      </ol>
      <p>The first line should stay quiet. The second should draw one short correction.</p>
    </div>
  );
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
