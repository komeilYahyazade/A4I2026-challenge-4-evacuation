// ADK chat over /run_sse (contract §A1). Events are camelCase, one `data: <json>` per event.
import { CONFIG } from "../config";
import type { AdkEvent } from "../contract";

export async function createSession(state: Record<string, unknown> = {}): Promise<string> {
  const res = await fetch(`${CONFIG.agentUrl}/apps/${CONFIG.app}/users/${encodeURIComponent(CONFIG.userId)}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state }),
  });
  if (!res.ok) throw new Error(`Could not create session: HTTP ${res.status} ${(await res.text()).slice(0, 200)}`);
  const s = (await res.json()) as { id: string };
  return s.id;
}

/** Yields ADK events as they arrive. Ends after the stream closes or an `error` event. */
export async function* runSse(sessionId: string, text: string, signal?: AbortSignal): AsyncGenerator<AdkEvent> {
  const res = await fetch(`${CONFIG.agentUrl}/run_sse`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({
      appName: CONFIG.app,
      userId: CONFIG.userId,
      sessionId,
      newMessage: { role: "user", parts: [{ text }] },
      streaming: false,
    }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`run_sse failed: HTTP ${res.status} ${(await res.text()).slice(0, 300)}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buf += decoder.decode(value, { stream: true });
    // Events are separated by a blank line; tolerate \r\n.
    let idx: number;
    while ((idx = buf.search(/\r?\n\r?\n/)) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx).replace(/^\r?\n\r?\n/, "");
      const ev = parseBlock(block);
      if (ev) {
        yield ev;
        if (ev.error) return;
      }
    }
    if (done) break;
  }
  const tail = parseBlock(buf);
  if (tail) yield tail;
}

export function parseBlock(block: string): AdkEvent | null {
  const data = block
    .split(/\r?\n/)
    .filter((l) => l.startsWith("data:"))
    .map((l) => l.slice(5).replace(/^ /, ""))
    .join("\n")
    .trim();
  if (!data) return null;
  try {
    return JSON.parse(data) as AdkEvent;
  } catch {
    return { error: `Unparseable event: ${data.slice(0, 120)}` };
  }
}
