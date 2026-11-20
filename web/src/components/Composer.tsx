import { useEffect, useState } from "react";

type Props = {
  status: string;
  detail: string;
  connected: boolean;
  onSend: (text: string) => void;
  onTyping: (active: boolean) => void;
};

export function Composer({ status, detail, connected, onSend, onTyping }: Props) {
  const [text, setText] = useState("");

  useEffect(() => {
    if (!text.trim()) {
      onTyping(false);
      return;
    }
    onTyping(true);
    const pulse = window.setInterval(() => onTyping(true), 3000);
    return () => window.clearInterval(pulse);
  }, [text, onTyping]);

  function submit() {
    const next = text.trim();
    if (!next || !connected) return;
    onSend(next);
    setText("");
  }

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <p className="composer-status" aria-live="polite">
        <span className={`status-dot ${status}`} />
        {detail}
      </p>
      <label className="composer-box">
        <span className="sr-only">Message</span>
        <textarea
          value={text}
          rows={2}
          placeholder="Ask a question, or leave a claim"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              submit();
            }
          }}
        />
      </label>
      <div className="composer-actions">
        <span>Enter to send · Shift+Enter for a new line</span>
        <button type="submit" disabled={!connected || !text.trim()}>
          Send
        </button>
      </div>
    </form>
  );
}
