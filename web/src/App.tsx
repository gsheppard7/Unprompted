import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import {
  conversationSocket,
  createConversation,
  createMemory,
  deleteConversation,
  deleteMemory,
  getHealth,
  listConversations,
  listMemories,
  listMessages,
  listThoughts,
  sendFeedback,
  updateConversation,
} from "./api";
import { teachBrain } from "./brain/api";
import { BrainPage } from "./brain/BrainPage";
import { ReviewList } from "./brain/ReviewList";
import type { BrainTab, TeachResult } from "./brain/types";
import { Composer } from "./components/Composer";
import { Inspector } from "./components/Inspector";
import { Sidebar } from "./components/Sidebar";
import { Transcript } from "./components/Transcript";
import type {
  Conversation,
  FeedbackLabel,
  Health,
  Memory,
  Message,
  Proactivity,
  ServerEvent,
  Thought,
} from "./types";

const MODES: { id: Proactivity; label: string; hint: string }[] = [
  { id: "off", label: "Off", hint: "Only answers when you write. No spontaneous messages." },
  { id: "conservative", label: "Conservative", hint: "Interrupts only for a strong, sourced conflict." },
  { id: "balanced", label: "Balanced", hint: "Also comes back to an open question after a short pause." },
];

const EXAMPLE_LINES = [
  "I think Jordan Hale was the best passer in the league last year.",
  "No way, he barely averaged five assists.",
];

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [apiDown, setApiDown] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [thoughts, setThoughts] = useState<Thought[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [connection, setConnection] = useState<"connected" | "reconnecting" | "offline">("offline");
  const [activity, setActivity] = useState("listening");
  const [panel, setPanel] = useState<"memory" | "activity" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exampleRunning, setExampleRunning] = useState(false);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [view, setView] = useState<"chat" | "brain">(() => readRoute().view);
  const [brainTab, setBrainTab] = useState<BrainTab>(() => readRoute().brainTab);
  const [memoryStatus, setMemoryStatus] = useState("accepted");
  const [review, setReview] = useState<TeachResult | null>(null);
  const [saveState, setSaveState] = useState<Record<string, "saving" | "saved" | "error">>({});
  const socketRef = useRef<WebSocket | null>(null);
  const activeRef = useRef<string | null>(null);

  const active = conversations.find((conversation) => conversation.id === activeId) ?? null;
  const mode = active?.proactivity_mode ?? "conservative";

  const detail = useMemo(() => statusDetail(connection, activity), [connection, activity]);

  const selectConversation = useCallback((id: string) => {
    setView("chat");
    setActiveId(id);
    activeRef.current = id;
    const next = `#/c/${id}`;
    if (window.location.hash !== next) {
      window.history.replaceState(null, "", next);
    }
  }, []);

  const openBrain = useCallback((tab: BrainTab = "overview", status?: string) => {
    setView("brain");
    setBrainTab(tab);
    if (status) setMemoryStatus(status);
    const next = `#/brain/${tab}`;
    if (window.location.hash !== next) {
      window.history.replaceState(null, "", next);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getHealth(), listConversations()])
      .then(([nextHealth, list]) => {
        if (cancelled) return;
        setHealth(nextHealth);
        setConversations(list);
        const route = readRoute();
        setView(route.view);
        setBrainTab(route.brainTab);
        const initial = list.find((conversation) => conversation.id === route.conversationId) ?? list[0];
        if (initial) {
          setActiveId(initial.id);
          activeRef.current = initial.id;
          if (route.view === "chat" && window.location.hash !== `#/c/${initial.id}`) {
            window.history.replaceState(null, "", `#/c/${initial.id}`);
          }
        }
      })
      .catch(() => {
        if (!cancelled) setApiDown(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onHash = () => {
      const route = readRoute();
      setView(route.view);
      setBrainTab(route.brainTab);
      if (route.conversationId) {
        setActiveId(route.conversationId);
        activeRef.current = route.conversationId;
      }
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (!activeId || view !== "chat") return;
    let cancelled = false;
    setMessages([]);
    setThoughts([]);
    setMemories([]);
    setActivity("listening");
    setError(null);
    Promise.all([listMessages(activeId), listThoughts(activeId), listMemories(activeId)])
      .then(([nextMessages, nextThoughts, nextMemories]) => {
        if (cancelled || activeRef.current !== activeId) return;
        setMessages(nextMessages);
        setThoughts(nextThoughts);
        setMemories(nextMemories);
      })
      .catch(() => {
        if (!cancelled) setError("Couldn't load this conversation.");
      });

    let socket: WebSocket | null = null;
    let stopped = false;
    let attempt = 0;
    let timer = 0;

    const connect = () => {
      socket = conversationSocket(activeId);
      socketRef.current = socket;
      socket.onopen = () => {
        if (stopped) return;
        attempt = 0;
        setConnection("connected");
        Promise.all([listMessages(activeId), listThoughts(activeId), listMemories(activeId)]).then(
          ([nextMessages, nextThoughts, nextMemories]) => {
            if (stopped || activeRef.current !== activeId) return;
            setMessages(nextMessages);
            setThoughts(nextThoughts);
            setMemories(nextMemories);
          },
        );
      };
      socket.onclose = () => {
        if (stopped) return;
        setConnection("reconnecting");
        const delay = Math.min(8000, 400 * 2 ** attempt);
        attempt += 1;
        timer = window.setTimeout(connect, delay);
      };
      socket.onerror = () => {
        socket?.close();
      };
      socket.onmessage = (event) => {
        if (activeRef.current !== activeId) return;
        const data = JSON.parse(String(event.data)) as ServerEvent;
        applyEvent(data, {
          setMessages,
          setThoughts,
          setMemories,
          setConversations,
          setActivity,
          setError,
        });
      };
    };

    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      socket?.close();
      if (socketRef.current === socket) socketRef.current = null;
    };
  }, [activeId, view]);

  const sendText = useCallback((text: string) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setError("Not connected yet.");
      return;
    }
    socket.send(JSON.stringify({ type: "user.message", payload: { text } }));
    setError(null);
  }, []);

  const onTyping = useCallback((activeTyping: boolean) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: "user.typing", payload: { active: activeTyping } }));
  }, []);

  async function onCreate() {
    const conversation = await createConversation();
    setConversations((current) => [conversation, ...current.filter((item) => item.id !== conversation.id)]);
    selectConversation(conversation.id);
  }

  async function onDelete(id: string) {
    if (pendingDeleteId !== id) {
      setPendingDeleteId(id);
      return;
    }
    await deleteConversation(id);
    setPendingDeleteId(null);
    const remaining = conversations.filter((conversation) => conversation.id !== id);
    setConversations(remaining);
    if (activeId === id) {
      if (remaining[0]) selectConversation(remaining[0].id);
      else setActiveId(null);
    }
  }

  async function onMode(next: Proactivity) {
    if (!activeId || next === mode) return;
    const updated = await updateConversation(activeId, { proactivity_mode: next });
    setConversations((current) => current.map((conversation) => (conversation.id === updated.id ? updated : conversation)));
  }

  async function onFeedback(messageId: string, label: FeedbackLabel) {
    const updated = await sendFeedback(messageId, label);
    setMessages((current) => upsertMessage(current, updated));
  }

  async function onAddMemory(content: string, source: string, verified: boolean) {
    if (!activeId) return;
    const memory = await createMemory(activeId, { content, source_label: source, verified });
    setMemories((current) => (current.some((item) => item.id === memory.id) ? current : [...current, memory]));
  }

  async function onDeleteMemory(id: string) {
    await deleteMemory(id);
    setMemories((current) => current.filter((memory) => memory.id !== id));
  }

  async function onRunExample() {
    setExampleRunning(true);
    try {
      sendText(EXAMPLE_LINES[0]);
      await wait(900);
      sendText(EXAMPLE_LINES[1]);
    } finally {
      setExampleRunning(false);
    }
  }

  function togglePanel(next: "memory" | "activity") {
    setPanel((current) => (current === next ? null : next));
  }

  async function onSaveToBrain(message: Message) {
    setSaveState((current) => ({ ...current, [message.id]: "saving" }));
    try {
      const result = await teachBrain(message.text, "conversation");
      setSaveState((current) => ({ ...current, [message.id]: "saved" }));
      setReview(result);
      setError(null);
    } catch (err) {
      setSaveState((current) => ({ ...current, [message.id]: "error" }));
      setError(err instanceof Error ? err.message : "Couldn't send that to the Brain.");
    }
  }

  if (apiDown) {
    return (
      <main className="offline-screen">
        <h1>Unprompted</h1>
        <p>The API isn't running, so the chat can't connect.</p>
        <pre>cd api && .venv/bin/uvicorn app.main:app --reload --port 8000</pre>
      </main>
    );
  }

  return (
    <div className={panel && view === "chat" ? "shell with-panel" : "shell"}>
      <Sidebar
        view={view}
        conversations={conversations}
        activeId={activeId}
        health={health}
        pendingDeleteId={pendingDeleteId}
        onOpenChat={() => {
          if (activeId) selectConversation(activeId);
          else setView("chat");
        }}
        onOpenBrain={() => openBrain(brainTab)}
        onSelect={(id) => {
          setPendingDeleteId(null);
          selectConversation(id);
        }}
        onCreate={() => {
          void onCreate();
        }}
        onDelete={(id) => {
          void onDelete(id);
        }}
      />
      {view === "brain" ? (
        <BrainPage tab={brainTab} memoryStatus={memoryStatus} onTab={openBrain} />
      ) : (
      <main className="chat">
        <header className="chat-head">
          <div>
            <h1>{active?.title ?? "Unprompted"}</h1>
            <p>{MODES.find((item) => item.id === mode)?.hint}</p>
          </div>
          <div className="head-controls">
            <div className="connection" data-state={connection}>
              <span />
              {connection === "connected" ? "Connected" : connection === "reconnecting" ? "Reconnecting" : "Offline"}
            </div>
            <div className="modes" role="radiogroup" aria-label="Proactivity">
              {MODES.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  role="radio"
                  aria-checked={mode === item.id}
                  className={mode === item.id ? "on" : ""}
                  onClick={() => {
                    void onMode(item.id);
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <div className="view-toggle">
              <button type="button" aria-pressed={panel === "memory"} onClick={() => togglePanel("memory")}>
                Memory
              </button>
              <button type="button" aria-pressed={panel === "activity"} onClick={() => togglePanel("activity")}>
                Activity
              </button>
            </div>
          </div>
        </header>
        {error && <p className="banner">{error}</p>}
        <Transcript
          messages={messages}
          memories={memories}
          isExample={Boolean(active?.title.startsWith("Example:"))}
          connected={connection === "connected"}
          exampleRunning={exampleRunning}
          onRunExample={() => {
            void onRunExample();
          }}
          onFeedback={(id, label) => {
            void onFeedback(id, label);
          }}
          saveState={saveState}
          onSaveToBrain={(message) => {
            void onSaveToBrain(message);
          }}
        />
        <Composer
          status={activity}
          detail={detail}
          connected={connection === "connected"}
          onSend={sendText}
          onTyping={onTyping}
        />
      </main>
      )}
      {review && (
        <div className="modal-back" role="presentation" onClick={() => setReview(null)}>
          <div
            className="modal"
            role="dialog"
            aria-labelledby="brain-review-title"
            onClick={(event) => event.stopPropagation()}
          >
            <header>
              <h2 id="brain-review-title">Save to Brain</h2>
              <button type="button" className="quiet" onClick={() => setReview(null)}>
                Close
              </button>
            </header>
            <ReviewList result={review} />
          </div>
        </div>
      )}
      {view === "chat" && panel && (
        <Inspector
          tab={panel}
          memories={memories}
          thoughts={thoughts}
          onClose={() => setPanel(null)}
          onAddMemory={(content, source, verified) => {
            void onAddMemory(content, source, verified);
          }}
          onDeleteMemory={(id) => {
            void onDeleteMemory(id);
          }}
        />
      )}
    </div>
  );
}

function applyEvent(
  event: ServerEvent,
  setters: {
    setMessages: Dispatch<SetStateAction<Message[]>>;
    setThoughts: Dispatch<SetStateAction<Thought[]>>;
    setMemories: Dispatch<SetStateAction<Memory[]>>;
    setConversations: Dispatch<SetStateAction<Conversation[]>>;
    setActivity: Dispatch<SetStateAction<string>>;
    setError: Dispatch<SetStateAction<string | null>>;
  },
) {
  const payload = event.payload;
  if (event.type === "message.created" || event.type === "assistant.message" || event.type === "assistant.proactive") {
    const message = payload.message as Message | undefined;
    if (message) setters.setMessages((current) => upsertMessage(current, message));
    return;
  }
  if (event.type === "thought.decision") {
    const thought = payload.thought as Thought | undefined;
    if (thought) setters.setThoughts((current) => [thought, ...current.filter((item) => item.id !== thought.id)]);
    return;
  }
  if (event.type === "memory.updated") {
    const memory = payload.memory as Memory | undefined;
    if (memory) {
      setters.setMemories((current) =>
        current.some((item) => item.id === memory.id) ? current : [...current, memory],
      );
    }
    return;
  }
  if (event.type === "conversation.updated") {
    const conversation = payload.conversation as Conversation | undefined;
    if (conversation) {
      setters.setConversations((current) =>
        current
          .map((item) => (item.id === conversation.id ? conversation : item))
          .sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1)),
      );
    }
    return;
  }
  if (event.type === "system.status") {
    const state = typeof payload.state === "string" ? payload.state : "listening";
    setters.setActivity(state);
    return;
  }
  if (event.type === "error") {
    const message = typeof payload.message === "string" ? payload.message : "Something went wrong.";
    setters.setError(message);
  }
}

function upsertMessage(messages: Message[], message: Message): Message[] {
  const index = messages.findIndex((item) => item.id === message.id);
  if (index === -1) return [...messages, message];
  const next = messages.slice();
  next[index] = message;
  return next;
}

function statusDetail(connection: string, activity: string): string {
  if (connection === "reconnecting") return "Reconnecting";
  if (connection === "offline") return "Offline";
  if (activity === "replying") return "Replying";
  if (activity === "considering") return "Considering a follow-up";
  if (activity === "paused") return "Proactivity is off";
  return "Listening";
}

function readRoute(): { view: "chat" | "brain"; conversationId: string | null; brainTab: BrainTab } {
  const hash = window.location.hash;
  const tabs: BrainTab[] = ["overview", "teach", "upload", "memories", "graph", "questions"];
  if (hash.startsWith("#/brain")) {
    const tab = hash.split("/")[2] as BrainTab;
    return { view: "brain", conversationId: null, brainTab: tabs.includes(tab) ? tab : "overview" };
  }
  const match = hash.match(/^#\/c\/([^/]+)/);
  return { view: "chat", conversationId: match?.[1] ?? null, brainTab: "overview" };
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
