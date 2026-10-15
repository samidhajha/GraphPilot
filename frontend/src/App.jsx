import { useState, useRef, useEffect } from "react";
import { sendMessage } from "./api";
import "./App.css";

const SAMPLE_QUESTIONS = [
  "What can you help me with?",
  "Explain how this app works",
  "Give me a quick example",
];

export default function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef(null);

  // Auto-scroll to newest message
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function handleSend(text) {
    const message = text.trim();
    if (!message || loading) return;

    setError("");
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: message }]);
    setLoading(true);

    try {
      const reply = await sendMessage(message);
      setMessages((prev) => [...prev, { role: "assistant", content: reply }]);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(e) {
    e.preventDefault();
    handleSend(input);
  }

  return (
    <div className="app">
      <header className="header">Chat</header>

      <main className="messages">
        {messages.length === 0 && (
          <div className="empty">
            <p>Try a sample question:</p>
            <div className="samples">
              {SAMPLE_QUESTIONS.map((q) => (
                <button key={q} onClick={() => handleSend(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>
            {m.content}
          </div>
        ))}

        {loading && <div className="bubble assistant typing">Thinking…</div>}
        {error && <div className="error">⚠ {error}</div>}
        <div ref={bottomRef} />
      </main>

      <form className="composer" onSubmit={handleSubmit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type your message…"
          maxLength={2000}
          disabled={loading}
        />
        <button type="submit" disabled={loading || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}