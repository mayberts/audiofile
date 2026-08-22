import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, LibraryAlbumOut, MissingTrackOut, ReleaseEditionOut, TrackCheckOut, TrackOut } from "../api/client";
import ArtworkPicker from "../components/ArtworkPicker";
import ReleaseSearchPicker from "../components/ReleaseSearchPicker";
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

  const [thumbOverride, setThumbOverride] = useState<string | null>(null);
  const [showArtworkPicker, setShowArtworkPicker] = useState(false);
  const displayThumb = thumbOverride ?? albumEntry?.thumb ?? null;

  function onArtworkChanged(thumb: string | null) {
    setThumbOverride(thumb);
    if (libraryStore.albums) {
      libraryStore.albums = libraryStore.albums.map((a) =>
        a.artist === artistName && a.album === albumName ? { ...a, thumb } : a,
      );
    }
  }

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
        {albumEntry?.rating_key && (
          <button
            className="image-edit-trigger"
            onClick={() => setShowArtworkPicker(true)}
            title="Change artwork"
          >
            {displayThumb ? (
              <img
                src={api.plexImageUrl(displayThumb)}
                alt={albumName}
                style={{ width: 96, height: 96, borderRadius: 8, objectFit: "cover", flexShrink: 0 }}
              />
            ) : (
              <div
                className="artist-card-fallback"
                style={{ width: 96, height: 96, borderRadius: 8, fontSize: "1.6rem" }}
              >
                {albumName.charAt(0).toUpperCase()}
              </div>
            )}
            <div className="image-edit-overlay">Edit</div>
          </button>
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

      {albumEntry && tracks && tracks.length > 0 && (
        <MissingTracksPanel artist={artistName} albumName={albumName} ratingKey={albumEntry.rating_key ?? ""} />
      )}

      {showArtworkPicker && albumEntry?.rating_key && (
        <ArtworkPicker
          ratingKey={albumEntry.rating_key}
          onClose={() => setShowArtworkPicker(false)}
          onChanged={onArtworkChanged}
        />
      )}
    </div>
  );
}

function MissingTracksPanel({
  artist,
  albumName,
  ratingKey,
}: {
  artist: string;
  albumName: string;
  ratingKey: string;
}) {
  const [result, setResult] = useState<TrackCheckOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // null = auto (search_release()'s best guess); set once someone picks a
  // specific release via the search picker, e.g. a deluxe/bonus-disc
  // reissue MusicBrainz lists under its own separate title.
  const [releaseOverride, setReleaseOverride] = useState<{ mbid: string; title: string } | null>(null);
  const [pickingRelease, setPickingRelease] = useState(false);

  function check(mbid?: string | null) {
    setLoading(true);
    setError(null);
    api
      .getTrackCheck(ratingKey, mbid)
      .then(setResult)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  function onPickRelease(release: ReleaseEditionOut) {
    setPickingRelease(false);
    setReleaseOverride({ mbid: release.release_mbid, title: release.title });
    check(release.release_mbid);
  }

  function resetToAutomatic() {
    setReleaseOverride(null);
    check(null);
  }

  return (
    <div style={{ marginTop: "1rem" }}>
      {!result && !loading && (
        <button className="secondary" onClick={() => check(releaseOverride?.mbid)} disabled={!ratingKey}>
          Check for Missing Tracks
        </button>
      )}
      {loading && <div className="panel">Checking MusicBrainz...</div>}
      {error && (
        <div className="panel">
          <p className="error-text" style={{ margin: 0 }}>
            {error}
          </p>
          <button className="secondary" style={{ marginTop: "0.7rem" }} onClick={() => check(releaseOverride?.mbid)}>
            Try again
          </button>
        </div>
      )}
      {result && !result.checked && (
        <div className="panel empty">Couldn't find this album on MusicBrainz to compare tracks.</div>
      )}
      {result && result.checked && (
        <>
          <div className="row" style={{ justifyContent: "space-between", margin: "0.6rem 0" }}>
            <span className="muted">
              Comparing against: {result.release_title || "MusicBrainz's best match"}
            </span>
            <div className="row">
              <button className="secondary" onClick={() => setPickingRelease(true)}>
                Compare against a different edition
              </button>
              {releaseOverride && (
                <button className="secondary" onClick={resetToAutomatic}>
                  Reset to automatic match
                </button>
              )}
            </div>
          </div>
          {result.missing_tracks.length === 0 && (
            <div className="panel empty">
              Nothing missing — all {result.expected_total} tracks MusicBrainz lists for this release are here.
            </div>
          )}
          {result.missing_tracks.length > 0 && (
            <MissingTracksTable
              // The owned album's own title, always -- not
              // result.release_title. Topping up against a differently-
              // titled comparison release (a deluxe reissue, a bonus disc
              // like "Album: Side B") should enrich this same library
              // entry, not fork a second Plex album under the compared
              // release's name.
              artist={artist}
              album={albumName}
              releaseMbid={result.release_mbid}
              tracks={result.missing_tracks}
            />
          )}
        </>
      )}

      {pickingRelease && (
        <ReleaseSearchPicker
          artist={artist}
          initialQuery={albumName}
          onClose={() => setPickingRelease(false)}
          onPick={onPickRelease}
        />
      )}
    </div>
  );
}

function MissingTracksTable({
  artist,
  album,
  releaseMbid,
  tracks,
}: {
  artist: string;
  album: string;
  releaseMbid: string | null;
  tracks: MissingTrackOut[];
}) {
  const [added, setAdded] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onAdd(track: MissingTrackOut) {
    setPending(track.title);
    setError(null);
    try {
      await api.createWanted({ artist, album, track: track.title, release_mbid: releaseMbid });
      setAdded((prev) => new Set(prev).add(track.title));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="panel">
      <h2>Missing Tracks</h2>
      {error && <div className="error-text" style={{ marginBottom: "0.6rem" }}>{error}</div>}
      <table>
        <thead>
          <tr>
            <th style={{ width: "3rem" }}>#</th>
            <th>Title</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {[...tracks]
            .sort((a, b) => (a.track_number ?? 0) - (b.track_number ?? 0))
            .map((t) => (
              <tr key={t.title}>
                <td className="muted">{t.track_number ?? "—"}</td>
                <td>{t.title}</td>
                <td>
                  {added.has(t.title) ? (
                    <span className="badge downloaded">In wanted list</span>
                  ) : (
                    <button className="secondary" onClick={() => onAdd(t)} disabled={pending === t.title}>
                      {pending === t.title ? "..." : "Add to Wanted"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}
