import { useEffect, useState } from "react";
import { answerQuestion, dismissQuestion, generateQuestions, getQuestions, skipQuestion, updateBrain } from "./api";
import { ReviewList } from "./ReviewList";
import type { QuestionState, TeachResult } from "./types";

type Props = {
  revision: number;
  onChanged: () => void;
};

export function QuestionsPanel({ revision, onChanged }: Props) {
  const [state, setState] = useState<QuestionState | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TeachResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    getQuestions()
      .then((next) => {
        if (!cancelled) {
          setState(next);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load questions.");
      });
    return () => {
      cancelled = true;
    };
  }, [revision]);

  async function generate(refresh: boolean) {
    setBusy(true);
    setError(null);
    try {
      const next = await generateQuestions(refresh);
      setState(next);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't suggest questions.");
    } finally {
      setBusy(false);
    }
  }

  async function pause(paused: boolean) {
    setBusy(true);
    try {
      await updateBrain({ questioning_paused: paused });
      const next = await getQuestions();
      setState(next);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't update questioning.");
    } finally {
      setBusy(false);
    }
  }

  async function answer(id: string) {
    const text = (drafts[id] ?? "").trim();
    if (!text) return;
    setBusy(true);
    setError(null);
    try {
      const next = await answerQuestion(id, text);
      setResult(next);
      setDrafts((current) => ({ ...current, [id]: "" }));
      setState(await getQuestions());
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save that answer.");
    } finally {
      setBusy(false);
    }
  }

  async function act(id: string, kind: "skip" | "dismiss") {
    setBusy(true);
    try {
      const next = kind === "skip" ? await skipQuestion(id) : await dismissQuestion(id);
      setState(next);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't update that question.");
    } finally {
      setBusy(false);
    }
  }

  const paused = Boolean(state?.paused);

  return (
    <div className="brain-stack">
      <section className="summary-card">
        <header>
          <h2>Questions</h2>
          <div className="review-actions">
            <button type="button" onClick={() => void generate(false)} disabled={busy || paused}>
              Suggest questions
            </button>
            <button type="button" className="quiet" onClick={() => void generate(true)} disabled={busy || paused}>
              Different questions
            </button>
            <button type="button" className="quiet" onClick={() => void pause(!paused)} disabled={busy}>
              {paused ? "Resume questioning" : "Pause questioning"}
            </button>
          </div>
        </header>
        <p>
          Questions look for missing context around interests, projects, and goals. Up to three are shown. Skip hides one
          for now. Dismiss keeps it from coming back.
        </p>
        {state?.notice && <p className="notice">{state.notice}</p>}
        {state?.message && <p className="muted">{state.message}</p>}
        {paused && <p className="notice">Questioning is paused. Existing suggestions stay saved until you resume.</p>}
      </section>
      {error && <p className="banner">{error}</p>}
      {!paused &&
        state?.questions.map((question) => (
          <article key={question.id} className="question-card">
            <h3>{question.question_text}</h3>
            {question.motivation && <p className="muted">{question.motivation}</p>}
            <form
              className="memory-form"
              onSubmit={(event) => {
                event.preventDefault();
                void answer(question.id);
              }}
            >
              <label>
                Answer
                <textarea
                  rows={3}
                  value={drafts[question.id] ?? ""}
                  onChange={(event) => setDrafts((current) => ({ ...current, [question.id]: event.target.value }))}
                />
              </label>
              <div className="review-actions">
                <button type="submit" disabled={busy || !(drafts[question.id] ?? "").trim()}>
                  Save answer
                </button>
                <button type="button" className="quiet" onClick={() => void act(question.id, "skip")} disabled={busy}>
                  Skip
                </button>
                <button type="button" className="quiet" onClick={() => void act(question.id, "dismiss")} disabled={busy}>
                  Dismiss
                </button>
              </div>
            </form>
          </article>
        ))}
      {result && (
        <section>
          <h2>From your answer</h2>
          <ReviewList result={result} onChanged={onChanged} />
        </section>
      )}
    </div>
  );
}
