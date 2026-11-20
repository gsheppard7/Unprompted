import { useEffect, useState } from "react";
import { reviewMemories } from "./api";
import { categoryLabel, stanceLabel, type TeachResult } from "./types";

type Props = {
  result: TeachResult;
  onChanged?: () => void;
};

export function ReviewList({ result, onChanged }: Props) {
  const [items, setItems] = useState(result.proposed);
  const [selected, setSelected] = useState<string[]>(result.proposed.map((item) => item.id));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    setItems(result.proposed);
    setSelected(result.proposed.map((item) => item.id));
    setNote(null);
    setError(null);
  }, [result]);

  function toggle(id: string) {
    setSelected((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  }

  async function decide(kind: "accept" | "reject") {
    const ids = items.filter((item) => selected.includes(item.id)).map((item) => item.id);
    if (ids.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      await reviewMemories(kind === "accept" ? ids : [], kind === "reject" ? ids : []);
      setItems((current) => current.filter((item) => !ids.includes(item.id)));
      setSelected((current) => current.filter((id) => !ids.includes(id)));
      setNote(kind === "accept" ? `Accepted ${ids.length}.` : `Rejected ${ids.length}.`);
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't update those memories.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="review">
      <p className="notice">{result.notice}</p>
      {result.duplicates.length > 0 && (
        <div className="duplicate-list">
          <h3>Already in the Brain</h3>
          <ul>
            {result.duplicates.map((item) => (
              <li key={`${item.matched_id}-${item.content}`}>
                {item.content}
                <span>
                  {" "}
                  matches a {item.matched_status} memory
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {items.length === 0 ? (
        <p className="muted">{note ?? "Nothing new is waiting for approval."}</p>
      ) : (
        <>
          <ul className="candidate-list">
            {items.map((item) => (
              <li key={item.id}>
                <label>
                  <input type="checkbox" checked={selected.includes(item.id)} onChange={() => toggle(item.id)} />
                  <span>
                    <strong>{item.content}</strong>
                    <span className="candidate-meta">
                      {categoryLabel(item.category)} · {stanceLabel(item.stance)} · heuristic {item.confidence.toFixed(2)}
                    </span>
                    {item.evidence_text && <span className="candidate-evidence">From: {item.evidence_text}</span>}
                  </span>
                </label>
              </li>
            ))}
          </ul>
          <div className="review-actions">
            <button type="button" onClick={() => void decide("accept")} disabled={busy || selected.length === 0}>
              Accept selected
            </button>
            <button type="button" className="quiet" onClick={() => void decide("reject")} disabled={busy || selected.length === 0}>
              Reject selected
            </button>
          </div>
        </>
      )}
      {note && items.length > 0 && <p className="muted">{note}</p>}
      {error && <p className="banner">{error}</p>}
    </div>
  );
}
