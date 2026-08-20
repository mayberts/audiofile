import { useEffect, useMemo, useState } from "react";
import { api, LibraryAlbumOut } from "../api/client";

export default function LibraryPage() {
  const [albums, setAlbums] = useState<LibraryAlbumOut[] | null>(null);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getLibrary();
      data.sort((a, b) => a.artist.localeCompare(b.artist) || a.album.localeCompare(b.album));
      setAlbums(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const filtered = useMemo(() => {
    if (!albums) return [];
    const q = filter.trim().toLowerCase();
    if (!q) return albums;
    return albums.filter(
      (a) => a.artist.toLowerCase().includes(q) || a.album.toLowerCase().includes(q),
    );
  }, [albums, filter]);

  const artistCount = useMemo(() => new Set((albums || []).map((a) => a.artist)).size, [albums]);

  return (
    <div>
      <h1>Library</h1>
      <div className="panel row">
        <input
          type="text"
          placeholder="Filter by artist or album..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ flex: 1 }}
        />
        <button className="secondary" onClick={load} disabled={loading}>
          {loading ? "Scanning..." : "Scan Plex Library"}
        </button>
      </div>

      {error && <div className="panel error-text">{error}</div>}

      {albums && (
        <p className="muted" style={{ margin: "0 0 0.6rem" }}>
          {albums.length} album{albums.length === 1 ? "" : "s"} across {artistCount} artist
          {artistCount === 1 ? "" : "s"}
          {filter.trim() && ` — ${filtered.length} matching`}
        </p>
      )}

      <div className="panel">
        {!albums && !loading && !error && <div className="empty">Nothing loaded yet.</div>}
        {albums && filtered.length === 0 && (
          <div className="empty">
            {albums.length === 0
              ? "No albums found in your Plex music library."
              : "No albums match that filter."}
          </div>
        )}
        {filtered.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Artist</th>
                <th>Album</th>
                <th>Year</th>
                <th>Tracks</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((a, i) => (
                <tr key={`${a.artist}::${a.album}::${i}`}>
                  <td>{a.artist}</td>
                  <td>{a.album}</td>
                  <td className="muted">{a.year ?? "—"}</td>
                  <td className="muted">{a.track_count ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
