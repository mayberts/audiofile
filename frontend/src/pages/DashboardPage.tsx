import { ReactNode, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  AlbumTrackGapOut,
  DownloadOut,
  DownloadStatus,
  LibraryAlbumOut,
  TrackGapScanOut,
  WantedOut,
  WantedStatus,
} from "../api/client";
import { libraryStore } from "../libraryStore";

function StatusChip({ status, count }: { status: string; count: number }) {
  // Reuses the exact badge classes already used everywhere else in the
  // app (WantedPage/DownloadsPage rows) -- same status strings, same
  // colors, so "3 failed" here means the same shade of red it does there.
  return (
    <span className={`badge ${status}`} style={{ fontWeight: 600 }}>
      {count} {status.replace(/_/g, " ")}
    </span>
  );
}

function StatTile({
  to,
  label,
  value,
  loading,
  error,
  sub,
}: {
  to: string;
  label: string;
  value: string;
  loading: boolean;
  error: boolean;
  sub?: ReactNode;
}) {
  return (
    <Link to={to} className="stat-tile">
      <div className="stat-tile-label">{label}</div>
      <div className="stat-tile-value">{loading ? "…" : error ? "—" : value}</div>
      {!loading && !error && sub}
      {error && <div className="stat-tile-sub muted">Couldn&apos;t load</div>}
    </Link>
  );
}

function statusCounts<T extends string>(items: { status: T }[]): Map<T, number> {
  const counts = new Map<T, number>();
  for (const item of items) counts.set(item.status, (counts.get(item.status) ?? 0) + 1);
  return counts;
}

export default function DashboardPage() {
  const [albums, setAlbums] = useState<LibraryAlbumOut[] | null>(libraryStore.albums);
  const [albumsError, setAlbumsError] = useState(false);

  const [gaps, setGaps] = useState<AlbumTrackGapOut[] | null>(libraryStore.trackGaps);
  const [gapsError, setGapsError] = useState(false);
  const [scan, setScan] = useState<TrackGapScanOut | null | undefined>(undefined);

  const [wanted, setWanted] = useState<WantedOut[] | null>(null);
  const [wantedError, setWantedError] = useState(false);

  const [downloads, setDownloads] = useState<DownloadOut[] | null>(null);
  const [downloadsError, setDownloadsError] = useState(false);

  useEffect(() => {
    // Each tile fetches (and fails) independently -- Plex not being
    // configured yet, say, shouldn't blank out the Wanted/Downloads tiles
    // too.
    if (libraryStore.albums !== null) {
      setAlbums(libraryStore.albums);
    } else {
      api
        .getLibrary()
        .then((data) => {
          libraryStore.albums = data;
          setAlbums(data);
        })
        .catch(() => setAlbumsError(true));
    }

    if (libraryStore.trackGaps !== null) {
      setGaps(libraryStore.trackGaps);
    } else {
      api
        .listTrackGaps()
        .then((data) => {
          libraryStore.trackGaps = data;
          setGaps(data);
        })
        .catch(() => setGapsError(true));
    }
    api.getTrackGapScan().then(setScan).catch(() => setScan(null));

    api.listWanted().then(setWanted).catch(() => setWantedError(true));
    api.listDownloads().then(setDownloads).catch(() => setDownloadsError(true));
  }, []);

  const libraryStats = useMemo(() => {
    if (!albums) return null;
    const artists = new Set(albums.map((a) => a.artist.toLowerCase()));
    const tracks = albums.reduce((sum, a) => sum + (a.track_count ?? 0), 0);
    return { albumCount: albums.length, artistCount: artists.size, trackCount: tracks };
  }, [albums]);

  const wantedCounts = useMemo(() => statusCounts<WantedStatus>(wanted || []), [wanted]);
  const downloadCounts = useMemo(() => statusCounts<DownloadStatus>(downloads || []), [downloads]);
  const activeDownloads = ["queued", "in_progress", "tagging"].reduce(
    (sum, s) => sum + (downloadCounts.get(s as DownloadStatus) ?? 0),
    0,
  );

  const scanRunning = scan?.status === "running";

  return (
    <div>
      <h1>Home</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        A quick look at your library, downloads, and what's still on the way.
      </p>

      <div className="stat-grid">
        <StatTile
          to="/library"
          label="Library"
          loading={!albums && !albumsError}
          error={albumsError}
          value={libraryStats ? String(libraryStats.albumCount) : "0"}
          sub={
            libraryStats && (
              <div className="stat-tile-sub muted">
                {libraryStats.artistCount} artist{libraryStats.artistCount === 1 ? "" : "s"} ·{" "}
                {libraryStats.trackCount} track{libraryStats.trackCount === 1 ? "" : "s"}
              </div>
            )
          }
        />

        <StatTile
          to="/missing-tracks"
          label="Missing tracks"
          loading={gaps === null && !gapsError}
          error={gapsError}
          value={gaps ? String(gaps.length) : "0"}
          sub={
            <div className="stat-tile-sub muted">
              {scanRunning
                ? `scanning — ${scan!.checked_albums} of ${scan!.total_albums || "?"} checked`
                : scan === null
                  ? "never scanned"
                  : gaps && gaps.length === 0
                    ? "nothing missing"
                    : `album${gaps && gaps.length === 1 ? "" : "s"} with gaps`}
            </div>
          }
        />

        <StatTile
          to="/wanted"
          label="Wanted list"
          loading={!wanted && !wantedError}
          error={wantedError}
          value={wanted ? String(wanted.length) : "0"}
          sub={
            wanted &&
            wanted.length > 0 && (
              <div className="stat-tile-sub row" style={{ flexWrap: "wrap", gap: "0.3rem" }}>
                {[...wantedCounts.entries()].map(([status, count]) => (
                  <StatusChip key={status} status={status} count={count} />
                ))}
              </div>
            )
          }
        />

        <StatTile
          to="/downloads"
          label="Active downloads"
          loading={!downloads && !downloadsError}
          error={downloadsError}
          value={String(activeDownloads)}
          sub={
            downloads &&
            downloads.length > 0 && (
              <div className="stat-tile-sub row" style={{ flexWrap: "wrap", gap: "0.3rem" }}>
                {[...downloadCounts.entries()]
                  .filter(([status]) => status !== "cancelled")
                  .map(([status, count]) => (
                    <StatusChip key={status} status={status} count={count} />
                  ))}
              </div>
            )
          }
        />
      </div>
    </div>
  );
}
