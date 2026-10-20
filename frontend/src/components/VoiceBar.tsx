import { useEffect, useRef, useState } from "react";
import { CONFIG } from "../config";
import type { AdkEvent, MapsSource } from "../contract";
import { dedupe, groundingToSources, useStore } from "../store";
import { LiveVoice, type VoiceState } from "../voice/live";

const LABEL: Record<VoiceState, string> = {
  idle: "Voice off",
  connecting: "Connecting…",
  ready: "Ready: hold to talk",
  listening: "Listening…",
  speaking: "Agent speaking…",
  error: "Voice unavailable",
};

/** Is voice on for this page? Flag in config.js AND the agent says voice works (voice_public on the public site). */
export function useVoiceAvailable(): boolean {
  const [ok, setOk] = useState(false);
  useEffect(() => {
    if (!CONFIG.voiceEnabled || (CONFIG.publicDemo && !CONFIG.voicePublic)) return;
    let live = true;
    fetch(`${CONFIG.agentUrl}/api/health`)
      .then((r) => r.json())
      .then((h: { voice?: boolean; voice_public?: boolean }) => live && setOk(CONFIG.publicDemo ? !!h.voice_public : !!h.voice))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);
  return ok;
}

/**
 * Push-to-talk plus a live toggle. Transcripts land in the chat log as turns; Google Maps sources from the tools a
 * turn used are shown under that turn, because spoken answers cannot carry attribution.
 */
export function VoiceBar() {
  const { d, act } = useStore();
  const [state, setState] = useState<VoiceState>("idle");
  const [live, setLive] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [started, setStarted] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  const voice = useRef<LiveVoice | null>(null);
  const turn = useRef<{ userId: string | null; agentId: string | null; userText: string; agentText: string; sources: MapsSource[]; n: number }>({
    userId: null,
    agentId: null,
    userText: "",
    agentText: "",
    sources: [],
    n: 0,
  });

  useEffect(() => {
    if (!started) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [started]);
  useEffect(() => () => voice.current?.close(), []);

  const onEvent = (ev: Record<string, unknown>) => {
    const t = turn.current;
    const inT = (ev.inputTranscription || ev.input_transcription) as { text?: string } | undefined;
    const outT = (ev.outputTranscription || ev.output_transcription) as { text?: string } | undefined;
    if (inT?.text) {
      if (!t.userId) {
        t.n++;
        t.userId = `vu${Date.now()}${t.n}`;
        t.userText = "";
        d({ type: "chatAdd", msg: { id: t.userId, role: "user", author: "voice", text: "", tools: [], groundingSources: [], toolSources: [] } });
      }
      t.userText += inT.text;
      d({ type: "chatPatch", id: t.userId, patch: { text: `🎙 ${t.userText.trim()}` } });
    }
    const parts = ((ev.content as { parts?: Record<string, unknown>[] } | undefined)?.parts || []) as Record<string, unknown>[];
    for (const p of parts) {
      const fr = (p.functionResponse || p.function_response) as { response?: Record<string, unknown> } | undefined;
      if (fr) {
        const { sources } = act.ingestToolResponse(fr.response);
        t.sources = dedupe([...t.sources, ...sources]);
        ensureAgent();
      }
    }
    const grounding = groundingToSources(ev as AdkEvent);
    if (grounding.length) {
      t.sources = dedupe([...t.sources, ...grounding]);
      ensureAgent();
    }
    if (outT?.text) {
      ensureAgent();
      t.userId = null; // the user's turn is over once the agent answers
      t.agentText += outT.text;
    }
    if (t.agentId) d({ type: "chatPatch", id: t.agentId, patch: { text: t.agentText.trim() || "…", toolSources: t.sources } });
    if (ev.turnComplete || ev.turn_complete) {
      t.userId = null;
      t.agentId = null;
      t.agentText = "";
      t.sources = [];
    }
    function ensureAgent() {
      if (t.agentId) return;
      t.n++;
      t.agentId = `va${Date.now()}${t.n}`;
      d({ type: "chatAdd", msg: { id: t.agentId, role: "agent", author: "voice", text: "…", tools: [], groundingSources: [], toolSources: [] } });
    }
  };

  const start = async (andLive: boolean) => {
    setErr(null);
    const v = new LiveVoice({
      onState: setState,
      onEvent,
      onError: setErr,
      onClose: (reason) => {
        setStarted(null);
        setLive(false);
        voice.current = null;
        if (!/closed by user|ended/.test(reason)) setErr(reason);
      },
    });
    voice.current = v;
    try {
      await v.connect();
      setStarted(Date.now());
      if (andLive) {
        setLive(true);
        v.setTalking(true);
      }
    } catch (e) {
      setErr((e as Error).message);
      setState("error");
      voice.current = null;
      v.close();
    }
  };

  const stop = () => {
    voice.current?.close();
    voice.current = null;
    setStarted(null);
    setLive(false);
  };

  const connected = !!voice.current && state !== "connecting" && state !== "idle" && state !== "error";
  const secs = started ? Math.floor((now - started) / 1000) : 0;
  return (
    <div className={`voice voice-${state}`}>
      <div className="voice-row">
        <button
          className={`mic ${state === "listening" ? "on" : ""}`}
          aria-label="Hold to talk"
          disabled={state === "connecting" || live}
          onPointerDown={async (e) => {
            e.preventDefault();
            if (!voice.current) await start(false);
            voice.current?.setTalking(true);
          }}
          onPointerUp={() => voice.current?.setTalking(false)}
          onPointerLeave={() => !live && voice.current?.setTalking(false)}
          onKeyDown={(e) => e.key === " " && voice.current?.setTalking(true)}
          onKeyUp={(e) => e.key === " " && voice.current?.setTalking(false)}
        >
          <span aria-hidden>🎙</span>
        </button>
        <div className="voice-status" role="status" aria-live="polite">
          <b>{LABEL[state]}</b>
          {started && (
            <span className="muted">
              {" "}
              · {Math.floor(secs / 60)}:{String(secs % 60).padStart(2, "0")} / 3:00
            </span>
          )}
        </div>
        <button
          className={`btn btn-small ${live ? "btn-primary" : ""}`}
          disabled={state === "connecting"}
          onClick={async () => {
            if (live) {
              voice.current?.setTalking(false);
              setLive(false);
            } else if (voice.current) {
              setLive(true);
              voice.current.setTalking(true);
            } else await start(true);
          }}
          aria-pressed={live}
        >
          {live ? "Live: on" : "Live"}
        </button>
        {connected && (
          <button className="btn btn-small" onClick={stop}>
            End
          </button>
        )}
      </div>
      {err && <div className="voice-error small">{err}</div>}
      {!err && !started && <div className="small muted">Hold the mic to talk, or turn on Live. Answers are spoken and written here; sources stay on screen.</div>}
    </div>
  );
}
