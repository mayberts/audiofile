import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, AlbumTrackGapOut, TrackGapScanOut } from "../api/client";

type SortKey = "percent" | "artist";

function percentMissing(g: AlbumTrackGapOut): number {
  // Falls back to the raw count when a release's total track count isn't
  // known (expected_total is null) -- rare, but sorting by 0 in that case
  // would bury a genuinely large gap at the bottom of a "worst first" sort.
  return g.expected_total ? g.missing_count / g.expected_total : g.missing_count;
}

export default function MissingTracksPage() {
  const [scan, setScan] = useState<TrackGapScanOut | null | undefined>(undefined);
  const [gaps, setGaps] = useState<AlbumTrackGapOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [query, setQuery] = useState("");
  const [sortBy, setSortBy] = useState<SortKey>("percent");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // Add-to-Wanted only ever queues a search -- it doesn't remove the track
  // from this album's gap (that only happens once it's actually downloaded
  // and a future scan/refresh notices), so "already added" has to be
  // tracked locally instead of derived from the gap data itself. Same
  // pattern AlbumDetailPage's own missing-tracks table already uses.
  const [addedTracks, setAddedTracks] = useState<Record<string, Set<string>>>({});
  const [bulkAdding, setBulkAdding] = useState<Set<string>>(new Set());
  const [pendingAddKey, setPendingAddKey] = useState<string | null>(null);
  const [pendingDismissKey, setPendingDismissKey] = useState<string | null>(null);
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

  function toggleExpand(ratingKey: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(ratingKey)) next.delete(ratingKey);
      else next.add(ratingKey);
      return next;
    });
  }

  function markAdded(ratingKey: string, titles: string[]) {
    setAddedTracks((prev) => {
      const next = { ...prev };
      const set = new Set(next[ratingKey] ?? []);
      titles.forEach((t) => set.add(t));
      next[ratingKey] = set;
      return next;
    });
  }

  async function onAddOne(g: AlbumTrackGapOut, title: string) {
    setPendingAddKey(`${g.rating_key}::${title}`);
    setActionError(null);
    try {
      await api.createWanted({ artist: g.artist, album: g.album, track: title, release_mbid: g.release_mbid });
      markAdded(g.rating_key, [title]);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setPendingAddKey(null);
    }
  }

  async function onAddAll(g: AlbumTrackGapOut) {
    const already = addedTracks[g.rating_key] ?? new Set<string>();
    const toAdd = g.missing_tracks.filter((t) => !already.has(t));
    if (toAdd.length === 0) return;
    setBulkAdding((prev) => new Set(prev).add(g.rating_key));
    setActionError(null);
    try {
      await Promise.all(
        toAdd.map((t) =>
          api.createWanted({ artist: g.artist, album: g.album, track: t, release_mbid: g.release_mbid }),
        ),
      );
      markAdded(g.rating_key, toAdd);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setBulkAdding((prev) => {
        const next = new Set(prev);
        next.delete(g.rating_key);
        return next;
      });
    }
  }

  async function onDismiss(g: AlbumTrackGapOut, title: string) {
    setPendingDismissKey(`${g.rating_key}::${title}`);
    setActionError(null);
    try {
      await api.dismissTrack(g.rating_key, title);
      // The backend already recomputed this album's persisted gap row (and
      // deletes it outright if that was the last missing track) -- pulling
      // fresh rather than patching local state locally keeps this in
      // lockstep with what the server actually decided.
      await refresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setPendingDismissKey(null);
    }
  }

  const running = scan?.status === "running";
  const progressPercent =
    running && scan.total_albums > 0 ? Math.min(100, (scan.checked_albums / scan.total_albums) * 100) : 0;

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const rows = q
      ? gaps.filter((g) => g.artist.toLowerCase().includes(q) || g.album.toLowerCase().includes(q))
      : gaps;
    return [...rows].sort((a, b) =>
      sortBy === "artist"
        ? a.artist.localeCompare(b.artist) || a.album.localeCompare(b.album)
        : percentMissing(b) - percentMissing(a),
    );
  }, [gaps, query, sortBy]);

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
          <div className="row" style={{ justifyContent: "space-between", gap: "0.6rem", flexWrap: "wrap" }}>
            <input
              type="text"
              placeholder="Filter by artist or album..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{ flex: "1 1 16rem" }}
            />
            <select value={sortBy} onChange={(e) => setSortBy(e.target.value as SortKey)}>
              <option value="percent">Sort: most missing first</option>
              <option value="artist">Sort: artist A–Z</option>
            </select>
          </div>
          <p className="muted">
            {filtered.length === gaps.length
              ? `${gaps.length} album${gaps.length === 1 ? "" : "s"} with missing tracks.`
              : `${filtered.length} of ${gaps.length} albums with missing tracks.`}
          </p>

          {actionError && <div className="panel error-text">{actionError}</div>}

          {filtered.length === 0 && <div className="panel empty">No albums match "{query}".</div>}

          {filtered.length > 0 && (
            <div className="panel" style={{ padding: 0 }}>
              {filtered.map((g) => (
                <GapRow
                  key={g.rating_key}
                  gap={g}
                  isExpanded={expanded.has(g.rating_key)}
                  onToggleExpand={() => toggleExpand(g.rating_key)}
                  added={addedTracks[g.rating_key] ?? new Set()}
                  bulkAdding={bulkAdding.has(g.rating_key)}
                  pendingAddKey={pendingAddKey}
                  pendingDismissKey={pendingDismissKey}
                  onAddAll={() => onAddAll(g)}
                  onAddOne={(title) => onAddOne(g, title)}
                  onDismiss={(title) => onDismiss(g, title)}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function GapRow({
  gap: g,
  isExpanded,
  onToggleExpand,
  added,
  bulkAdding,
  pendingAddKey,
  pendingDismissKey,
  onAddAll,
  onAddOne,
  onDismiss,
}: {
  gap: AlbumTrackGapOut;
  isExpanded: boolean;
  onToggleExpand: () => void;
  added: Set<string>;
  bulkAdding: boolean;
  pendingAddKey: string | null;
  pendingDismissKey: string | null;
  onAddAll: () => void;
  onAddOne: (title: string) => void;
  onDismiss: (title: string) => void;
}) {
  const remainingToAdd = g.missing_tracks.filter((t) => !added.has(t)).length;

  return (
    <div style={{ borderTop: "1px solid var(--border)" }}>
      <div className="row" style={{ padding: "0.5rem 1rem", gap: "0.8rem" }}>
        <Link
          to={`/library/${encodeURIComponent(g.artist)}/${encodeURIComponent(g.album)}`}
          className="row"
          style={{ flex: 1, minWidth: 0, gap: "0.8rem", color: "inherit", textDecoration: "none" }}
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
        </Link>
        <span className="badge not_found" style={{ flexShrink: 0 }}>
          Missing {g.missing_count}
          {g.expected_total ? ` of ${g.expected_total}` : ""}
        </span>
        <button className="secondary" style={{ flexShrink: 0 }} onClick={onAddAll} disabled={bulkAdding || remainingToAdd === 0}>
          {bulkAdding ? "Adding..." : remainingToAdd === 0 ? "All added" : "Add all to Wanted"}
        </button>
        <button className="secondary" style={{ flexShrink: 0 }} onClick={onToggleExpand}>
          {isExpanded ? "Hide tracks" : "Show tracks"}
        </button>
      </div>

      {isExpanded && (
        <table style={{ margin: "0 1rem 0.6rem", width: "calc(100% - 2rem)" }}>
          <tbody>
            {g.missing_tracks.map((title) => {
              const addKey = `${g.rating_key}::${title}`;
              const isAdded = added.has(title);
              return (
                <tr key={title}>
                  <td style={{ padding: "0.25rem 0" }}>{title}</td>
                  <td style={{ width: "1%", whiteSpace: "nowrap", textAlign: "right" }}>
                    {isAdded ? (
                      <span className="badge downloaded">In wanted list</span>
                    ) : (
                      <button
                        className="secondary"
                        style={{ marginRight: "0.4rem" }}
                        onClick={() => onAddOne(title)}
                        disabled={pendingAddKey === addKey}
                      >
                        {pendingAddKey === addKey ? "..." : "Add to Wanted"}
                      </button>
                    )}
                    <button
                      className="secondary"
                      onClick={() => onDismiss(title)}
                      disabled={pendingDismissKey === addKey}
                      title="Not actually missing -- stop counting this track against this album"
                    >
                      {pendingDismissKey === addKey ? "..." : "Dismiss"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
