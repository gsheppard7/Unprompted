import { useEffect, useState } from "react";
import { deleteBrainMemory, listBrainMemories, updateBrainMemory } from "./api";
import {
  CATEGORIES,
  categoryLabel,
  formatWhen,
  stanceLabel,
  type BrainMemory,
  type MemoryStatus,
  type Stance,
} from "./types";

type Props = {
  revision: number;
  status: string;
  onStatus: (status: string) => void;
  onChanged: () => void;
};

const STATUSES: Array<MemoryStatus | "all"> = ["accepted", "proposed", "rejected", "superseded", "all"];

export function Explorer({ revision, status, onStatus, onChanged }: Props) {
  const [category, setCategory] = useState("all");
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [memories, setMemories] = useState<BrainMemory[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listBrainMemories({
      status: status === "all" ? "all" : status,
      category: category === "all" ? "all" : category,
      q: submitted,
    })
      .then((payload) => {
        if (!cancelled) {
          setMemories(payload.memories);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load memories.");
      });
    return () => {
      cancelled = true;
    };
  }, [revision, status, category, submitted]);

  async function remove(id: string) {
    if (pendingDelete !== id) {
      setPendingDelete(id);
      return;
    }
    await deleteBrainMemory(id);
    setPendingDelete(null);
    setMemories((current) => current.filter((memory) => memory.id !== id));
    onChanged();
  }

  return (
    <div className="brain-stack">
      <form
        className="filter-bar"
        onSubmit={(event) => {
          event.preventDefault();
          setSubmitted(query.trim());
        }}
      >
        <label>
          Search
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="robotics, projects, names" />
        </label>
        <label>
          Status
          <select value={status} onChange={(event) => onStatus(event.target.value)}>
            {STATUSES.map((item) => (
              <option key={item} value={item}>
                {categoryLabel(item)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Category
          <select value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="all">All</option>
            {CATEGORIES.map((item) => (
              <option key={item} value={item}>
                {categoryLabel(item)}
              </option>
            ))}
          </select>
        </label>
        <button type="submit">Search</button>
      </form>
      <p className="muted">
        Accepted inferences stay labeled as inferences. A heuristic score is not a probability. Editing text does not
        rebuild relationships, and it does not change the original source.
      </p>
      {error && <p className="banner">{error}</p>}
      {memories.length === 0 ? (
        <p className="muted">No memories in this view.</p>
      ) : (
        <div className="memory-cards">
          {memories.map((memory) => (
            <article key={memory.id}>
              {editing === memory.id ? (
                <EditForm
                  memory={memory}
                  others={memories.filter((item) => item.id !== memory.id && item.status === "accepted")}
                  onCancel={() => setEditing(null)}
                  onSaved={(next) => {
                    setMemories((current) => current.map((item) => (item.id === next.id ? next : item)));
                    setEditing(null);
                    onChanged();
                  }}
                />
              ) : (
                <>
                  <p className="memory-content">{memory.content}</p>
                  <div className="memory-meta">
                    <span className="pill">{categoryLabel(memory.category)}</span>
                    <span className={`pill stance-${memory.stance}`}>{stanceLabel(memory.stance)}</span>
                    <span className="pill">{memory.status}</span>
                    <span title={memory.confidence_note}>heuristic {memory.confidence.toFixed(2)}</span>
                    <time dateTime={memory.updated_at}>{formatWhen(memory.updated_at)}</time>
                  </div>
                  <p className="muted">
                    Source: {memory.source.label}
                    {memory.source.preview ? ` — ${memory.source.preview}` : ""}
                  </p>
                  {memory.evidence_text && <p className="muted">Evidence: {memory.evidence_text}</p>}
                  {memory.entities.length > 0 && (
                    <p className="muted">Entities: {memory.entities.map((entity) => entity.name).join(", ")}</p>
                  )}
                  {(memory.valid_from || memory.valid_to) && (
                    <p className="muted">
                      Valid {memory.valid_from || "…"} to {memory.valid_to || "…"}
                    </p>
                  )}
                  <div className="review-actions">
                    <button type="button" className="quiet" onClick={() => setEditing(memory.id)}>
                      Edit
                    </button>
                    <button type="button" className="quiet" onClick={() => void remove(memory.id)}>
                      {pendingDelete === memory.id ? "Confirm delete" : "Delete"}
                    </button>
                  </div>
                </>
              )}
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

function EditForm({
  memory,
  others,
  onCancel,
  onSaved,
}: {
  memory: BrainMemory;
  others: BrainMemory[];
  onCancel: () => void;
  onSaved: (memory: BrainMemory) => void;
}) {
  const [content, setContent] = useState(memory.content);
  const [category, setCategory] = useState(memory.category);
  const [stance, setStance] = useState<Stance>(memory.stance);
  const [validFrom, setValidFrom] = useState(memory.valid_from ?? "");
  const [validTo, setValidTo] = useState(memory.valid_to ?? "");
  const [supersedes, setSupersedes] = useState(memory.supersedes_id ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const updated = await updateBrainMemory(memory.id, {
        content,
        category,
        stance,
        valid_from: validFrom,
        valid_to: validTo,
        supersedes_id: supersedes || null,
      });
      onSaved(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save that memory.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="memory-form"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <label>
        Memory
        <textarea rows={3} value={content} onChange={(event) => setContent(event.target.value)} />
      </label>
      <div className="filter-bar">
        <label>
          Category
          <select value={category} onChange={(event) => setCategory(event.target.value as BrainMemory["category"])}>
            {CATEGORIES.map((item) => (
              <option key={item} value={item}>
                {categoryLabel(item)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Stance
          <select value={stance} onChange={(event) => setStance(event.target.value as Stance)}>
            <option value="explicit">Stated</option>
            <option value="inferred">Inferred</option>
            <option value="hypothesis">Hypothesis</option>
          </select>
        </label>
      </div>
      <div className="filter-bar">
        <label>
          Valid from
          <input value={validFrom} onChange={(event) => setValidFrom(event.target.value)} placeholder="2024-09-01" />
        </label>
        <label>
          Valid to
          <input value={validTo} onChange={(event) => setValidTo(event.target.value)} placeholder="2025-06-01" />
        </label>
      </div>
      <label>
        This correction replaces
        <select value={supersedes} onChange={(event) => setSupersedes(event.target.value)}>
          <option value="">Nothing</option>
          {others.map((item) => (
            <option key={item.id} value={item.id}>
              {item.content}
            </option>
          ))}
        </select>
      </label>
      <p className="muted">Replacing a note marks the older one superseded and keeps its source text.</p>
      {error && <p className="banner">{error}</p>}
      <div className="review-actions">
        <button type="submit" disabled={busy || content.trim().length < 3}>
          Save
        </button>
        <button type="button" className="quiet" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
