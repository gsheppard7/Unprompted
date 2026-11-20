import { useState } from "react";
import { teachBrain } from "./api";
import { ReviewList } from "./ReviewList";
import type { TeachResult } from "./types";

type Props = {
  modelReady: boolean;
  onChanged: () => void;
};

export function TeachPanel({ modelReady, onChanged }: Props) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TeachResult | null>(null);

  async function submit() {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const next = await teachBrain(text.trim(), "manual");
      setResult(next);
      setText("");
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't read that note.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="brain-stack">
      <section className="summary-card">
        <h2>Teach the Brain</h2>
        <p>
          Write something about yourself. It is split into separate memory candidates, and nothing is accepted until you
          approve it.
        </p>
        <p className="notice">
          {modelReady
            ? "A model key is configured, so this text may be sent to that provider for extraction. The key stays on the server."
            : "No model key is configured. Extraction stays on this machine and only catches recognizable patterns."}
        </p>
        <form
          className="memory-form"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label>
            About you
            <textarea
              rows={6}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="I'm a computer science student interested in robotics, machine learning, and software engineering. I enjoy climbing and baseball."
            />
          </label>
          <button type="submit" disabled={busy || !text.trim()}>
            {busy ? "Extracting…" : "Extract memories"}
          </button>
        </form>
        {error && <p className="banner">{error}</p>}
      </section>
      {result && <ReviewList result={result} onChanged={onChanged} />}
    </div>
  );
}
