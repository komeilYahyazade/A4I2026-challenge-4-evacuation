import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { CONFIG } from "../config";
import type { Affected, ChatResponse, Source } from "../types";
import { LiveVoice, type VoiceState } from "../voice/live";
import { Segments } from "./Attribution";

const SUGGESTIONS = [
  "Which neighbourhoods would struggle most to self-evacuate?",
  "Is shelter 70851 still there, and can a wheelchair user get in?",
  "If Ian came again, which shelters would we lose?",
  "Who is left without a shelter seat, and where would one new site help?",
];

type Msg =
  | { id: string; role: "user"; text: string; via: "text" | "voice" | "typed" }
  | { id: string; role: "agent"; resp: ChatResponse }
  | { id: string; role: "voice"; text: string; sources: Source[]; tools: string[] }
  | { id: string; role: "error"; text: string };

const VOICE_LABEL: Record<VoiceState, string> = {
  idle: "Voice off", connecting: "Connecting…", ready: "Ready: hold to talk", listening: "Listening…",
  speaking: "Agent speaking…", error: "Voice unavailable",
};
let seq = 0;
const nid = () => `m${Date.now()}${seq++}`;

/** Sources from a tool response (verify_shelter returns Google Maps sources), re-numbered per turn. */
function toolSources(resp: unknown): Source[] {
  const r = resp as { sources?: Source[] } | null;
  return Array.isArray(r?.sources) ? r!.sources.filter((s) => s && s.provider === "Google Maps") : [];
}

export function AgentDrawer({ county, briefId, onHighlight, onClose }: {
  county: string; briefId: string | null; onHighlight: (a: Affected) => void; onClose: () => void;
}) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [session, setSession] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const log = useRef<HTMLDivElement>(null);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" }); }, [msgs, busy]);
  useEffect(() => { setSession(null); setMsgs([]); }, [county]);
  const add = (m: Msg) => setMsgs((xs) => [...xs, m]);
  const patch = (id: string, f: (m: Msg) => Msg) => setMsgs((xs) => xs.map((m) => (m.id === id ? f(m) : m)));

  const send = async (q: string) => {
    if (!q.trim() || busy) return;
    add({ id: nid(), role: "user", text: q, via: "text" }); setText(""); setBusy(true);
    try {
      const r = await api.chat(q, session, county, briefId);
      setSession(r.session_id);
      add({ id: nid(), role: "agent", resp: r });
      if (r.highlight.tracts.length + r.highlight.shelters.length + r.highlight.care_facilities.length) onHighlight(r.highlight);
    } catch (e) {
      const err = e as ApiError;
      add({ id: nid(), role: "error", text: err.body?.retryable ? `${err.message} Try again in a minute.` : err.message });
    } finally { setBusy(false); }
  };

  return (
    <section className="drawer" aria-label="Ask the agent">
      <header className="drawer-head">
        <h2>Ask the agent</h2>
        <button className="icon-btn" aria-label="Close" onClick={onClose}>✕</button>
      </header>
      {CONFIG.VOICE_ENABLED !== false && <VoiceBar county={county} add={add} patch={patch} onHighlight={onHighlight} />}
      <div className="chat-log" ref={log}>
        {!msgs.length && <div className="empty">Ask a planning question by text or voice. Live <span translate="no" style={{ whiteSpace: "nowrap" }}>Google Maps</span> checks take 10–25 s each. This is a planning tool: for anything happening now, call the county emergency operations center or 911.</div>}
        {msgs.map((m) => m.role === "user" ? (
          <div key={m.id} className="msg user"><div className="bubble">{m.via === "voice" ? "🎙 " : m.via === "typed" ? "⌨ " : ""}{m.text || "…"}</div></div>
        ) : m.role === "error" ? (
          <div key={m.id} className="msg agent"><div className="bubble err">{m.text}</div></div>
        ) : m.role === "voice" ? (
          <div key={m.id} className="msg agent"><div className="bubble">
            <div className="small muted">🔊 spoken answer</div>
            {m.text || "…"}
            {m.sources.length > 0 && <Segments segments={[{ text: "Google Maps sources for this answer:", source_ids: m.sources.map((s) => s.id), freshness: "live" }]} sources={m.sources} />}
            {m.tools.length > 0 && <div className="small muted">tools: {m.tools.join(", ")}</div>}
          </div></div>
        ) : (
          <div key={m.id} className={`msg agent${m.resp.refused ? " refused" : ""}`}>
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
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type a question for the agent…" aria-label="Chat message" />
          <button className="btn primary" disabled={busy || !text.trim()}>Send</button>
        </form>
      </div>
    </section>
  );
}

/** Push-to-talk plus a live toggle (ported from main's VoiceBar). Transcripts land in the shared log. */
function VoiceBar({ county, add, patch, onHighlight }: {
  county: string; add: (m: Msg) => void; patch: (id: string, f: (m: Msg) => Msg) => void; onHighlight: (a: Affected) => void;
}) {
  const [state, setState] = useState<VoiceState>("idle");
  const [live, setLive] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [started, setStarted] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  const [typed, setTyped] = useState("");
  const voice = useRef<LiveVoice | null>(null);
  const turn = useRef({ userId: null as string | null, agentId: null as string | null, userText: "", agentText: "", sources: [] as Source[], tools: [] as string[] });

  useEffect(() => { if (!started) return; const t = setInterval(() => setNow(Date.now()), 500); return () => clearInterval(t); }, [started]);
  useEffect(() => () => voice.current?.close(), []);
  useEffect(() => { voice.current?.close(); voice.current = null; setStarted(null); setLive(false); }, [county]);

  const onEvent = (ev: Record<string, unknown>) => {
    const t = turn.current;
    const inT = (ev.inputTranscription || ev.input_transcription) as { text?: string } | undefined;
    const outT = (ev.outputTranscription || ev.output_transcription) as { text?: string } | undefined;
    const final = ev.partial === false;  // partial pieces stream first, then one final full text
    if (inT?.text) {
      if (!t.userId) { t.userId = nid(); t.userText = ""; add({ id: t.userId, role: "user", text: "", via: "voice" }); }
      t.userText = final ? inT.text : t.userText + inT.text;
      const id = t.userId, txt = t.userText.trim();
      patch(id, (m) => ({ ...m, text: txt } as Msg));
    }
    const parts = ((ev.content as { parts?: Record<string, unknown>[] } | undefined)?.parts || []);
    for (const p of parts) {
      const fc = (p.functionCall || p.function_call) as { name?: string; args?: Record<string, unknown> } | undefined;
      if (fc?.name) {
        t.tools = [...new Set([...t.tools, fc.name])];
        if (fc.name === "verify_shelter" && fc.args?.shelter_id) onHighlight({ tracts: [], shelters: [String(fc.args.shelter_id)], care_facilities: [] });
      }
      const fr = (p.functionResponse || p.function_response) as { response?: unknown } | undefined;
      if (fr) {
        for (const s of toolSources(fr.response)) {
          if (!t.sources.some((x) => x.uri === s.uri && x.title === s.title)) t.sources = [...t.sources, { ...s, id: `src_${t.sources.length + 1}` }];
        }
      }
    }
    if (outT?.text) {
      if (!t.agentId) { t.agentId = nid(); add({ id: t.agentId, role: "voice", text: "…", sources: [], tools: [] }); }
      t.userId = null;
      t.agentText = final ? outT.text : t.agentText + outT.text;
    }
    if (t.agentId) {
      const id = t.agentId, txt = t.agentText.trim() || "…", srcs = t.sources, tools = t.tools;
      patch(id, (m) => ({ ...m, text: txt, sources: srcs, tools } as Msg));
    }
    if ((ev.turnComplete || ev.turn_complete) && t.agentText.trim()) {
      turn.current = { userId: null, agentId: null, userText: "", agentText: "", sources: [], tools: [] };
    }
  };

  const start = async (andLive: boolean, mic = true) => {
    setErr(null);
    const v = new LiveVoice({
      onState: setState, onEvent, onError: setErr,
      onClose: (reason) => { setStarted(null); setLive(false); voice.current = null; if (!/closed by user|ended/.test(reason)) setErr(reason); },
    }, county);
    voice.current = v;
    try {
      await v.connect(mic);
      setStarted(Date.now());
      if (andLive) { setLive(true); v.setTalking(true); }
    } catch (e) { setErr((e as Error).message); setState("error"); voice.current = null; v.close(); }
  };
  const stop = () => { voice.current?.close(); voice.current = null; setStarted(null); setLive(false); };
  const speakTyped = async () => {
    const q = typed.trim();
    if (!q) return;
    setTyped("");
    if (!voice.current) { await start(false, false); if (!voice.current) return; }
    turn.current.userId = null;
    add({ id: nid(), role: "user", text: q, via: "typed" });
    voice.current.sendText(q);
  };
  const connected = !!voice.current && !["connecting", "idle", "error"].includes(state);
  const secs = started ? Math.floor((now - started) / 1000) : 0;

  return (
    <div className={`voice voice-${state}`}>
      <div className="voice-row">
        <button className={`mic${state === "listening" ? " on" : ""}`} aria-label="Hold to talk" disabled={state === "connecting" || live}
          onPointerDown={async (e) => { e.preventDefault(); if (!voice.current) await start(false); voice.current?.setTalking(true); }}
          onPointerUp={() => voice.current?.setTalking(false)}
          onPointerLeave={() => !live && voice.current?.setTalking(false)}
          onKeyDown={(e) => e.key === " " && voice.current?.setTalking(true)}
          onKeyUp={(e) => e.key === " " && voice.current?.setTalking(false)}>🎙</button>
        <div className="voice-status" role="status" aria-live="polite">
          <b>{VOICE_LABEL[state]}</b>{started && <span className="muted"> · {Math.floor(secs / 60)}:{String(secs % 60).padStart(2, "0")} / 3:00</span>}
        </div>
        <button className={`btn small${live ? " primary" : ""}`} disabled={state === "connecting"} aria-pressed={live}
          onClick={async () => {
            if (live) { voice.current?.setTalking(false); setLive(false); }
            else if (voice.current) { setLive(true); voice.current.setTalking(true); }
            else await start(true);
          }}>{live ? "Live: on" : "Live"}</button>
        {connected && <button className="btn small" onClick={stop}>End</button>}
      </div>
      <form className="voice-type" onSubmit={(e) => { e.preventDefault(); void speakTyped(); }}>
        <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="…or type, and hear the answer" aria-label="Type to the voice agent" />
        <button className="btn small" disabled={!typed.trim() || state === "connecting"}>Speak it</button>
      </form>
      {err && <div className="err small">{err}</div>}
      {!err && !started && <div className="small muted">Hold the mic to talk, or turn on Live. Answers are spoken and written here; Google Maps sources stay on screen. Gemini Live, 3-minute sessions.</div>}
    </div>
  );
}
