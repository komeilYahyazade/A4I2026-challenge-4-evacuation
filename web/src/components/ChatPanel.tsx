import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import type { Affected, ChatResponse } from "../types";
import { Segments } from "./Attribution";

const SUGGESTIONS = [
  "Which neighbourhoods would struggle most to self-evacuate?",
  "Is shelter 70851 still there, and can a wheelchair user get in?",
  "How many people here depend on electricity for medical equipment?",
  "Which shelters are recorded in the storm-surge zone?",
];

type Msg = { role: "user"; text: string } | { role: "agent"; resp: ChatResponse } | { role: "error"; text: string };

export function ChatPanel({ county, briefId, onHighlight }: { county: string; briefId: string | null; onHighlight: (a: Affected) => void }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [session, setSession] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const log = useRef<HTMLDivElement>(null);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" }); }, [msgs, busy]);
  useEffect(() => { setSession(null); setMsgs([]); }, [county]);

  const send = async (q: string) => {
    if (!q.trim() || busy) return;
    setMsgs((m) => [...m, { role: "user", text: q }]); setText(""); setBusy(true);
    try {
      const r = await api.chat(q, session, county, briefId);
      setSession(r.session_id);
      setMsgs((m) => [...m, { role: "agent", resp: r }]);
      if (r.highlight.tracts.length + r.highlight.shelters.length + r.highlight.care_facilities.length) onHighlight(r.highlight);
    } catch (e) {
      const err = e as ApiError;
      setMsgs((m) => [...m, { role: "error", text: err.body?.retryable ? `${err.message} Try again in a minute.` : err.message }]);
    } finally { setBusy(false); }
  };

  return (
    <div className="chat">
      <div className="chat-log" ref={log}>
        {!msgs.length && <div className="empty">Ask a planning question. Live <span translate="no" style={{ whiteSpace: "nowrap" }}>Google Maps</span> checks take 10–25 s each. This is a planning tool: for anything happening now, call the county emergency operations center or 911.</div>}
        {msgs.map((m, i) => m.role === "user" ? (
          <div key={i} className="msg user"><div className="bubble">{m.text}</div></div>
        ) : m.role === "error" ? (
          <div key={i} className="msg agent"><div className="bubble err">{m.text}</div></div>
        ) : (
          <div key={i} className={`msg agent${m.resp.refused ? " refused" : ""}`}>
            <div className="bubble">
              {m.resp.refused && <div className="small" style={{ fontWeight: 700, color: "var(--critical)" }}>Declined: planning only</div>}
              <Segments segments={m.resp.reply} sources={m.resp.sources} />
              {m.resp.refused && <div className="small"><b>For anything happening now, contact the county emergency operations center or call 911.</b></div>}
              {m.resp.tool_calls.length > 0 && <div className="small muted">tools: {m.resp.tool_calls.map((t) => `${t.name} (${(t.duration_ms / 1000).toFixed(1)}s)`).join(", ")}</div>}
            </div>
          </div>
        ))}
        {busy && <div className="msg agent"><div className="bubble muted">Thinking…</div></div>}
      </div>
      <div className="chat-input">
        <div className="suggest">{SUGGESTIONS.map((s) => <button key={s} onClick={() => send(s)} disabled={busy}>{s}</button>)}</div>
        <form onSubmit={(e) => { e.preventDefault(); send(text); }}>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask about a tract, shelter or gap…" aria-label="Chat message" />
          <button className="btn primary" disabled={busy || !text.trim()}>Send</button>
        </form>
      </div>
    </div>
  );
}
