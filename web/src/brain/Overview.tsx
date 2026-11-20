import { useEffect, useState } from "react";
import { clearBrain, exportBrain, getBrain, refreshSummary, updateBrain } from "./api";
import { categoryLabel, formatWhen, stanceLabel, type BrainTab, type Overview as OverviewData } from "./types";

type Props = {
  revision: number;
  onChanged: () => void;
  onOpen: (tab: BrainTab, status?: string) => void;
};

export function Overview({ revision, onChanged, onOpen }: Props) {
  const [data, setData] = useState<OverviewData | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getBrain()
      .then((overview) => {
        if (cancelled) return;
        setData(overview);
        setName(overview.profile.name);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load the Brain.");
      });
    return () => {
      cancelled = true;
    };
  }, [revision]);

  async function saveName() {
    const next = name.trim();
    if (!data || next === data.profile.name) return;
    setBusy(true);
    try {
      const profile = await updateBrain({ name: next });
      setData({ ...data, profile });
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't rename the profile.");
    } finally {
      setBusy(false);
    }
  }

  async function refresh() {
    setBusy(true);
    setNotice(null);
    try {
      const overview = await refreshSummary();
      setData(overview);
      setNotice(overview.summary_notice ?? "Summary refreshed.");
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't refresh the summary.");
    } finally {
      setBusy(false);
    }
  }

  async function download() {
    const payload = await exportBrain();
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "unprompted-brain.json";
    link.click();
    URL.revokeObjectURL(url);
  }

  async function wipe() {
    setBusy(true);
    try {
      await clearBrain();
      setConfirmClear(false);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't clear the Brain.");
    } finally {
      setBusy(false);
    }
  }

  if (!data) {
    return <p className="muted">{error ?? "Loading the Brain…"}</p>;
  }

  const counts = data.counts;
  return (
    <div className="brain-stack">
      {error && <p className="banner">{error}</p>}
      <form
        className="name-form"
        onSubmit={(event) => {
          event.preventDefault();
          void saveName();
        }}
      >
        <label>
          Profile name
          <input value={name} onChange={(event) => setName(event.target.value)} maxLength={80} />
        </label>
        <button type="submit" disabled={busy || name.trim() === data.profile.name}>
          Save name
        </button>
      </form>

      <section className="summary-card">
        <header>
          <h2>What the Brain knows</h2>
          <button type="button" onClick={() => void refresh()} disabled={busy}>
            Refresh summary
          </button>
        </header>
        <p className="summary-text">{data.summary.text}</p>
        {data.summary.uncertainties.length > 0 && (
          <ul className="uncertainty">
            {data.summary.uncertainties.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        )}
        {data.narrative && (
          <div className="narrative">
            <h3>Model narrative</h3>
            <p>{data.narrative.notice}</p>
            <p className="summary-text">{data.narrative.text}</p>
          </div>
        )}
        {notice && <p className="notice">{notice}</p>}
      </section>

      <div className="stat-grid">
        <button type="button" onClick={() => onOpen("memories", "accepted")}>
          <strong>{counts.accepted}</strong>
          <span>Accepted memories</span>
        </button>
        <button type="button" onClick={() => onOpen("graph")}>
          <strong>{counts.relationships}</strong>
          <span>Relationships</span>
        </button>
        <button type="button" onClick={() => onOpen("questions")}>
          <strong>{counts.pending_questions}</strong>
          <span>Unanswered questions</span>
        </button>
        <button type="button" onClick={() => onOpen("memories", "proposed")}>
          <strong>{counts.proposed}</strong>
          <span>Waiting for review</span>
        </button>
      </div>

      <div className="split">
        <section>
          <h2>Recently learned</h2>
          {data.recent_memories.length === 0 ? (
            <p className="muted">Accepted memories will show up here.</p>
          ) : (
            <ul className="plain-list">
              {data.recent_memories.map((memory) => (
                <li key={memory.id}>
                  <strong>{memory.content}</strong>
                  <span>
                    {categoryLabel(memory.category)} · {stanceLabel(memory.stance)} · {formatWhen(memory.updated_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section>
          <h2>Recent changes</h2>
          {data.recent_changes.length === 0 ? (
            <p className="muted">No changes yet.</p>
          ) : (
            <ul className="plain-list">
              {data.recent_changes.map((change) => (
                <li key={change.id}>
                  <strong>{change.detail}</strong>
                  <span>{formatWhen(change.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {data.notices.map((item) => (
        <p className="notice" key={item}>
          {item}
        </p>
      ))}

      <div className="review-actions">
        <button type="button" className="quiet" onClick={() => void download()}>
          Export JSON
        </button>
        {confirmClear ? (
          <>
            <button type="button" className="danger" onClick={() => void wipe()} disabled={busy}>
              Clear the Brain
            </button>
            <button type="button" className="quiet" onClick={() => setConfirmClear(false)}>
              Cancel
            </button>
          </>
        ) : (
          <button type="button" className="quiet" onClick={() => setConfirmClear(true)}>
            Clear Brain…
          </button>
        )}
      </div>
      {confirmClear && (
        <p className="notice">
          This deletes local memories, sources, questions, the graph, and the activity log. Conversation history stays.
          Text already sent to a model provider is not deleted there, and this does not erase backups outside the app.
        </p>
      )}
    </div>
  );
}
