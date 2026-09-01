import { FormEvent, useState } from "react";
import { api, SearchFile } from "../api/client";

function formatSize(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  return mb >= 1000 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`;
}

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloadingKey, setDownloadingKey] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function onSearch(e: FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    setMessage(null);
    try {
      const res = await api.search(query.trim());
      setResults(res.results);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function onDownload(file: SearchFile) {
    const key = `${file.username}:${file.filename}`;
    setDownloadingKey(key);
    setError(null);
    try {
      await api.download({
        username: file.username,
        filename: file.filename,
        size: file.size,
      });
      setMessage(`Queued: ${file.filename.split(/[\\/]/).pop()}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDownloadingKey(null);
    }
  }

  return (
    <div>
      <h1>Search Soulseek</h1>
      <form className="panel inline" onSubmit={onSearch}>
        <input
          type="text"
          placeholder="Artist, album, or track..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button type="submit" disabled={loading}>
          {loading ? "Searching..." : "Search"}
        </button>
      </form>

      {error && <div className="panel error-text">{error}</div>}
      {message && <div className="panel">{message}</div>}
      {loading && (
        <div className="panel muted">
          Searching — this waits patiently for slow-to-respond peers, so less-shared content can take up to
          a couple of minutes.
        </div>
      )}

      <div className="panel">
        {results.length === 0 && !loading && <div className="empty">No results yet — try a search above.</div>}
        {results.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>File</th>
                <th>User</th>
                <th>Format</th>
                <th>Bitrate</th>
                <th>Size</th>
                <th>Slot</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => {
                const key = `${r.username}:${r.filename}`;
                const name = r.filename.split(/[\\/]/).pop();
                return (
                  <tr key={key}>
                    <td title={r.filename}>{name}</td>
                    <td className="muted">{r.username}</td>
                    <td>{r.extension.toUpperCase()}</td>
                    <td>{r.bitrate ? `${r.bitrate} kbps` : "—"}</td>
                    <td>{formatSize(r.size)}</td>
                    <td>{r.slots_free ? "Free" : "Busy"}</td>
                    <td>
                      <button onClick={() => onDownload(r)} disabled={downloadingKey === key}>
                        {downloadingKey === key ? "..." : "Download"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
