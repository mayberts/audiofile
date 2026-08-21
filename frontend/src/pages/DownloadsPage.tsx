import { useEffect, useState } from "react";
import { api, DownloadOut } from "../api/client";

export default function DownloadsPage() {
  const [downloads, setDownloads] = useState<DownloadOut[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      const data = await api.listDownloads();
      setDownloads(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, []);

  async function onCancel(id: number) {
    try {
      await api.cancelDownload(id);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const [retrying, setRetrying] = useState<number | null>(null);

  async function onRetry(id: number) {
    setRetrying(id);
    try {
      await api.retryDownload(id);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRetrying(null);
    }
  }

  const hasCompleted = downloads.some((d) => ["done", "failed", "cancelled"].includes(d.status));

  async function onClearCompleted() {
    try {
      await api.clearCompletedDownloads();
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Downloads</h1>
        {hasCompleted && (
          <button className="secondary" onClick={onClearCompleted}>
            Clear Completed
          </button>
        )}
      </div>
      {error && <div className="panel error-text">{error}</div>}
      <div className="panel">
        {downloads.length === 0 && <div className="empty">No downloads yet.</div>}
        {downloads.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Track</th>
                <th>User</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Destination</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {downloads.map((d) => {
                const name = d.slskd_filename.split(/[\\/]/).pop();
                // An album-batch download has no single hint_track (every
                // row in the batch would otherwise show the same
                // "Artist — Album" label with no way to tell tracks apart),
                // so fall back to the actual filename before the album name.
                const label = [d.hint_artist, d.hint_track || name || d.hint_album].filter(Boolean).join(" — ");
                const cancellable = d.status === "queued" || d.status === "in_progress";
                const retryable = d.status === "failed";
                return (
                  <tr key={d.id}>
                    <td>
                      {label}
                      {d.error && <div className="error-text">{d.error}</div>}
                    </td>
                    <td className="muted">{d.slskd_username}</td>
                    <td>
                      <span className={`badge ${d.status}`}>{d.status.replace("_", " ")}</span>
                    </td>
                    <td>
                      <div className="progress-bar">
                        <div style={{ width: `${Math.min(100, d.progress_percent)}%` }} />
                      </div>
                    </td>
                    <td className="muted" title={d.final_path || ""}>
                      {d.final_path ? d.final_path.split("/").slice(-2).join("/") : "—"}
                    </td>
                    <td>
                      {cancellable && (
                        <button className="secondary" onClick={() => onCancel(d.id)}>
                          Cancel
                        </button>
                      )}
                      {retryable && (
                        <button
                          className="secondary"
                          onClick={() => onRetry(d.id)}
                          disabled={retrying === d.id}
                          title="Re-run tagging/organizing without re-downloading — the file already landed, only this step failed."
                        >
                          {retrying === d.id ? "Retrying..." : "Retry"}
                        </button>
                      )}
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
