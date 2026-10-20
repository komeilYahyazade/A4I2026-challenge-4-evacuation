import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { MOCK_ONLY } from "../config";
import { useStore, type ChatMsg } from "../store";
import { MapsAttribution, protectGM } from "./MapsAttribution";

const SUGGESTIONS = [
  "Which neighborhoods in Lee County would struggle most to self-evacuate?",
  "How many people here depend on electricity for medical equipment?",
  "Where would residents of the top tract go, and how far is it?",
  "Which of those shelters can actually take a wheelchair?",
  "Is the nearest shelter to the top tract still there? What is it now?",
  "Which of my shelters is itself in the surge zone?",
  "What should I fix before the season?",
];

export function ChatPanel() {
  const { s, act } = useStore();
  const [text, setText] = useState("");
  const session = useRef<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  // Braces matter: newer browsers return a Promise from scrollIntoView, and React would call it as a cleanup.
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth" });
  }, [s.chat.length]);

  const send = (t: string) => {
    const v = t.trim();
    if (!v || s.busy.chat) return;
    setText("");
    void act.sendChat(v, session);
  };

  return (
    <div className="chat">
      <div className="chat-log">
        {s.chat.length === 0 && (
          <div className="chat-empty">
            <p className="muted small">Ask the readiness agent. It queries BigQuery, checks named buildings on Google Maps, and says what it cannot tell you.</p>
            {MOCK_ONLY && <p className="mock-note">Mock mode: chat needs the live agent, and the panels show MOCK data.</p>}
            <div className="suggestions">
              {SUGGESTIONS.map((q) => (
                <button key={q} className="chip" onClick={() => send(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {s.chat.map((m) => (
          <Message key={m.id} m={m} />
        ))}
        {s.busy.chat && <div className="msg agent typing">Working: querying BigQuery and Google Maps can take 10–30 s…</div>}
        <div ref={end} />
      </div>
      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
      >
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask about Lee County's readiness…" disabled={s.busy.chat} />
        <button className="btn" disabled={s.busy.chat || !text.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}

function Message({ m }: { m: ChatMsg }) {
  if (m.author === "tools") {
    return (
      <div className="msg tools">
        {m.tools.map((t, i) => (
          <span key={t.id + i} className={`tool-chip ${t.status}`} title={t.payloadType || ""}>
            {t.status === "calling" ? "⋯" : "✓"} {t.name}
            {t.payloadType ? <small> → {t.payloadType}</small> : null}
          </span>
        ))}
      </div>
    );
  }
  return (
    <div className={`msg ${m.role}`}>
      <div className="msg-body">{m.role === "user" ? m.text : <Markdown text={m.text} />}</div>
      {/* Attribution immediately after the content it supports. */}
      <MapsAttribution sources={m.groundingSources} />
      {m.toolSources.length > 0 && <MapsAttribution sources={m.toolSources} lead="Shelter checks in this answer, source" />}
    </div>
  );
}

/** Tiny markdown: headings, bullets, numbered lists, **bold**, `code`. Builds React nodes only. */
function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r/g, "").split("\n");
  const out: ReactNode[] = [];
  let list: { ordered: boolean; items: ReactNode[] } | null = null;
  const flush = () => {
    if (!list) return;
    const L = list;
    out.push(L.ordered ? <ol key={out.length}>{L.items}</ol> : <ul key={out.length}>{L.items}</ul>);
    list = null;
  };
  lines.forEach((raw, i) => {
    const line = raw.trimEnd();
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    const num = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    const head = /^(#{1,4})\s+(.*)$/.exec(line);
    if (bullet || num) {
      const ordered = !!num;
      if (!list || list.ordered !== ordered) {
        flush();
        list = { ordered, items: [] };
      }
      list.items.push(<li key={i}>{inline((bullet || num)![1])}</li>);
      return;
    }
    flush();
    if (head) out.push(<h4 key={i}>{inline(head[2])}</h4>);
    else if (line.trim()) out.push(<p key={i}>{inline(line)}</p>);
  });
  flush();
  return <>{out}</>;
}

function inline(s: string): ReactNode {
  const parts = s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((p, i) =>
    p.startsWith("**") && p.endsWith("**") ? (
      <b key={i}>{protectGM(p.slice(2, -2))}</b>
    ) : p.startsWith("`") && p.endsWith("`") ? (
      <code key={i}>{p.slice(1, -1)}</code>
    ) : (
      <Fragment key={i}>{protectGM(p)}</Fragment>
    ),
  );
}
