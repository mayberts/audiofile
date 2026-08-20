import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, LibraryAlbumOut } from "../api/client";
import { libraryStore } from "../libraryStore";

export default function ArtistDetailPage() {
  const { artist: artistName = "" } = useParams<{ artist: string }>();
  const [albums, setAlbums] = useState<LibraryAlbumOut[] | null>(libraryStore.albums);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (libraryStore.albums === null) {
      setLoading(true);
      setError(null);
      api
        .getLibrary()
        .then((data) => {
          libraryStore.albums = data;
          setAlbums(data);
        })
        .catch((err) => setError(err instanceof Error ? err.message : String(err)))
        .finally(() => setLoading(false));
    }
  }, []);

  const artistAlbums = useMemo(() => {
    if (!albums) return [];
    return albums
      .filter((a) => a.artist === artistName)
      .sort((a, b) => (a.year ?? 0) - (b.year ?? 0) || a.album.localeCompare(b.album));
  }, [albums, artistName]);

  const artistThumb = artistAlbums.find((a) => a.artist_thumb)?.artist_thumb ?? null;
  const artistRatingKey = artistAlbums.find((a) => a.artist_rating_key)?.artist_rating_key ?? null;
  const totalTracks = artistAlbums.reduce((sum, a) => sum + (a.track_count ?? 0), 0);

  const [bio, setBio] = useState<string | null>(null);
  useEffect(() => {
    setBio(null);
    if (!artistRatingKey) return;
    api
      .getArtistBio(artistRatingKey)
      .then((res) => setBio(res.summary))
      .catch(() => setBio(null));
  }, [artistRatingKey]);

  return (
    <div>
      <p style={{ marginBottom: "0.8rem" }}>
        <Link to="/library" className="muted">
          &larr; Back to Library
        </Link>
      </p>

      <div className="row" style={{ alignItems: "center", marginBottom: "1rem", gap: "1rem" }}>
        {artistThumb && (
          <img
            src={api.plexImageUrl(artistThumb)}
            alt={artistName}
            style={{ width: 72, height: 72, borderRadius: 8, objectFit: "cover", flexShrink: 0 }}
          />
        )}
        <div>
          <h1 style={{ margin: 0 }}>{artistName}</h1>
          {artistAlbums.length > 0 && (
            <p className="muted" style={{ margin: "0.2rem 0 0" }}>
              {artistAlbums.length} album{artistAlbums.length === 1 ? "" : "s"} · {totalTracks} track
              {totalTracks === 1 ? "" : "s"}
            </p>
          )}
        </div>
      </div>

      {bio && (
        <div className="panel">
          <p style={{ margin: 0, lineHeight: 1.5 }}>{bio}</p>
        </div>
      )}

      {loading && <div className="panel">Loading...</div>}
      {error && <div className="panel error-text">{error}</div>}

      {!loading && !error && artistAlbums.length === 0 && (
        <div className="panel empty">No albums found for this artist.</div>
      )}

      {artistAlbums.length > 0 && (
        <div className="artist-grid">
          {artistAlbums.map((a) => (
            <AlbumCard key={a.album} album={a} />
          ))}
        </div>
      )}
    </div>
  );
}

function AlbumCard({ album }: { album: LibraryAlbumOut }) {
  const [imgFailed, setImgFailed] = useState(false);
  const showImage = album.thumb && !imgFailed;

  return (
    <Link
      className="artist-card"
      to={`/library/${encodeURIComponent(album.artist)}/${encodeURIComponent(album.album)}`}
    >
      {showImage ? (
        <img
          src={api.plexImageUrl(album.thumb!)}
          alt={album.album}
          loading="lazy"
          onError={() => setImgFailed(true)}
        />
      ) : (
        <div className="artist-card-fallback">{album.album.charAt(0).toUpperCase()}</div>
      )}
      <div className="artist-card-label">
        <div className="artist-card-name">{album.album}</div>
        <div className="artist-card-meta">
          {album.year ?? "—"} · {album.track_count ?? "—"} track{album.track_count === 1 ? "" : "s"}
        </div>
      </div>
    </Link>
  );
}
