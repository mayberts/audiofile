import { useEffect, useState } from "react";
import { api, PlexGapOut } from "../api/client";

export default function PlexGapsPage() {
  const [gaps, setGaps] = useState<PlexGapOut[]>([]);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function refresh() {
    try {
      setGaps(await api.listPlexGaps());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function onScan() {
    setScanning(true);
    setError(null);
    setMessage(null);
    try {
      const res = await api.scanPlexGaps();
      setMessage(`Scan complete — found ${res.new_missing_albums} new missing album(s).`);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setScanning(false);
    }
  }

  async function onAddToWanted(id: number) {
    await api.addGapToWanted(id);
    refresh();
  }

  return (
    <div>
      <h1>Plex Library Gaps</h1>
      <div className="panel row">
        <p className="muted" style={{ flex: 1, margin: 0 }}>
          Compares each artist in your Plex music library against their official studio albums on
          MusicBrainz, and lists anything you don't already have. This can take a while for large
          libraries (MusicBrainz is rate-limited to ~1 request/second).
        </p>
        <button onClick={onScan} disabled={scanning}>
          {scanning ? "Scanning..." : "Scan Plex Library"}
        </button>
      </div>

      {error && <div className="panel error-text">{error}</div>}
      {message && <div className="panel">{message}</div>}

      <div className="panel">
        {gaps.length === 0 && <div className="empty">No gaps found yet — run a scan above.</div>}
        {gaps.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Artist</th>
                <th>Album</th>
                <th>Released</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {gaps.map((g) => (
                <tr key={g.id}>
                  <td>{g.artist}</td>
                  <td>{g.album}</td>
                  <td className="muted">{g.first_release_date || "—"}</td>
                  <td>
                    {g.added_to_wanted ? (
                      <span className="badge downloaded">In wanted list</span>
                    ) : (
                      <button className="secondary" onClick={() => onAddToWanted(g.id)}>
                        Add to Wanted
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
