import { useEffect, useState } from "react";
import { api, WantedReviewCandidateOut } from "../api/client";

interface CandidatePickerProps {
  wantedId: number;
  label: string;
  onClose: () => void;
  onResolved: () => void;
}

function formatSize(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(0)} MB`;
}

function matchHint(tier: string): string {
  return tier === "auto" ? "Strong match" : "Possible match";
}

export default function CandidatePicker({ wantedId, label, onClose, onResolved }: CandidatePickerProps) {
  const [candidates, setCandidates] = useState<WantedReviewCandidateOut[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function load() {
    setLoading(true);
    setError(null);
    api
      .listWantedCandidates(wantedId)
      .then(setCandidates)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(load, [wantedId]);

  async function pick(candidateId: number) {
    setSubmitting(true);
    setError(null);
    try {
      await api.pickWantedCandidate(wantedId, candidateId);
      onResolved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  async function rejectAll() {
    setSubmitting(true);
    setError(null);
    try {
      await api.rejectWantedCandidates(wantedId);
      onResolved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-panel modal-panel-wide" onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: "0.4rem" }}>
          <h2 style={{ margin: 0 }}>{label}</h2>
          <button className="secondary" onClick={onClose} disabled={submitting}>
            Close
          </button>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>
          Nothing found looked confident enough to grab automatically — pick which of these is
          actually the album, or skip this scan and try again later.
        </p>

        {error && (
          <div className="row" style={{ justifyContent: "space-between", marginBottom: "0.8rem" }}>
            <div className="error-text">{error}</div>
            {candidates === null && (
              <button className="secondary" onClick={load} disabled={loading}>
                {loading ? "Retrying..." : "Retry"}
              </button>
            )}
          </div>
        )}

        {loading && <div className="muted">Loading candidates...</div>}

        {!loading && candidates && candidates.length === 0 && (
          <div className="panel empty" style={{ marginBottom: "0.8rem" }}>
            No pooled candidates left for this item — it may have already been resolved elsewhere.
          </div>
        )}

        {!loading && candidates && candidates.length > 0 && (
          <div className="panel" style={{ maxHeight: "60vh", overflowY: "auto", padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>Shared by</th>
                  <th>Files</th>
                  <th>Size</th>
                  <th>Match</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((c) => (
                  <tr key={c.id}>
                    <td>
                      {c.username}
                      <div className="muted">{c.directory}</div>
                    </td>
                    <td>{c.file_count}</td>
                    <td className="muted">{formatSize(c.total_size_bytes)}</td>
                    <td className="muted">{matchHint(c.tier)}</td>
                    <td>
                      <button className="secondary" onClick={() => pick(c.id)} disabled={submitting}>
                        Use this
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="row" style={{ marginTop: "1rem", justifyContent: "flex-end" }}>
          <button className="secondary" onClick={rejectAll} disabled={submitting}>
            None of these — keep searching
          </button>
        </div>
      </div>
    </div>
  );
}
