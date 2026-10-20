import { useState } from "react";
import { api, ApiError } from "../api";
import type { FieldAssessment, ShelterContact } from "../types";
import { hhmm } from "../format";
import { GoogleMaps } from "./Attribution";

const SAYS: Record<FieldAssessment["wheelchair_unaccompanied"], string> = {
  yes: "a wheelchair user can get in unaccompanied today",
  no: "a wheelchair user cannot get in unaccompanied today",
  unclear: "it is unclear whether a wheelchair user can get in unaccompanied",
};

/** The latest field assessment: what the operator said, labelled as field evidence with its date. */
export function FieldAssessmentCard({ a }: { a: FieldAssessment }) {
  return (
    <div className="card assess">
      <b>Field assessment</b> <span className="fresh field">{a.assessed_at.slice(0, 10)}</span>
      <div style={{ margin: "6px 0" }}>{a.assessor_role ?? "The shelter operator"} says {SAYS[a.wheelchair_unaccompanied]}.</div>
      {a.notes && <div className="muted">"{a.notes}"</div>}
      <div className="muted small" style={{ marginTop: 4 }}>
        Accessible entrance unlocked at all hours: {a.ramp_unlocked_24_7} · generator powers medical outlets: {a.generator_powers_medical_outlets}
      </div>
    </div>
  );
}

function ContactCard({ c }: { c: ShelterContact }) {
  return (
    <div className="contact">
      <div><b>{c.maps_name ?? "Public listing"}</b> {c.phone ? <> · <a href={`tel:${c.phone}`}>{c.phone}</a></> : " · no public phone found"}
        {c.website && <> · <a href={c.website} target="_blank" rel="noopener noreferrer">website</a></>}</div>
      {c.note && <div className="muted small">{c.note}</div>}
      {c.sources.length > 0 && (
        <div className="src small">Source: <GoogleMaps /> —{" "}
          {c.sources.map((s, j) => <span key={s.id}>{j > 0 && " · "}<a href={s.uri ?? undefined} target="_blank" rel="noopener noreferrer">{s.title || "place"}</a></span>)}
          {" "}<span className="fresh live">live · {hhmm(c.checked_at)}</span> <span className="muted">(shown, not stored)</span>
        </div>
      )}
    </div>
  );
}

type Turn = { who: "you" | "agent"; text: string };

/** The README's add-on: a conversation that produces the accessibility record nobody wrote down.
 *  The manager calls the operator and relays what they say; the agent asks the follow-ups. */
export function AssessPanel({ shelterId, onSaved }: { shelterId: string; onSaved: (a: FieldAssessment) => void }) {
  const [open, setOpen] = useState(false);
  const [session, setSession] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [contact, setContact] = useState<ShelterContact | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState<FieldAssessment | null>(null);

  const send = async (message: string, shown = true) => {
    setBusy(true); setErr(null);
    if (shown) setTurns((t) => [...t, { who: "you", text: message }]);
    try {
      const r = await api.assess(shelterId, message, session);
      setSession(r.session_id);
      if (r.contact) setContact(r.contact);
      setTurns((t) => [...t, { who: "agent", text: r.reply }]);
      if (r.saved) { setSaved(r.saved); onSaved(r.saved); }
    } catch (e) { setErr((e as ApiError).message); } finally { setBusy(false); }
  };

  if (!open) {
    return (
      <button className="btn" style={{ marginTop: 6 }} onClick={() => { setOpen(true); send("I want to record a field assessment for this shelter.", false); }}>
        Record a field assessment
      </button>
    );
  }
  return (
    <div className="card assess">
      <b>Field assessment</b> <span className="muted small">— you call the operator, the agent asks the follow-ups</span>
      {contact && <ContactCard c={contact} />}
      {turns.map((t, i) => <div key={i} className={`turn ${t.who}`}>{t.text}</div>)}
      {busy && <div className="muted">Thinking…</div>}
      {err && <div className="err">{err}</div>}
      {saved ? (
        <div className="saved">Saved: {SAYS[saved.wheelchair_unaccompanied]}. The brief has been updated.</div>
      ) : (
        <form onSubmit={(e) => { e.preventDefault(); const v = text.trim(); if (v && !busy) { setText(""); send(v); } }}>
          <textarea value={text} onChange={(e) => setText(e.target.value)} disabled={busy}
            placeholder={'What did the operator say? e.g. "There\'s a ramp at the side door but it\'s chained after school hours."'} />
          <button className="btn primary" disabled={busy || !text.trim()}>Send</button>
        </form>
      )}
    </div>
  );
}
