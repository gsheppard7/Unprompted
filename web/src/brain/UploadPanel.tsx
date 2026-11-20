import { useState } from "react";
import { uploadBrain } from "./api";
import { ReviewList } from "./ReviewList";
import type { TeachResult } from "./types";

type Props = {
  modelReady: boolean;
  onChanged: () => void;
};

export function UploadPanel({ modelReady, onChanged }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TeachResult | null>(null);
  const [name, setName] = useState<string | null>(null);

  async function send(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError(null);
    setName(file.name);
    try {
      const next = await uploadBrain(file);
      setResult(next);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't read that file.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="brain-stack">
      <section className="summary-card">
        <h2>Upload knowledge</h2>
        <p>TXT, Markdown, and PDF up to 2 MB. The file is read on this machine. DOCX can be added later.</p>
        <p className="notice">
          {modelReady
            ? "If extraction uses the configured model, the extracted text — not the original file bytes — is sent to that provider."
            : "No model key is configured, so the file text stays on this machine."}
        </p>
        <label
          className="dropzone"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            void send(event.dataTransfer.files[0]);
          }}
        >
          <input
            type="file"
            accept=".txt,.md,.markdown,.pdf,text/plain,text/markdown,application/pdf"
            onChange={(event) => {
              void send(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
          <strong>{busy ? "Reading…" : "Choose a file or drop it here"}</strong>
          <span>{name ?? "The original file is not kept. Extracted text and its filename are."}</span>
        </label>
        {error && <p className="banner">{error}</p>}
      </section>
      {result && <ReviewList result={result} onChanged={onChanged} />}
    </div>
  );
}
