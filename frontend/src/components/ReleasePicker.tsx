import { useEffect, useState } from "react";
import { api, ReleaseEditionOut } from "../api/client";

interface ReleasePickerProps {
  albumTitle: string;
  releaseGroupMbid: string;
  onClose: () => void;
  onPick: (releaseMbid: string | null) => void;
}

export default function ReleasePicker({ albumTitle, releaseGroupMbid, onClose, onPick }: ReleasePickerProps) {
  const [editions, setEditions] = useState<ReleaseEditionOut[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    api
      .getReleaseEditions(releaseGroupMbid)
      .then(setEditions)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(load, [releaseGroupMbid]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-panel modal-panel-wide" onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: "0.4rem" }}>
          <h2 style={{ margin: 0 }}>{albumTitle}</h2>
          <button className="secondary" onClick={onClose}>
            Close
          </button>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>
          Pick a specific release instead of letting audiofile guess one.
        </p>

        {error && (
          <div className="row" style={{ justifyContent: "space-between", marginBottom: "0.8rem" }}>
            <div className="error-text">{error}</div>
            {editions === null && (
              <button className="secondary" onClick={load} disabled={loading}>
                {loading ? "Retrying..." : "Retry"}
              </button>
            )}
          </div>
        )}

        {loading && <div className="muted">Checking MusicBrainz...</div>}

        {!loading && editions && editions.length === 0 && (
          <div className="panel empty" style={{ marginBottom: "0.8rem" }}>
            MusicBrainz doesn't list any specific releases for this album.
          </div>
        )}

        {!loading && editions && editions.length > 0 && (
          <div className="panel" style={{ maxHeight: "60vh", overflowY: "auto", padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>Release</th>
                  <th>Tracks</th>
                  <th>Country / Date</th>
                  <th>Label</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {editions.map((ed) => (
                  <tr key={ed.release_mbid}>
                    <td>
                      {ed.title}
                      {ed.disambiguation && <div className="muted">{ed.disambiguation}</div>}
                      {ed.format && <div className="muted">{ed.format}</div>}
                    </td>
                    <td>{ed.track_count}</td>
                    <td className="muted">
                      {ed.country || "—"}
                      {ed.date ? ` · ${ed.date}` : ""}
                    </td>
                    <td className="muted">
                      {ed.label || "—"}
                      {ed.catalog_number ? ` (${ed.catalog_number})` : ""}
                    </td>
                    <td>
                      <button className="secondary" onClick={() => onPick(ed.release_mbid)}>
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
          <button className="secondary" onClick={() => onPick(null)}>
            Let audiofile pick automatically
          </button>
        </div>
      </div>
    </div>
  );
}
