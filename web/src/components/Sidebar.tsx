import type { Conversation, Health } from "../types";

type Props = {
  view: "chat" | "brain";
  conversations: Conversation[];
  activeId: string | null;
  health: Health | null;
  pendingDeleteId: string | null;
  onOpenChat: () => void;
  onOpenBrain: () => void;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
};

export function Sidebar({
  view,
  conversations,
  activeId,
  health,
  pendingDeleteId,
  onOpenChat,
  onOpenBrain,
  onSelect,
  onCreate,
  onDelete,
}: Props) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="mark" aria-hidden="true">
          <svg viewBox="0 0 32 32">
            <circle cx="16" cy="16" r="10" fill="none" stroke="currentColor" strokeWidth="1.6" strokeDasharray="46 16" />
          </svg>
        </span>
        <div>
          <div className="wordmark">Unprompted</div>
          <p className="tagline">Speaks only when it should.</p>
        </div>
      </div>
      <nav className="app-nav" aria-label="Sections">
        <button type="button" aria-pressed={view === "chat"} onClick={onOpenChat}>
          Chat
        </button>
        <button type="button" aria-pressed={view === "brain"} onClick={onOpenBrain}>
          Brain
        </button>
      </nav>
      <button className="new-chat" type="button" onClick={onCreate}>
        New conversation
      </button>
      <nav className="conversation-list" aria-label="Conversations">
        {conversations.map((conversation) => (
          <div key={conversation.id} className={conversation.id === activeId ? "conversation active" : "conversation"}>
            <button type="button" className="conversation-select" onClick={() => onSelect(conversation.id)}>
              <span className="conversation-title">{conversation.title}</span>
              <span className="conversation-mode">{modeLabel(conversation.proactivity_mode)}</span>
            </button>
            <button
              type="button"
              className="conversation-delete"
              onClick={() => onDelete(conversation.id)}
              aria-label={
                pendingDeleteId === conversation.id
                  ? `Confirm delete ${conversation.title}`
                  : `Delete ${conversation.title}`
              }
            >
              {pendingDeleteId === conversation.id ? "Confirm" : "Delete"}
            </button>
          </div>
        ))}
      </nav>
      <footer className="sidebar-foot">
        <span className={health?.model_mode === "live" ? "model-pill live" : "model-pill"}>
          {health?.model_mode === "live" ? `Model · ${health.model_name}` : "Demo cognition"}
        </span>
        <p>Decisions are logged. Nothing is sent unless this conversation is open.</p>
      </footer>
    </aside>
  );
}

function modeLabel(mode: Conversation["proactivity_mode"]): string {
  if (mode === "off") return "Off";
  if (mode === "balanced") return "Balanced";
  return "Conservative";
}
