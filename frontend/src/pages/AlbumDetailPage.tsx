import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, LibraryAlbumOut, TrackOut } from "../api/client";
import { libraryStore } from "../libraryStore";

function formatDuration(ms: number | null): string {
  if (!ms) return "—";
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

export default function AlbumDetailPage() {
  const { artist: artistName = "", album: albumName = "" } = useParams<{ artist: string; album: string }>();
  const [albums, setAlbums] = useState<LibraryAlbumOut[] | null>(libraryStore.albums);
  const [tracks, setTracks] = useState<TrackOut[] | null>(null);
  const [loadingLibrary, setLoadingLibrary] = useState(false);
  const [loadingTracks, setLoadingTracks] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (libraryStore.albums === null) {
      setLoadingLibrary(true);
      api
        .getLibrary()
        .then((data) => {
          libraryStore.albums = data;
          setAlbums(data);
        })
        .catch((err) => setError(err instanceof Error ? err.message : String(err)))
        .finally(() => setLoadingLibrary(false));
    }
  }, []);

  const albumEntry = useMemo(
    () => (albums || []).find((a) => a.artist === artistName && a.album === albumName) ?? null,
    [albums, artistName, albumName],
  );

  useEffect(() => {
    if (!albumEntry?.rating_key) return;
    setLoadingTracks(true);
    setError(null);
    api
      .getAlbumTracks(albumEntry.rating_key)
      .then(setTracks)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoadingTracks(false));
  }, [albumEntry?.rating_key]);

  return (
    <div>
      <p style={{ marginBottom: "0.8rem" }}>
        <Link to={`/library/${encodeURIComponent(artistName)}`} className="muted">
          &larr; Back to {artistName}
        </Link>
      </p>

      <div className="row" style={{ alignItems: "center", marginBottom: "1rem", gap: "1rem" }}>
        {albumEntry?.thumb && (
          <img
            src={api.plexImageUrl(albumEntry.thumb)}
            alt={albumName}
            style={{ width: 96, height: 96, borderRadius: 8, objectFit: "cover", flexShrink: 0 }}
          />
        )}
        <div>
          <h1 style={{ margin: 0 }}>{albumName}</h1>
          <p className="muted" style={{ margin: "0.2rem 0 0" }}>
            {artistName}
            {albumEntry?.year ? ` · ${albumEntry.year}` : ""}
            {albumEntry?.track_count ? ` · ${albumEntry.track_count} tracks` : ""}
          </p>
        </div>
      </div>

      {(loadingLibrary || loadingTracks) && <div className="panel">Loading...</div>}
      {error && <div className="panel error-text">{error}</div>}

      {!loadingLibrary && !albumEntry && !error && (
        <div className="panel empty">Couldn't find that album — try going back to Library and rescanning.</div>
      )}

      {tracks && tracks.length === 0 && !loadingTracks && (
        <div className="panel empty">No tracks found for this album.</div>
      )}

      {tracks && tracks.length > 0 && (
        <div className="panel">
          <table>
            <thead>
              <tr>
                <th style={{ width: "3rem" }}>#</th>
                <th>Title</th>
                <th style={{ width: "5rem" }}>Length</th>
              </tr>
            </thead>
            <tbody>
              {tracks.map((t, i) => (
                <tr key={i}>
                  <td className="muted">{t.track_number ?? i + 1}</td>
                  <td>{t.title}</td>
                  <td className="muted">{formatDuration(t.duration_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
