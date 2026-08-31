import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, LibraryAlbumOut } from "../api/client";
import { libraryStore } from "../libraryStore";

interface ArtistSummary {
  artist: string;
  thumb: string | null;
  albumCount: number;
  trackCount: number;
  albumTitles: string[];
  // True when this artist has no owned albums at all -- added purely via
  // "Add Artist" to browse their discography, not backed by anything in
  // the Plex-derived library snapshot.
  trackedOnly: boolean;
}

function summarizeByArtist(albums: LibraryAlbumOut[], trackedNames: string[]): ArtistSummary[] {
  // Keyed case-insensitively -- someone can (and did) type "steps" into Add
  // Artist while Plex/MusicBrainz's canonical casing is "Steps"; without
  // normalizing, those show up as two unrelated cards instead of merging
  // once the tracked artist's first album actually gets downloaded.
  const byArtist = new Map<string, ArtistSummary>();
  for (const a of albums) {
    const key = a.artist.toLowerCase();
    let entry = byArtist.get(key);
    if (!entry) {
      entry = { artist: a.artist, thumb: a.artist_thumb, albumCount: 0, trackCount: 0, albumTitles: [], trackedOnly: false };
      byArtist.set(key, entry);
    }
    entry.albumCount += 1;
    entry.trackCount += a.track_count ?? 0;
    entry.albumTitles.push(a.album);
    if (!entry.thumb && a.artist_thumb) entry.thumb = a.artist_thumb;
  }
  // A tracked artist who already owns something just shows up as a normal
  // (non-tracked-only) card above, under the owned entry's (canonical)
  // casing -- tracking them again would be a no-op, so only artists with
  // zero owned albums get the "tracked only" card.
  for (const name of trackedNames) {
    const key = name.toLowerCase();
    if (!byArtist.has(key)) {
      byArtist.set(key, { artist: name, thumb: null, albumCount: 0, trackCount: 0, albumTitles: [], trackedOnly: true });
    }
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
          {artist.trackedOnly
            ? "Not in library yet"
            : `${artist.albumCount} album${artist.albumCount === 1 ? "" : "s"} · ${artist.trackCount} track${artist.trackCount === 1 ? "" : "s"}`}
        </div>
      </div>
    </Link>
  );
}

export default function LibraryPage() {
  const [albums, setAlbums] = useState<LibraryAlbumOut[] | null>(libraryStore.albums);
  const [trackedArtists, setTrackedArtists] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newArtist, setNewArtist] = useState("");
  const [adding, setAdding] = useState(false);
  const navigate = useNavigate();

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

  function loadTrackedArtists() {
    api
      .listTrackedArtists()
      .then((rows) => setTrackedArtists(rows.map((r) => r.artist)))
      .catch(() => {
        // Non-critical -- the owned-album grid still works without this.
      });
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
    loadTrackedArtists();
  }, []);

  async function onAddArtist(e: FormEvent) {
    e.preventDefault();
    const name = newArtist.trim();
    if (!name || adding) return;
    setAdding(true);
    setError(null);
    try {
      const tracked = await api.trackArtist(name);
      setNewArtist("");
      loadTrackedArtists();
      // Go straight to their page -- browsing the discography is the whole
      // point of adding them, no reason to make someone click the new card.
      navigate(`/library/${encodeURIComponent(tracked.artist)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setAdding(false);
    }
  }

  const artists = useMemo(() => summarizeByArtist(albums || [], trackedArtists), [albums, trackedArtists]);
  const totalTracks = useMemo(() => artists.reduce((sum, a) => sum + a.trackCount, 0), [artists]);

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

      <form className="panel row" onSubmit={onAddArtist}>
        <input
          type="text"
          placeholder="Add an artist to browse (not in your library yet)..."
          value={newArtist}
          onChange={(e) => setNewArtist(e.target.value)}
          style={{ flex: 1 }}
        />
        <button type="submit" className="secondary" disabled={adding || !newArtist.trim()}>
          {adding ? "Adding..." : "+ Add Artist"}
        </button>
      </form>

      {error && <div className="panel error-text">{error}</div>}

      {albums && (
        <p className="muted" style={{ margin: "0 0 0.6rem" }}>
          {albums.length} album{albums.length === 1 ? "" : "s"} across {artists.length} artist
          {artists.length === 1 ? "" : "s"} · {totalTracks} track{totalTracks === 1 ? "" : "s"}
          {filter.trim() && ` — ${filtered.length} matching`}
        </p>
      )}

      {!loading && !error && filtered.length === 0 && (
        <div className="panel empty">
          {artists.length === 0
            ? 'Nothing here yet — click "Scan Plex Library" above, or add an artist to browse.'
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
