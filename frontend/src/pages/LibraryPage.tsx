import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, LibraryAlbumOut } from "../api/client";
import { libraryStore } from "../libraryStore";

interface ArtistSummary {
  artist: string;
  thumb: string | null;
  albumCount: number;
  trackCount: number;
  albumTitles: string[];
}

function summarizeByArtist(albums: LibraryAlbumOut[]): ArtistSummary[] {
  const byArtist = new Map<string, ArtistSummary>();
  for (const a of albums) {
    let entry = byArtist.get(a.artist);
    if (!entry) {
      entry = { artist: a.artist, thumb: a.artist_thumb, albumCount: 0, trackCount: 0, albumTitles: [] };
      byArtist.set(a.artist, entry);
    }
    entry.albumCount += 1;
    entry.trackCount += a.track_count ?? 0;
    entry.albumTitles.push(a.album);
    if (!entry.thumb && a.artist_thumb) entry.thumb = a.artist_thumb;
  }
  return [...byArtist.values()].sort((x, y) => x.artist.localeCompare(y.artist));
}

function ArtistCard({ artist }: { artist: ArtistSummary }) {
  const [imgFailed, setImgFailed] = useState(false);
  const showImage = artist.thumb && !imgFailed;

  return (
    <Link className="artist-card" to={`/library/${encodeURIComponent(artist.artist)}`}>
      {showImage ? (
        <img
          src={api.plexImageUrl(artist.thumb!)}
          alt={artist.artist}
          loading="lazy"
          onError={() => setImgFailed(true)}
        />
      ) : (
        <div className="artist-card-fallback">{artist.artist.charAt(0).toUpperCase()}</div>
      )}
      <div className="artist-card-label">
        <div className="artist-card-name">{artist.artist}</div>
        <div className="artist-card-meta">
          {artist.albumCount} album{artist.albumCount === 1 ? "" : "s"} · {artist.trackCount} track
          {artist.trackCount === 1 ? "" : "s"}
        </div>
      </div>
    </Link>
  );
}

export default function LibraryPage() {
  const [albums, setAlbums] = useState<LibraryAlbumOut[] | null>(libraryStore.albums);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getLibrary();
      libraryStore.albums = data;
      setAlbums(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function rescan() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.scanLibrary();
      libraryStore.albums = data;
      setAlbums(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (libraryStore.albums === null) {
      load();
    }
  }, []);

  const artists = useMemo(() => summarizeByArtist(albums || []), [albums]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return artists;
    return artists.filter(
      (a) => a.artist.toLowerCase().includes(q) || a.albumTitles.some((t) => t.toLowerCase().includes(q)),
    );
  }, [artists, filter]);

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
        <button className="secondary" onClick={rescan} disabled={loading}>
          {loading ? "Scanning..." : "Scan Plex Library"}
        </button>
      </div>

      {error && <div className="panel error-text">{error}</div>}

      {albums && (
        <p className="muted" style={{ margin: "0 0 0.6rem" }}>
          {albums.length} album{albums.length === 1 ? "" : "s"} across {artists.length} artist
          {artists.length === 1 ? "" : "s"}
          {filter.trim() && ` — ${filtered.length} matching`}
        </p>
      )}

      {!albums && !loading && !error && <div className="panel empty">Nothing loaded yet.</div>}
      {albums && filtered.length === 0 && (
        <div className="panel empty">
          {artists.length === 0
            ? 'Nothing scanned yet — click "Scan Plex Library" above.'
            : "No artists match that filter."}
        </div>
      )}

      {filtered.length > 0 && (
        <div className="artist-grid">
          {filtered.map((a) => (
            <ArtistCard key={a.artist} artist={a} />
          ))}
        </div>
      )}
    </div>
  );
}
