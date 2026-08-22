import { FormEvent, useState } from "react";
import { api, ReleaseEditionOut } from "../api/client";

interface ReleaseSearchPickerProps {
  artist: string;
  initialQuery: string;
  onClose: () => void;
  onPick: (release: ReleaseEditionOut) => void;
}

export default function ReleaseSearchPicker({ artist, initialQuery, onClose, onPick }: ReleaseSearchPickerProps) {
  const [query, setQuery] = useState(initialQuery);
  const [results, setResults] = useState<ReleaseEditionOut[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);

  function search(e?: FormEvent) {
    e?.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    setSearched(true);
    api
      .searchReleases(artist, query.trim())
      .then(setResults)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-panel modal-panel-wide" onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: "0.4rem" }}>
          <h2 style={{ margin: 0 }}>Compare against a different release</h2>
          <button className="secondary" onClick={onClose}>
            Close
          </button>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>
          Search MusicBrainz for {artist} — useful for a deluxe/bonus-disc reissue listed under its own title.
        </p>

        <form className="row" style={{ marginBottom: "0.8rem" }} onSubmit={search}>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Release title"
            style={{ flex: 1 }}
          />
          <button type="submit" disabled={loading || !query.trim()}>
            {loading ? "Searching..." : "Search"}
          </button>
        </form>

        {error && (
          <div className="row" style={{ justifyContent: "space-between", marginBottom: "0.8rem" }}>
            <div className="error-text">{error}</div>
            <button className="secondary" onClick={() => search()} disabled={loading}>
              {loading ? "Retrying..." : "Retry"}
            </button>
          </div>
        )}

        {!loading && searched && !error && results && results.length === 0 && (
          <div className="panel empty" style={{ marginBottom: "0.8rem" }}>
            No matching releases found on MusicBrainz.
          </div>
        )}

        {!loading && results && results.length > 0 && (
          <div className="panel" style={{ maxHeight: "50vh", overflowY: "auto", padding: 0 }}>
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
                {results.map((ed) => (
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
                      <button className="secondary" onClick={() => onPick(ed)}>
                        Use this
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
