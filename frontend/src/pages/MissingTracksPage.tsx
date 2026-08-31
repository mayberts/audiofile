import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, AlbumTrackGapOut, TrackGapScanOut } from "../api/client";

export default function MissingTracksPage() {
  const [scan, setScan] = useState<TrackGapScanOut | null | undefined>(undefined);
  const [gaps, setGaps] = useState<AlbumTrackGapOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  // Avoids stacking up a second polling interval if refresh() is still
  // in flight (a slow request) when the next tick fires.
  const refreshing = useRef(false);

  async function refresh() {
    if (refreshing.current) return;
    refreshing.current = true;
    try {
      const [scanStatus, gapList] = await Promise.all([api.getTrackGapScan(), api.listTrackGaps()]);
      setScan(scanStatus);
      setGaps(gapList);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      refreshing.current = false;
    }
  }

  useEffect(() => {
    refresh();
    // Polls continuously (not just while running) so a scan started from
    // another tab/device still shows up here without a manual reload.
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, []);

  async function onStart() {
    setStarting(true);
    setError(null);
    try {
      const started = await api.startTrackGapScan();
      setScan(started);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setStarting(false);
    }
  }

  async function onCancel() {
    setCancelling(true);
    setError(null);
    try {
      await api.cancelTrackGapScan();
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCancelling(false);
    }
  }

  const running = scan?.status === "running";
  const progressPercent =
    running && scan.total_albums > 0 ? Math.min(100, (scan.checked_albums / scan.total_albums) * 100) : 0;

  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Missing Tracks</h1>
        <div className="row">
          {running ? (
            <button className="secondary" onClick={onCancel} disabled={cancelling}>
              {cancelling ? "Cancelling..." : "Cancel Scan"}
            </button>
          ) : (
            <button onClick={onStart} disabled={starting}>
              {starting ? "Starting..." : "Scan for Missing Tracks"}
            </button>
          )}
        </div>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        Checks every album in your library against MusicBrainz's tracklist and lists only the ones
        actually missing tracks. Runs in the background — safe to leave this page and come back later.
      </p>

      {error && <div className="panel error-text">{error}</div>}

      {scan === undefined && !error && <div className="panel">Loading...</div>}

      {running && (
        <div className="panel">
          <p style={{ margin: "0 0 0.5rem" }}>
            Checked {scan.checked_albums} of {scan.total_albums || "?"} albums...
          </p>
          <div className="progress-bar">
            <div style={{ width: `${progressPercent}%` }} />
          </div>
        </div>
      )}

      {scan && scan.status === "failed" && (
        <div className="panel error-text">Last scan failed{scan.last_error ? `: ${scan.last_error}` : "."}</div>
      )}

      {scan === null && !running && (
        <div className="panel empty">Never scanned yet — click "Scan for Missing Tracks" above.</div>
      )}

      {scan && gaps.length === 0 && scan.status === "completed" && (
        <div className="panel empty">Nothing missing — every album checked has all its tracks.</div>
      )}

      {gaps.length > 0 && (
        <>
          <p className="muted">
            {gaps.length} album{gaps.length === 1 ? "" : "s"} with missing tracks.
          </p>
          <div className="panel" style={{ padding: 0 }}>
            {gaps.map((g) => (
              <Link
                key={g.rating_key}
                to={`/library/${encodeURIComponent(g.artist)}/${encodeURIComponent(g.album)}`}
                className="row"
                style={{
                  padding: "0.5rem 1rem",
                  gap: "0.8rem",
                  borderTop: "1px solid var(--border)",
                  color: "inherit",
                  textDecoration: "none",
                }}
              >
                {g.thumb ? (
                  <img
                    src={api.plexImageUrl(g.thumb)}
                    alt=""
                    loading="lazy"
                    style={{ width: 40, height: 40, borderRadius: 6, objectFit: "cover", flexShrink: 0 }}
                  />
                ) : (
                  <div className="artist-card-fallback" style={{ width: 40, height: 40, borderRadius: 6, fontSize: "1rem", flexShrink: 0 }}>
                    {g.album.charAt(0).toUpperCase()}
                  </div>
                )}
                <div style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {g.album}
                  <div className="muted">{g.artist}</div>
                </div>
                <span className="badge not_found" style={{ flexShrink: 0 }}>
                  Missing {g.missing_count}
                  {g.expected_total ? ` of ${g.expected_total}` : ""}
                </span>
              </Link>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
