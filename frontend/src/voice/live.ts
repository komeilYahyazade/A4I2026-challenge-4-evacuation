// Gemini Live over ADK /run_live (contract §A3). Mic -> AudioWorklet -> 16 kHz PCM16 base64 frames (~100 ms);
// server audio (24 kHz PCM16) -> a scheduled playback queue that barge-in ("interrupted") clears at once.
import { CONFIG } from "../config";

export type VoiceState = "idle" | "connecting" | "ready" | "listening" | "speaking" | "error";

export interface VoiceHandlers {
  onState: (s: VoiceState) => void;
  onEvent: (ev: Record<string, unknown>) => void;
  onError: (msg: string) => void;
  onClose: (reason: string) => void;
}

const WORKLET = `
class Cap extends AudioWorkletProcessor {
  process(inputs) { const ch = inputs[0] && inputs[0][0]; if (ch) this.port.postMessage(ch.slice(0)); return true; }
}
registerProcessor("cap", Cap);`;

function b64FromBytes(bytes: Uint8Array): string {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

function bytesFromB64(b64: string): Uint8Array {
  const bin = atob(b64.replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export async function createVoiceSession(): Promise<string> {
  const res = await fetch(`${CONFIG.agentUrl}/apps/voice/users/${encodeURIComponent(CONFIG.userId)}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state: { county_fips: CONFIG.countyFips } }),
  });
  if (!res.ok) throw new Error(res.status === 429 ? "Voice demo limit reached; try again later." : `Could not start a voice session (HTTP ${res.status}).`);
  return ((await res.json()) as { id: string }).id;
}

export class LiveVoice {
  private ws: WebSocket | null = null;
  private micCtx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private playCtx: AudioContext | null = null;
  private playing = new Set<AudioBufferSourceNode>();
  private nextAt = 0;
  private pending: number[] = [];
  private sending = false;
  private state: VoiceState = "idle";

  constructor(private h: VoiceHandlers) {}

  private set(s: VoiceState) {
    this.state = s;
    this.h.onState(s);
  }

  /** mic=false connects for typed turns only (the agent still answers aloud). */
  async connect(mic = true): Promise<void> {
    this.set("connecting");
    if (mic) await this.openMic();
    this.playCtx = new AudioContext({ sampleRate: 24000 });
    await this.openSocket();
  }

  private async openMic(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      this.set("error");
      throw new Error("This browser cannot use a microphone here (it needs a mic and a secure page). Type your question below instead.");
    }
    try {
      // Mic first, so a denied permission fails fast with a clear message.
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch (e) {
      const name = (e as DOMException).name;
      this.set("error");
      throw new Error(
        name === "NotAllowedError" || name === "SecurityError"
          ? "Microphone permission was denied. Allow the microphone for this site in the browser's address bar, then try again."
          : name === "NotFoundError" || name === "NotSupportedError" || name === "NotReadableError"
            ? "No usable microphone found. Type your question below instead."
            : `Microphone error: ${(e as Error).message}. Type your question below instead.`,
      );
    }
    this.micCtx = new AudioContext();
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" }));
    await this.micCtx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    const src = this.micCtx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.micCtx, "cap");
    const ratio = this.micCtx.sampleRate / 16000;
    let carry = 0;
    this.node.port.onmessage = (m: MessageEvent<Float32Array>) => {
      if (!this.sending) return;
      const input = m.data;
      // Downsample by averaging each ~ratio-sample window to 16 kHz.
      for (let pos = carry; pos < input.length; pos += ratio) {
        const a = Math.floor(pos);
        const b = Math.min(input.length, Math.floor(pos + ratio));
        let sum = 0;
        for (let i = a; i < b; i++) sum += input[i];
        this.pending.push(b > a ? sum / (b - a) : input[a] || 0);
        carry = pos + ratio - input.length;
      }
      if (this.pending.length >= 1600) this.flush();
    };
    src.connect(this.node);
  }

  get hasMic(): boolean {
    return !!this.node;
  }

  private async openSocket(): Promise<void> {
    const sessionId = await createVoiceSession();
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const base = CONFIG.agentUrl ? CONFIG.agentUrl.replace(/^http/, "ws") : `${proto}//${location.host}`;
    const qs = new URLSearchParams({ app_name: "voice", user_id: CONFIG.userId, session_id: sessionId, modalities: "AUDIO" });
    await new Promise<void>((resolve, reject) => {
      const ws = new WebSocket(`${base}/run_live?${qs}`);
      this.ws = ws;
      ws.onopen = () => {
        this.set("ready");
        resolve();
      };
      ws.onerror = () => reject(new Error("Could not connect to the voice service."));
      ws.onmessage = (m) => this.onMessage(m.data);
      ws.onclose = (ev) => {
        this.teardown();
        this.set("idle");
        this.h.onClose(ev.code === 4008 || ev.reason?.includes("limit") ? ev.reason || "Voice session limit reached." : ev.reason || (ev.code === 1000 ? "Voice session ended." : `Voice connection closed (${ev.code}).`));
      };
    });
  }

  /** Push-to-talk / live: start or stop streaming mic frames. */
  setTalking(on: boolean) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    if (on) {
      this.stopPlayback(); // talking over the agent interrupts it locally too
      this.sending = true;
      this.set("listening");
    } else if (this.sending) {
      this.flush();
      this.sending = false;
      // ~600 ms of silence so the server-side voice activity detection closes the turn.
      this.sendPcm(new Float32Array(9600));
      this.set(this.playing.size ? "speaking" : "ready");
    }
  }

  sendText(text: string) {
    this.ws?.send(JSON.stringify({ content: { role: "user", parts: [{ text }] } }));
  }

  close() {
    try {
      this.ws?.send(JSON.stringify({ close: true }));
    } catch {
      /* already closed */
    }
    this.ws?.close(1000, "closed by user");
    this.teardown();
    this.set("idle");
  }

  private flush() {
    if (!this.pending.length) return;
    const f = Float32Array.from(this.pending);
    this.pending = [];
    this.sendPcm(f);
  }

  private sendPcm(f: Float32Array) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const pcm = new Int16Array(f.length);
    for (let i = 0; i < f.length; i++) pcm[i] = Math.max(-1, Math.min(1, f[i])) * 0x7fff;
    this.ws.send(JSON.stringify({ blob: { mime_type: "audio/pcm;rate=16000", data: b64FromBytes(new Uint8Array(pcm.buffer)) } }));
  }

  private onMessage(raw: unknown) {
    let ev: Record<string, unknown>;
    try {
      ev = JSON.parse(String(raw));
    } catch {
      return;
    }
    if (ev.interrupted) this.stopPlayback();
    const parts = ((ev.content as { parts?: Record<string, unknown>[] } | undefined)?.parts || []) as Record<string, unknown>[];
    for (const p of parts) {
      const inline = (p.inlineData || p.inline_data) as { mimeType?: string; mime_type?: string; data?: string } | undefined;
      const mime = inline?.mimeType || inline?.mime_type || "";
      if (inline?.data && mime.startsWith("audio/pcm")) this.play(inline.data, Number(/rate=(\d+)/.exec(mime)?.[1] || 24000));
    }
    this.h.onEvent(ev);
  }

  private play(b64: string, rate: number) {
    if (!this.playCtx) return;
    const bytes = bytesFromB64(b64);
    const pcm = new Int16Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 2));
    const buf = this.playCtx.createBuffer(1, pcm.length, rate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 0x8000;
    const node = this.playCtx.createBufferSource();
    node.buffer = buf;
    node.connect(this.playCtx.destination);
    const at = Math.max(this.playCtx.currentTime + 0.02, this.nextAt);
    node.start(at);
    this.nextAt = at + buf.duration;
    this.playing.add(node);
    if (!this.sending) this.set("speaking");
    node.onended = () => {
      this.playing.delete(node);
      if (!this.playing.size && this.state === "speaking") this.set("ready");
    };
  }

  /** Barge-in: drop everything queued, immediately. */
  stopPlayback() {
    for (const n of this.playing) {
      try {
        n.stop();
      } catch {
        /* not started */
      }
    }
    this.playing.clear();
    this.nextAt = 0;
    if (this.state === "speaking") this.set("ready");
  }

  private teardown() {
    this.sending = false;
    this.stopPlayback();
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.micCtx?.close().catch(() => {});
    void this.playCtx?.close().catch(() => {});
    this.node = null;
    this.stream = null;
    this.micCtx = null;
    this.playCtx = null;
    this.ws = null;
  }
}
